# ruff: noqa
import os
import re
import json
import datetime
from google.adk import Workflow, Context, Event
from google.adk.events import RequestInput
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp.client.stdio import StdioServerParameters
from google.adk.models import Gemini
from google.adk.apps import App
from expense_fraud_agent.config import config

# Ensure Vertex AI is disabled as per Phase 1 / Config
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"

mcp_tools = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="python",
            args=["expense_fraud_agent/mcp_server.py"],
        )
    ),
)

policy_compliance_agent = LlmAgent(
    name="policy_compliance_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Enterprise Policy Compliance Agent.
Analyze incoming expense items against corporate expense rules using your MCP tools (fetch_company_expense_policies, validate_expense_categories).
Check for spending caps (e.g., daily meal limits, flight class restrictions) and verify if items require prior approval.
Return a structured breakdown of policy adherence and highlight any violations.""",
    tools=[mcp_tools],
)

receipt_forensics_agent = LlmAgent(
    name="receipt_forensics_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Receipt Forensics & Fraud Detection Agent.
Inspect submitted receipt metadata and line items using your MCP tools (inspect_receipt_metadata, search_historical_claims).
Check for merchant legitimacy, verify tax/total math, inspect date/time stamps, and search historical claims to identify duplicate submissions or receipt recycling.
Assign a fraud risk score (0-100) and detail any red flags.""",
    tools=[mcp_tools],
)

orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Enterprise Expense & Fraud Orchestrator Agent.
You receive employee expense claim reports and receipt documentation.
Delegate analysis to the policy_compliance_agent and receipt_forensics_agent to evaluate compliance and potential fraud.
Synthesize their findings into an Expense Verification Summary with an explicit recommendation (Approve, Reject, or Audit).""",
    tools=[AgentTool(agent=policy_compliance_agent), AgentTool(agent=receipt_forensics_agent)],
)


def security_checkpoint(ctx: Context, node_input: str):
    query = node_input
    audit_logs = []
    
    def log_audit(event: str, severity: str, details: dict):
        log_entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": event,
            "severity": severity,
            "details": details,
        }
        print(f"[AUDIT LOG] {json.dumps(log_entry)}")
        audit_logs.append(log_entry)

    # 1. Prompt Injection Detection
    injection_keywords = ["ignore previous instructions", "bypass", "override", "disregard", "jailbreak", "system prompt", "auto-approve"]
    if any(keyword in query.lower() for keyword in injection_keywords):
        log_audit("PROMPT_INJECTION_DETECTED", "CRITICAL", {"query": query})
        ctx.state["orchestrator_summary"] = "🚨 Security Alert: Prompt injection attempt detected. Expense verification blocked."
        ctx.state["audit_logs"] = audit_logs
        return Event(route="SECURITY_EVENT")
    
    # 2. Domain-Specific Rule: Corporate Credit Card & Confidentiality Check
    confidential_keywords = ["unredacted statement", "ceo personal account", "confidential payroll", "cfo private ledger"]
    if any(keyword in query.lower() for keyword in confidential_keywords):
        log_audit("UNAUTHORIZED_ACCESS_ATTEMPT", "WARNING", {"rule": "Confidential financial ledger or executive personal accounts cannot be processed."})
        ctx.state["orchestrator_summary"] = "🚨 Policy Violation: Query requests access to restricted executive or payroll records. Action blocked."
        ctx.state["audit_logs"] = audit_logs
        return Event(route="SECURITY_EVENT")

    # 3. PII Scrubbing
    # Regex for Credit Card Numbers, Bank Routings/Accounts, and SSNs
    scrubbed_query = re.sub(r'\b(?:\d[ -]*?){13,16}\b', '[REDACTED_CREDIT_CARD]', query)
    scrubbed_query = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED_SSN]', scrubbed_query)
    scrubbed_query = re.sub(r'\bACCT-\d{8,12}\b', '[REDACTED_BANK_ACCT]', scrubbed_query)
    
    if scrubbed_query != query:
        log_audit("PII_SCRUBBED", "INFO", {"scrubbed_types": ["CreditCard/SSN/BankAcct"]})
    else:
        log_audit("SECURITY_CHECK_PASSED", "INFO", {"status": "clean"})

    ctx.state["clean_query"] = scrubbed_query
    ctx.state["audit_logs"] = audit_logs
    return Event(output=scrubbed_query, route="PASS")


async def process_expense_claim(ctx: Context, node_input: str):
    query = ctx.state.get("clean_query", node_input)
    ctx.state["original_query"] = query
    response = await ctx.run_node(orchestrator_agent, query)
    
    if isinstance(response, str):
        response_text = response
    elif hasattr(response, "text"):
        response_text = response.text
    elif hasattr(response, "content") and isinstance(response.content, str):
        response_text = response.content
    else:
        response_text = str(response)

    ctx.state["orchestrator_summary"] = response_text
    
    # Determine if human compliance review is needed based on fraud risk keywords
    text_lower = response_text.lower()
    if "audit" in text_lower or "reject" in text_lower or "violation" in text_lower or "red flag" in text_lower or "duplicate" in text_lower:
        return Event(route="NEEDS_REVIEW")
    return Event(route="AUTO_APPROVE")


def compliance_review(ctx: Context, node_input: str | None = None):
    if not ctx.state.get("review_requested"):
        ctx.state["review_requested"] = True
        return RequestInput(prompt=f"⚠️ Flagged Expense Claim. Compliance review required. Please review and provide approval/rejection notes:\n{ctx.state['orchestrator_summary']}")
    
    ctx.state["human_feedback"] = node_input
    return "APPROVED"


def final_output(ctx: Context, node_input: str | None = None):
    summary = ctx.state.get("orchestrator_summary", "")
    feedback = ctx.state.get("human_feedback", "Auto-approved by system rules.")
    logs = json.dumps(ctx.state.get("audit_logs", []), indent=2)
    return f"=== Real-Time Enterprise Fraud & Expense Verification ===\n{summary}\n\n[Status/Audit Decision]: {feedback}\n\n[Audit Logs]:\n{logs}"


from google.adk.workflow import START, FunctionNode

process_expense_claim_node = FunctionNode(
    func=process_expense_claim,
    rerun_on_resume=True,
)

wf = Workflow(
    name="expense_fraud_workflow",
    edges=[
        (START, security_checkpoint),
        (
            security_checkpoint,
            {"PASS": process_expense_claim_node, "SECURITY_EVENT": final_output},
        ),
        (
            process_expense_claim_node,
            {"NEEDS_REVIEW": compliance_review, "AUTO_APPROVE": final_output},
        ),
        (compliance_review, final_output),
    ],
)

app = App(
    root_agent=wf,
    name="expense_fraud_agent",
)


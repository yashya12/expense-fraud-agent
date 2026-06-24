import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult

server = Server("expense_fraud_mcp_server")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="fetch_company_expense_policies",
            description="Returns corporate rules, spending caps, and pre-approval requirements for a specific expense category.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Expense category (e.g. Meals, Flights, Entertainment, Hotel)"}
                },
                "required": ["category"]
            }
        ),
        Tool(
            name="validate_expense_categories",
            description="Checks if an item name and amount fall into prohibited spending categories or violate daily caps.",
            inputSchema={
                "type": "object",
                "properties": {
                    "item_name": {"type": "string", "description": "Name or description of the expense item"},
                    "amount": {"type": "number", "description": "Total amount claimed for the item"}
                },
                "required": ["item_name", "amount"]
            }
        ),
        Tool(
            name="inspect_receipt_metadata",
            description="Validates tax calculations and checks if the merchant is a legitimate, verifiable business entity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "receipt_id": {"type": "string", "description": "Receipt invoice number or ID"},
                    "merchant": {"type": "string", "description": "Merchant or vendor name"},
                    "total_amount": {"type": "number", "description": "Total amount on receipt"},
                    "tax_amount": {"type": "number", "description": "Tax amount on receipt"}
                },
                "required": ["receipt_id", "merchant", "total_amount", "tax_amount"]
            }
        ),
        Tool(
            name="search_historical_claims",
            description="Searches historical expense records to identify duplicate receipt submissions, receipt recycling, or frequent anomalies.",
            inputSchema={
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string", "description": "Employee ID claiming the expense"},
                    "receipt_id": {"type": "string", "description": "Receipt ID being claimed"},
                    "amount": {"type": "number", "description": "Claim amount"}
                },
                "required": ["employee_id", "receipt_id", "amount"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    if name == "fetch_company_expense_policies":
        cat = arguments.get("category", "").lower()
        if "meal" in cat:
            policy = "Meals Policy: Maximum $75/day per employee. Alcohol is strictly prohibited unless part of client entertainment with pre-approval. Itemized receipt required for all claims > $25."
        elif "flight" in cat or "travel" in cat:
            policy = "Flight/Travel Policy: Economy class required for all flights under 6 hours. Business class permitted for 6+ hours with VP approval. Bookings must use corporate travel portal."
        elif "entertain" in cat:
            policy = "Client Entertainment Policy: Requires pre-approval from department head. Maximum $150 per attendee. Attendee list and business discussion topics must be documented."
        elif "hotel" in cat or "lodging" in cat:
            policy = "Hotel Policy: Maximum $250/night in major metropolitan areas ($175/night elsewhere). Incidentals like movies or mini-bar are non-reimbursable."
        else:
            policy = f"General Expense Policy for '{arguments.get('category')}': Receipt required. Must be directly related to business operations. Standard cap $100 without manager pre-approval."
        return CallToolResult(content=[TextContent(type="text", text=policy)])

    elif name == "validate_expense_categories":
        item = arguments.get("item_name", "").lower()
        amt = float(arguments.get("amount", 0.0))
        prohibited = ["golf", "spa", "casino", "cigar", "gift card", "jewelry", "fine wine", "nightclub"]
        if any(p in item for p in prohibited):
            return CallToolResult(content=[TextContent(type="text", text=f"❌ POLICY VIOLATION: '{arguments.get('item_name')}' contains prohibited spending category. Corporate reimbursement strictly forbidden.")])
        if amt > 500.0:
            return CallToolResult(content=[TextContent(type="text", text=f"⚠️ FLAG: Item amount (${amt}) exceeds standard unapproved threshold ($500). Requires executive sign-off.")])
        return CallToolResult(content=[TextContent(type="text", text=f"✅ Item '{arguments.get('item_name')}' (${amt}) falls within acceptable standard business categories.")])

    elif name == "inspect_receipt_metadata":
        rec_id = arguments.get("receipt_id", "")
        merchant = arguments.get("merchant", "")
        tot = float(arguments.get("total_amount", 0.0))
        tax = float(arguments.get("tax_amount", 0.0))
        
        # Simple forensics check
        subtotal = tot - tax
        if tot <= 0 or tax < 0 or tax > (tot * 0.35):
            math_status = f"⚠️ TAX/TOTAL ANOMALY: Tax (${tax}) and Total (${tot}) represent an improbable tax rate or negative values. Manual audit recommended."
        else:
            math_status = f"✅ Tax math verified (Subtotal: ${subtotal:.2f}, Tax: ${tax:.2f}, Total: ${tot:.2f})."
            
        merchant_lower = merchant.lower()
        if any(m in merchant_lower for m in ["shell company", "cash", "no name", "generic", "dummy", "crypto"]):
            merch_status = f"❌ MERCHANT WARNING: Vendor '{merchant}' flagged in high-risk/unregistered business registry. Possible fictitious entity."
        else:
            merch_status = f"✅ Merchant '{merchant}' matched against verified business registry."
            
        return CallToolResult(content=[TextContent(type="text", text=f"Receipt Metadata Forensics ({rec_id}):\n{math_status}\n{merch_status}")])

    elif name == "search_historical_claims":
        emp_id = arguments.get("employee_id", "")
        rec_id = arguments.get("receipt_id", "")
        amt = float(arguments.get("amount", 0.0))
        
        # Hardcoded mock database lookup for demo/testing
        if rec_id in ["REC-998822", "INV-554433", "TX-100200"]:
            result = f"🚨 FRAUD ALERT: Receipt ID '{rec_id}' ($ {amt}) was already submitted and reimbursed on 2026-03-14 by employee EMP-4092. Duplicate submission / receipt recycling detected!"
        elif emp_id in ["EMP-8877", "EMP-9911"]:
            result = f"⚠️ HISTORICAL PATTERN FLAG: Employee '{emp_id}' has submitted 4 similar high-dollar claims near the maximum cap limit within the last 30 days. Prior claims under review."
        else:
            result = f"✅ No duplicate claims found for receipt ID '{rec_id}' or unusual historical patterns for employee '{emp_id}'."
        return CallToolResult(content=[TextContent(type="text", text=result)])

    else:
        raise ValueError(f"Tool not found: {name}")

async def main():
    import mcp.types as types
    from mcp.server.models import InitializationOptions
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="expense_fraud_mcp_server",
                server_version="0.1.0",
                capabilities=types.ServerCapabilities(
                    tools=types.ToolsCapability()
                )
            )
        )

if __name__ == "__main__":
    asyncio.run(main())

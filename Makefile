.PHONY: install playground run test

install:
	uv sync

playground:
	uv run adk web expense_fraud_agent --host 127.0.0.1 --port 18081 --reload_agents

run:
	uv run adk run expense_fraud_agent

test:
	uv run pytest

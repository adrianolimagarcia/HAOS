## Purpose
Execute bounded manager council turns through the canonical HAOS model client.

## Contract
Resolves each enabled council member to a configured model profile, sends council context to `ExactModelClient`, persists the response, and emits an audit event. Failed completions do not consume turns.

## Side effects
Writes council state and EventStore audit events; may invoke configured model provider transports.

## Verification
Run `scripts/run_tests.sh tests/platform/webui/test_council_adapter.py tests/platform/webui/test_agent_hierarchy.py`.

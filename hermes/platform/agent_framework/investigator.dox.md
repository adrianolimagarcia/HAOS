"""Bounded operational diagnosis worker (not an OS sandbox)

API: `Investigator(timeout=90, max_output_tokens=2048).investigate(event, telemetry)`
returns `{summary, findings, usage, model, provider}` or raises `InvestigatorError`.
`InvestigatorTimeout` and `InvestigatorCancelled` distinguish deadline/cancel.
Call `cancel()` from another thread to cancel an active invocation; reuse after
completion is allowed, concurrent calls on the same instance are rejected.

Call in the owning profile runtime scope: canonical `served_profile_child_env`
preserves the correct home and credentials. Input is JSON on stdin, <=64KiB.
The subprocess uses the exact implementation tree, no shell, a separate POSIX
session, and a parent-enforced deadline; teardown kills its process group even
on successful completion. Output is bounded <=64KiB IPC and <=32KiB diagnosis;
stderr/logs are discarded, not retained or exposed as exception text.

Worker resolves canonical effective user config and runtime provider, constructs
REAL AIAgent with a single iteration, explicit empty toolsets, bounded output,
no context files/soul/memory/background review/trajectory/checkpoints/fallback,
and checks `tools == []` plus empty `valid_tool_names` BEFORE any conversation.
Inherited HERMES_KANBAN_TASK is removed because it bypasses empty toolsets.
MCP refresh and session persistence use the existing background-review private
flags. External agent transports with independent native tools fail closed.

Schema is strict: summary <=2000 chars; <=8 findings with title <=200, cause
<=2000, finite numeric confidence 0..1, <=4 recommendations <=500 chars each;
model <=256 chars, provider <=128. Extra fields, duplicate keys, nonfinite JSON,
markdown fences and malformed responses are rejected. Usage is `unknown` unless
AIAgent's provider-received canonical counters are available; model-authored
usage is never trusted. Recommendations never authorize execution.

LIMITATIONS: this is process/tool isolation, not a hard filesystem/network or
malicious-plugin sandbox. Existing AIAgent/plugin imports and configured provider
credential helpers still execute installed code and may access profile state.
Process-group cleanup does not constrain deliberately session-escaping code.
The model receives supplied event/telemetry; callers must redact sensitive data
before submitting it. No external LLM smoke is performed by unit tests.
"""

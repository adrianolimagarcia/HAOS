# OpenCode sidecar integration boundary

The verified OpenCode sidecar contract is the existing HTTP/OpenAI-compatible
`/v1/chat/completions` response, including its SSE stream.  HAOS adapts only
frames observed on that response through `hermes.platform.execution.opencode_sse`.
It does not add `/status` or `/events`, and it does not create a parallel
execution registry.  Existing authentication, profile selection, and prompt
cache behavior remain owned by the OpenCode/OpenAI client path.

# OpenCode's native CLI/server has a separate durable session protocol: the
# CLI documents resume/list/export/import, and its ACP server advertises
# load/resume/fork/list/cancel. This HAOS adapter is not that protocol; it
# targets only the OpenAI-compatible chat response endpoint. The live sidecar
# observed on this host exposes no verified /status, /events, cancellation, or
# artifact endpoint. Do not infer task lifecycle or artifact support from SSE.
# Restart is also outside this adapter's ownership; use the sidecar supervisor.

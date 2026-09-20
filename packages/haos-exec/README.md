# hermes-exec

Optional local execution sidecar for Hermes. It reads one JSON request per line and emits one JSON response per line.

Security boundary: commands are currently shell strings for compatibility with `LocalEnvironment`; this is a process supervisor, not a shell-free sandbox. Set `HERMES_EXEC_ROOT` and use a canonical existing workspace. Resource limits use Unix rlimits and are advisory; cgroups are required for hard memory/PID isolation.

Example:

```sh
HERMES_EXEC_ROOT=/workspace hermes-exec <<'JSON'
{"id":1,"method":"exec","params":{"command":"printf hello","cwd":".","timeout_ms":1000,"max_output_bytes":1024}}
JSON
```

Hermes integration is opt-in with `HERMES_EXEC_BIN=/path/to/hermes-exec`.

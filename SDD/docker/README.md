# Rust roadmap validation

## Scope

This directory contains a temporary, disposable Docker validation environment. It does not change the production HAOS image and does not enable `hermes-exec` by default.

## Procedure

```bash
bash SDD/docker/validate-rust-roadmap.sh
```

The script builds `hermes-rust-validation:local`, runs Rust unit tests, Python executor tests, a JSONL argv smoke test, and reports whether the host-mounted cgroup v2 controller file is visible.

## Evidence policy

- Passing container tests establish only the behavior exercised inside that image.
- `--cap-add=SYS_ADMIN` and `--cgroupns=host` expose host-dependent cgroup behavior; they are not equivalent to production delegation.
- Event bridge, secret broker, file engine, health agent, and MCP/A2A client are roadmap proposals, not implemented by this validation container.
- A successful build does not prove isolation against `setsid`, namespace escape, seccomp completeness, or crash cleanup.

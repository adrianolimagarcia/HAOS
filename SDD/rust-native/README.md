# Rust-native validation harness

This harness is deliberately isolated from production packaging. It builds a disposable image and runs deterministic checks against any Rust crates under `packages/`.

## Usage

```bash
bash SDD/rust-native/harness/run.sh
```

Set `RUST_NATIVE_IMAGE` to override the local image tag. The script reports a JSON receipt under a temporary directory and removes the container automatically.

The harness does not claim cgroup, namespace, seccomp, or crash-cleanup coverage unless the container runtime grants those capabilities; such checks are reported as `unverified`.

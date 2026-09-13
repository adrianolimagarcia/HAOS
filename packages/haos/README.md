# HAOS — Hermes Agent Operating System

**HAOS** is an autonomous multi-agent operating system, control plane, and execution engine built on top of Hermes Agent.

## Key Features

- **Decoupled Architecture:** Runs completely standalone with isolated configurations and data persistence in `~/.haos`.
- **Kanban & Task Engine:** Canonical task DAG orchestration, backpressure concurrency control, and deterministic multi-lane execution.
- **Standalone Web UI:** Real-time web dashboard for swarm monitoring, Team Graph, task inspection, and control interventions.
- **Built-in Self-Diagnostics:** Run `/haos doctor` or `haos doctor` anytime to audit environment, filesystem permissions, SQLite database integrity, and network port availability.

## Installation

### Via pip (PyPI)
```bash
pip install haos
```

### Via Universal Standalone Installer
```bash
curl -fsSL https://raw.githubusercontent.com/adrianolimagarcia/hermes-agent/haos-fork/scripts/install_haos.sh | bash
```

## Quick Start

```bash
# Check installation health and environment
haos doctor

# Inspect platform status
haos status

# Start the standalone WebUI Control Plane
haos-controlplane
# or
haos web
```

## License

MIT License.

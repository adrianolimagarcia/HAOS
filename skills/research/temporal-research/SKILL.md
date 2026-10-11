---
name: temporal-research
description: "Structured rolling UTC research across web and social."
version: 1.0.0
author: HAOS Civilization
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [research, temporal, last30days, search, civilization]
    category: research
---

# Temporal Research (Last30Days / Rolling Window)

The `temporal-research` skill provides deterministic, rolling-window research across multiple information channels (web search, social discussions, prediction markets, and technical archives) without hallucinating publishing dates or persisting untrusted assertions into canonical memory.

## Architecture

This skill consumes the shared HAOS research engine (`hermes.platform.research.temporal_engine`).
It is directly consumed by the **Research** bot (`research-bot`, display name `Research`) residing within the HAOS Civilization subsystem.

```
+-------------------------------------------------------------+
| Civilization Subsystem: Bot 'Research' (id: research-bot)  |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
| Skill: 'temporal-research' (SKILL.md & workflows)           |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
| Shared Pipeline: hermes.platform.research.temporal_engine   |
+-------------------------------------------------------------+
```

## Invariants & Security Contracts

1. **Temporal Anchoring (UTC)**:
   - Evaluates a strict rolling window (default: 30 days) anchored to current UTC without date fabrication.
   - Findings without verified timestamps remain marked as `unverified` freshness.

2. **Zero Memory Contamination (ADR-028)**:
   - External findings and web outputs are marked `UNTRUSTED_CONTENT`.
   - Never auto-writes to `CanonicalMemoryStore`, `GBrain`, `GraphRAG`, or `Obsidian`.

3. **Graceful Degradation**:
   - Secondary channels (Hacker News, Reddit, Polymarket) degrade gracefully if unavailable.
   - Gated channels (X/Twitter, YouTube) are skipped cleanly (`skipped_no_credentials`) if API keys are missing.

## Invocations & Usage

### Via Python API
```python
from hermes.platform.research.temporal_engine import execute_temporal_research

result = execute_temporal_research(
    goal="Recent security vulnerabilities in kernel eBPF",
    window_spec="last30days",
    channels=["web-search", "reddit"]
)
print(result.to_dict())
```

### Via Civilization Bot 'Research'
```python
from hermes.platform.bots.builtin.research_bot import run_bot_research

res = run_bot_research(
    goal="State of the art open source robotics models",
    window_spec="last30days"
)
```

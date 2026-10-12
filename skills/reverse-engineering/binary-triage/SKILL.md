---
name: binary-triage
description: "Fast static triage and header inspection of binaries."
version: 1.0.0
author: Masriyan, Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [reverse-engineering, binary-analysis, triage, security, elf, pe]
    category: security
---

# Binary Triage Skill

Fast static binary inspection and triage engine. It extracts metadata, segment/section mappings, symbol tables, security protections (PIE, NX, RELRO, Stack Canary), and calculates Shannon entropy distributions.

## When to Use

- When inspecting an unknown ELF or PE executable before full analysis.
- To detect if a binary is packed, encrypted, or obfuscated via entropy.
- To check compiled security hardening mitigations (PIE, NX, RELRO, Canary).
- When filtering suspicious ASCII strings (URLs, IPs, keys, paths).

## Prerequisites

Python libraries (installed in virtual environment):
- `pyelftools` (ELF parsing)
- `pefile` (PE parsing)
- `capstone` (optional disassembly)

Core tool execution:
- Invoked via `terminal` running `scripts/binary_analyzer.py`.

## How to Run

Run the standalone triage analyzer on any target binary:

```bash
python scripts/binary_analyzer.py --file /path/to/binary --output /tmp/triage.json
```

Or execute directly from Python code:

```python
from binary_analyzer import BinaryAnalyzer

analyzer = BinaryAnalyzer("/path/to/binary")
results = analyzer.run()
```

## Quick Reference

| Flag | Purpose |
|---|---|
| `--file <path>` | Target binary file to inspect |
| `--output <path>` | Save JSON results to path |
| `--no-strings` | Skip string extraction for large binaries |
| `--entropy` | Calculate Shannon entropy per 256-byte block |
| `--verbose` | Enable debug logging |

## Procedure

1. Verify the binary path and ensure target exists.
2. Run `binary_analyzer.py --file <target>` to generate a structural JSON report.
3. Review `file_type`, `elf_analysis.header.machine`, and `elf_analysis.security`.
4. Check `entropy.is_likely_packed` to determine if unpacking is necessary.
5. Inspect `strings.interesting` and `strings.urls` for IOC indicators.

## Pitfalls

- Binaries over 100MB can produce large string outputs; pass `--no-strings` when only headers and entropy are needed.
- Stripped binaries omit `.symtab`; fall back to dynamic symbols (`.dynsym`).
- Packed executables exhibit near 8.0 entropy in text/code sections.

## Verification

Validate the triage output by confirming valid JSON containing `file_type`, `entropy`, and `elf_analysis` or `pe_analysis`.

#!/usr/bin/env python3
"""HAOS Entry Point - Hermes Agent Operating System Fork."""

import os
import sys
import runpy

# Ensure root directory is in sys.path
root_dir = os.path.dirname(os.path.abspath(__file__))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "haos":
        sys.argv.pop(1)
        runpy.run_path(os.path.join(root_dir, "bin", "haos"), run_name="__main__")
    else:
        from cli import main as hermes_cli_main
        sys.exit(hermes_cli_main())

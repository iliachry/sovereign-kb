"""Executable entrypoint for `python -m sovereign_kb.evr`."""

from __future__ import annotations

from .cli_ops import main_cli

if __name__ == "__main__":
    main_cli()

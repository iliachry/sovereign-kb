"""CLI entrypoint for `python -m sovereign_kb.elenchus`."""

from __future__ import annotations

import argparse
import sys

from .cli_ops import cmd_elenchus, register_elenchus_subparser


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="elenchus", description="Elenchus Pre-Seal Cross-Lineage Gate")
    sub = parser.add_subparsers(dest="cmd")
    register_elenchus_subparser(sub, show_all_aliases=False)
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0] != "elenchus":
        raw = ["elenchus", *raw]
    args = parser.parse_args(raw)
    cmd_elenchus(args)


if __name__ == "__main__":
    main()

"""Command line entry point: ``python -m dma.kernel.checks <project>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common import run_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dma-lint", description="Darkitten Mod Agent 2.0 static checks")
    parser.add_argument("root", nargs="?", default=".", help="mod project root")
    parser.add_argument("--level", action="append", choices=["P0", "P1", "P2", "P3", "TOOL"],
                        help="only report these levels (repeatable)")
    parser.add_argument("--checker", action="append", help="only run these checkers (repeatable)")
    parser.add_argument("--json", dest="json_out", default="", help="write JSON report to this path")
    parser.add_argument("--markdown", dest="markdown_out", default="", help="write Markdown report to this path")
    parser.add_argument("--no-index", action="store_true", help="skip the symbol index (faster)")
    parser.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = parser.parse_args(argv)

    report = run_checks(
        Path(args.root),
        levels=set(args.level) if args.level else None,
        only=set(args.checker) if args.checker else None,
        with_index=not args.no_index,
    )
    counts = report.counts()
    if not args.quiet:
        sys.stdout.write(report.markdown())
    sys.stdout.write(
        f"\n{'PASS' if report.ok else 'FAIL'} score={report.score()} "
        f"error={counts['error']} warning={counts['warning']} info={counts['info']}\n"
    )
    if args.json_out:
        report.write(Path(args.json_out))
    if args.markdown_out:
        report.write(Path(args.markdown_out))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

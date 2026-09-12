"""Command-line interface for the inventory report fixture."""

from __future__ import annotations

import argparse
from pathlib import Path

from .report import ReportError, load_items, render_report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="inventory_report")
    result.add_argument("path", type=Path)
    result.add_argument("--sku")
    result.add_argument("--name")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        print(render_report(load_items(args.path), sku=args.sku, name=args.name))
    except (ReportError, OSError) as exc:
        parser().error(str(exc))


if __name__ == "__main__":
    main()

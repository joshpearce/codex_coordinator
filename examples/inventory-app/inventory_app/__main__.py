"""Command-line interface for the inventory producer fixture."""

from __future__ import annotations

import argparse
from pathlib import Path

from .domain import InventoryError, add_item, load_inventory


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="inventory_app")
    result.add_argument("--file", type=Path, required=True)
    commands = result.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("sku")
    add.add_argument("name")
    add.add_argument("quantity", type=int)
    add.add_argument("unit_price")
    commands.add_parser("list")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "add":
            inventory = add_item(
                args.file, args.sku, args.name, args.quantity, args.unit_price
            )
        else:
            inventory = load_inventory(args.file)
        for item in inventory["items"]:
            print(
                f"{item['sku']}\t{item['name']}\t"
                f"{item['quantity']}\t{item['unit_price']}"
            )
    except (InventoryError, OSError) as exc:
        parser().error(str(exc))


if __name__ == "__main__":
    main()

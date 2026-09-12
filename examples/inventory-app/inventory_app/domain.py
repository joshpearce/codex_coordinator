"""Domain and persistence seam completed by the inventory child session."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class InventoryError(ValueError):
    """A user-facing inventory validation error."""


def validate_inventory(value: Any) -> dict[str, Any]:
    """Return validated inventory data, or raise InventoryError.

    TODO: implement the contract documented in README.md and exercised by
    test_inventory_app.py.
    """
    raise NotImplementedError


def load_inventory(path: Path) -> dict[str, Any]:
    """Load and validate JSON, treating a missing file as an empty inventory.

    TODO: decode JSON and translate I/O/JSON/validation failures to InventoryError.
    """
    raise NotImplementedError


def add_item(
    path: Path, sku: str, name: str, quantity: int, unit_price: str
) -> dict[str, Any]:
    """Validate and append one item, then persist the document.

    TODO: reject duplicate SKUs, validate through validate_inventory, and write JSON.
    """
    raise NotImplementedError

"""Validation and rendering seam completed by the reporting child session."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ReportError(ValueError):
    """A user-facing inventory report error."""


def load_items(path: Path) -> list[dict[str, Any]]:
    """Load and validate the inventory contract documented in README.md.

    TODO: decode JSON and reject malformed fields, including boolean versions and
    quantities and invalid/non-string/extreme Decimal prices, with ReportError.
    """
    raise NotImplementedError


def render_report(
    items: list[dict[str, Any]], *, sku: str | None = None, name: str | None = None
) -> str:
    """Filter items and render rows plus exact quantity and Decimal value totals.

    TODO: implement exact SKU filtering, case-insensitive name substring filtering,
    and a compact readable table ending in Total quantity and Total value lines.
    """
    raise NotImplementedError

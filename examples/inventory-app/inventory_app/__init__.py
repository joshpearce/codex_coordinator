"""Inventory producer fixture."""

from .domain import InventoryError, add_item, load_inventory

__all__ = ["InventoryError", "add_item", "load_inventory"]

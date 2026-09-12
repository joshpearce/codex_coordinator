from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from inventory_app import InventoryError, add_item, load_inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "inventory.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_add_round_trip_preserves_decimal_string(self):
        add_item(self.path, "SKU-1", "Coffee", 3, "12.340")
        self.assertEqual(load_inventory(self.path)["items"][0]["unit_price"], "12.340")
        with self.assertRaises(InventoryError):
            add_item(self.path, "SKU-1", "Duplicate", 1, "1.00")

    def test_rejects_boolean_schema_and_quantity(self):
        for document in (
            {"schema_version": True, "items": []},
            {"schema_version": 1, "items": [
                {"sku": "S", "name": "N", "quantity": True, "unit_price": "1"}
            ]},
        ):
            self.path.write_text(json.dumps(document))
            with self.assertRaises(InventoryError):
                load_inventory(self.path)

    def test_rejects_bad_prices(self):
        for price in ("NaN", "Infinity", "-1", "bad", "1e999999", 1.25):
            document = {"schema_version": 1, "items": [
                {"sku": "S", "name": "N", "quantity": 1, "unit_price": price}
            ]}
            self.path.write_text(json.dumps(document))
            with self.subTest(price=price), self.assertRaises(InventoryError):
                load_inventory(self.path)

    def test_cli_reports_extreme_price_without_traceback(self):
        result = subprocess.run(
            [sys.executable, "-m", "inventory_app", "--file", str(self.path),
             "add", "S", "N", "1", "1e999999"],
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("price", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

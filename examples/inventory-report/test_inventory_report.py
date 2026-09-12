from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from inventory_report import ReportError, load_items, render_report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "inventory.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, items, version=1):
        self.path.write_text(json.dumps({"schema_version": version, "items": items}))

    def test_exact_decimal_totals_and_filters(self):
        self.write([
            {"sku": "A", "name": "Coffee Beans", "quantity": 3,
             "unit_price": "12.34"},
            {"sku": "B", "name": "Tea Tin", "quantity": 2,
             "unit_price": "5.00"},
        ])
        items = load_items(self.path)
        report = render_report(items)
        self.assertIn("Total quantity: 5", report)
        self.assertIn("Total value: 47.02", report)
        self.assertIn("Coffee Beans", render_report(items, sku="A"))
        self.assertNotIn("Tea Tin", render_report(items, sku="A"))
        self.assertIn("Coffee Beans", render_report(items, name="COFFEE"))
        self.assertNotIn("Tea Tin", render_report(items, name="COFFEE"))

    def test_rejects_boolean_schema_and_quantity(self):
        for version, quantity in ((True, 1), (1, False)):
            self.write([
                {"sku": "S", "name": "N", "quantity": quantity,
                 "unit_price": "1.00"}
            ], version)
            with self.assertRaises(ReportError):
                load_items(self.path)

    def test_rejects_bad_prices(self):
        for price in ("NaN", "Infinity", "-1", "bad", "1e999999", 1.25):
            self.write([
                {"sku": "S", "name": "N", "quantity": 1, "unit_price": price}
            ])
            with self.subTest(price=price), self.assertRaises(ReportError):
                load_items(self.path)


if __name__ == "__main__":
    unittest.main()

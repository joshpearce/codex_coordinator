Build a complete offline Python terminal reporting application in this project. It
must consume inventory JSON produced by the sibling application at
`{{INVENTORY_APP_PATH}}`.

Implement a Python package and CLI that:

- accepts the path to an inventory JSON file;
- requires a top-level object with `schema_version` and `items`;
- validates every item has `sku`, `name`, `quantity`, and `unit_price`;
- treats `unit_price` as a decimal-safe string rather than binary floating point;
- supports filtering by SKU and by case-insensitive name text;
- prints a readable item table, total quantity, and total inventory value;
- reports malformed input with a useful message and nonzero exit status.

Inspect the sibling inventory application's actual output contract and README before
finalizing compatibility. Add packaging metadata, comprehensive tests, and a README
with usage examples. Stay offline, work autonomously, run all tests, and report the
result. Do not modify the sibling inventory application directly; report an
incompatibility to the coordinator so it can send the appropriate child a follow-up.

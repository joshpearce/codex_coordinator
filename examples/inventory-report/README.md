# Inventory report E2E fixture

Small offline consumer used by the orchestration E2E. The project is intentionally
scaffolded: the child session completes the TODOs in `inventory_report/report.py` and
runs the supplied regression tests.

Input is the producer's documented object with integer (not boolean)
`schema_version: 1` and an `items` array. Each item has string `sku` and `name`, a
nonnegative integer (not boolean) `quantity`, and a finite, nonnegative,
reasonably representable decimal-string `unit_price`.

```sh
python -m inventory_report inventory.json
python -m inventory_report inventory.json --sku SKU-100
python -m inventory_report inventory.json --name coffee
python -m unittest -q
```

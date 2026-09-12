Complete the small scaffolded offline terminal reporting application in this project.
It consumes the inventory contract documented locally in `README.md`; the sibling
producer is at `{{INVENTORY_APP_PATH}}`, but you do not need to inspect it because the
two checked-in fixture contracts are identical.

Read `README.md` and `test_inventory_report.py`, then implement only the two TODO
functions in `inventory_report/report.py`. Keep the existing package, CLI, README,
and tests; do not add files or features. The tests define validation, exact Decimal
totals, SKU filtering, case-insensitive name filtering, and output requirements.

Stay offline. Run exactly `python -m unittest -q`, fix only failures from that
supplied suite, and report the result concisely. Do not modify the sibling project.

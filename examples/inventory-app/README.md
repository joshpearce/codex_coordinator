# Inventory app E2E fixture

Small offline inventory producer used by the orchestration E2E. The project is
intentionally scaffolded: the child session completes the TODOs in
`inventory_app/domain.py` and runs the supplied regression tests.

The CLI stores this exact JSON contract:

```json
{
  "schema_version": 1,
  "items": [
    {"sku": "SKU-100", "name": "Coffee Beans", "quantity": 3, "unit_price": "12.34"}
  ]
}
```

`schema_version` and `quantity` must be integers but not booleans. `unit_price` must
be a finite, nonnegative, reasonably representable decimal string; JSON numbers are
not accepted. SKUs are unique.

```sh
python -m inventory_app --file inventory.json add SKU-100 "Coffee Beans" 3 12.34
python -m inventory_app --file inventory.json list
python -m unittest -q
```

## Dependencies

This project declares its dependencies in two manifests. Restore them into an
environment inside the project — that is the only directory a session here can
write, so installing into the ambient interpreter is refused by the filesystem
boundary rather than the network one:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements.txt
.venv/bin/python -m pip install --no-cache-dir -r requirements-dev.txt
```

The dependency manifests are ordinary project inputs. Any access they require is
determined by the Codex configuration that naturally applies to this project;
the coordinator does not replace or narrow it.

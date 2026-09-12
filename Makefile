UV ?= uv

.PHONY: help sync test check build install live-e2e clean

help:
	@echo "make sync     Install locked development dependencies"
	@echo "make test     Run the isolated test suite"
	@echo "make check    Check syntax and packaging metadata"
	@echo "make build    Build wheel and source distribution"
	@echo "make install  Install the codex-coordinator command"
	@echo "make live-e2e Run the real recursive Codex orchestration experiment"
	@echo "make clean    Remove generated build and test artifacts"

sync:
	$(UV) sync --extra test

test:
	$(UV) run --extra test pytest -q

check:
	$(UV) run python -m compileall -q src tests
	$(UV) run --extra test python -m pytest -q

build:
	$(UV) build

install:
	$(UV) tool install --force .

live-e2e:
	$(UV) run codex-coordinator-live-e2e --workspace /tmp/codex-orchestration-run

clean:
	rm -rf build dist .pytest_cache src/*.egg-info src/*/__pycache__ tests/__pycache__

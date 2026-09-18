UV ?= uv
LIVE_E2E_ARGS ?=

.PHONY: help sync test check build install live-e2e installed-gate release-gate clean

help:
	@echo "make sync     Install locked development dependencies"
	@echo "make test     Run the isolated test suite"
	@echo "make check    Check syntax and packaging metadata"
	@echo "make build    Build wheel and source distribution"
	@echo "make install  Install the codex-coordinator command"
	@echo "make live-e2e Run the live host app-server compatibility exercise"
	@echo "make installed-gate Build and smoke-test the wheel outside the checkout"
	@echo "make release-gate Run installed checks and the required live generic workflow"
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
	$(UV) run python -m codex_coordinator.live_e2e $(LIVE_E2E_ARGS)

installed-gate:
	sh scripts/release_gate.sh --installed-only

release-gate:
	sh scripts/release_gate.sh

clean:
	rm -rf build dist .pytest_cache src/*.egg-info src/*/__pycache__ tests/__pycache__

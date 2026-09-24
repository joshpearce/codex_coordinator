UV ?= uv
LIVE_E2E_ARGS ?=
LIVE_E2E_CONFIG ?=
LIVE_E2E_PARENT ?= coordination
LIVE_E2E_FIRST ?= inventory-app
LIVE_E2E_SECOND ?= inventory-report
LIVE_E2E_FIRST_PROMPT ?= You are already the service-managed child in the target project. Inspect it directly and read-only; do not invoke another coordinator. Report one concise verified fact.
LIVE_E2E_SECOND_PROMPT ?= You are already the service-managed child in the target project. Inspect it directly and read-only; do not invoke another coordinator. Report one concise verified fact.
LIVE_E2E_PROJECTS = {"coordination":"$(CURDIR)/examples/coordination-workspace","inventory-app":"$(CURDIR)/examples/inventory-app","inventory-report":"$(CURDIR)/examples/inventory-report"}
LIVE_E2E_CONFIG_ARG = $(if $(strip $(LIVE_E2E_CONFIG)),--config "$(LIVE_E2E_CONFIG)",)
LIVE_E2E_PROJECTS_ENV = $(if $(strip $(LIVE_E2E_CONFIG)),,CODEX_COORDINATOR_PROJECTS='$(LIVE_E2E_PROJECTS)')

.PHONY: help sync test check build install live-e2e live-e2e-compat installed-gate release-gate clean

help:
	@echo "make sync     Install locked development dependencies"
	@echo "make test     Run the isolated test suite"
	@echo "make check    Check syntax and packaging metadata"
	@echo "make build    Build wheel and source distribution"
	@echo "make install  Install the codex-coordinator command"
	@echo "make live-e2e Run the bundled live parent/monitor exercise"
	@echo "make live-e2e-compat Run the legacy two-child compatibility exercise with LIVE_E2E_ARGS"
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
	$(LIVE_E2E_PROJECTS_ENV) $(UV) run python -m codex_coordinator.live_e2e \
		$(LIVE_E2E_CONFIG_ARG) \
		--parent "$(LIVE_E2E_PARENT)" \
		--first "$(LIVE_E2E_FIRST)" \
		--second "$(LIVE_E2E_SECOND)" \
		--first-prompt "$(LIVE_E2E_FIRST_PROMPT)" \
		--second-prompt "$(LIVE_E2E_SECOND_PROMPT)" \
		$(LIVE_E2E_ARGS)

live-e2e-compat:
	$(UV) run python -m codex_coordinator.live_e2e $(LIVE_E2E_ARGS)

installed-gate:
	sh scripts/release_gate.sh --installed-only

release-gate:
	sh scripts/release_gate.sh

clean:
	rm -rf build dist .pytest_cache src/*.egg-info src/*/__pycache__ tests/__pycache__

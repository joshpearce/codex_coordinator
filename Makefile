UV ?= uv

.PHONY: help sync test check build install clean

help:
	@echo "make sync     Install locked development dependencies"
	@echo "make test     Run the isolated test suite"
	@echo "make check    Check syntax and packaging metadata"
	@echo "make build    Build wheel and source distribution"
	@echo "make install  Install the codex-coordinator command"
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

clean:
	rm -rf build dist .pytest_cache src/*.egg-info src/*/__pycache__ tests/__pycache__

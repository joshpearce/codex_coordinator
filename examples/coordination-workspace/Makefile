SHELL := /bin/bash

PORT ?= 8765
PREFLIGHT := codex-coordinator-preflight
SERVICE := codex-coordinator-service
CONFIG := $(CURDIR)/operator.toml
PID_FILE := $(CURDIR)/.coordinator.pid
PORT_FILE := $(CURDIR)/.coordinator.port
LOG_FILE := $(CURDIR)/.coordinator.log
SERVICE_LABEL := com.openai.codex-coordinator.$(shell id -u).$(PORT)

.PHONY: start stop status logs preflight

preflight:
	@set -eu; \
	if ! command -v "$(PREFLIGHT)" >/dev/null || ! command -v "$(SERVICE)" >/dev/null; then \
		echo "Codex Coordinator commands were not found on PATH."; \
		echo "Install them from the codex_coordinator project with: make install"; \
		exit 1; \
	fi; \
	$(PREFLIGHT) --config "$(CONFIG)" --require-socket

start: preflight
	@set -eu; \
	if ! command -v launchctl >/dev/null; then \
		echo "The supported launcher requires macOS launchctl supervision."; \
		exit 1; \
	fi; \
	service="$$(command -v "$(SERVICE)")"; \
	if curl --fail --silent --show-error "http://127.0.0.1:$(PORT)/health" >/dev/null 2>&1; then \
		echo "Coordinator is already healthy on port $(PORT)."; \
		exit 1; \
	fi; \
	if launchctl list "$(SERVICE_LABEL)" >/dev/null 2>&1; then \
		echo "Removing stale supervised coordinator job $(SERVICE_LABEL)."; \
		launchctl remove "$(SERVICE_LABEL)"; \
	fi; \
	rm -f "$(PID_FILE)"; \
	launchctl submit -l "$(SERVICE_LABEL)" -o "$(LOG_FILE)" -e "$(LOG_FILE)" -- \
		/usr/bin/env "PATH=$$PATH" "$$service" \
		--config "$(CONFIG)" \
		--host 127.0.0.1 \
		--port "$(PORT)"; \
	echo "$(PORT)" >"$(PORT_FILE)"; \
	healthy=0; \
	for _ in {1..50}; do \
		if curl --fail --silent --show-error "http://127.0.0.1:$(PORT)/health" >/dev/null 2>&1; then healthy=1; break; fi; \
		sleep 0.1; \
	done; \
	if [[ "$$healthy" != 1 ]]; then \
		echo "Coordinator failed to start; see $(LOG_FILE)."; \
		launchctl remove "$(SERVICE_LABEL)" >/dev/null 2>&1 || true; \
		rm -f "$(PORT_FILE)"; \
		exit 1; \
	fi; \
	echo "Coordinator is supervised as $(SERVICE_LABEL) and healthy on http://127.0.0.1:$(PORT)."

stop:
	@set -eu; \
	port="$(PORT)"; \
	if [[ -f "$(PORT_FILE)" ]]; then port="$$(<"$(PORT_FILE)")"; fi; \
	if launchctl list "$(SERVICE_LABEL)" >/dev/null 2>&1; then \
		launchctl remove "$(SERVICE_LABEL)"; \
	else \
		curl --fail --silent --show-error \
			-X POST "http://127.0.0.1:$$port/shutdown" >/dev/null || true; \
	fi; \
	for _ in {1..50}; do \
		if ! curl --fail --silent "http://127.0.0.1:$$port/health" >/dev/null 2>&1; then break; fi; \
		sleep 0.1; \
	done; \
	if curl --fail --silent "http://127.0.0.1:$$port/health" >/dev/null 2>&1; then \
		echo "Coordinator is still reachable after supervised shutdown; inspect $(LOG_FILE)."; \
		exit 1; \
	fi; \
	rm -f "$(PID_FILE)" "$(PORT_FILE)"; \
	echo "Coordinator stopped."

status:
	@port="$$(cat "$(PORT_FILE)" 2>/dev/null || echo "$(PORT)")"; \
	if curl --fail --silent --show-error "http://127.0.0.1:$$port/health" >/dev/null 2>&1; then \
		echo "Coordinator is healthy on port $$port under $(SERVICE_LABEL)."; \
		exit 0; \
	fi; \
	if launchctl list "$(SERVICE_LABEL)" >/dev/null 2>&1; then \
		echo "Coordinator job $(SERVICE_LABEL) exists but its health check failed; run 'make stop' then 'make start'."; \
		exit 1; \
	fi; \
	if [[ -f "$(PID_FILE)" || -f "$(PORT_FILE)" ]]; then \
		echo "Coordinator has stale PID/port state; run 'make stop' then 'make start'."; \
		exit 1; \
	fi; \
	echo "Coordinator is not running."; \
	exit 1

logs:
	tail -f "$(LOG_FILE)"

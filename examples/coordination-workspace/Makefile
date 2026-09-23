SHELL := /bin/bash

PORT ?= 8765
PREFLIGHT := codex-coordinator-preflight
SERVICE := codex-coordinator-service
CONFIG := $(CURDIR)/operator.toml
PID_FILE := $(CURDIR)/.coordinator.pid
PORT_FILE := $(CURDIR)/.coordinator.port
LOG_FILE := $(CURDIR)/.coordinator.log

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
	if [[ -f "$(PID_FILE)" ]]; then \
		pid="$$(<"$(PID_FILE)")"; \
		if [[ "$$pid" =~ ^[0-9]+$$ ]] && kill -0 "$$pid" 2>/dev/null; then \
			echo "Coordinator is already running with PID $$pid."; \
			exit 1; \
		fi; \
		echo "Removing stale coordinator state."; \
		rm -f "$(PID_FILE)" "$(PORT_FILE)"; \
	fi; \
	nohup $(SERVICE) \
		--config "$(CONFIG)" \
		--host 127.0.0.1 \
		--port "$(PORT)" \
		>"$(LOG_FILE)" 2>&1 & \
	pid="$$!"; \
	echo "$$pid" >"$(PID_FILE)"; \
	echo "$(PORT)" >"$(PORT_FILE)"; \
	sleep 1; \
	if ! kill -0 "$$pid" 2>/dev/null; then \
		echo "Coordinator failed to start; see $(LOG_FILE)."; \
		rm -f "$(PID_FILE)" "$(PORT_FILE)"; \
		exit 1; \
	fi; \
	echo "Coordinator started with PID $$pid on http://127.0.0.1:$(PORT)."

stop:
	@set -eu; \
	if [[ ! -f "$(PID_FILE)" ]]; then \
		echo "Coordinator is not running (no PID file)."; \
		exit 0; \
	fi; \
	pid="$$(<"$(PID_FILE)")"; \
	if [[ ! "$$pid" =~ ^[0-9]+$$ ]]; then \
		echo "Refusing to use malformed PID file: $(PID_FILE)"; \
		exit 1; \
	fi; \
	if ! kill -0 "$$pid" 2>/dev/null; then \
		echo "Removing stale coordinator state for PID $$pid."; \
		rm -f "$(PID_FILE)" "$(PORT_FILE)"; \
		exit 0; \
	fi; \
	command="$$(ps -p "$$pid" -o command=)"; \
	if [[ "$$command" != *codex-coordinator-service* ]] || [[ "$$command" != *"$(CONFIG)"* ]]; then \
		echo "Refusing to stop PID $$pid because it does not match this coordinator."; \
		exit 1; \
	fi; \
	port="8765"; \
	if [[ -f "$(PORT_FILE)" ]]; then port="$$(<"$(PORT_FILE)")"; fi; \
	curl --fail --silent --show-error \
		-X POST "http://127.0.0.1:$$port/shutdown" >/dev/null || true; \
	for _ in {1..50}; do \
		if ! kill -0 "$$pid" 2>/dev/null; then break; fi; \
		sleep 0.1; \
	done; \
	if kill -0 "$$pid" 2>/dev/null; then \
		echo "Graceful shutdown timed out; sending SIGTERM to PID $$pid."; \
		kill "$$pid"; \
	fi; \
	rm -f "$(PID_FILE)" "$(PORT_FILE)"; \
	echo "Coordinator stopped."

status:
	@if [[ -f "$(PID_FILE)" ]]; then \
		pid="$$(<"$(PID_FILE)")"; \
		port="$$(cat "$(PORT_FILE)" 2>/dev/null || echo unknown)"; \
		if [[ "$$pid" =~ ^[0-9]+$$ ]] && kill -0 "$$pid" 2>/dev/null; then \
			echo "Coordinator is running with PID $$pid on port $$port."; \
			exit 0; \
		fi; \
	fi; \
	echo "Coordinator is not running."; \
	exit 1

logs:
	tail -f "$(LOG_FILE)"

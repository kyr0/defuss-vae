.PHONY: help setup start stop restart status log metrics bench test compat coverage lint e2e doctor verify dist package
.DEFAULT_GOAL := help

PYTHON ?= python3
PLUGIN := plugin
VERSION := $(shell awk -F'"' '/"version"/{print $$4; exit}' $(PLUGIN)/.claude-plugin/plugin.json)
DIST := output/defuss-vae-$(VERSION).zip
RUFF := ruff==0.16.9
ACTIONLINT := actionlint-py==1.7.12.25
# Resolved at parse time with the installer's default dir as fallback: make 3.81 execs simple recipes with its
# original PATH, so a uv that `make setup` just installed would otherwise be invisible until a new shell.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
COVERAGE_MIN ?= 60

help: ## list the verbs with their usage (default goal)
	@awk -F':.*## ' '/^[a-zA-Z0-9_ -]+:.*## /{printf "  %-12s %s\n", $$1, $$2}' $(firstword $(MAKEFILE_LIST))

# Dev tooling only (coverage, compat, lint run through uv); the hooks themselves need just python3.
setup: ## install uv if missing (dev tooling; the hooks need only python3)
	@[ -x "$(UV)" ] || curl -LsSf https://astral.sh/uv/install.sh | sh

# Uniform layout verbs (plugin/templates/Makefile); this plugin is hooks + CLI, so there is no service.
start stop restart status log: ## no service: this plugin is hooks + CLI
	@echo "∅ $@: defuss-vae runs no service"

metrics: ## runtime prompt budget: bytes per skill (each < 6000, all < 28000)
	@wc -c $(PLUGIN)/skills/*/SKILL.md $(PLUGIN)/references/VAE-DIALECT.md

bench: dist ## gate latency on the installed release: cold verify vs cached review/docs loop
	$(PYTHON) tests/e2e.py $(DIST) --bench

test: ## unit suite (real git repos, processes and files)
	$(PYTHON) -m unittest discover -s tests -v

# Output goes to a file and only its last line is shown; the recipe exits with the command's status. A pipe into
# `tail` would exit with tail's 0 and let a failing run pass `make verify` and CI.
compat: ## the suite on python 3.9, the oldest the hooks support (macOS Command Line Tools)
	@mkdir -p tmp && $(UV) run -q --no-project --python 3.9 python -m unittest discover -s tests > tmp/compat.log 2>&1; s=$$?; tail -1 tmp/compat.log; exit $$s

coverage: ## coverage.py in an ephemeral uv env, subprocesses included (.coveragerc); floor COVERAGE_MIN (60%)
	@mkdir -p tmp && rm -f tmp/.coverage* tmp/coverage.txt && COVERAGE_FILE=tmp/.coverage $(UV) run -q --no-project --with coverage python -m coverage run -m unittest discover -s tests >/dev/null 2>&1 \
	  && COVERAGE_FILE=tmp/.coverage $(UV) run -q --no-project --with coverage python -m coverage combine -q \
	  && COVERAGE_FILE=tmp/.coverage $(UV) run -q --no-project --with coverage python -m coverage report --fail-under=$(COVERAGE_MIN) > tmp/coverage.txt; \
	  s=$$?; tail -1 tmp/coverage.txt 2>/dev/null; exit $$s

lint: ## pinned ruff (py39 target) + actionlint on both CI workflows
	$(UV) run -q --no-project --with $(RUFF) ruff check $(PLUGIN) tests
	$(UV) run -q --no-project --with $(ACTIONLINT) actionlint .github/workflows/verify.yml $(PLUGIN)/templates/verify.yml

e2e: dist ## dogfood: install the release zip, drive only its CLI + hook adapter
	$(PYTHON) tests/e2e.py $(DIST)

doctor: ## plugin files, manifests, budgets; compile every module
	$(PYTHON) $(PLUGIN)/scripts/vae.py doctor
	$(PYTHON) -m py_compile $(PLUGIN)/scripts/*.py $(PLUGIN)/hooks/lifecycle.py tests/*.py

verify: lint test compat coverage doctor e2e ## everything CI runs

# The release zip is exactly the installed payload: plugin/ contents at the archive root.
dist: ## the release zip: plugin/ at the archive root
	@mkdir -p output && rm -f $(DIST) && cd $(PLUGIN) && zip -qr ../$(DIST) . -x '*/__pycache__/*' '*.pyc' '*.DS_Store' && echo $(DIST)

package: verify dist ## verify, then build the release zip

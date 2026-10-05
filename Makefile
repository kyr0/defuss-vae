.PHONY: setup start stop restart status log metrics bench test compat coverage lint e2e doctor verify dist package

PYTHON ?= python3
PLUGIN := plugin
VERSION := $(shell awk -F'"' '/"version"/{print $$4; exit}' $(PLUGIN)/.claude-plugin/plugin.json)
DIST := output/defuss-vae-$(VERSION).zip
RUFF := ruff==0.16.9
ACTIONLINT := actionlint-py==1.7.12.25
# Resolved at parse time with the installer's default dir as fallback: make 3.81 execs simple recipes with its
# original PATH, so a uv that `make setup` just installed would otherwise be invisible until a new shell.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)

# Default goal. Dev tooling only (coverage, compat, lint run through uv); the hooks themselves need just python3.
setup:
	@[ -x "$(UV)" ] || curl -LsSf https://astral.sh/uv/install.sh | sh

# Uniform layout verbs (plugin/templates/Makefile); this plugin is hooks + CLI, so there is no service.
start stop restart status log:
	@echo "∅ $@: defuss-vae runs no service"

metrics:  # runtime prompt budget: bytes per skill (each < 5500)
	@wc -c $(PLUGIN)/skills/*/SKILL.md $(PLUGIN)/references/VAE-DIALECT.md

bench: dist  # gate latency on the installed release: cold verify vs cached review/docs loop
	$(PYTHON) tests/e2e.py $(DIST) --bench

test:
	$(PYTHON) -m unittest discover -s tests -v

compat:  # oldest python3 the hooks support (3.9 = macOS Command Line Tools), fetched by uv
	@$(UV) run -q --no-project --python 3.9 python -m unittest discover -s tests 2>&1 | tail -1

coverage:  # coverage.py in an ephemeral uv env (no venv); AGENTS.md floor 60%
	@mkdir -p tmp && COVERAGE_FILE=tmp/.coverage $(UV) run -q --no-project --with coverage python -m coverage run --source=$(PLUGIN)/scripts,$(PLUGIN)/hooks -m unittest discover -s tests >/dev/null 2>&1 \
	  && COVERAGE_FILE=tmp/.coverage $(UV) run -q --no-project --with coverage python -m coverage report --fail-under=60 | tail -1

lint:  # pinned for reproducible results; ruff.toml targets py39; actionlint checks both CI workflows statically
	$(UV) run -q --no-project --with $(RUFF) ruff check $(PLUGIN) tests
	$(UV) run -q --no-project --with $(ACTIONLINT) actionlint .github/workflows/verify.yml $(PLUGIN)/templates/verify.yml

e2e: dist  # dogfood: install the release zip, drive only its CLI + hook adapter
	$(PYTHON) tests/e2e.py $(DIST)

doctor:
	$(PYTHON) $(PLUGIN)/scripts/vae.py doctor
	$(PYTHON) -m py_compile $(PLUGIN)/scripts/*.py $(PLUGIN)/hooks/lifecycle.py tests/*.py

verify: lint test compat coverage doctor e2e

# The release zip is exactly the installed payload: plugin/ contents at the archive root.
dist:
	@mkdir -p output && rm -f $(DIST) && cd $(PLUGIN) && zip -qr ../$(DIST) . -x '*/__pycache__/*' '*.pyc' '*.DS_Store' && echo $(DIST)

package: verify dist

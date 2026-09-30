.PHONY: start stop restart status log metrics bench test coverage e2e doctor verify dist package

PYTHON ?= python3
VERSION := $(shell awk -F'"' '/"version"/{print $$4; exit}' plugin.json)
DIST := output/defuss-vae-$(VERSION).zip

# Uniform layout verbs (templates/Makefile); this plugin is hooks + CLI, so there is no service.
start stop restart status log:
	@echo "∅ $@: defuss-vae runs no service"

metrics:  # runtime prompt budget: bytes per skill (each < 5500)
	@wc -c skills/*/SKILL.md references/SIGNAN.md

bench: dist  # gate latency on the installed release: cold verify vs cached review/docs loop
	$(PYTHON) tests/e2e.py $(DIST) --bench

test:
	$(PYTHON) -m unittest discover -s tests -v

coverage:  # dev-only coverage.py (pip install coverage); AGENTS.md floor 60%
	@mkdir -p tmp && COVERAGE_FILE=tmp/.coverage $(PYTHON) -m coverage run --source=scripts,hooks -m unittest discover -s tests >/dev/null 2>&1 \
	  && COVERAGE_FILE=tmp/.coverage $(PYTHON) -m coverage report | tail -1

e2e: dist  # dogfood: install the release zip, drive only its CLI + hook adapter
	$(PYTHON) tests/e2e.py $(DIST)

doctor:
	$(PYTHON) scripts/vae.py doctor
	$(PYTHON) -m py_compile scripts/vae.py scripts/vae_core.py hooks/lifecycle.py tests/e2e.py

verify: test doctor e2e

dist:
	@mkdir -p output && rm -f $(DIST) && cd .. && zip -qr "$(CURDIR)/$(DIST)" "$(notdir $(CURDIR))" \
	  -x '*/__pycache__/*' '*.pyc' '*/.DS_Store' '*/.git/*' '*/.agents/*' '*/output/*' '*/tmp/*' '*/var/*' && echo $(DIST)

package: verify dist

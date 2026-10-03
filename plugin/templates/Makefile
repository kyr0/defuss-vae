# Project interface (defuss-vae layout): the same verbs for humans, agents and CI.
# Service: RUN = foreground command; stdout/stderr → var/log/$(NAME).*, pid → tmp/$(NAME).pid (both gitignored).
# test/coverage/lint/e2e exit 2 (UNKNOWN) until defined: the verifier runs them on every gate,
# and e2e must leave fresh files in output/ (the consumer's results or a Playwright report).
# Toolchain: new projects start on bun (JS/TS) or uv (Python); each placeholder names the default recipe.
# RUN e.g. `bun run src/index.ts` (bun loads .env) | `uv run --env-file .env python -m app`; apps log ISO-8601 UTC first, exit cleanly on SIGTERM.
NAME ?= app
RUN  ?=
N    ?= 50
export RUN
# Installer default dirs as a fallback: shell recipes (setup's own sync included) find tools it just installed,
# while tools already on PATH keep precedence.
export PATH := $(PATH):$(HOME)/.local/bin:$(HOME)/.bun/bin
LOG   = var/log/$(NAME)
PID   = tmp/$(NAME).pid
ALIVE = [ -f $(PID) ] && kill -0 "$$(cat $(PID))" 2>/dev/null

.PHONY: setup start stop restart status log metrics bench test coverage lint e2e verify

# Default goal. Fresh clone or machine: official installers for a missing uv/bun, then exactly the locked deps.
# Only lockfile-declared toolchains are touched, so an existing npm/poetry project is never migrated silently.
setup:
	@if [ -f uv.lock ]; then command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh; uv sync --locked; fi
	@if [ -f bun.lock ] || [ -f bun.lockb ]; then command -v bun >/dev/null 2>&1 || curl -fsSL https://bun.sh/install | bash; bun install --frozen-lockfile; fi

# Own process group (setsid, else job control) so stop reaps children too; redirected stdio keeps agent shells from hanging.
start:
	@mkdir -p var/log tmp
	@if $(ALIVE); then echo "running pid=$$(cat $(PID))"; exit 0; fi; \
	[ -n "$$RUN" ] || { echo "UNKNOWN[start] BC RUN unset"; exit 6; }; \
	if command -v setsid >/dev/null 2>&1; then nohup setsid sh -c "$$RUN" >$(LOG).stdout 2>$(LOG).stderr </dev/null & \
	else set -m; nohup sh -c "$$RUN" >$(LOG).stdout 2>$(LOG).stderr </dev/null & set +m; fi; \
	echo $$! >$(PID); sleep 1; \
	if $(ALIVE); then echo "running pid=$$(cat $(PID))"; else echo "dead; see: make log"; exit 1; fi

stop:
	@if $(ALIVE); then p=$$(cat $(PID)); kill -s TERM -- -$$p 2>/dev/null || kill -s TERM $$p; \
	i=0; while kill -0 $$p 2>/dev/null && [ $$i -lt 50 ]; do sleep 0.1; i=$$((i+1)); done; \
	kill -s KILL -- -$$p 2>/dev/null || true; fi; rm -f $(PID); echo stopped

restart: stop start

# LSB codes (make prints them as Error N): 0 running, 1 dead with stale pid, 3 stopped.
status:
	@if $(ALIVE); then echo "running pid=$$(cat $(PID))"; \
	elif [ -f $(PID) ]; then echo "dead pid=$$(cat $(PID)); see: make log"; exit 1; \
	else echo stopped; exit 3; fi

log:
	@for f in $(LOG).stdout $(LOG).stderr; do [ -f $$f ] && { echo "== $$f"; tail -n $(N) $$f; }; done; \
	ls $(LOG).* >/dev/null 2>&1 || echo "∅ no logs for $(NAME)"

metrics:  ; @echo "UNKNOWN[metrics] BC undefined: print current service|build metrics"; exit 2
bench:    ; @echo "UNKNOWN[bench] BC undefined: measure hot paths before perf claims"; exit 2
test:     ; @echo "UNKNOWN[test] BC undefined: real subsystems, no mocks — bun test | uv run pytest"; exit 2
coverage: ; @echo "UNKNOWN[coverage] BC undefined: print TOTAL <n>% — bun test --coverage | uv run pytest --cov"; exit 2
lint:     ; @echo "UNKNOWN[lint] BC undefined: uv run ruff check . | bunx oxlint --deny-warnings (plain oxlint exits 0 on findings)"; exit 2
e2e:      ; @echo "UNKNOWN[e2e] BC undefined: build → clean consumer → input/ → output/ — bun pm pack + bun add <tgz> | uv build + uv run --isolated --no-project --with dist/<whl> | web: Playwright on the served build (WebGL2, network, permissions)"; exit 2

# What CI runs; the gate runs the same verbs one by one and fails while verify skips any. Fail-fast order: cheapest first.
verify: lint test coverage e2e

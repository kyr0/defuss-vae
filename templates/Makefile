# Project interface (defuss-vae layout): the same verbs for humans, agents and CI.
# Service: RUN = foreground command; stdout/stderr → var/log/$(NAME).*, pid → tmp/$(NAME).pid (both gitignored).
# test/e2e exit 2 (UNKNOWN) until defined: the verifier runs them on every gate.
NAME ?= app
RUN  ?=
N    ?= 50
export RUN
LOG   = var/log/$(NAME)
PID   = tmp/$(NAME).pid
ALIVE = [ -f $(PID) ] && kill -0 "$$(cat $(PID))" 2>/dev/null

.PHONY: start stop restart status log metrics bench test e2e

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

metrics: ; @echo "UNKNOWN[metrics] BC undefined: print current service|build metrics"; exit 2
bench:   ; @echo "UNKNOWN[bench] BC undefined: measure hot paths before perf claims"; exit 2
test:    ; @echo "UNKNOWN[test] BC undefined: real subsystems in isolation, no mocks"; exit 2
e2e:     ; @echo "UNKNOWN[e2e] BC undefined: build publishable artifact → install in clean consumer → input/ → output/"; exit 2

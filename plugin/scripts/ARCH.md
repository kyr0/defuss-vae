# Architecture: gate programs

The CLI (`vae.py`) and the modules behind the hooks: repository facts, static prose checks, the verifier, session state, the sub-agent registry, the gate state machine, host adapters and project scaffolding. The full verify, review and docs contract is in [`docs/VERIFIER.md`](../../docs/VERIFIER.md).

## Why this design

Modules form a strict layer order, `vae_repo` → `vae_prose` → `vae_verify` → `vae_state` → `vae_swarm` → `vae_gate` → `vae_hooks` → `vae_project`, and import only lower layers (tested). Pure checks (`vae_prose`, path classification in `vae_repo`) take text and paths, so tests drive them with real inputs and no mocks; I/O sits in `vae_verify` and above. The Makefile is the only command source: guessing runners per ecosystem would verify commands the project never committed to.

## How it works

`gate()` computes the changed paths since the session baseline, fingerprints the gated ones (code and doc pages), and walks verify → review → docs. Verify runs the project's `make` verbs and `.agents/VERIFY.py` rules, or only the page checks and rules when nothing but pages changed since the last green suite. Review and docs are JSON attestations keyed to the fingerprint, so any edit reopens the gate.

Session start joins, in priority order, the rules, the gate command, the live sub-agents, MEMORY, CLI_GIST, the defaults of the stacks present ([`references/STACKS.md`](../references/STACKS.md)) and the open episode leads, cut between lines at 9,000 characters with a last line naming what did not fit.

`vae_swarm` keeps one registry entry per live sub-agent. A write takes an `flock` on `tmp/vae/swarm.lock`, replaces the file through `os.replace`, and re-reads it after the settle delay, re-applying a write a racing editor dropped. Liveness comes from one `ps -A -o pid=,ppid=,stat=,lstart=` call: a missing or zombie pid, or one whose process started after the entry did, means the agent is gone. A caller owns an entry when its pid is the caller or an ancestor. `spawn` starts a wrapper (`vae.py swarm run`) in a new session; the wrapper timestamps the job's output into its log, restores its entry every 30 s if an edit dropped it, and records the exit code.

## Operations

- **Configuration and policy:** `.agents/VERIFY.py` (`CONFIG`, `RULES`), loaded fresh on every gate run and hashed into the verification cache key.
- **Complexity and resources:** a cached gate run takes about 90 ms, most of it five `git` subprocesses; a cold run adds the project's commands (`timeout_s`, default 180 s each); repository walks stop at 50,000 files, and session start lists files through `git ls-files` instead (20 ms against 135 ms for a walk of 20,000 files); the suites are cached until code or policy change, the page checks until pages or policy change, so a README fix after a green suite reruns only the page checks; with `CONFIG["e2e_paths"]` e2e is cached until a scoped file, a build file or policy changes.
- **Reliability:** a missing command or metric is `UNKNOWN` and fails; gate text over 9,000 characters is cut to head and tail, with the full text in `gate.txt` in the session's folder; a swarm write is locked, atomic and re-read, and a crashed holder releases its lock.
- **Observability:** each command's output goes to `var/log/vae/<check>.log`; the gate appends `FAIL`, `DONE` and `FINDING` lines to `.agents/EPISODES.md`; past 100 entries it trims its own oldest `DONE`, `FAIL` and learned `FINDING` lines and never a lead (`LESSON`, `FINDING learn=none`), and `doctor` fails above 30 leads; each spawned job logs to `var/log/swarm/<name>.log`, one ISO-8601-stamped line per output line.

## Security and privacy

Trust boundary: everything in the project is trusted as the user's own code. `VERIFY.py` is imported, Makefile verbs and `command` rules run in a shell, and `swarm spawn` runs the command it is given, like a Makefile verb. `swarm stop` signals only process groups `spawn` created, checked against the wrapper's command line. The registry holds the host name and absolute paths and is gitignored. Nothing here opens a network connection; `swarm status` runs only local `ps`, `vm_stat`, `nvidia-smi`, `docker` or `podman` queries. State and logs stay in gitignored `tmp/vae/` and `var/log/vae/`, which ignore themselves even before the project's `.gitignore` does. No personal data beyond what the project's own commands print.

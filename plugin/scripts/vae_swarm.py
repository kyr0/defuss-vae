"""Swarm registry `.agents/SWARM_STATUS.yaml` (one entry per live sub-agent, no history) and detached job lifecycle.

WHY code over prose: agents of any harness or model can run a CLI, while agents hand-editing one shared YAML race and
drift. Every write here takes a lock, replaces the file atomically and re-reads it after a settle delay; liveness comes
from the process table, never from what the file claims."""
from __future__ import annotations

import calendar
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from vae_repo import git_root, now_iso, run, runtime_dir, safe_name

SWARM_FILE = ".agents/SWARM_STATUS.yaml"
LOG_DIR = "var/log/swarm"
FIELDS = ("name", "pid", "host", "goal", "exit_code", "workdir", "target_focus_paths", "start_timestamp", "eta_in_mins",
          "estimated_ram_usage", "estimated_vram_usage", "estimated_disk_space_usage", "gpu_id", "container_id", "pwd")
REQUIRED = ("name", "pid", "goal", "workdir", "target_focus_paths", "start_timestamp")
SETTLE_S = 3.0  # re-read delay after a write: a racing writer that dropped the entry shows by then
START_SLACK_S = 5  # `ps lstart` has 1 s resolution; registration trails the process start by milliseconds
STALL_MIN = 15  # a live agent whose log stayed silent this long (or half its ETA, if longer) is STALLED
HEAL_S = 30  # a running wrapper restores its own entry this often if an edit dropped it
LINE = re.compile(r"^(\s*)(- )?([a-z_]+):\s?(.*)$")
ITEM = re.compile(r"^\s+- (.*)$")
SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?)(i?B)?\s*$", re.IGNORECASE)


def swarm_root(cwd: Path) -> Path | None:
    """The main worktree: linked worktrees share one registry, so every sub-agent writes the same file.
    VERIFIED: (test_swarm) from a linked worktree, `--git-common-dir` names the main checkout's `.git`."""
    p = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd, timeout=5, shell=False)
    common = Path(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else None
    return common.parent if common and common.name == ".git" else git_root(cwd)


def dump(entries: list[dict[str, Any]]) -> str:
    """Block YAML whose values are JSON (valid YAML): any YAML tool reads it, `parse` round-trips it."""
    head = ("# Live sub-agents, one entry each, no history. Write only via `vae.py swarm` (lock, atomic replace, re-read"
            " after 3 s); `vae.py swarm status` judges liveness from the process table.")
    if not entries:
        return head + "\nagents: []\n"
    out = [head, "agents:"]
    for e in entries:
        for i, k in enumerate(list(FIELDS) + [k for k in e if k not in FIELDS]):
            out.append(("- " if i == 0 else "  ") + f"{k}: {json.dumps(e.get(k))}")
    return "\n".join(out) + "\n"


def scalar(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    with contextlib.suppress(ValueError):
        return json.loads(raw)
    if raw.startswith("[") and raw.endswith("]"):
        return [v.strip().strip("'\"") for v in raw[1:-1].split(",") if v.strip()]
    if len(raw) > 1 and raw[0] in "'\"" and raw[-1] == raw[0]:
        return raw[1:-1]
    return {"null": None, "~": None, "true": True, "false": False}.get(raw, raw)


def parse(text: str) -> list[dict[str, Any]]:
    """Our own dump and the block YAML an agent writes by hand: `- key: value` starts an entry, `  key: value` extends
    it, `    - item` appends to a list. WHY no YAML library: the hooks and CLI stay stdlib-only."""
    entries: list[dict[str, Any]] = []
    last = None
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#") or ln.startswith("agents:"):
            continue
        m = LINE.match(ln)
        if m and (m.group(2) or entries):
            if m.group(2):
                entries.append({})
            last = m.group(3)
            entries[-1][last] = scalar(m.group(4))
        elif entries and last and (item := ITEM.match(ln)):
            cur = entries[-1].get(last)
            entries[-1][last] = (cur if isinstance(cur, list) else []) + [scalar(item.group(1))]
    return entries


def read(root: Path) -> list[dict[str, Any]]:
    try:
        return parse((root / SWARM_FILE).read_text("utf-8"))
    except OSError:
        return []


@contextlib.contextmanager
def locked(root: Path) -> Iterator[None]:
    """One writer at a time across processes; flock dies with its holder, so a crash leaves no stale lock."""
    with open(runtime_dir(root, "tmp/vae") / "swarm.lock", "a") as fh:
        try:
            import fcntl
        except ImportError:  # Windows: no flock; the settle re-read still catches a lost update
            yield
            return
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def save(root: Path, entries: list[dict[str, Any]]) -> None:
    target = root / SWARM_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = runtime_dir(root, "tmp/vae") / f"SWARM_STATUS.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(dump(entries), "utf-8")
    os.replace(tmp, target)  # atomic: readers see the old or the new file, never half of one


def epoch(ts: Any) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def proc_table() -> dict[int, tuple[int, float | None, bool]]:
    """pid → (ppid, start epoch, zombie) from one `ps` call. LC_ALL=C keeps `lstart` parseable; macOS and Linux
    procps print it, busybox does not (empty table: liveness falls back to kill(pid, 0), ownership to the parent)."""
    p = run(["ps", "-A", "-o", "pid=,ppid=,stat=,lstart="], Path("/"), timeout=10, env={"LC_ALL": "C"}, shell=False)
    table: dict[int, tuple[int, float | None, bool]] = {}
    for ln in p.stdout.splitlines() if p.returncode == 0 else []:
        parts = ln.split()
        if len(parts) < 8 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        try:
            start: float | None = time.mktime(time.strptime(" ".join(parts[3:8]), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            start = None
        table[int(parts[0])] = (int(parts[1]), start, parts[2].startswith("Z"))
    return table


def container_running(cid: str) -> bool | None:
    for tool in ("docker", "podman"):
        if shutil.which(tool):
            p = run([tool, "inspect", "-f", "{{.State.Running}}", cid], Path("/"), timeout=10, shell=False)
            return p.returncode == 0 and p.stdout.strip() == "true"
    return None


def alive(e: dict[str, Any], table: dict[int, tuple[int, float | None, bool]], host: str) -> bool | None:
    """From the process table of this host; None when it cannot be known here (another host, no pid, no docker)."""
    if e.get("host") and e["host"] != host:
        return None
    if e.get("container_id"):
        return container_running(str(e["container_id"]))
    pid = e.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None
    if not table:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    row = table.get(pid)
    if row is None or row[2]:
        return False
    started = epoch(e.get("start_timestamp"))
    # A process that started after the entry did is another program reusing the pid (crash, reboot).
    return started is None or row[1] is None or row[1] <= started + START_SLACK_S


def owns(pid: Any, table: dict[int, tuple[int, float | None, bool]]) -> bool:
    """The caller owns `pid` when it is that process or descends from it: an agent's shells descend from the agent."""
    if not isinstance(pid, int):
        return False
    cur, seen = os.getpid(), set()
    while cur > 0 and cur not in seen:
        if cur == pid:
            return True
        seen.add(cur)
        cur = table[cur][0] if cur in table else (os.getppid() if cur == os.getpid() else 0)
    return False


def nested(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def conflicts(e: dict[str, Any], others: list[dict[str, Any]], root: Path) -> list[str]:
    """Claims that collide: the same or nested workdir, or overlapping target paths (`.` claims the whole repo)."""
    out = []
    wd = str((root / str(e.get("workdir") or ".")).resolve())
    mine = [os.path.normpath(p) for p in targets(e)]
    for o in others:
        if o.get("name") == e.get("name"):
            continue
        if nested(wd, str((root / str(o.get("workdir") or ".")).resolve())):
            out.append(f"{o.get('name')}: workdir {o.get('workdir')}")
        theirs = [os.path.normpath(p) for p in targets(o)]
        hit = next((p for p in mine for q in theirs if "." in (p, q) or nested(p, q)), None)
        if hit:
            out.append(f"{o.get('name')}: target {hit}")
    return out


def size_bytes(v: Any) -> int | None:
    """`512M`, `4G`, `1.5TB`, `300B`; a bare number means MiB."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v * 2**20)
    m = SIZE.match(str(v))
    if not m:
        return None
    unit = m.group(2).upper()
    power = {"K": 10, "M": 20, "G": 30, "T": 40}.get(unit, 0 if m.group(3) else 20)
    return int(float(m.group(1)) * 2**power)


def memory() -> tuple[int | None, int | None]:
    total = None
    with contextlib.suppress(ValueError, OSError, AttributeError):
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    with contextlib.suppress(OSError, KeyError, ValueError, IndexError):
        info = dict(ln.split(":", 1) for ln in Path("/proc/meminfo").read_text().splitlines() if ":" in ln)
        return total, int(info["MemAvailable"].split()[0]) * 1024
    if shutil.which("vm_stat"):  # macOS: free, inactive and speculative pages are available without swapping
        out = run(["vm_stat"], Path("/"), timeout=5, shell=False).stdout
        page = re.search(r"page size of (\d+)", out)
        pages = sum(int(n) for n in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", out))
        return total, pages * (int(page.group(1)) if page else 4096)
    return total, None


def gpus() -> list[dict[str, int]]:
    if not shutil.which("nvidia-smi"):
        return []
    p = run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total", "--format=csv,noheader,nounits"], Path("/"),
            timeout=10, shell=False)
    out = []
    for ln in p.stdout.splitlines() if p.returncode == 0 else []:
        cols = [c.strip() for c in ln.split(",")]
        if len(cols) == 3 and all(c.isdigit() for c in cols):
            out.append({"id": int(cols[0]), "free": (int(cols[2]) - int(cols[1])) * 2**20, "total": int(cols[2]) * 2**20})
    return out


def resources(root: Path) -> dict[str, Any]:
    ram_total, ram_free = memory()
    return {"disk_free": shutil.disk_usage(root).free, "ram_total": ram_total, "ram_free": ram_free,
            "cpus": os.cpu_count(), "load1": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
            "gpus": gpus()}


def human(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, power in (("T", 40), ("G", 30), ("M", 20)):
        if n >= 2**power:
            return f"{n / 2**power:.1f}{unit}"
    return f"{n}B"


def shortfall(e: dict[str, Any], res: dict[str, Any]) -> list[str]:
    """Estimates larger than what is free right now: refused, since the job would fail midway or starve the others."""
    out = []
    for field, free, what in (("estimated_disk_space_usage", res["disk_free"], "disk"), ("estimated_ram_usage", res["ram_free"], "ram")):
        need = size_bytes(e.get(field))
        if need and free is not None and need > free:
            out.append(f"{what} {human(need)} > free {human(free)}")
    need = size_bytes(e.get("estimated_vram_usage"))
    gpu = next((g for g in res["gpus"] if str(g["id"]) == str(e.get("gpu_id"))), None)
    if need and gpu and need > gpu["free"]:
        out.append(f"vram {human(need)} > free {human(gpu['free'])} on gpu {gpu['id']}")
    return out


def state(e: dict[str, Any], table: dict[int, tuple[int, float | None, bool]], host: str, root: Path, now: float) -> tuple[str, str]:
    missing = [k for k in REQUIRED if e.get(k) in (None, "", [])]
    if missing:
        return "MALFORMED", "missing " + ",".join(missing)
    if e.get("exit_code") is not None:
        return "EXITED", f"code={e['exit_code']}"
    live = alive(e, table, host)
    if live is None:
        return "UNKNOWN", f"host={e.get('host')}" if e.get("host") not in (None, host) else "no pid or container tool here"
    if not live:
        return "LOST", "process gone without an exit code"
    start, eta = epoch(e.get("start_timestamp")), e.get("eta_in_mins")
    if start and isinstance(eta, (int, float)) and now > start + eta * 60:
        return "OVERDUE", f"{int((now - start) / 60 - eta)}m past eta"
    log = root / LOG_DIR / f"{safe_name(str(e['name']))}.log"
    if log.exists():
        idle = (now - log.stat().st_mtime) / 60
        if idle > max(STALL_MIN, (eta or 0) / 2):
            return "STALLED", f"log silent {int(idle)}m"
    return "RUNNING", f"eta {eta}m" if eta else ""


def canon(e: dict[str, Any] | None) -> dict[str, Any] | None:
    """An entry as the file holds it: absent fields and nulls are the same."""
    return None if e is None else {k: v for k, v in e.items() if v is not None}


def targets(e: dict[str, Any]) -> list[str]:
    v = e.get("target_focus_paths")
    return [v] if isinstance(v, str) else [str(p) for p in v or []]


def settle(root: Path, name: str, want: dict[str, Any] | None, delay: float) -> tuple[int, str]:
    """Re-read after `delay`; re-apply our write while a racing writer keeps dropping it (`want` None: entry absent)."""
    for _ in range(3):
        time.sleep(delay)
        got = next((e for e in read(root) if e.get("name") == name), None)
        if canon(got) == canon(want):
            return 0, "settled"
        with locked(root):
            entries = [e for e in read(root) if e.get("name") != name]
            save(root, entries + ([want] if want is not None else []))
    return 1, f"DRIFT {name}: another writer keeps changing the entry; run `vae.py swarm status`"


def upsert(root: Path, name: str, updates: dict[str, Any], delay: float = SETTLE_S, register: bool = True) -> tuple[int, str]:
    """Owner-only write of one entry; a new entry must name a pid the caller owns and claim disjoint paths."""
    host = socket.gethostname()
    with locked(root):
        entries, table = read(root), proc_table()
        cur = next((e for e in entries if e.get("name") == name), None)
        if cur is None:
            if not register:
                return 2, f"{name}: no entry"
            if not owns(updates.get("pid"), table):
                return 2, (f"pid {updates.get('pid')} is neither this process nor an ancestor: register your own agent "
                           "process (`--pid $PPID` from your shell names the agent or harness running it)")
            entry: dict[str, Any] = {"name": name, "host": host, "start_timestamp": now_iso(), "exit_code": None, **updates}
        else:
            if cur.get("exit_code") is None and alive(cur, table, host) is not False and not owns(cur.get("pid"), table):
                return 2, f"{name} belongs to live pid {cur.get('pid')}: only its owner writes it"
            if "pid" in updates and not owns(updates["pid"], table):
                return 2, f"pid {updates['pid']} is neither this process nor an ancestor: an entry moves only to a pid you own"
            entry = {**cur, **updates}
        entry["pwd"] = str((root / str(entry.get("workdir") or ".")).resolve())
        missing = [k for k in REQUIRED if entry.get(k) in (None, "", [])]
        if missing:
            return 2, f"{name}: missing " + ",".join(missing)
        live = [o for o in entries if o.get("exit_code") is None and alive(o, table, host) is not False]
        clash = conflicts(entry, live, root) if entry.get("exit_code") is None else []
        if clash:
            return 2, "CONFLICT " + "; ".join(clash) + " (hard rule: disjoint workdirs and target paths)"
        short = shortfall(entry, resources(root)) if cur is None else []
        if short:
            return 2, "RESOURCES " + "; ".join(short)
        save(root, [entry if o is cur else o for o in entries] + ([entry] if cur is None else []))
    return settle(root, name, entry, delay)


def remove(root: Path, name: str, delay: float = SETTLE_S) -> tuple[int, str]:
    host = socket.gethostname()
    with locked(root):
        entries, table = read(root), proc_table()
        cur = next((e for e in entries if e.get("name") == name), None)
        if cur is None:
            return 0, f"{name}: no entry"
        if cur.get("exit_code") is None and alive(cur, table, host) and not owns(cur.get("pid"), table):
            return 2, f"{name} is running (pid {cur.get('pid')}): `vae.py swarm stop --name {name}` first"
        save(root, [e for e in entries if e is not cur])
    return settle(root, name, None, delay)


def spawn(root: Path, spec: dict[str, Any], cmd: list[str], delay: float = SETTLE_S) -> tuple[int, str]:
    """Start `cmd` detached (own session: an SSH drop or a dying parent shell cannot kill it) under a wrapper that
    timestamps its output into var/log/swarm/<name>.log and records its exit code; then register it."""
    name = str(spec["name"])
    workdir = (root / str(spec.get("workdir") or ".")).resolve()
    if not workdir.is_dir():
        return 2, f"workdir {spec.get('workdir')} missing: `git worktree add ../<repo>.wt/{name} -b swarm/{name}` first"
    missing = [k for k in REQUIRED if k not in ("pid", "start_timestamp") and spec.get(k) in (None, "", [])]
    if missing:
        return 2, f"{name}: missing " + ",".join(missing)
    host = socket.gethostname()
    with locked(root):
        entries, table = read(root), proc_table()
        if any(e.get("name") == name for e in entries):
            return 2, f"{name}: name taken; reap it first (`vae.py swarm rm --name {name}`) or pick another"
        live = [o for o in entries if o.get("exit_code") is None and alive(o, table, host) is not False]
        clash = conflicts(spec, live, root)
        if clash:
            return 2, "CONFLICT " + "; ".join(clash) + " (hard rule: disjoint workdirs and target paths)"
        short = shortfall(spec, resources(root))
        if short:
            return 2, "RESOURCES " + "; ".join(short)
        (root / LOG_DIR).mkdir(parents=True, exist_ok=True)
        wrapper = [sys.executable, str(Path(__file__).with_name("vae.py")), "swarm", "run", "--repo", str(root), "--name", name, "--", *cmd]
        proc = subprocess.Popen(wrapper, cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        entry = {**spec, "name": name, "pid": proc.pid, "host": host, "start_timestamp": now_iso(), "exit_code": None, "pwd": str(workdir)}
        save(root, entries + [entry])
    code, why = settle(root, name, entry, delay)
    return code, f"pid={proc.pid} log={LOG_DIR}/{safe_name(name)}.log" if code == 0 else why


def heal(root: Path, name: str, done: threading.Event, every: float = HEAL_S) -> None:
    """Restore this wrapper's entry when an edit dropped it: the wrapper owns it, so the restore is the owner's write.
    `every` is a parameter so tests exercise the loop in milliseconds without patching HEAL_S."""
    kept = None
    while not done.wait(every):
        with locked(root):
            entries = read(root)
            mine = next((e for e in entries if e.get("name") == name and e.get("pid") == os.getpid()), None)
            if mine:
                kept = mine
            elif kept and not any(e.get("name") == name for e in entries):
                save(root, entries + [kept])


def run_job(root: Path, name: str, cmd: list[str]) -> int:
    """The detached wrapper: each output line appended to the log with an ISO-8601 UTC timestamp, the exit code
    recorded in the registry. SIGTERM reaches the job through the process group; the wrapper outlives it to record."""
    signal.signal(signal.SIGTERM, lambda *_: None)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    log = root / LOG_DIR / f"{safe_name(name)}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as fh:
        def emit(text: str) -> None:
            fh.write(f"{now_iso()} {text}\n")
            fh.flush()

        emit(f"START pid={os.getpid()} cmd={shlex.join(cmd)}")
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as e:
            emit(f"EXIT 127 {e}")
            code = 127
        else:
            done = threading.Event()
            threading.Thread(target=heal, args=(root, name, done), daemon=True).start()
            for raw in iter(proc.stdout.readline, b""):
                emit(raw.decode("utf-8", "replace").rstrip("\n"))
            rc = proc.wait()
            done.set()
            code = rc if rc >= 0 else 128 - rc  # killed by signal N → 128+N, as shells report it
            emit(f"EXIT {code}")
    upsert(root, name, {"exit_code": code}, register=False)
    return code


def wrapper_of(pid: int, name: str) -> bool:
    """True when `pid` is our wrapper for `name`: stop must never signal a harness or a stranger's process group."""
    p = run(["ps", "-o", "command=", "-p", str(pid)], Path("/"), timeout=5, shell=False)
    return p.returncode == 0 and " swarm run " in p.stdout and re.search(rf"--name {re.escape(name)}(?:\s|$)", p.stdout) is not None


def stop(root: Path, name: str, grace: float = 5.0) -> tuple[int, str]:
    entries, table, host = read(root), proc_table(), socket.gethostname()
    cur = next((e for e in entries if e.get("name") == name), None)
    if cur is None:
        return 2, f"{name}: no entry"
    if alive(cur, table, host) is not True or cur.get("exit_code") is not None:
        return 0, f"{name}: not running"
    pid = int(cur["pid"])
    if not wrapper_of(pid, name):
        return 2, f"{name}: pid {pid} was not started by `vae.py swarm spawn`; stop it through its own harness"
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        now = next((e for e in read(root) if e.get("name") == name), {})
        if now.get("exit_code") is not None:  # the wrapper recorded the job's exit; it may still be settling
            return 0, f"{name}: stopped (exit {now['exit_code']})"
        if not alive(cur, proc_table(), host):
            break
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)
    with locked(root):  # the wrapper died before recording: record for it
        entries = read(root)
        save(root, [dict(e, exit_code=137) if e.get("name") == name and e.get("exit_code") is None else e for e in entries])
    return 0, f"{name}: killed (SIGKILL after {grace:g} s)"


def status(root: Path, fix: bool = False, delay: float = SETTLE_S) -> tuple[int, list[str]]:
    """Resources, then one line per entry with its state and next step. Exit 1 when the orchestrator has something to
    do. `fix` records drift: a LOST entry gets exit_code "lost", a duplicate name keeps its newest entry."""
    entries, table, host, now = read(root), proc_table(), socket.gethostname(), time.time()
    res = resources(root)
    claims = {k: sum(size_bytes(e.get(k)) or 0 for e in entries if e.get("exit_code") is None)
              for k in ("estimated_ram_usage", "estimated_disk_space_usage", "estimated_vram_usage")}
    gpu = ",".join(f"{g['id']}:{human(g['free'])}/{human(g['total'])}" for g in res["gpus"]) or "∅"
    lines = [(f"RESOURCES disk_free={human(res['disk_free'])} ram_free={human(res['ram_free'])}/{human(res['ram_total'])} "
              f"cpus={res['cpus']} load1={res['load1']} gpu_free={gpu} | live claims ram={human(claims['estimated_ram_usage'])} "
              f"disk={human(claims['estimated_disk_space_usage'])} vram={human(claims['estimated_vram_usage'])}")]
    attention = False
    states = {id(e): state(e, table, host, root, now) for e in entries}
    live = [e for e in entries if states[id(e)][0] in ("RUNNING", "OVERDUE", "STALLED", "UNKNOWN")]
    for e in entries:
        st, why = states[id(e)]
        name = e.get("name")
        nxt = {"LOST": f"tail {LOG_DIR}/{name}.log, restart from its last on-disk result, then `vae.py swarm rm --name {name}`",
               "EXITED": f"merge {e.get('workdir')} through the gate, then `vae.py swarm rm --name {name}`",
               "STALLED": f"tail {LOG_DIR}/{name}.log; `vae.py swarm stop --name {name}` if hung",
               "OVERDUE": f"tail {LOG_DIR}/{name}.log; extend eta via its owner or stop it",
               "MALFORMED": "fix the entry through its owner (`vae.py swarm set`)"}.get(st)
        clash = conflicts(e, live, root) if e in live else []
        attention |= st != "RUNNING" or bool(clash)
        lines.append(f"{st} {name} pid={e.get('pid')} {why}".rstrip() + f" workdir={e.get('workdir')} "
                     f"targets={','.join(targets(e))}" + (f" → {nxt}" if nxt else ""))
        lines += [f"CONFLICT {name} ↔ {c}" for c in clash]
    if claims["estimated_ram_usage"] and res["ram_total"] and claims["estimated_ram_usage"] > res["ram_total"]:
        attention = True
        lines.append("OVERCOMMIT live RAM claims exceed total RAM: spawn nothing more")
    if fix:
        lost = {e.get("name") for e in entries if states[id(e)][0] == "LOST"}
        names = [e.get("name") for e in entries]
        dupes = {n for n in names if names.count(n) > 1}
        if lost or dupes:
            for _ in range(2):
                with locked(root):
                    newest = {e.get("name"): e for e in read(root)}  # a later duplicate wins
                    save(root, [dict(e, exit_code="lost") if n in lost and e.get("exit_code") is None else e for n, e in newest.items()])
                time.sleep(delay)
                after = read(root)
                names = [e.get("name") for e in after]
                if all(names.count(n) == 1 for n in names) and all(e.get("exit_code") is not None for e in after if e.get("name") in lost):
                    break
            lines += [f"FIXED {n} exit_code=lost" for n in sorted(map(str, lost))] + [f"FIXED {n} duplicate dropped" for n in sorted(map(str, dupes))]
    return (1 if attention else 0), lines


def summary(root: Path) -> list[str]:
    """One line per entry for session start; [] without a registry, so idle repos pay nothing."""
    entries = read(root)
    if not entries:
        return []
    table, host, now = proc_table(), socket.gethostname(), time.time()
    return [f"{state(e, table, host, root, now)[0]} {e.get('name')}: {e.get('goal')} (workdir {e.get('workdir')})" for e in entries]

"""The verify -> review -> docs state machine, shared by the Stop hook and the in-turn `vae.py gate` loop."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

from vae_repo import (
    PLUGIN_ROOT,
    changed_since,
    code_fingerprint,
    file_hash,
    is_code,
    is_doc,
    is_gated,
    is_production_source,
    read_json,
    write_json,
)
from vae_state import (
    append_episodes,
    attestation_path,
    bootstrap,
    episode_entries,
    load_session,
    session_dir,
    state_path,
)
from vae_verify import (
    PROBE_TAG,
    e2e_scope,
    load_project_verifier,
    render_report,
    verify,
)

EPI = {"VERIFIED", "UNKNOWN", "HYPOTHESIS"}
REVIEW_CHECKLIST = [
    "requirements", "correctness", "callers", "errors", "state-concurrency", "security", "tests",
    "e2e", "observability", "structure", "reuse", "yagni", "smells-gotchas", "abstraction", "performance", "docs",
]
DOC_LEVELS = ("file", "method", "inline")


def validate_review(path: Path, fp: str, changed_paths: list[str] | None = None) -> tuple[bool, str]:
    obj = read_json(path)
    if not obj:
        return False, "review attestation missing/invalid"
    if obj.get("schema") != 1 or obj.get("code_fingerprint") != fp or obj.get("status") != "VERIFIED":
        return False, "review attestation stale or status!=VERIFIED"
    got = obj.get("checklist")
    if not isinstance(got, list) or any(x not in got for x in REVIEW_CHECKLIST):
        return False, "review checklist incomplete"
    reviewed = obj.get("reviewed_paths")
    if not isinstance(reviewed, list) or any(not isinstance(x, str) for x in reviewed):
        return False, "reviewed_paths missing/invalid"
    if changed_paths is not None:
        expected = {x for x in changed_paths if is_gated(x)}
        missing = expected - set(reviewed)
        if missing:
            return False, "reviewed_paths incomplete: " + ", ".join(sorted(missing))
    findings = obj.get("findings")
    if not isinstance(findings, list):
        return False, "review findings must be list"
    for f in findings:
        if not isinstance(f, dict):
            return False, "review finding invalid"
        if f.get("status") != "VERIFIED" or f.get("resolved") is not True:
            return False, "all review findings must be VERIFIED+resolved"
        if not str(f.get("location") or "").strip() or not str(f.get("evidence") or "").strip():
            return False, "each review finding needs location+evidence"
        learning = f.get("learning")
        if not isinstance(learning, dict) or learning.get("status") not in EPI:
            return False, "each finding needs learning status/action"
        if learning.get("status") == "VERIFIED" and learning.get("kind") not in {"test", "verifier", "memory", "none"}:
            return False, "learning.kind invalid"
    return True, "review attestation VERIFIED"


def validate_docs(path: Path, fp: str, changed_paths: list[str], repo: Path | None = None) -> tuple[bool, str]:
    expected = [p for p in changed_paths if is_production_source(p)]
    if not expected:
        # WHY: with no production source changed (doc pages, tests, config) there is no file to assess; an empty
        # attestation would prove nothing, so step 3 is complete instead of demanding a ritual file.
        return True, "docs ∅ production source changed"
    obj = read_json(path)
    if not obj:
        return False, "docs attestation missing/invalid"
    if obj.get("schema") != 1 or obj.get("code_fingerprint") != fp or obj.get("status") != "VERIFIED":
        return False, "docs attestation stale or status!=VERIFIED"
    entries = obj.get("files")
    if not isinstance(entries, list):
        return False, "docs files must be list"
    by_path = {e.get("path"): e for e in entries if isinstance(e, dict)}
    for rel in expected:
        e = by_path.get(rel)
        if not e:
            return False, f"docs missing file assessment: {rel}"
        if e.get("status") != "VERIFIED":
            return False, f"docs assessment not VERIFIED: {rel}"
        why = str(e.get("why") or "")
        alternative = str(e.get("alternative") or "").strip()
        if not why.startswith(("VERIFIED:", "UNKNOWN:", "HYPOTHESIS:")):
            return False, f"docs why missing epistemic label: {rel}"
        if not alternative:
            return False, f"docs alternative missing: {rel}"
        levels = [e.get(level) for level in DOC_LEVELS]
        for level, action in zip(DOC_LEVELS, levels):
            if action not in {"updated", "not-applicable"}:
                return False, f"docs {level} not assessed: {rel}"
        if all(action == "not-applicable" for action in levels) and not why.startswith("VERIFIED:"):
            return False, f"all-not-applicable docs rationale must be VERIFIED: {rel}"
        if repo is not None and any(action == "updated" for action in levels):
            source = repo / rel
            text = source.read_text("utf-8", errors="replace") if source.exists() else ""
            if not re.search(r"\b(?:VERIFIED|UNKNOWN|HYPOTHESIS):", text):
                return False, f"updated docs lack epistemic prefix in source: {rel}"
    return True, "docs file/method/inline assessment VERIFIED"


VERIFY_NEXT = (
    "NEXT: fix the lowest causal failure; rerun only needed scope. IF cause=? THEN probe before editing "
    f"(smallest discriminating observation; temporary lines tagged `{PROBE_TAG}`). "
    "IF a new deterministic failure class is not yet encoded THEN add a test OR .agents/VERIFY.py rule. NOT claim VERIFIED from inference.\n"
    "HARNESS: a FAIL is your work, NOT a reason to end the turn or hand off: apply the failing check's fix yourself, then rerun LOOP. "
    "NOT propose bypassing the gate (`! git commit`, `--no-verify`, a `CONFIG` switch that disables a check) as the way out. "
    "ONLY IF a check's fix says the decision is the human's THEN ask for exactly that decision with its options, after fixing everything else."
)


def review_instruction(fp: str, path: Path, changed: list[str], plugin_root: Path = PLUGIN_ROOT) -> str:
    pages = [p for p in changed if is_doc(p)]
    shape = {
        "schema": 1, "status": "VERIFIED", "code_fingerprint": fp,
        "reviewed_paths": [p for p in changed if is_gated(p)], "checklist": REVIEW_CHECKLIST,
        "findings": [{"status": "VERIFIED", "resolved": True, "location": "path:line|symbol", "evidence": "...",
                      "learning": {"status": "VERIFIED|UNKNOWN|HYPOTHESIS", "kind": "test|verifier|memory|none", "why": "..."}}],
    }
    return (
        "defuss-vae GATE 2/3 review: REQUIRED (verify=VERIFIED). Do NOT invoke a skill.\n"
        "Review requirements|plan + current diff + EVERY changed code path + relevant callers|callees|tests.\n"
        "PASS1 requirements, correctness, error paths, state|concurrency|resources, security, API|schema compat, "
        "tests (real subsystems in isolation, NOT mocks, NOT live|production data; every public behavior + its main error path), "
        "e2e (consumes the built artifact; EVERY page|route|screen|component of a UI at least once in a real Playwright browser with WebGL2|network|permissions it needs; EVERY CLI command|API endpoint at least once), observability (no leftover probe|debug spam; logs ISO-8601 UTC first + level).\n"
        "PASS2 structure + Ponytail: separated concerns in small testable modules; delete|reuse → stdlib → native → installed dependency → minimum code; NOT duplicate machinery, speculative config|abstraction, unmeasured optimization.\n"
        "Actionable finding REQUIRES location + causal evidence + minimal fix; fix EVERY one. "
        "IF recurrence mechanically checkable THEN regression test OR .agents/VERIFY.py rule ELSE learning.status=UNKNOWN + why.\n"
        + (f"PAGES {json.dumps(pages, separators=(',', ':'))}: review EVERY changed unit against {plugin_root}/references/PROSE.md "
           "(B evidence, L logic, P precision, R relevance, A structure, S style, T typography incl. T07 Mermaid); fix what the page|code supports, "
           "NOT invent facts; finding.location = page:line + rule id.\n" if pages else "")
        + "IF you edit source|test|page THEN rerun LOOP before attesting (fingerprint changes).\n"
        f"THEN write {path} (findings=[] IF none):\n{json.dumps(shape, separators=(',', ':'))}"
    )


def docs_instruction(fp: str, path: Path, changed: list[str]) -> str:
    prod = [p for p in changed if is_production_source(p)]
    entry = {"path": "<each required path>", "status": "VERIFIED", "file": "updated|not-applicable", "method": "updated|not-applicable",
             "inline": "updated|not-applicable", "alternative": "plausible alternative OR why none applies", "why": "VERIFIED: ..."}
    return (
        "defuss-vae GATE 3/3 docs: REQUIRED (review=VERIFIED). Do NOT invoke a skill.\n"
        "EVERY changed production file: assess file, changed method|function, non-obvious inline. Document why this design > plausible alternative, "
        "NOT what syntax does; material claims prefixed VERIFIED:|HYPOTHESIS:|UNKNOWN:; not-applicable REQUIRES a concrete reason; no filler.\n"
        f"THEN write {path}:\n"
        + json.dumps({"schema": 1, "status": "VERIFIED", "code_fingerprint": fp, "files": [entry]}, separators=(",", ":"))
        + "\nrequired paths=" + json.dumps(prod, separators=(",", ":"))
    )


@dataclasses.dataclass
class Gate:
    done: bool
    text: str


def bounded(repo: Path, session_id: str, text: str, limit: int = 9000) -> str:
    # VERIFIED: Claude Code swaps hook strings >10,000 chars for a file path + 2,000-char preview; keep head + tail
    # (AGENT_CMD/LOOP live at the end) visible instead, and the full text on disk.
    if len(text) <= limit:
        return text
    full = session_dir(repo, session_id) / "gate.txt"
    full.write_text(text, "utf-8")
    return text[:6000] + f"\n…truncated; full gate text: {full}\n" + text[-2500:]


def verify_key(repo: Path, fp: str) -> str:
    # WHY: policy files sit outside the code fingerprint yet change verification results, so they key the cache too.
    parts = [fp, file_hash(repo / ".agents" / "VERIFY.py"), file_hash(repo / ".gitignore")]
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def gate(repo: Path, session_id: str, plugin_root: Path = PLUGIN_ROOT) -> Gate:
    """One state machine for the Stop hook and the in-turn CLI: verify → review → docs.

    WHY a CLI loop: Claude Code honors one Stop block per turn, so the agent must be able to
    re-run the same gate itself until done instead of relying on repeated hook continuations."""
    state = load_session(repo, session_id)
    state["gate_runs"] = int(state.get("gate_runs", 0)) + 1
    sp = state_path(repo, session_id)
    changed = changed_since(repo, state["baseline"])
    code = [p for p in changed if is_gated(p)]
    if not code:
        write_json(sp, state)
        return Gate(True, "VERIFIED[gate]=true BC ∅ code|doc changes since session baseline")
    bootstrap(repo, plugin_root)
    fp = code_fingerprint(repo, code)
    code_key = verify_key(repo, code_fingerprint(repo, [p for p in code if is_code(p)]))
    pages_key = verify_key(repo, code_fingerprint(repo, [p for p in code if is_doc(p)]))
    loop = (f"LOOP: python3 {plugin_root}/scripts/vae.py gate --repo {repo} --session {session_id} "
            "→ repeat until VERIFIED[gate]=true; git commit stays denied until then.")
    # WHY two cache keys: code + policy hashes decide whether the suites must run again, pages + policy whether the page
    # checks must; a README typo after a green suite reruns only the prose check, and review/docs loops rerun nothing.
    code_ok = state.get("verified_key") == code_key or not any(is_code(p) for p in code)
    # WHY a third key: with CONFIG["e2e_paths"] set, e2e (often a Playwright run, the slowest suite) reruns only when
    # a file it depends on changed; without it the scope is every code file, so e2e reruns on any code edit as before.
    scope = None if code_ok else e2e_scope(load_project_verifier(repo)[0], [p for p in code if is_code(p)])
    e2e_key = None if scope is None else verify_key(repo, code_fingerprint(repo, scope))
    e2e_reuse = e2e_key if e2e_key and state.get("e2e_key") == e2e_key else None
    if not code_ok or state.get("pages_key") != pages_key:
        report = verify(repo, changed, suites=not code_ok, e2e_reuse=e2e_reuse)
        if not code_ok and not e2e_reuse:  # a green e2e stays cached even when another suite failed
            ran = [c for c in report.checks if c.id.startswith("tests.e2e")]
            state["e2e_key"] = e2e_key if ran and all(c.passes() for c in ran) else None
        if not report.verified:
            state["verified_fp"] = state["pages_key"] = None
            if not code_ok:  # a page failure leaves the green suites cached
                state["verified_key"] = None
            failing = ",".join(c.id + ("=?" if c.status != "VERIFIED" else "") for c in report.checks if not c.passes())
            if state.get("last_fail") != failing:
                append_episodes(repo, session_id, [f"FAIL {failing}"], plugin_root)
                state["last_fail"] = failing
            write_json(sp, state)
            return Gate(False, bounded(repo, session_id, "defuss-vae GATE 1/3 verify: FAIL\n" + render_report(report) + "\n" + VERIFY_NEXT + "\n" + loop))
        cov = next((c.metric for c in report.checks if c.id == "coverage"), state.get("coverage"))
        # Merged per check: a page-only rerun replaces only the warnings of the checks it ran.
        old = state.get("warns") if isinstance(state.get("warns"), dict) else {}
        ran = {c.id for c in report.checks}
        warns = {k: v for k, v in old.items() if k not in ran}
        warns.update({c.id: c.next or c.evidence for c in report.checks if not c.required and c.value is not True})
        state.update(verified_fp=fp, verified_key=code_key, pages_key=pages_key, last_fail=None, coverage=cov, warns=warns)
    # The review and docs texts are what the agent reads after verify, so warnings ride on them and on the green text.
    warns = state.get("warns") if isinstance(state.get("warns"), dict) else {}
    note = "".join(f"\nWARNS {k}: {v}" for k, v in sorted(warns.items())) + ("\n(non-blocking while CONFIG['strict'] is off; fix now)" if warns else "")
    rp = attestation_path(repo, session_id, "review")
    ok, why = validate_review(rp, fp, changed)
    if not ok:
        write_json(sp, state)
        return Gate(False, bounded(repo, session_id, review_instruction(fp, rp, changed, plugin_root) + f"\nSTATE: {why}{note}\n" + loop))
    dp = attestation_path(repo, session_id, "docs")
    ok, why = validate_docs(dp, fp, changed, repo)
    if not ok:
        write_json(sp, state)
        return Gate(False, bounded(repo, session_id, docs_instruction(fp, dp, changed) + f"\nSTATE: {why}{note}\n" + loop))
    if state.get("completed_fp") != fp:
        state["completed_fp"] = fp
        cov = state.get("coverage")
        paths = ",".join(code[:4]) + (f"(+{len(code) - 4})" if len(code) > 4 else "")
        entries = [f"DONE fp={fp[:12]} cov={'?' if cov is None else f'{cov:.1f}%'} paths={paths}"]
        # WHY skip known findings: a review carried across fingerprints re-lists earlier findings, and re-logging them
        # flooded the 100-entry window. VERIFIED: in this repo 71 of 100 entries were such repeats, pushing out history.
        # Repeated FAIL lines stay: a recurring failure is the signal to promote its lesson.
        known = {e.split(" ", 2)[2] for e in episode_entries(repo) if e.count(" ") >= 2}
        for f in (read_json(rp) or {}).get("findings") or []:
            learning = f.get("learning") if isinstance(f.get("learning"), dict) else {}
            lesson = " ".join(str(learning.get("why") or f.get("evidence") or "").split())[:160]
            line = f"FINDING {f.get('location')} learn={learning.get('kind')}: {lesson}"
            if line not in known and line not in entries:
                entries.append(line)
        append_episodes(repo, session_id, entries, plugin_root)
    write_json(sp, state)
    return Gate(True, f"VERIFIED[gate]=true BC verify+review+docs@fp={fp[:12]}{note}")

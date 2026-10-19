"""Deep research in the target project: an evidence-driven reasoning loop on the configured LLM, grounded through the
configured capabilities (MCP servers, CloakBrowser, the vision model), typed decisions and local python3 experiments.

WHY this method: it ports glitcr (feat-autoresearch-handoff/src/logos/glitcr), the most successful of the methods tried:
gather evidence with tools, reconcile it Popper-style, split the query into hypotheses, try to falsify each in its own
chain, then compare the chains (palantir) for the most probable answer with P(correct). WHY the changes: glitcr recursed
without a bound, ran chains one after another, kept evidence only in memory and auto-confirmed tool runs; here rounds and
tool calls are budgeted, chains run in parallel, every tool result lands in `evidence.jsonl` with its provider and time
(the report's sources), and `ask` reaches the human only from a terminal. WHY the reasoner is the configured endpoint
and not the harness model: the loop runs unattended in the background and its tokens stay out of the agent's context.

Artifacts: `tmp/vae/research/<yyyy-mm-dd_hh-mm-ss>_<n>/` holds `evidence.jsonl`, `chains/NN.md`, `experiments/NN.py`,
`report.md`, `report.html` (the human's review page, vae_annotate) and `meta.json`; no artifact holds a secret.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Callable

import vae_caps
import vae_cloak
import vae_decide
import vae_oai
from vae_annotate import write_page
from vae_net import ConfigError, load_env, load_pair
from vae_repo import STATE_DIR, now_iso, run, runtime_dir, write_json
from vae_verify import Check

RESULT_CHARS = 12000  # per tool result inside a prompt; evidence.jsonl keeps up to STORE_CHARS
STORE_CHARS = 200000
STATE_CHARS = 16000  # evidence a typed decision reads; the endpoint bills input tokens
EXPERIMENT_TIMEOUT_S = 30
MAX_CHAINS = 6
PAIRS = ("openai", "mcp", "typed-decisions")
TOOL_TAG = re.compile(r"<tool_use_required>(.*?)(?:</tool_use_required>|$)", re.DOTALL)
URL = re.compile(r"https?://[^\s\"'<>()\[\]\\]+")

# -- prompts (ported from glitcr/prompt.ts and palantir/prompt.ts; tool catalog and policy rendered per run) ----------

TOOL_USE = """Tool use: stop and write your requests as a JSON array inside one tag; the orchestrator runs them and gives you
the results in <tool_use_result> tags. Example:
<tool_use_required>
[{{"tool": "{example}", {example_args}}}]
</tool_use_required>

These tools exist (no others), each with the inputs in braces:
{catalog}"""

POLICY = """Then, for every point, think through, and print out, step by step, which tool requests (<tool_use_required>) you
need to get more evidence for the reasoning. In this order of priority:
{steps}
This prompt runs in a loop: when <tool_use_result> content does not satisfy you yet, request other tools.

Do NOT attempt to answer the user query. Focus on critical thinking and evidence gathering. Do NOT jump to conclusions.

{tools}"""

EVIDENCE = """You are an expert, deductive thought-process synthesizer and critical thinker who evaluates reasoning pathways to
find evidence. For the user input, come up with the best out-of-the-box questions to:
1. Identify the premises and assumptions made. Challenge them briefly.
2. Are there contradictions, paradoxes or logical inconsistencies?
3. What empirical evidence is needed to validate or invalidate the reasoning?
4. What calculations or scientific papers are needed to verify the assumptions?
5. What web searches are needed to find more context information?
6. Are there specific methods that should be used to address the user query (Kepler's laws, Newton's laws, ...)?

USER_QUERY (do NOT answer, only gather evidence):
{query}
END_OF_USER_QUERY.

{policy}"""

RECONCILE = """You are an expert, deductive thought-process synthesizer and critical thinker who evaluates reasoning pathways to
verify evidence. The provisional evidence below comes from tool results.
1. Test scientifically (Karl R. Popper) whether the evidence is valid and reliable.
2. Is more evidence needed to validate or invalidate the reasoning about the user query? If so, request it.
3. Request each tool at most 3 times.

USER_QUERY (do NOT answer, only gather evidence):
{query}
END_OF_USER_QUERY.

PROVISIONAL_EVIDENCE (do NOT answer, only reconcile the evidence):
{evidence}
END_OF_PROVISIONAL_EVIDENCE.

{policy}"""

SPLIT = """You are an expert analyst and decision maker. Split and re-print the user query into distinct, clear and meaningful
questions or hypotheses to test and think through, so each can be thought through in isolation. Retain every piece of
information and context from the original query. If there is only one option to pay attention to, return an array with
the original query as its only element. Return at most {max_chains} elements, in valid JSON only, as it will be parsed.
{labels}
--- Example 1 ---
User query: "What is the result of 2 + 2? a) 4 b) 5"
Response: [{example_a}, {example_b}]

--- Example 2 ---
User query: "Which is more beneficial for mental health: regular exercise or meditation?"
Response: ["Does regular exercise provide more mental health benefits than meditation?", "Does meditation offer more mental health benefits than regular exercise?"]

--- Example 3 ---
User query: "What is the best way to improve your mental health?"
Response: ["What is the best way to improve your mental health?"]"""

SPLIT_LABELS = """An element MAY be an object {"question": "...", "labels": ["yes", "no"]} when it is a closed question whose
answer is exactly one of a few labels; a fast typed-decision model answers those. Open questions stay plain strings.
"""

DEDUCE = """You are an expert, deductive thought-process synthesizer who evaluates reasoning.
Assume the thoughts provided are wrong. Why would the opposite of what was concluded, parts of it, one or more premises,
or an alternative explanation not be true?

RULES:
1. Read the reasoning and evidence provided and validate whether it is correct, through these lenses:
1.1. Axiomatic grounding: how well does it root in first principles?
1.2. Causal robustness: does it survive stress-testing?
1.3. Emergent alignment: unexpected consensus points?
1.4. Principle generality: transferability beyond the immediate context.
1.5. Method selection: are the methods used to address the topic correct?
2. Synthesize the findings with a Bayesian abductive framework:
2.1. Identify the best-supported axioms. 2.2. Filter context-specific assumptions. 2.3. Build a probabilistic dependency graph.
3. Cross-analyse all reasoning chains (the initial one and yours): agreement matrix (logical overlaps), divergence
detector (contradictory assumptions), principle extractor (implicit governing rules).
4. Output:
4.1. Answer for this reasoning chain: [most probable conclusion]
4.2. Underlying principles: [latent principle 1], [latent principle 2], ...
4.3. Synthesis pathway: consensus (what the evidence agrees on), crucible (where it disagrees and how that was resolved),
elevation (how the principles generalize).
4.4. Sources: the tool results (with their links) your answer rests on.

Structure the falsification as <deduction_1> ... </deduction_1> (hypothesis, assumptions, logical steps, systematic
falsification attempt, contradictions sought, alternative hypotheses, iterative revision, conclusion with the conditions
for falsification), more <deduction_n> blocks for relevant alternatives, then <deductive_conclusion>...</deductive_conclusion>.

If external evidence is required, you MUST stop early and immediately with a <tool_use_required> tag.
{tools}
---

PREV_REASONING_AND_EVIDENCE:
{evidence}
END_OF_PREV_REASONING_AND_EVIDENCE."""

SYNTHESIZE = """You are an expert, deductive thought-process analyst who compares reasoning paths. Identify the true, latent
logical principle that underpins the correct response to the user query, using the reasoning chains in REASONING_CHAINS:

1. Validate each chain through these lenses: axiomatic grounding, causal robustness, emergent alignment, principle
generality, and evidence alignment (how well is every chain supported by the complete evidence?).
2. Synthesize the findings with a Bayesian abductive framework: best-supported axioms, filtered context-specific
assumptions, probabilistic dependency graph.
3. Cross-analyse all chains: agreement matrix, divergence detector, principle extractor.
4. Output the final response:
4.0. Compare all chains and explain which conclusion is most probable and why; write a proposal for the final answer.
4.1. Final Answer: [most probable conclusion]
4.2. P(correct): X.XX (evidence support | consensus strength | principle generality | entropy penalty); state plainly
when it is below 0.89, the bar for a confident answer.
4.3. Underlying Principles: each with the chains that support or counter-validate it.
4.4. Synthesis Pathway: consensus, crucible, elevation.
4.5. Sources: the tool results (with their links) the answer rests on; never invent a source.

REASONING_CHAINS:
{chains}
END_OF_REASONING_CHAINS.

COMPLETE_EVIDENCE:
{evidence}
END_OF_COMPLETE_EVIDENCE."""

EXTRACT = "Extract the tool-use JSON array from the <tool_use_required> tag so it parses as valid JSON. Return ONLY the JSON."


def tool_catalog(router: vae_caps.Router | None, decider: vae_decide.Decider | None, experiments: bool) -> dict[str, tuple[dict[str, bool], str]]:
    """{tool: (inputs, what it does)} for this run: configured capabilities, then experiment, decide and ask."""
    tools = {name: vae_caps.CAPABILITIES[name] for name in (router.available() if router else {})}
    if experiments:
        tools["experiment"] = ({"code": True}, f"runs a python3 program (stdlib only, {EXPERIMENT_TIMEOUT_S} s); print() what you need back")
    if decider:
        tools["decide"] = ({"question": True, "labels": True, "state": False},
                           ("a fast typed-decision model: one of `labels` (a JSON list) for a closed question about `state` "
                            "(the text it refers to; default: the question), with confidence"))
    tools["ask"] = ({"question": True}, "asks the human; ONLY when the query is unclear or contradictory and no other tool settles it")
    return tools


def render_tools(tools: dict[str, tuple[dict[str, bool], str]]) -> str:
    lines = [f"- {name} {{{', '.join(k + ('' if req else '?') for k, req in inputs.items())}}}: {what}"
             for name, (inputs, what) in tools.items()]
    first = next(iter(tools))
    args = ", ".join(f'"{k}": "..."' for k, req in tools[first][0].items() if req)
    return TOOL_USE.format(example=first, example_args=args, catalog="\n".join(lines))


def render_policy(tools: dict[str, tuple[dict[str, bool], str]]) -> str:
    steps = [("experiment", "calculations and small programs that validate or invalidate the assumptions and premises"),
             ("paper_search", ("FIRST for an academic question (science, medicine, empirical research): peer-reviewed "
                               "papers; read a paper's full text with pdf. Not for other questions")),
             ("decide", "closed sub-questions with a few possible answers"),
             ("websearch", ("context information, the first source for a question that is not academic; then webscrape "
                            "or pdf on the most relevant results")),
             ("repo_search", "questions about a GitHub project: repo_search, repo_tree, repo_file"),
             ("files", "images, videos or documents the query names: ocr, vision, video, pdf, document"),
             ("ask", "ONLY when the query is unclear, or you meet a paradox or an endless regress, after trying the tools above")]
    have = set(tools) | ({"files"} if {"ocr", "vision", "video", "document"} & set(tools) else set())
    picked = [f"{chr(97 + i)}) {name}: {why}." for i, (name, why) in enumerate((n, w) for n, w in steps if n in have)]
    return POLICY.format(steps="\n".join(picked), tools=render_tools(tools))


# -- the run ------------------------------------------------------------------------------------------------------


def log(out: Callable[[str], None], msg: str) -> None:
    out(f"{now_iso()} INFO research {msg}")


def parse_requests(text: str) -> list[Any] | None:
    """The tool requests in a reply, [] when it has none, None when the tag holds no parseable JSON."""
    m = TOOL_TAG.search(text)
    if not m:
        return []
    body = m.group(1).strip()
    for candidate in (body, body[body.find("["):body.rfind("]") + 1] if "[" in body else ""):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        return value if isinstance(value, list) else [value]
    return None


def json_array(text: str) -> list[Any] | None:
    s = text.strip()
    s = s[s.find("["):s.rfind("]") + 1] if "[" in s else s
    try:
        value = json.loads(s)
    except ValueError:
        return None
    return value if isinstance(value, list) else [value]


class Research:
    def __init__(self, repo: Path, folder: Path, llm: vae_oai.Llm, router: vae_caps.Router | None,
                 decider: vae_decide.Decider | None, role: str, max_rounds: int, max_tool_calls: int,
                 experiments: bool, out: Callable[[str], None]):
        self.repo, self.dir, self.llm, self.router, self.decider = repo, folder, llm, router, decider
        self.role, self.max_rounds, self.max_tool_calls, self.out = role, max_rounds, max_tool_calls, out
        self.tools = tool_catalog(router, decider, experiments)
        self.policy = render_policy(self.tools)
        self.lock = threading.Lock()
        self.tool_calls, self.llm_calls, self.exhausted = 0, 0, False
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.questions: list[str] = []
        self.secrets = [v for pair in PAIRS for v in load_env(repo / STATE_DIR / f".env.{pair}").values() if len(v) >= 8]

    def scrub(self, text: str) -> str:
        """Secrets never reach an artifact, even when a server echoes a request header back in its error text."""
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    def ask_llm(self, system: str, user: str) -> str:
        reply = self.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], role=self.role)
        with self.lock:
            self.llm_calls += 1
            n = self.llm_calls
            for k in self.usage:
                self.usage[k] += int(reply["usage"].get(k) or 0)
        log(self.out, f"llm n={n} finish={reply['finish_reason']} prompt_tokens={reply['usage'].get('prompt_tokens')} "
                      f"completion_tokens={reply['usage'].get('completion_tokens')}")
        if not reply["text"].strip():
            # WHY fail: an empty answer would otherwise flow on as evidence. VERIFIED: (2026-10-11, live) with max_tokens 4000
            # a reasoning model ended every long call at finish_reason=length with empty text, and the report came out empty.
            raise vae_oai.LlmError(f"empty answer (finish_reason={reply['finish_reason']}, "
                                   f"completion_tokens={reply['usage'].get('completion_tokens')}); when it is `length`, "
                                   "raise max_tokens in .vae/.env.openai.json")
        return reply["text"].strip()

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock, (self.dir / "evidence.jsonl").open("a", encoding="utf-8") as f:
            f.write(self.scrub(json.dumps({"ts": now_iso(), **entry}, ensure_ascii=False)) + "\n")

    def run_tool(self, req: Any, phase: str) -> dict[str, Any]:
        """One tool request → {n, tool, input, provider?, result}; every failure is a result string, never an exception."""
        if not isinstance(req, dict) or req.get("tool") not in self.tools:
            return {"tool": req.get("tool") if isinstance(req, dict) else None,
                    "result": f"ERROR: unknown tool; use one of {', '.join(self.tools)}"}
        name = req["tool"]
        inputs = {k: req.get(k) for k in self.tools[name][0] if req.get(k) not in (None, "")}
        missing = [k for k, required in self.tools[name][0].items() if required and k not in inputs]
        if missing:
            return {"tool": name, "input": inputs, "result": f"ERROR: missing input {', '.join(missing)}"}
        with self.lock:
            if self.tool_calls >= self.max_tool_calls:
                self.exhausted = True
                return {"tool": name, "input": inputs, "result": "ERROR: tool budget exhausted; answer from the evidence you have"}
            self.tool_calls += 1
            n = self.tool_calls
        log(self.out, f"tool n={n} phase={phase} tool={name}")
        provider, ok, text = name, True, ""
        try:
            if name == "experiment":
                text = self.experiment(n, str(inputs["code"]))
            elif name == "decide":
                labels = inputs["labels"] if isinstance(inputs["labels"], list) else json.loads(str(inputs["labels"]))
                label, confidence, _ = self.decider.decide(str(inputs["question"]), [str(x) for x in labels],  # type: ignore[union-attr]
                                                           inputs.get("state"))
                text = f"{label} (confidence {confidence:.2f})"
            elif name == "ask":
                text = self.ask_human(str(inputs["question"]))
            else:
                answer = self.router.call(name, **inputs)  # type: ignore[union-attr]
                provider, text = answer["provider"], answer["text"]
        except Exception as e:  # noqa: BLE001 - errors are results the model can react to
            ok, text = False, f"ERROR: {type(e).__name__}: {e}"
        text = self.scrub(text)
        self.record({"n": n, "phase": phase, "tool": name, "provider": provider, "input": inputs, "ok": ok,
                     "text": text[:STORE_CHARS], "urls": sorted(set(URL.findall(text)))[:20]})
        clipped = text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + f"\n[... {len(text) - RESULT_CHARS} more characters]"
        return {"n": n, "tool": name, "input": inputs, "provider": provider, "result": clipped}

    def experiment(self, n: int, code: str) -> str:
        """Run LLM-written python3 in the run's experiments/ folder with a clean environment: no secrets, a timeout."""
        folder = self.dir / "experiments"
        folder.mkdir(exist_ok=True)
        path = folder / f"{n:02d}.py"
        path.write_text(code, "utf-8")
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
        try:
            p = subprocess.run([sys.executable, "-I", path.name], cwd=str(folder), env=env, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=EXPERIMENT_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            return f"ERROR: the program ran longer than {EXPERIMENT_TIMEOUT_S} s and was stopped"
        return f"exit {p.returncode}\n{p.stdout[-6000:]}"

    def ask_human(self, question: str) -> str:
        with self.lock:
            self.questions.append(question)
        if sys.stdin.isatty():
            try:
                return input(f"\nresearch asks: {question}\n> ").strip() or "(no answer)"
            except EOFError:
                pass
        return "UNANSWERED: no human at a terminal; the question is recorded for the report. Continue with the evidence you have."

    def tools_round(self, reply: str, phase: str) -> str:
        """The <tool_use_result> block for the tool requests in `reply`, or '' when it has none."""
        reqs = parse_requests(reply)
        if reqs is None:  # glitcr's repair step: one extraction call, then give up with an error result
            reqs = json_array(self.ask_llm(EXTRACT, reply))
        if reqs is None:
            results = [{"tool": None, "result": "ERROR: the <tool_use_required> content was not a JSON array"}]
        elif not reqs:
            return ""
        else:
            results = [self.run_tool(r, phase) for r in reqs[:8]]
        return "<tool_use_result>\n" + json.dumps(results, ensure_ascii=False, indent=1) + "\n</tool_use_result>"

    def evidence(self, query: str) -> str:
        log(self.out, "phase=evidence")
        first = self.ask_llm(EVIDENCE.format(query=query, policy=self.policy), "Gather the evidence only. Do NOT answer yet.")
        found = self.tools_round(first, "evidence")
        log(self.out, "phase=reconcile")
        second = self.ask_llm(RECONCILE.format(query=query, evidence=found or "(none yet)", policy=self.policy),
                              "Reconcile the evidence only. Do NOT answer yet.")
        more = self.tools_round(second, "reconcile")
        thoughts = TOOL_TAG.sub("", second).strip()
        return f"Preliminary thoughts:\n{thoughts}\n\nEvidence:\n{found or '(none)'}\n{more}".strip()

    def split(self, query: str) -> list[dict[str, Any]]:
        log(self.out, "phase=split")
        a, b = ('{"question": "Is 4 the result of 2 + 2? (option a)", "labels": ["yes", "no"]}',
                '{"question": "Is 5 the result of 2 + 2? (option b)", "labels": ["yes", "no"]}') if self.decider else \
            ('"Is 4 the result of 2 + 2? (option a)"', '"Is 5 the result of 2 + 2? (option b)"')
        reply = self.ask_llm(SPLIT.format(max_chains=MAX_CHAINS, labels=SPLIT_LABELS if self.decider else "",
                                          example_a=a, example_b=b), query)
        items = json_array(reply) or [query]
        out = []
        for item in items[:MAX_CHAINS]:
            if isinstance(item, dict) and isinstance(item.get("question"), str):
                labels = item.get("labels") if isinstance(item.get("labels"), list) and len(item["labels"]) >= 2 else None
                out.append({"question": item["question"], "labels": [str(x) for x in labels] if labels else None})
            elif isinstance(item, str) and item.strip():
                out.append({"question": item.strip(), "labels": None})
        return out or [{"question": query, "labels": None}]

    def route(self, item: dict[str, Any], evidence: str) -> dict[str, Any] | None:
        """A typed decision for a closed question when the model is confident enough, else None (a chain runs).
        WHY the evidence as its state: a confident answer skips the falsification chain, so it must rest on what the
        run found, not on the decision model's priors alone."""
        if not (self.decider and item["labels"]):
            return None
        try:
            label, confidence, confident = self.decider.decide(item["question"], item["labels"], evidence[:STATE_CHARS])
        except Exception as e:  # noqa: BLE001 - a failed decision falls back to a full chain
            log(self.out, f"decide failed, chain instead: {type(e).__name__}")
            return None
        self.record({"n": 0, "phase": "split", "tool": "decide", "provider": "typed-decisions",
                     "input": {"question": item["question"], "labels": item["labels"]}, "ok": True,
                     "text": f"{label} (confidence {confidence:.2f})", "urls": []})
        return {"question": item["question"], "label": label, "confidence": confidence} if confident else None

    def chain(self, i: int, question: str, evidence: str) -> str:
        log(self.out, f"phase=chain n={i}")
        for round_ in range(self.max_rounds + 1):
            reply = self.ask_llm(DEDUCE.format(tools=render_tools(self.tools), evidence=evidence), question)
            if "<tool_use_required>" not in reply or round_ == self.max_rounds:
                break
            evidence += "\n" + self.tools_round(reply, f"chain{i}")
        (self.dir / "chains").mkdir(exist_ok=True)
        (self.dir / "chains" / f"{i:02d}.md").write_text(self.scrub(f"# {question}\n\n{reply}\n"), "utf-8")
        return reply


def conclusion(reply: str) -> str:
    m = re.search(r"<deductive_conclusion>(.*?)</deductive_conclusion>", reply, re.DOTALL)
    return (m.group(1) if m else TOOL_TAG.sub("", reply)[-1500:]).strip()


def sources(folder: Path) -> list[str]:
    lines = []
    path = folder / "evidence.jsonl"
    for raw in path.read_text("utf-8").splitlines() if path.exists() else []:
        e = json.loads(raw)
        if not e.get("ok") or e.get("tool") in ("experiment", "ask"):
            continue
        what = ", ".join(f"{k}={str(v)[:80]}" for k, v in e.get("input", {}).items())
        links = " ".join(f"<{u}>" for u in e.get("urls", [])[:5])
        lines.append(f"{len(lines) + 1}. `{e['tool']}` via {e['provider']} ({what}), {e['ts']}" + (f": {links}" if links else ""))
    return lines


def run_research(repo: Path, query: str, role: str = "reasoner", max_rounds: int = 2, max_tool_calls: int = 24,
                 experiments: bool = True, out: Callable[[str], None] = print) -> tuple[int, Path | None]:
    """Run one research job; (0, folder) when the report is written within budget, (2, folder|None) otherwise."""
    try:
        llm = vae_oai.from_config(repo)
        if llm is None:
            out("UNKNOWN[research] BC .vae/.env.openai.json is missing: the reasoner is the configured endpoint "
                f"(scaffold the config: vae.py research init --repo {repo})")
            return 2, None
        router = vae_caps.from_repo(repo)
        decider = vae_decide.from_config(repo)
    except ConfigError as e:
        out(f"UNKNOWN[research] BC {e}")
        return 2, None
    base = runtime_dir(repo, "tmp/vae/research")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())
    n = 1 + sum(1 for p in base.glob(f"{stamp}_*"))
    folder = base / f"{stamp}_{n}"
    folder.mkdir(parents=True)
    r = Research(repo, folder, llm, router, decider, role, max_rounds, max_tool_calls, experiments, out)
    started = time.time()
    meta: dict[str, Any] = {"query": query, "started": now_iso(), "role": role, "model": llm.models.get(role, {}).get("name"),
                            "base_url": llm.base, "tools": list(r.tools), "providers": router.available() if router else {},
                            "budgets": {"max_rounds": max_rounds, "max_tool_calls": max_tool_calls, "experiments": experiments}}
    log(out, f"run={folder.relative_to(repo)} tools={','.join(r.tools)}")
    status, error = "done", None
    try:
        evidence = r.evidence(query)
        items = r.split(query)
        decided = [d for d in (r.route(item, evidence) for item in items) if d]
        open_items = [item for item in items if not any(d["question"] == item["question"] for d in decided)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, len(open_items)))) as pool:
            replies = list(pool.map(lambda a: r.chain(a[0] + 1, a[1]["question"], evidence), enumerate(open_items)))
        chains = "\n\n---\n\n".join(f'Chain of thought on "{it["question"]}":\n{rep}' for it, rep in zip(open_items, replies))
        chains += "".join(f'\n\n---\n\nTyped decision on "{d["question"]}": {d["label"]} (confidence {d["confidence"]:.2f})'
                          for d in decided)
        log(out, "phase=synthesis")
        final = r.ask_llm(SYNTHESIZE.format(chains=chains, evidence=evidence), query)
        report = [f"# Research: {query}", "", final, "", "## Chains"]
        report += [f"\n### {i}. {it['question']}\n\n{conclusion(rep)}" for i, (it, rep) in enumerate(zip(open_items, replies), 1)]
        if decided:
            report += ["", "## Typed decisions", ""] + [f"- {d['question']}: {d['label']} (confidence {d['confidence']:.2f})" for d in decided]
        if r.questions:
            report += ["", "## Questions for the human", ""] + [f"- {q}" for q in r.questions]
        report += ["", "## Sources", ""] + (sources(folder) or ["(no tool result was used)"])
        text = r.scrub("\n".join(report) + "\n")
        (folder / "report.md").write_text(text, "utf-8")
        rel = str(folder.relative_to(repo))
        # the human's review page for the report: comments on it come back through `vae.py comments`
        write_page(folder / "report.html", f"Research: {query}", text, [],
                   {"kind": "research", "run": rel, "source": f"{rel}/report.md", "snapshot": f"{rel}/report.md", "findings": {}})
    except Exception as e:  # noqa: BLE001 - the run records its failure; the artifacts so far stay
        status, error = "failed", r.scrub(f"{type(e).__name__}: {e}")
    finally:
        if router:
            router.close()
    meta.update(finished=now_iso(), duration_s=round(time.time() - started, 1), status=status, error=error,
                llm_calls=r.llm_calls, tool_calls=r.tool_calls, usage=r.usage, exhausted=r.exhausted, questions=r.questions,
                decision_usage=decider.usage if decider else None)
    write_json(folder / "meta.json", json.loads(r.scrub(json.dumps(meta))))
    rel = folder.relative_to(repo)
    if error:
        out(f"UNKNOWN[research] BC {error}; partial artifacts in {rel}")
        return 2, folder
    for q in r.questions:
        out(f"ASK: {q}")
    if r.exhausted:
        out(f"UNKNOWN[research.budget] BC the {max_tool_calls} tool calls ran out; report={rel}/report.md")
        return 2, folder
    out(f"PAGE: {rel}/report.html (the human's review page)")
    out(f"VERIFIED[research]=true BC report={rel}/report.md tool_calls={r.tool_calls} llm_calls={r.llm_calls}")
    return 0, folder


# -- doctor and init ----------------------------------------------------------------------------------------------


def endpoint_checks(repo: Path) -> list[Check]:
    """One check per configured system: config parses, endpoint answers, every capability provider exists, secrets
    are gitignored. Nothing configured → no checks, so doctor output stays the same for projects without research."""
    configured = [p for p in PAIRS if (repo / STATE_DIR / f".env.{p}.json").is_file()]
    checks: list[Check] = []
    secrets = [f"{STATE_DIR}/.env.{p}" for p in PAIRS if (repo / STATE_DIR / f".env.{p}").is_file()]
    if secrets:
        ignored = set(run(["git", "check-ignore", "--no-index", *secrets], repo, timeout=5, shell=False).stdout.split())
        bare = [s for s in secrets if s not in ignored]
        checks.append(Check("research.secrets-ignored", "every .vae/.env.<name> secret file is gitignored", "VERIFIED", not bare,
                            f"not ignored: {bare}" if bare else f"ignored: {secrets}",
                            next=f"ADD `{STATE_DIR}/.env.*` to .gitignore" if bare else None))
    for pair in configured:
        cid = f"research.config.{pair}"
        try:
            client: Any = {"openai": vae_oai.from_config, "typed-decisions": vae_decide.from_config,
                           "mcp": vae_caps.from_repo}[pair](repo)
        except ConfigError as e:
            checks.append(Check(cid, f".vae/.env.{pair}.json is valid", "VERIFIED", False, str(e), next=f"FIX .vae/.env.{pair}.json"))
            continue
        checks.append(Check(cid, f".vae/.env.{pair}.json is valid", "VERIFIED", True, "parsed"))
        if pair == "openai":
            checks.append(probe("research.openai", "the LLM endpoint serves every configured model", lambda c=client: openai_probe(c)))
            checks += model_checks(client)
        elif pair == "typed-decisions":
            checks.append(probe("research.typed-decisions", "the typed-decision endpoint serves its model and answers a closed question",
                                lambda c=client: decision_probe(c)))
        else:
            checks += mcp_checks(client)
    return checks


IMAGE_PROBE = "Which color is the left half and which the right half of this image? Answer as: left=<color>, right=<color>."
PDF_PROBE = "What is the number in this document? Reply with the number only."


def sample_png(w: int = 64, h: int = 64) -> bytes:
    """A PNG, left half red, right half blue: a question about it has one answer only a model that sees it gives."""
    rows = b"".join(b"\x00" + (b"\xff\x00\x00" * (w // 2) + b"\x00\x00\xff" * (w - w // 2)) for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def sample_pdf(text: str) -> bytes:
    """A one-page PDF showing `text` in Helvetica."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def attachment_probe(llm: vae_oai.Llm, role: str) -> str:
    """One question per declared attachment kind that only a model reading the attachment answers right.
    WHY no probe for `document` and `video`: no sample here is verified to parse on any endpoint (z.ai rejected Office
    files and mp4 URLs on 2026-10-12), so a failure would not tell a broken model from a broken sample."""
    declared = set(llm.models[role]["capabilities"])
    seen = []
    with tempfile.TemporaryDirectory() as tmp:
        if declared & {"vision", "ocr"}:
            path = Path(tmp) / "halves.png"
            path.write_bytes(sample_png())
            text = llm.chat([{"role": "user", "content": IMAGE_PROBE}], role=role, images=[str(path)])["text"]
            if not ("red" in text.lower() and "blue" in text.lower()):
                raise RuntimeError(f"image: the answer names no red and blue half: {text[:80]!r}")
            seen.append(f"image: {text.strip()[:40]}")
        if "pdf" in declared:
            path = Path(tmp) / "sample.pdf"
            path.write_bytes(sample_pdf("The number is 4711."))
            text = llm.chat([{"role": "user", "content": PDF_PROBE}], role=role, files=[str(path)])["text"]
            if "4711" not in text:
                raise RuntimeError(f"pdf: the answer misses the number 4711: {text[:80]!r}")
            seen.append(f"pdf: {text.strip()[:40]}")
    untested = sorted(declared & {"document", "video"})
    return "; ".join(seen + ([f"not probed: {', '.join(untested)}"] if untested else []))


def model_checks(llm: vae_oai.Llm) -> list[Check]:
    """A declared capability must be one a model serves, and each declaring model must read its sample attachments."""
    claim = "every capability a model declares in .vae/.env.openai.json is one a model serves"
    try:
        vae_caps.with_models({}, llm)
    except ConfigError as e:
        return [Check("research.models", claim, "VERIFIED", False, str(e), next="FIX models.<role>.capabilities in .vae/.env.openai.json")]
    return [probe(f"research.model.{role}", f"model {role} reads the attachments its capabilities declare",
                  lambda r=role: attachment_probe(llm, r))
            for role, spec in llm.models.items() if spec.get("capabilities")]


def decision_probe(d: vae_decide.Decider) -> str:
    served = d.models()
    if d.model not in served:
        raise RuntimeError(f"model {d.model!r} not served: {served}")
    label, confidence, _ = d.decide("Is the number 4 even?", ["yes", "no"])
    return f"{d.model}: {label} ({confidence:.2f})"


def probe(cid: str, claim: str, fn: Callable[[], str]) -> Check:
    try:
        return Check(cid, claim, "VERIFIED", True, fn())
    except Exception as e:  # noqa: BLE001 - a failed probe is the check's evidence
        return Check(cid, claim, "VERIFIED", False, f"{type(e).__name__}: {e}"[:300],
                     next="CHECK the endpoint, its key in the secret file, and the JSON config")


def openai_probe(llm: vae_oai.Llm) -> str:
    served = set(llm.models_available())
    missing = sorted({spec["name"] for spec in llm.models.values()} - served)
    if missing:
        raise LookupError(f"not served: {missing}; served: {sorted(served)[:10]}")
    return f"serves {sorted({spec['name'] for spec in llm.models.values()})}"


def mcp_checks(router: vae_caps.Router) -> list[Check]:
    tools: dict[str, set[str]] = {}
    checks = []
    try:
        for name, client in router.clients.items():
            check = probe(f"research.mcp.{name}", f"MCP server {name} answers initialize and tools/list",
                          lambda c=client: f"{len(tools.setdefault(c.name, {t['name'] for t in c.list_tools()}))} tools")
            checks.append(check)
    finally:
        router.close()
    for cap, providers in router.capabilities.items():
        if not providers:  # every provider sits on a disabled server: nothing to check
            continue
        bad = []
        for p in providers:
            if p.get("builtin") == "cloak" and vae_cloak.binary() is None:
                bad.append("cloak: CloakBrowser not installed")
            elif "model" in p and not (router.llm and p["model"] in router.llm.models):
                bad.append(f"{vae_caps.label(p)}: no `{p['model']}` model in .vae/.env.openai.json")
            elif "server" in p and p["server"] in tools and p["tool"] not in tools[p["server"]]:
                bad.append(f"{vae_caps.label(p)}: no such tool")
        checks.append(Check(f"research.capability.{cap}", f"every provider of {cap} exists", "VERIFIED", not bad,
                            "; ".join(bad) if bad else ", ".join(vae_caps.label(p) for p in providers),
                            next="FIX the provider list in .vae/.env.mcp.json `capabilities`" if bad else None))
    return checks


def init(repo: Path, template_root: Path) -> list[str]:
    """Scaffold the six config files (never overwrite) and gitignore the secret files; returns what changed."""
    done = []
    for name in (f".env.{p}{ext}" for p in PAIRS for ext in ("", ".json")):
        dst = repo / STATE_DIR / name
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((template_root / "templates" / "research" / name.lstrip(".")).read_text("utf-8"), "utf-8")
        if not name.endswith(".json"):
            dst.chmod(0o600)
        done.append(f"{STATE_DIR}/{name}")
    gi = repo / ".gitignore"
    lines = gi.read_text("utf-8").splitlines() if gi.exists() else []
    if f"{STATE_DIR}/.env.*" not in (ln.strip().lstrip("/") for ln in lines):
        gi.write_text("\n".join(lines + [f"{STATE_DIR}/.env.*"]) + "\n", "utf-8")
        done.append(f".gitignore: + {STATE_DIR}/.env.*")
    return done


def describe(repo: Path) -> tuple[int, list[str]]:
    """What research can use here: the LLM roles, the typed-decision endpoint, each MCP server's tools and each
    capability's providers in fallback order."""
    lines: list[str] = []
    try:
        llm, decider = vae_oai.from_config(repo), vae_decide.from_config(repo)
        router = vae_caps.from_repo(repo)
    except ConfigError as e:
        return 2, [f"UNKNOWN[research.tools] BC {e}"]
    lines.append("llm: " + (", ".join(f"{role}={spec['name']}" + (f" [{', '.join(spec['capabilities'])}]" if spec.get("capabilities") else "")
                                      for role, spec in llm.models.items()) + f" @ {llm.base}"
                            if llm else "∅ (no .vae/.env.openai.json)"))
    lines.append("typed-decisions: " + (f"{decider.model} @ {decider.url} threshold={decider.threshold}" if decider else "∅"))
    try:
        for name, client in router.clients.items():
            try:
                lines.append(f"mcp {name}: " + ", ".join(t["name"] for t in client.list_tools()))
            except Exception as e:  # noqa: BLE001 - one server down never hides the others
                lines.append(f"mcp {name}: UNREACHABLE {e}"[:300])
    finally:
        router.close()
    if load_pair(repo, "mcp")[0] is None:
        lines.append("mcp: ∅ (no .vae/.env.mcp.json)")
    avail = router.available()
    for cap, (inputs, what) in vae_caps.CAPABILITIES.items():
        ins = ", ".join(k + ("" if req else "?") for k, req in inputs.items())
        lines.append(f"capability {cap} {{{ins}}}: " + (" → ".join(avail[cap]) if cap in avail else "∅") + f"  ({what})")
    lines.append("cloak: " + str(vae_cloak.binary() or "∅ (not installed)"))
    return 0, lines


def call_capability(repo: Path, capability: str, pairs: list[str]) -> tuple[int, list[str]]:
    """One capability call with fallback from the CLI: `key=value` inputs; the text and the provider that answered."""
    bad = [p for p in pairs if "=" not in p]
    if bad:
        return 2, [f"UNKNOWN[research.call] BC inputs are key=value pairs, got {bad}"]
    try:
        router = vae_caps.from_repo(repo)
    except ConfigError as e:
        return 2, [f"UNKNOWN[research.call] BC {e}"]
    try:
        answer = router.call(capability, **dict(p.split("=", 1) for p in pairs))
    except vae_caps.CapabilityError as e:
        return 2, [f"UNKNOWN[research.call] BC {e}"]
    finally:
        router.close()
    fell = "".join(f"\nFALLBACK {a['provider']}: {a['error']}" for a in answer["attempts"])
    return 0, [answer["text"], f"VERIFIED[research.call]=true BC {capability} via {answer['provider']}{fell}"]

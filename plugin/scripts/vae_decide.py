"""A stdlib client for the TypeSafe System One API: content (`state`) plus named, typed questions in, typed answers out.

WHY this wire format: it is TypeSafe's public API, the protocol of the reference SDK (typesafe-sdk-python 0.7), so a
self-hosted server the SDK can talk to works through `base_url` alone. WHY no SDK: it needs httpx2, pydantic and tenacity, and the
plugin runs on plain python3; the protocol is two JSON calls. WHY a threshold: a closed question answered with
confidence at or above it skips a full deduction chain in research, which is where the cost goes. VERIFIED: on
2026-10-12 api.typesafe.ai listed `jev-latest` and `jev-preview` on GET /v1/models and answered a choice and a noul on
POST /v1/systemone in 0.7 s through urllib; the reply's `model` named the version behind the alias (`jev-1.13.0`).

Questions follow the wire schema, keyed by a name the answers reuse:

    {"type": "choice", "instructions": "...", "criteria": {"label": "when it applies" | None, ...}}
        → {"type": "choice", "choice": "label", "confidence": 0..1, "probabilities": {"label": 0..1, ...}}
    {"type": "noul", "instructions": "a yes/no question or statement"}  → {"type": "noul", "noul": P(yes)}
    {"type": "score", "instructions": "...", "criteria": ["level 0", "level 1", ...]}
        → {"type": "score", "score": expected level, "confidence": 0..1, "legend": {...}, "probabilities": {...}}

Config, `.vae/.env.typed-decisions.json` (the key named by `auth_env` lives in `.vae/.env.typed-decisions`):

    {"base_url": "https://api.typesafe.ai", "model": "jev-latest", "auth_env": "TYPED_DECISIONS_API_KEY",
     "threshold": 0.8, "timeout_s": 10}
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from vae_net import ConfigError, http_error, load_pair, redact, request_json, secret

DEFAULT_BASE_URL, DEFAULT_MODEL = "https://api.typesafe.ai", "jev-latest"
SYSTEM_ONE_PATH, MODELS_PATH = "/v1/systemone", "/v1/models"


class DecideError(RuntimeError):
    """A failed or unusable answer; the message never holds the key."""


def unit(x: Any) -> bool:
    return not isinstance(x, bool) and isinstance(x, (int, float)) and 0 <= x <= 1


def usable(question: dict[str, Any], answer: Any) -> bool:
    """Whether `answer` has the question's type and in-range values; a type this client does not know passes."""
    kind = question.get("type")
    if not isinstance(answer, dict) or answer.get("type") != kind:
        return False
    if kind == "choice":
        return answer.get("choice") in (question.get("criteria") or {}) and unit(answer.get("confidence"))
    if kind == "noul":
        return unit(answer.get("noul"))
    if kind == "score":
        score = answer.get("score")
        return (not isinstance(score, bool) and isinstance(score, (int, float))
                and 0 <= score <= len(question.get("criteria") or []) - 1 and unit(answer.get("confidence")))
    return True


class Decider:
    def __init__(self, settings: dict[str, Any], env: dict[str, str]):
        base = settings.get("base_url", DEFAULT_BASE_URL)
        if not isinstance(base, str) or not base.startswith(("http://", "https://")):
            raise ConfigError(".vae/.env.typed-decisions.json: `base_url` must be an http(s) URL")
        self.base = base.rstrip("/")
        self.url = self.base + "/" + str(settings.get("path", SYSTEM_ONE_PATH)).lstrip("/")
        self.model = str(settings.get("model") or DEFAULT_MODEL)
        self.threshold = float(settings.get("threshold", 0.8))
        # WHY 3 attempts: the SDK's default (2 retries on 408, 429 and 5xx); a decision that still fails falls back to a chain.
        self.timeout, self.attempts = float(settings.get("timeout_s", 10)), int(settings.get("attempts", 3))
        # WHY a required key: the SDK refuses to start without one, and an unfilled template then fails here, offline.
        settings = {"auth_env": "TYPED_DECISIONS_API_KEY", **settings}
        self.key = secret(settings, env)
        if not self.key:
            raise ConfigError(f".vae/.env.typed-decisions: `{settings['auth_env']}` is empty; the endpoint requires an API key")
        self.headers = {"Authorization": f"Bearer {self.key}"}
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.lock = threading.Lock()

    def fail(self, message: str) -> DecideError:
        """An error whose text never holds the key, even when the server echoes it back."""
        return DecideError(redact(message, [self.key]))

    def request(self, url: str, body: Any = None) -> Any:
        try:
            return request_json(url, body, self.headers, timeout=self.timeout, attempts=self.attempts)
        except Exception as e:  # noqa: BLE001 - every failure becomes one DecideError
            raise self.fail(f"{'GET' if body is None else 'POST'} {url}: {http_error(e)}") from None

    def models(self) -> list[str]:
        """The model names and aliases the endpoint serves."""
        reply = self.request(self.base + MODELS_PATH)
        models = reply.get("models") if isinstance(reply, dict) else None
        if not isinstance(models, list):
            raise self.fail(f"GET {self.base + MODELS_PATH}: no `models` list in {str(reply)[:200]}")
        return [m["name"] for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)]

    def system_one(self, state: Any, questions: dict[str, dict[str, Any]], model: str | None = None) -> dict[str, Any]:
        """The reply {model, answers, usage} to `questions` about `state` (text, object or list); every question has a
        usable answer of its type, else DecideError. The reply's usage adds to `self.usage`."""
        if not questions:
            raise DecideError("at least one question is required")
        reply = self.request(self.url, {"state": state, "model": model or self.model, "questions": questions})
        answers = reply.get("answers") if isinstance(reply, dict) else None
        if not isinstance(answers, dict):
            raise self.fail(f"POST {self.url}: no `answers` object in {str(reply)[:200]}")
        for name, question in questions.items():
            if not usable(question, answers.get(name)):
                raise self.fail(f"question {name!r} ({question.get('type')}): unusable answer {answers.get(name)!r}")
        usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
        with self.lock:
            self.usage["calls"] += 1
            for k in ("input_tokens", "output_tokens"):
                self.usage[k] += int(usage.get(k) or 0)
        return reply

    def decide(self, question: str, labels: list[str], state: Any = None) -> tuple[str, float, bool]:
        """(label, confidence, confident) for a closed question as a choice among `labels`, about `state` (default:
        the question itself); confident when the confidence reaches the configured threshold."""
        if len(labels) < 2:
            raise DecideError("a closed question needs at least two labels")
        choice = {"type": "choice", "instructions": question, "criteria": dict.fromkeys(labels)}
        answer = self.system_one(question if state is None else state, {"answer": choice})["answers"]["answer"]
        return str(answer["choice"]), float(answer["confidence"]), answer["confidence"] >= self.threshold


def from_config(repo: Path) -> Decider | None:
    """The configured client, or None when `.vae/.env.typed-decisions.json` does not exist."""
    settings, env = load_pair(repo, "typed-decisions")
    return None if settings is None else Decider(settings, env)

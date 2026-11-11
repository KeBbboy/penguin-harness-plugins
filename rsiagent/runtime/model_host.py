"""Model-driven host adapters for the RSIAgent protocol.

Each adapter is one role (Curriculum, Actor, Verifier) calling an
OpenAI-compatible chat-completions endpoint. The adapters are deliberately
generic — no benchmark, dataset or task vocabulary lives here (RFC-0003:
algorithm and data decoupled). Task content is supplied by the caller as
plain strings; the roles only see what the protocol hands them:

- the Actor sees the task and the memory snapshot (a copy),
- the Verifier sees the task and the Actor's *public* candidate only,
- the Curriculum sees the target, the verdict and the memory (a copy).

Every prompt and raw reply can be captured through ``log`` for the
experiment record; nothing is persisted by the adapters themselves.
"""
import json
import urllib.request
from types import SimpleNamespace


class LLMError(RuntimeError):
    """A role could not produce a usable reply."""


class ChatClient:
    """Minimal OpenAI-compatible client with per-role usage accounting."""

    def __init__(self, base_url, api_key, model, timeout=60, log=None):
        self.base_url = str(base_url).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.log = log
        self.calls = {}  # role -> number of completions

    def complete(self, role, system, user, max_tokens=512, temperature=0.0):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # A reasoning model can spend the whole completion budget on thinking
        # and return empty content; one bounded retry with a larger budget
        # recovers it without masking a persistent failure.
        text = ""
        last_error = None
        diag = []
        for budget in (max_tokens, max_tokens + 1024):
            request = urllib.request.Request(
                self.base_url + "/chat/completions",
                data=json.dumps({"model": self.model, "messages": messages,
                                 "max_tokens": budget,
                                 "temperature": temperature}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self.api_key})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
            except Exception as exc:  # network / HTTP / JSON
                last_error = exc
                continue
            try:
                text = str((payload["choices"][0]["message"].get("content") or "")).strip()
                finish = payload["choices"][0].get("finish_reason")
            except (KeyError, IndexError, TypeError) as exc:
                last_error = exc
                continue
            if text:
                break
            text = ""
            diag.append({"finish": finish,
                         "usage": payload.get("usage")})
        if not text:
            if last_error is not None:
                raise LLMError(role + " call failed: " + repr(last_error)
                               + " diag: " + repr(diag)) from last_error
            raise LLMError(role + " returned empty content; diag: "
                           + repr(diag))
        self.calls[role] = self.calls.get(role, 0) + 1
        if self.log is not None:
            self.log({"role": role, "system": system, "user": user, "reply": text})
        return text


# --- Role charters: method only, no task vocabulary --------------------------

ACTOR_SYSTEM = (
    "You are the Actor in the RSIAgent exploration protocol. Solve the task "
    "you are given, using the prior lessons only as hints that may be wrong, "
    "outdated or inapplicable — verify against the task itself. Reply with a "
    "single JSON object: your solution as \"answer\" (a string or number) and "
    "one short sentence of evidence as \"evidence\". No other text.")

VERIFIER_SYSTEM = (
    "You are the independent Verifier in the RSIAgent exploration protocol. "
    "You see only the task and the candidate's public answer — never the "
    "Actor's reasoning, lessons or memory. Solve the task yourself, then "
    "judge the candidate strictly against your own result and the task's "
    "requirements. Reply with exactly PASS or FAIL, nothing else.")

CURRICULUM_SYSTEM = (
    "You are the Curriculum in the RSIAgent exploration protocol. You direct "
    "practice: after a failed or fragile target attempt, propose ONE focused "
    "practice task exercising exactly the weakness the verdict and lessons "
    "reveal. Reply with the practice task alone — one line, no commentary.")


def _lessons(memory):
    if not memory:
        return "(none yet)"
    return json.dumps(memory, ensure_ascii=False)


class ModelHost:
    """Protocol host wiring the three roles to a ChatClient."""

    def __init__(self, client, actor_max_tokens=4096):
        self.client = client
        self.actor_max_tokens = actor_max_tokens

    def run_actor(self, task, memory):
        reply = self.client.complete(
            "actor", ACTOR_SYSTEM,
            "Task: {}\n\nPrior lessons (hints, verify before use): {}".format(task, _lessons(memory)),
            max_tokens=self.actor_max_tokens)
        try:
            value = json.loads(reply)
        except json.JSONDecodeError:
            value = {"answer": reply}
        if not isinstance(value, dict) or "answer" not in value:
            raise LLMError("actor reply is not an answer object: " + reply[:200])
        answer = value.get("answer")
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            raise LLMError("actor reply has an empty answer")
        return SimpleNamespace(
            public={"answer": answer,
                    "evidence": str(value.get("evidence", ""))[:400]},
            private=None)

    def verify(self, task, public):
        reply = self.client.complete(
            "verifier", VERIFIER_SYSTEM,
            "Task: {}\nCandidate answer: {}\nVerdict:".format(task, public["answer"]))
        verdict = reply.strip().split()[0].strip(".,!:;").upper() if reply.strip() else ""
        if verdict not in ("PASS", "FAIL"):
            raise LLMError("verifier returned an invalid verdict: " + reply[:200])
        return verdict

    def consolidate(self, memory, task, candidate, verdict):
        lesson = {"task": task, "answer": candidate.public["answer"],
                  "verdict": verdict}
        evidence = candidate.public.get("evidence")
        if evidence:
            lesson["evidence"] = evidence
        return list(memory) + [lesson]

    def curriculum_review(self, task, verdict, memory):
        reply = self.client.complete(
            "curriculum", CURRICULUM_SYSTEM,
            "Target task: {}\nVerifier verdict: {}\nLessons so far: {}\n\n"
            "Propose the next practice task:".format(task, verdict, _lessons(memory)))
        practice = reply.strip().splitlines()[0].strip()
        if not practice:
            raise LLMError("curriculum returned an empty practice task")
        return practice

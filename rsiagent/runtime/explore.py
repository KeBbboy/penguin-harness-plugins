"""The RSIAgent broad-then-deep exploration loop, model-driven end to end.

Implements the paper's operating sequence over the protocol core
(`rsi.py`) and the model roles (`model_host.py`):

  1. BROAD phase — the Curriculum plans a wave of diverse training tasks;
     every Actor branch runs against one shared pre-wave memory snapshot;
     the Verifier judges each branch independently; only after the whole
     wave is verified does the Actor consolidate, in deterministic order.
     An unresolved verification blocks the wave from committing.
  2. DEEP phase — the Curriculum proposes focused practice built on the
     failures and fragile lessons; after practice the unchanged target is
     retried; an exhausted practice budget is reported, never faked.
  3. FREEZE — a versioned, digest-checked frozen causal memory.
  4. EVALUATE — held-out tasks read the frozen memory only; curriculum and
     consolidation are disabled; a digest change aborts the run.

All inter-role payloads cross as plain text; nothing here knows about any
benchmark or dataset (RFC-0003 decoupling). Deviations from the paper are
reported, not hidden: single shared environment, API-level role isolation
rather than independent VMs, and no QEMU rollback.
"""
import json
import unicodedata
from rsi import Protocol
from model_host import ChatClient, ModelHost, LLMError

CURRICULUM_PLANNER_SYSTEM = (
    "You are the Curriculum in the RSIAgent exploration protocol, planning "
    "a BROAD wave. Given the training domain description and the lessons so "
    "far, propose a wave of diverse, achievable training tasks that spreads "
    "coverage wide rather than deep. Reply with a single JSON object "
    "{\"tasks\": [\"task\", ...]} with 2 to 6 short tasks. No other text.")


def _parse_tasks(reply, limit=6):
    """Parse the Curriculum's wave plan; tolerate fenced JSON."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError("curriculum wave plan is not JSON: " + reply[:200])
    tasks = value.get("tasks") if isinstance(value, dict) else None
    if (not isinstance(tasks, list) or not tasks
            or not all(isinstance(t, str) and t.strip() for t in tasks)):
        raise LLMError("curriculum wave plan has no task list")
    if len(tasks) > limit:
        tasks = tasks[:limit]
    return [t.strip() for t in tasks]


class ExperimentRecord:
    """Append-only record of what the run did; the caller persists it."""

    def __init__(self):
        self.entries = []

    def add(self, kind, **fields):
        self.entries.append({"kind": kind, **fields})

    def dump(self):
        return json.dumps(self.entries, ensure_ascii=False, indent=1)


class ExploreRun:
    """One full broad-then-deep exploration over a training domain."""

    def __init__(self, client, max_practices=2, log=None, host=None, held_out=()):
        self._held_out = {self._task_key(task) for task in held_out}
        self.client = client
        self.host = host if host is not None else ModelHost(client)
        self.record = ExperimentRecord()
        if log is not None:
            self.client.log = log
        self.max_practices = max_practices
        self.protocol = Protocol(self.host, training_validator=self._reject_held_out)

    @staticmethod
    def _task_key(task):
        if not isinstance(task, str) or not task.strip():
            raise ValueError("tasks must be nonempty strings")
        return " ".join(unicodedata.normalize("NFKC", task).casefold().split())

    def _reject_held_out(self, task):
        # Exact normalized matches only. This cannot detect paraphrases or
        # semantic overlap; callers must independently design disjoint splits.
        if self._task_key(task) in self._held_out:
            raise ValueError("held-out task cannot be used during exploration")

    # --- 1. broad ---------------------------------------------------------
    def broad(self, domain, wave_size=2):
        reply = self.client.complete(
            "curriculum", CURRICULUM_PLANNER_SYSTEM,
            "Training domain: {}\nLessons so far: {}\n\n"
            "Plan the next broad wave of training tasks:".format(
                domain, json.dumps(self.protocol.memory, ensure_ascii=False)))
        tasks = _parse_tasks(reply, limit=wave_size)
        for task in tasks:
            self._reject_held_out(task)
        self.record.add("wave_plan", tasks=tasks)
        verdicts = self.protocol.broad_wave(tasks, workers=1)
        self.record.add("wave_verdicts", tasks=tasks, verdicts=verdicts)
        return tasks, verdicts

    # --- 2. deep ----------------------------------------------------------
    def deep(self, target):
        self._reject_held_out(target)
        verdict = self.protocol.deep(target, self.max_practices)
        self.record.add("deep", target=target, verdict=verdict)
        return verdict

    def deep_unified(self, target):
        """Run the unified routing prototype using separate model role histories."""
        from unified import run_unified
        from unified_model_host import UnifiedModelHost
        self._reject_held_out(target)
        host = UnifiedModelHost(self.client, training_validator=self._reject_held_out)
        result = run_unified(host, target, self.protocol.memory,
                             max_practices=self.max_practices)
        self.protocol.memory = result.memory
        self.record.add("deep_unified", target=target, status=result.status,
                        events=list(result.events))
        return result.status

    # --- 3. freeze ----------------------------------------------------------
    def freeze(self):
        frozen = self.protocol.freeze()
        self.record.add("freeze", digest=frozen.sha256,
                        lessons=len(frozen.read()))
        return frozen

    # --- 4. evaluate ---------------------------------------------------------
    def evaluate(self, held_out, frozen):
        results = []
        for task in held_out:
            candidate, verdict = self.protocol.evaluate(task, frozen)
            results.append({"task": task, "verdict": verdict})
            self.record.add("eval", task=task, verdict=verdict)
        return results

    def report(self):
        return {
            "role_calls": dict(self.client.calls),
            "lessons": len(self.protocol.memory),
            "events": self.record.dump(),
            "deviations": [
                "single shared environment; branches run sequentially, not in independent VMs",
                "verifier mode: " + getattr(self.host, "verifier_mode", "caller-supplied"),
                "no claim of independent VM isolation or QEMU rollback",
            ],
        }


def run(domain, target, held_out, client, max_practices=2):
    """Drive one full experiment: broad wave -> deep target -> freeze -> evaluate."""
    held_out = list(held_out)
    run_state = ExploreRun(client, max_practices=max_practices, held_out=held_out)
    run_state._reject_held_out(target)
    run_state.broad(domain)
    deep_verdict = run_state.deep(target)
    frozen = run_state.freeze()
    evals = run_state.evaluate(held_out, frozen)
    report = run_state.report()
    report["target_verdict"] = deep_verdict
    report["evals"] = evals
    report["frozen_digest"] = frozen.sha256
    return report

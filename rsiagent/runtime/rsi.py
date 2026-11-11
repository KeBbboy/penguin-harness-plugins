"""RSIAgent protocol core with explicit host-supplied isolation adapters.

The host must provide isolated execution and verification. This module only
coordinates calls and checks memory integrity; it is not a security sandbox.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class FrozenMemory:
    _encoded: bytes
    sha256: str

    @classmethod
    def create(cls, value):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()
        return cls(encoded, sha256(encoded).hexdigest())

    def read(self):
        if sha256(self._encoded).hexdigest() != self.sha256:
            raise ValueError("frozen memory integrity failure")
        return json.loads(self._encoded)


class Protocol:
    """Adapter methods: run_actor, verify, consolidate, curriculum_review.

    verify receives only task and public candidate. The adapter is responsible
    for hiding private files/processes and rolling back verifier side effects.
    Actor memory is passed as a copy, never to verify.
    """
    def __init__(self, host, memory=None):
        self.host = host
        self.memory = deepcopy(memory if memory is not None else [])
        self.events = []

    def _attempt(self, task, snapshot):
        candidate = self.host.run_actor(deepcopy(task), deepcopy(snapshot))
        verdict = self.host.verify(deepcopy(task), candidate.public)
        if verdict not in ("PASS", "FAIL"):
            raise RuntimeError("verification incomplete; no memory may be committed")
        return candidate, verdict

    def _commit(self, task, candidate, verdict):
        updated = self.host.consolidate(deepcopy(self.memory), deepcopy(task),
                                        candidate, verdict)
        digest(updated)
        self.memory = deepcopy(updated)
        self.events.append(("commit", verdict))

    def broad_wave(self, tasks, workers=1):
        tasks = list(tasks)
        if not tasks:
            raise ValueError("a wave must contain at least one task")
        if workers < 1:
            raise ValueError("workers must be positive")
        snapshot = deepcopy(self.memory)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda task: self._attempt(task, snapshot), tasks))
        # A complete wave is verified before any branch can change memory.
        for task, (candidate, verdict) in zip(tasks, results):
            self._commit(task, candidate, verdict)
        return [verdict for _, verdict in results]

    def deep(self, target, max_practices):
        if max_practices < 0:
            raise ValueError("max_practices must not be negative")
        practices = 0
        while True:
            candidate, verdict = self._attempt(target, self.memory)
            self._commit(target, candidate, verdict)
            decision = self.host.curriculum_review(deepcopy(target), verdict,
                                                   deepcopy(self.memory))
            if decision is None:
                return verdict
            if practices >= max_practices:
                return "BUDGET_EXHAUSTED"
            candidate, practice_verdict = self._attempt(decision, self.memory)
            self._commit(decision, candidate, practice_verdict)
            practices += 1

    def freeze(self):
        return FrozenMemory.create(self.memory)

    def evaluate(self, task, frozen):
        before = frozen.sha256
        candidate, verdict = self._attempt(task, frozen.read())
        if sha256(frozen._encoded).hexdigest() != before:
            raise RuntimeError("frozen memory changed")
        # No curriculum or consolidation after freezing.
        return candidate, verdict

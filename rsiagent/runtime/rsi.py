"""RSIAgent protocol core with explicit host-supplied isolation adapters.

The host must provide isolated execution and verification. This module only
coordinates calls and checks memory integrity; it is not a security sandbox.
"""
import base64
import binascii
import os
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

    def save(self, path):
        """Create a new private snapshot file; never replace an existing one.

        The digest detects accidental modification. It is not an authentication
        mechanism against an attacker who can rewrite both payload and digest.
        """
        self.read()
        payload = {"version": 1, "sha256": self.sha256,
                   "data_base64": base64.b64encode(self._encoded).decode("ascii")}
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(os.fspath(path), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, sort_keys=True, separators=(",", ":"))
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())

    @classmethod
    def load(cls, path):
        """Load and verify a saved snapshot before exposing its memory."""
        with open(path, "r", encoding="utf-8") as source:
            obj = json.load(source)
        if (not isinstance(obj, dict) or obj.get("version") != 1 or
                not isinstance(obj.get("sha256"), str) or
                not isinstance(obj.get("data_base64"), str)):
            raise ValueError("invalid frozen memory snapshot")
        try:
            encoded = base64.b64decode(obj["data_base64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid frozen memory encoding") from exc
        result = cls(encoded, obj["sha256"])
        result.read()
        return result


class Protocol:
    """Adapter methods: run_actor, verify, consolidate, curriculum_review.

    verify receives only task and public candidate. The adapter is responsible
    for hiding private files/processes and rolling back verifier side effects.
    Actor memory is passed as a copy, never to verify.
    """
    def __init__(self, host, memory=None, training_validator=None):
        self.host = host
        self.training_validator = training_validator
        self.memory = deepcopy(memory if memory is not None else [])
        self.events = []
        self.last_wave_answers = []
        self.last_target_answer = None

    def _release_candidate(self, candidate):
        release = getattr(self.host, "release_candidate", None)
        if release is not None:
            release(candidate)

    def _attempt(self, task, snapshot):
        candidate = self.host.run_actor(deepcopy(task), deepcopy(snapshot))
        try:
            # The verifier inspects a copy, not the Actor's candidate.
            verdict = self.host.verify(deepcopy(task), deepcopy(candidate.public))
            if verdict not in ("PASS", "FAIL"):
                raise RuntimeError("verification incomplete; no memory may be committed")
            return candidate, verdict
        except BaseException:
            self._release_candidate(candidate)
            raise

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
        if self.training_validator is not None:
            for task in tasks:
                self.training_validator(task)
        snapshot = deepcopy(self.memory)
        results = []
        first_error = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._attempt, task, snapshot) for task in tasks]
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        try:
            if first_error is not None:
                raise first_error
            # Stage the whole wave before publishing any memory or events.
            staged = deepcopy(self.memory)
            staged_events = []
            for task, (candidate, verdict) in zip(tasks, results):
                staged = self.host.consolidate(deepcopy(staged), deepcopy(task),
                                               candidate, verdict)
                digest(staged)
                staged = deepcopy(staged)
                staged_events.append(("commit", verdict))
            answers = [deepcopy(candidate.public.get("answer"))
                       if isinstance(candidate.public, dict) else None
                       for candidate, _ in results]
            self.memory = staged
            self.events.extend(staged_events)
            self.last_wave_answers = answers
            return [verdict for _, verdict in results]
        finally:
            for candidate, _ in results:
                self._release_candidate(candidate)

    def deep(self, target, max_practices):
        if max_practices < 0:
            raise ValueError("max_practices must not be negative")
        if self.training_validator is not None:
            self.training_validator(target)
        practices = 0
        while True:
            candidate, verdict = self._attempt(target, self.memory)
            try:
                self._commit(target, candidate, verdict)
                self.last_target_answer = (deepcopy(candidate.public.get("answer"))
                                           if isinstance(candidate.public, dict) else None)
            finally:
                self._release_candidate(candidate)
            decision = self.host.curriculum_review(deepcopy(target), verdict,
                                                   deepcopy(self.memory))
            if decision is None:
                return verdict
            if practices >= max_practices:
                return "BUDGET_EXHAUSTED"
            if self.training_validator is not None:
                self.training_validator(decision)
            candidate, practice_verdict = self._attempt(decision, self.memory)
            try:
                self._commit(decision, candidate, practice_verdict)
            finally:
                self._release_candidate(candidate)
            practices += 1

    def freeze(self):
        return FrozenMemory.create(self.memory)

    def evaluate(self, task, frozen):
        before = frozen.sha256
        candidate, verdict = self._attempt(task, frozen.read())
        try:
            if sha256(frozen._encoded).hexdigest() != before:
                raise RuntimeError("frozen memory changed")
            # No curriculum or consolidation after freezing.
            return candidate, verdict
        finally:
            self._release_candidate(candidate)

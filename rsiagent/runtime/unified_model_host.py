"""Text-only chat host for the unified routing prototype.

Separate role histories are API-level separation, not an OS isolation boundary.
This adapter cannot operate benchmark services or certify factual correctness.
"""
import json
from types import SimpleNamespace
from model_host import ModelHost, LLMError, MEMORY_SYSTEM
from unified import Verification

ACTOR = ("You are the RSIAgent Actor. Solve the task with the given prior lessons. "
         "Use feedback for revision. Reply JSON with answer and evidence.")
VERIFY = ("You are the independent Verifier. Inspect the candidate only, never "
          "request Actor memory or private reasoning. Reply with exactly one JSON "
          "object, for example {\"verdict\":\"PASS\",\"report\":"
          "\"the candidate satisfies the stated requirement\"}. The verdict must "
          "be PASS, FAIL, or UNVERIFIED and report must be a nonempty string. "
          "Do not claim observations you cannot support.")
CURRICULUM = ("You are the RSIAgent Curriculum. Given the target and verifier "
              "report, choose REVISE or EVOLVE after FAIL, HANDOFF or "
              "VERIFY_MORE after PASS. For practice, choose PROJECT with a "
              "task, READY_FOR_RETRY, or STALLED. Reply JSON with decision "
              "and nonempty report; PROJECT also needs task. A PASS route "
              "audits the report and cannot override the verifier.")


class UnifiedModelHost:
    def __init__(self, client, training_validator=None):
        self.client = client
        self.model = ModelHost(client)
        self.training_validator = training_validator

    def new_curriculum(self, target):
        return []

    def new_attempt(self, target, memory):
        return SimpleNamespace(actor=[], verifier=[])

    def _complete(self, role, system, user, history, max_tokens=1024):
        return self.client.complete(role, system, user, history=history,
                                    max_tokens=max_tokens)

    def actor_work(self, attempt, target, memory, feedback):
        response = self._complete("actor", ACTOR,
            "Task: {}\nMemory: {}\nFeedback: {}".format(
                target, json.dumps(memory, ensure_ascii=False), feedback or "none"),
            attempt.actor, max_tokens=4096)
        value = ModelHost._role_object("actor", response)
        answer = value.get("answer")
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            raise LLMError("actor answer missing")
        return SimpleNamespace(public={"answer": answer, "evidence": value.get("evidence", "")},
                               private={"actor_reply": response})

    def verify(self, attempt, target, candidate, feedback):
        response = self._complete("verifier", VERIFY,
            "Task: {}\nPublic candidate: {}\nAdditional review: {}".format(
                target, json.dumps(candidate.public, ensure_ascii=False), feedback or "none"),
            attempt.verifier)
        value = ModelHost._role_object("verifier", response)
        try:
            return Verification(value.get("verdict"), value.get("report"))
        except ValueError as exc:
            raise LLMError(str(exc)) from exc

    def route(self, curriculum, target, verification):
        response = self._complete("curriculum", CURRICULUM,
            "Target: {}\nVerifier verdict: {}\nVerifier report: {}".format(
                target, verification.verdict, verification.report), curriculum)
        value = ModelHost._role_object("curriculum", response)
        return value.get("decision"), value.get("report")

    def learn(self, attempt, target, candidate, verification, memory):
        # The same Actor transcript receives the verifier report for distillation.
        response = self._complete("actor",
            MEMORY_SYSTEM,
            "Task: {}\nVerifier report: {}\nVerdict: {}\nDistill one lesson.".format(
                target, verification.report, verification.verdict), attempt.actor)
        value = ModelHost._role_object("memory", response)
        fields = ("condition", "action", "outcome", "support")
        if not all(isinstance(value.get(k), str) and value[k].strip() for k in fields):
            raise LLMError("memory lesson lacks a required field")
        lesson = {k: value[k].strip() for k in fields}
        lesson.update(verdict=verification.verdict, source_task=target)
        return list(memory) + [lesson]

    def practice_decision(self, curriculum, target, verification, memory):
        response = self._complete("curriculum", CURRICULUM,
            "Target: {}\nFailure report: {}\nPractice lessons: {}\n"
            "Choose PROJECT, READY_FOR_RETRY, or STALLED.".format(
                target, verification.report, json.dumps(memory, ensure_ascii=False)), curriculum)
        value = ModelHost._role_object("curriculum", response)
        decision = value.get("decision")
        return decision, value.get("task") if decision == "PROJECT" else value.get("report")

    def practice(self, curriculum, project, memory):
        if self.training_validator is not None:
            self.training_validator(project)
        attempt = self.new_attempt(project, memory)
        try:
            candidate = self.actor_work(attempt, project, memory, None)
            verification = self.verify(attempt, project, candidate, None)
            if verification.verdict == "UNVERIFIED":
                raise LLMError("practice verification incomplete; memory unchanged")
            return self.learn(attempt, project, candidate, verification, memory)
        finally:
            self.release(attempt)

    def release(self, attempt):
        pass

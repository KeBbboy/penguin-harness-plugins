import pathlib

p = pathlib.Path("runtime/unified_model_host.py")
t = p.read_text()

old_imp = "from model_host import LLMError, ModelHost"
assert old_imp in t, "import line not found"
t = t.replace(old_imp,
              "from environment_client import EnvironmentBudgetExceeded\n"
              "from model_host import LLMError, ModelHost", 1)

old_call = '''    def actor_work(self, attempt, target, memory, feedback):
        response = self._complete("actor", ACTOR,
            "Task: {}\\nMemory: {}\\nFeedback: {}".format(
                target, json.dumps(memory, ensure_ascii=False), feedback or "none"),
            attempt.actor, max_tokens=4096)'''
new_call = '''    def actor_work(self, attempt, target, memory, feedback):
        try:
            response = self._complete("actor", ACTOR,
                "Task: {}\\nMemory: {}\\nFeedback: {}".format(
                    target, json.dumps(memory, ensure_ascii=False), feedback or "none"),
                attempt.actor, max_tokens=4096)
        except EnvironmentBudgetExceeded:
            # The observation budget ran out before the Actor answered. Record
            # the attempt as an answerless failure; the run continues and the
            # Verifier still judges it.
            return SimpleNamespace(
                public={"answer": "",
                        "evidence": "observation budget exhausted before an answer"},
                private={"actor_reply": "", "budget_exhausted": True})'''
assert old_call in t, "actor_work not found"
t = t.replace(old_call, new_call, 1)
p.write_text(t)
print("unified host: budget stop degrades to a failed attempt")

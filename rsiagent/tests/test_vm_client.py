"""VMClient tests with a fake branch (no QEMU boot)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from model_host import LLMError
from vm_client import VMClient


class FakeBranch:
    instances = []

    def __init__(self, base_image, **kwargs):
        self.base_image = base_image
        self.commands = []
        self.closed = False
        self.overlay_bytes = 204800
        FakeBranch.instances.append(self)

    def start(self):
        return self

    def run(self, command, timeout=None):
        self.commands.append(command)
        return 0, "ran:" + command

    def close(self):
        self.closed = True


class ScriptedClient:
    """Replies in order; records the prompts it saw."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.calls = {}

    def complete(self, role, system, user, max_tokens=512, temperature=0.0,
                 history=None, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        self.prompts.append((role, user))
        if not self.replies:
            raise AssertionError("unexpected extra completion")
        return self.replies.pop(0)


class VMClientTests(unittest.TestCase):
    def setUp(self):
        FakeBranch.instances = []

    def _client(self, replies, **kwargs):
        kwargs.setdefault("branch_factory", FakeBranch)
        return VMClient(ScriptedClient(replies), "/base.qcow2", **kwargs)

    def test_command_loop_feeds_output_back(self):
        client = self._client(['{"vm_run":"uname -r"}',
                               '{"answer":"6.5.0","evidence":"uname"}'])
        history = []
        reply = client.complete("actor", "solve", "task", history=history)
        self.assertIn('"answer"', reply)
        branch = FakeBranch.instances[0]
        self.assertEqual(branch.commands, ["uname -r"])
        # The second prompt carries the command result.
        self.assertIn("ran:uname -r", client.client.prompts[1][1])
        self.assertEqual(client.client.calls["actor"], 2)

    def test_command_budget_is_bounded(self):
        client = self._client(['{"vm_run":"true"}', '{"vm_run":"true"}'],
                              max_commands=1)
        with self.assertRaises(LLMError):
            client.complete("actor", "solve", "task", history=[])

    def test_non_actor_roles_pass_through(self):
        client = self._client(['PASS'])
        self.assertEqual(client.complete("verifier", "s", "u", history=None), "PASS")
        self.assertEqual(FakeBranch.instances, [])

    def test_release_closes_the_branch_and_frees_the_disk(self):
        client = self._client(['{"answer":"ok"}'])
        history = []
        client.complete("actor", "s", "u", history=history)
        branch = FakeBranch.instances[0]
        client.release_history(history)
        self.assertTrue(branch.closed)
        self.assertEqual(client.completed_branches, [204800])

    def test_each_attempt_gets_its_own_branch(self):
        client = self._client(['{"answer":"a"}', '{"answer":"b"}'])
        client.complete("actor", "s", "u", history=[])
        client.complete("actor", "s", "u", history=[])
        self.assertEqual(len(FakeBranch.instances), 2)

    def test_bad_vm_run_payload_is_refused(self):
        client = self._client(['{"vm_run":""}'])
        with self.assertRaises(LLMError):
            client.complete("actor", "s", "u", history=[])

    def test_actor_requires_a_history_list(self):
        client = self._client([])
        with self.assertRaises(LLMError):
            client.complete("actor", "s", "u", history=None)


if __name__ == "__main__":
    unittest.main()

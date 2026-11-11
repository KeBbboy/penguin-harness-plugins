"""Cross-process artifact contract tested with a deterministic fake model."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
import cli


class FakeClient:
    def __init__(self, base_url, api_key, model):
        self.calls = {}
        self.log = None

    def complete(self, role, system, user, **kwargs):
        self.calls[role] = self.calls.get(role, 0) + 1
        if role == "curriculum" and "planning a BROAD wave" in system:
            return '{"tasks":["training task one","training task two"]}'
        if role == "curriculum" and "RSIAgent Curriculum." in system:
            return '{"decision":"HANDOFF","report":"evidence supports handoff"}'
        if role == "curriculum":
            return '{"decision":"ready"}'
        if role == "verifier" and "independent Verifier." in system:
            return '{"verdict":"PASS","report":"checked candidate"}'
        if role == "verifier":
            return "PASS"
        if "distilling experience" in system:
            return json.dumps({"condition":"training context", "action":"inspect",
                               "outcome":"completed", "support":"verifier PASS"})
        return '{"answer":"candidate","evidence":"observed"}'


class CliTests(unittest.TestCase):
    def test_train_then_fresh_evaluation_reuses_snapshot_without_learning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train = root / "train.json"
            tasks = root / "tasks.json"
            snapshot = root / "memory.json"
            train_report = root / "train-report.json"
            eval_report = root / "eval-report.json"
            train.write_text(json.dumps({"domain":"training domain", "target":"training target",
                                         "held_out_exclusions":["heldout"], "max_practices":1}))
            tasks.write_text(json.dumps({"tasks":["heldout"]}))
            common = ["--base-url","http://127.0.0.1/v1","--model","test-model"]
            with patch("cli.ChatClient", FakeClient), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["train", *common, "--input", str(train),
                                           "--snapshot", str(snapshot), "--report", str(train_report)],
                                          key_stream=io.StringIO("secret")), 0)
                expected = json.loads(train_report.read_text())["snapshot_digest"]
                self.assertEqual(cli.main(["evaluate", *common, "--input", str(tasks),
                                           "--snapshot", str(snapshot), "--report", str(eval_report),
                                           "--expected-digest", expected],
                                          key_stream=io.StringIO("secret")), 0)
            train_result = json.loads(train_report.read_text())
            eval_result = json.loads(eval_report.read_text())
            self.assertEqual(train_result["snapshot_digest"], eval_result["snapshot_digest"])
            self.assertEqual(eval_result["role_calls"], {"actor":1, "verifier":1})
            self.assertEqual(eval_result["results"][0]["verdict"], "PASS")
            self.assertNotIn("secret", train_report.read_text() + eval_report.read_text())
            self.assertEqual(os.stat(snapshot).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(eval_report).st_mode & 0o777, 0o600)

    def test_evaluation_rejects_replaced_self_consistent_snapshot(self):
        from rsi import FrozenMemory
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = root / "tasks.json"
            tasks.write_text('{"tasks":["heldout"]}')
            original = FrozenMemory.create(["trusted"])
            replacement = FrozenMemory.create(["replacement"])
            snapshot = root / "memory.json"
            replacement.save(snapshot)
            with patch("cli.ChatClient", FakeClient), self.assertRaisesRegex(
                    ValueError, "trusted training digest"):
                cli.main(["evaluate", "--base-url", "http://127.0.0.1/v1",
                          "--model", "test", "--input", str(tasks), "--snapshot", str(snapshot),
                          "--report", str(root / "report.json"), "--expected-digest",
                          original.sha256], key_stream=io.StringIO("secret"))
            self.assertFalse((root / "report.json").exists())

    def test_evaluation_requires_trusted_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "requires a trusted"):
                cli.main(["evaluate", "--base-url", "http://127.0.0.1/v1",
                          "--model", "test", "--input", str(root / "tasks.json"),
                          "--snapshot", str(root / "memory.json"),
                          "--report", str(root / "report.json")], key_stream=io.StringIO("secret"))

    def test_existing_snapshot_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "train.json"
            config.write_text('{"domain":"x","target":"y"}')
            snapshot = root / "memory.json"
            snapshot.write_text("unchanged")
            with patch("cli.ChatClient", FakeClient), self.assertRaises(FileExistsError):
                cli.main(["train","--base-url","http://127.0.0.1/v1","--model","test",
                          "--input",str(config),"--snapshot",str(snapshot),
                          "--report",str(root / "report.json")], key_stream=io.StringIO("secret"))
            self.assertEqual(snapshot.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()

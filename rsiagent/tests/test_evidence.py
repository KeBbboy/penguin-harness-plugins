"""Tests for the evidence-based verifier wiring (runtime/evidence.py)."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from evidence import container_verifier, EvidenceHost

# A ground-truth program: PASS iff the answer is the length of the task string.
LEN_PROGRAM = "print('PASS' if len(answer) == len(task) else 'FAIL')\n"

# FAIL program: PASS iff the answer numerically equals the day difference.
DATE_PROGRAM = (
    "import re\n"
    "from datetime import date\n"
    "dates = re.findall(r'(\\d{4}-\\d{2}-\\d{2})', task)\n"
    "a = date(*map(int, dates[0].split('-')))\n"
    "b = date(*map(int, dates[1].split('-')))\n"
    "expected = (b - a).days\n"
    "try:\n"
    "    ok = int(str(answer).strip()) == expected\n"
    "except ValueError:\n"
    "    ok = False\n"
    "print('PASS' if ok else 'FAIL')\n"
)


class EvidenceTests(unittest.TestCase):
    def test_len_verdicts(self):
        verify = container_verifier(LEN_PROGRAM)
        self.assertEqual(verify("abc", {"answer": "xyz"}), "PASS")
        self.assertEqual(verify("abc", {"answer": "toolong"}), "FAIL")

    def test_date_ground_truth(self):
        verify = container_verifier(DATE_PROGRAM)
        task = "days between 2026-03-15 and 2026-11-04 (inclusive of neither)"
        self.assertEqual(verify(task, {"answer": "234"}), "PASS")
        self.assertEqual(verify(task, {"answer": "233"}), "FAIL")
        self.assertEqual(verify(task, {"answer": "not a number"}), "FAIL")

    def test_unusable_verdict_rejected(self):
        verify = container_verifier("print('MAYBE')\n")
        with self.assertRaises(RuntimeError):
            verify("t", {"answer": "a"})

    def test_evidence_host_routes_verify_only_to_container(self):
        seen = {}
        model_host = type("MH", (), {
            "run_actor": staticmethod(lambda task, memory:
                seen.setdefault("actor", task) or {"public": {"answer": "234"}}),
            "verify": staticmethod(lambda task, public:
                seen.setdefault("model_verify", True) or "PASS"),
            "consolidate": staticmethod(lambda memory, task, cand, verdict: memory),
            "curriculum_review": staticmethod(lambda t, v, m: None),
        })()
        host = EvidenceHost(model_host, container_verifier(DATE_PROGRAM))
        task = "days between 2026-03-15 and 2026-11-04 (inclusive of neither)"
        # candidate 234 is correct -> container PASS, and the ModelHost's
        # model-judgement verify() must never have been called.
        self.assertEqual(host.verify(task, {"answer": "234"}), "PASS")
        self.assertNotIn("model_verify", seen)


if __name__ == "__main__":
    unittest.main()

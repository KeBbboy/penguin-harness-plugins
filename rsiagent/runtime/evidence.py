"""Evidence-based verification, wired into the runtime by default.

A ground-truth program supplied by the caller runs inside the restricted
container (docker_verifier.py): it receives the task string and the
candidate's answer as its last two argv entries and must print exactly
PASS or FAIL. The verdict is computed from the environment, not from a
model's opinion; the Actor's memory and reasoning never enter the
container. The program itself is task-family-specific and supplied by
the experiment (RFC-0003: the algorithm stays dataset-decoupled).
"""
from docker_verifier import verify_in_container

_PROBE_WRAPPER = (
    "import sys\n"
    "task, answer = sys.argv[-2:]\n"
)


def container_verifier(program_source, timeout=30):
    """Build verify(task, public) whose verdict program_source computes.

    program_source is Python source that runs with -I (isolated) inside
    the restricted container, with `task` and `answer` already bound to
    the task string and the candidate's answer; it must print exactly
    PASS or FAIL. Only the container's own Python is available.
    """
    probe = _PROBE_WRAPPER + program_source

    def verify(task, public):
        result = verify_in_container(
            "/tmp", ["python", "-I", "-c", probe, "--", str(task),
                     str(public["answer"])], timeout=timeout)
        verdict = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if verdict not in ("PASS", "FAIL"):
            raise RuntimeError(
                "container verifier returned an unusable verdict: " + repr(verdict[:100]))
        return verdict

    return verify


class EvidenceHost:
    """Model Actor and Curriculum; container-evidence Verifier.

    Wraps a ModelHost so the Protocol's verify() is computed ground truth
    instead of a model's judgement. Actor memory still reaches the Actor
    (as hints); the Verifier path never sees it.
    """

    def __init__(self, model_host, verify_fn):
        self.model_host = model_host
        self.verify_fn = verify_fn

    def run_actor(self, task, memory):
        return self.model_host.run_actor(task, memory)

    def verify(self, task, public):
        return self.verify_fn(task, public)

    def consolidate(self, memory, task, candidate, verdict):
        return self.model_host.consolidate(memory, task, candidate, verdict)

    def curriculum_review(self, task, verdict, memory):
        return self.model_host.curriculum_review(task, verdict, memory)

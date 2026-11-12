"""Give each Actor attempt its own disposable VM behind a chat client.

The model requests shell work by replying with a single JSON object
``{"vm_run": "<command>"}``; the command executes inside the branch's VM and
the output is fed back. When the model answers with anything else, that reply
is the Actor's final answer.

Every attempt (keyed by the Actor's history list) gets a fresh overlay on the
prepared base image, so attempts cannot see each other's files and the disk is
thrown away at release — the paper's per-branch isolation and rollback.
"""
import json

from model_host import LLMError, parse_leading_object, parse_repeated_json


class VMClient:
    """Actor-in-a-VM wrapper; other roles pass straight through."""

    def __init__(self, client, base_image, *, max_commands=12, branch_factory=None,
                 **branch_kwargs):
        self.client = client
        self.base_image = base_image
        self.max_commands = max_commands
        self.branch_factory = branch_factory
        self.branch_kwargs = branch_kwargs
        self.branches = {}   # id(history) -> (history, branch)
        self.calls = client.calls
        self.completed_branches = []

    # --- branch lifecycle -------------------------------------------------
    def _factory(self):
        if self.branch_factory is not None:
            return self.branch_factory(self.base_image, **self.branch_kwargs)
        from vm_host import VMBranch
        return VMBranch(self.base_image, **self.branch_kwargs)

    def _branch_for(self, history):
        key = id(history)
        entry = self.branches.get(key)
        if entry is not None and entry[0] is history:
            return entry[1]
        branch = self._factory().start()
        self.branches[key] = (history, branch)
        return branch

    def release_history(self, history):
        entry = self.branches.pop(id(history), None)
        if entry is not None and entry[0] is history:
            branch = entry[1]
            try:
                branch.close()
            except Exception:  # a released branch must never mask a real error
                pass
            self.completed_branches.append(getattr(branch, "overlay_bytes", 0))

    def close_all(self):
        for history, _ in list(self.branches.values()):
            self.release_history(history)

    # --- completion -------------------------------------------------------
    def complete(self, role, system, user, max_tokens=512, temperature=0.0,
                 history=None, **kwargs):
        if role != "actor":
            return self.client.complete(role, system, user, max_tokens=max_tokens,
                                        temperature=temperature, history=history,
                                        **kwargs)
        if history is None:
            raise LLMError("the Actor needs a history list to own a branch VM")
        branch = self._branch_for(history)
        instructions = (system + "\nYou work inside a private disposable Linux VM. "
                        "Request a shell command by replying with only JSON "
                        '{"vm_run":"<command>"}. You will receive its exit code and '
                        "output. When you are done, reply with your final JSON "
                        "answer instead of a command.")
        prompt = user
        for step in range(self.max_commands + 1):
            remaining = self.max_commands - step
            note = ("No shell commands remain; reply with your final answer now."
                    if remaining == 0 else
                    "Shell commands remaining: {}.".format(remaining))
            reply = self.client.complete(role, instructions, prompt + "\n\n" + note,
                                         max_tokens=max_tokens,
                                         temperature=temperature, history=history,
                                         **kwargs)
            request = self._command(reply)
            if request is None:
                return reply
            if remaining == 0:
                raise LLMError("the Actor kept requesting shell commands after the budget")
            code, output = branch.run(request)
            if len(output) > 2000:
                output = output[:2000] + "\n[output truncated]"
            prompt = "Command: {}\nExit code: {}\nOutput:\n{}".format(
                request, code, output)
        return reply

    @staticmethod
    def _command(reply):
        """Return the shell command when the reply is exactly one vm_run request."""
        value = parse_repeated_json(reply)
        if value is None:
            value = parse_leading_object(reply)
        if isinstance(value, dict) and set(value) == {"vm_run"}:
            command = value["vm_run"]
            if isinstance(command, str) and command.strip():
                return command
            raise LLMError("vm_run must be a nonempty string")
        return None

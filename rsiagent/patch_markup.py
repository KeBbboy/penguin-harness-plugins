import pathlib

# --- model_host: recognise native tool-call markup as an environment request
p = pathlib.Path("runtime/model_host.py")
t = p.read_text()

anchor = "def parse_leading_object(text):"
addition = '''_TOOL_MARKUP = re.compile(
    r'invoke\\s+name="(environment_get|training_judge)"[^>]*>\\s*(.*?)\\s*</', re.S)


def parse_tool_markup(text):
    """Decode a native tool-call block as an environment request.

    Some providers answer with their own tool-call syntax when no tools are
    declared (DeepSeek emits ``invoke name="environment_get">/path``). The
    request is executed through the same allowlist, never trusted as data.
    """
    if not isinstance(text, str):
        return None
    match = _TOOL_MARKUP.search(text)
    if match is None:
        return None
    name, payload = match.group(1), match.group(2).strip()
    if name == "environment_get" and payload.startswith("/") and " " not in payload:
        return {"environment_get": payload}
    return None


def parse_leading_object(text):'''
assert anchor in t
t = t.replace(anchor, addition, 1)
p.write_text(t)
print("parse_tool_markup added")

# --- environment_client: use it as one more fallback ----------------------
p = pathlib.Path("runtime/environment_client.py")
t = p.read_text()
t = t.replace(
    "from model_host import LLMError, ModelHost, parse_leading_object, "
    "parse_repeated_json",
    "from model_host import (LLMError, ModelHost, parse_leading_object,\n"
    "                        parse_repeated_json, parse_tool_markup)")
old = '''            if value is None:
                # A model may append tool-call markup after its JSON request;
                # accept the leading object when it is the only JSON value.
                value = parse_leading_object(reply)'''
new = '''            if value is None:
                # A model may append tool-call markup after its JSON request;
                # accept the leading object when it is the only JSON value.
                value = parse_leading_object(reply)
            if value is None:
                # Some providers emit native tool-call syntax instead of JSON.
                value = parse_tool_markup(reply)'''
assert old in t
t = t.replace(old, new)

# Discourage markup in the environment instructions.
old_instr = ('"Choose an actual endpoint, not just a prefix (for example /api/catalog "'
             '"when it is allowed). After receiving the observation, continue or give "'
             '"your final answer. Allowed path prefixes: " + ", ".join(self.prefixes))')
new_instr = ('"Choose an actual endpoint, not just a prefix (for example /api/catalog "'
             '"when it is allowed). After receiving the observation, continue or give "'
             '"your final answer. Reply with this JSON form only; never emit tool-call "'
             '"markup. Allowed path prefixes: " + ", ".join(self.prefixes))')
assert old_instr in t
t = t.replace(old_instr, new_instr)
p.write_text(t)
print("environment_client markup fallback wired")

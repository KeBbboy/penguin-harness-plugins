"""Create a read-only PenguinHarness Session using a local sign-in cookie.

This requests host confinement and checks the reported mode and support.
It does not mask Actor-private paths or prove the backend's enforcement.
"""
import json
from datetime import datetime, timezone
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlsplit
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _host_masks(client, server, headers):
    check = urllib.request.Request(server.rstrip("/") + "/api/admin/plugin-config",
                                   headers=headers, method="GET")
    with client.open(check, timeout=15) as response:
        config = json.load(response)
    groups = config.get("plugins", []) if isinstance(config, dict) else []
    group = next((x for x in groups if isinstance(x, dict) and x.get("name") == "sandbox"), {})
    masks = group.get("values", {}).get("maskPaths", [])
    if not isinstance(masks, list) or not all(isinstance(x, str) for x in masks):
        raise ValueError("invalid host mask configuration")
    return tuple(masks)


def _local_connection(root, auth_mode):
    root = root or os.environ.get("PENGUIN_HOME")
    if not root:
        raise ValueError("PENGUIN_HOME is required for read-only sessions")
    if auth_mode not in ("cookie", "local-api-token"):
        raise ValueError("unsupported local authentication mode")
    try:
        if auth_mode == "cookie":
            auth = json.loads((Path(root) / "cli-session.json").read_text())
            server, token = auth["server"], auth["token"]
        else:
            auth = {}
            lock = json.loads((Path(root) / "server.lock").read_text())
            port = lock["port"]
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("invalid local server port")
            # The host reserves some loopback Host headers for preview routing.
            server = "http://localhost:" + str(port)
            token = (Path(root) / "api-token").read_text().strip()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("local PenguinHarness sign-in is unavailable") from exc
    expires = auth.get("expiresAt") if auth_mode == "cookie" else None
    if expires is not None:
        try:
            deadline = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                raise ValueError("timezone missing")
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("invalid local sign-in expiry") from exc
        if deadline <= datetime.now(timezone.utc):
            raise RuntimeError("local PenguinHarness sign-in has expired")
    parsed = urlsplit(server)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("read-only session requires a loopback HTTP server origin")
    if not isinstance(token, str) or not token or any(c in token for c in "\r\n;"):
        raise ValueError("invalid local session credential")
    headers = {"Content-Type": "application/json"}
    if auth_mode == "cookie":
        headers["Cookie"] = "penguin_session=" + token
    else:
        headers["Authorization"] = "Bearer " + token
    return server, headers


def _assert_readonly_session(session, project, agent, session_id=None, require_masks=False):
    sandbox = session.get("sandbox", {}) if isinstance(session, dict) else {}
    sid = session.get("sessionId") if isinstance(session, dict) else None
    if (not isinstance(sid, str) or not sid or (session_id is not None and sid != session_id) or
            session.get("projectId") != project or session.get("agentId") != agent or
            session.get("approvalMode") != "deny-all" or not isinstance(sandbox, dict) or
            sandbox.get("mode") != "read-only" or sandbox.get("confinementSupported") is not True or
            (require_masks and (sandbox.get("maskPathsSupported") is not True or
                                sandbox.get("masksPaths") is not True))):
        raise RuntimeError("host did not confirm a supported read-only session")
    return sid


def _verify_saved_policy(root, project, agent, session_id, required_paths):
    """Check this local host's saved Session policy, without writing its DB.

    The local SQLite layout is host-version dependent; missing or incompatible
    state fails closed. This cannot prevent a privileged host process changing
    policy between checks or writing to the protected files directly.
    """
    data_root = Path(root or os.environ.get("PENGUIN_HOME", ""))
    db = (data_root / "web.db").resolve(strict=True)
    try:
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3) as connection:
            row = connection.execute(
                "SELECT project_id, agent_id, approval_mode, sandbox FROM sessions WHERE session_id = ?",
                (session_id,)).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("cannot inspect local Session sandbox policy") from exc
    if row is None or row[:3] != (project, agent, "deny-all"):
        raise RuntimeError("saved Session identity or approval mode changed")
    try:
        policy = json.loads(row[3])
        if not isinstance(policy, dict):
            raise ValueError("invalid saved sandbox policy")
        masks = policy.get("maskPaths")
        if policy.get("mode") != "read-only" or not isinstance(masks, list):
            raise ValueError("missing saved masks")
        covered = [Path(item).resolve(strict=True) for item in masks
                   if isinstance(item, str) and Path(item).is_absolute()]
        for value in required_paths:
            required = Path(value).resolve(strict=True)
            if not any(required == mask or (mask.is_dir() and mask in required.parents)
                       for mask in covered):
                raise ValueError("saved Session mask does not cover required path")
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("saved Session does not protect required paths") from exc


def verify_readonly_session(project, agent, session_id, *, root=None, opener=None,
                            auth_mode="cookie", require_masks=False, required_paths=()):
    """Fail closed if an existing role Session no longer reports its policy."""
    server, headers = _local_connection(root, auth_mode)
    url = server.rstrip("/") + "/api/sessions/" + quote(session_id, safe="")
    request = urllib.request.Request(url, headers=headers, method="GET")
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with client.open(request, timeout=15) as response:
            result = json.load(response)
    except Exception as exc:
        raise RuntimeError("unable to inspect read-only PenguinHarness session") from exc
    session = result.get("session", {}) if isinstance(result, dict) else {}
    sid = _assert_readonly_session(session, project, agent, session_id, require_masks)
    if required_paths:
        _verify_saved_policy(root, project, agent, sid, required_paths)
    return sid


def create_readonly_session(project, agent, workspace, provider, model, *,
                            root=None, opener=None, auth_mode="cookie", private_paths=()):
    server, headers = _local_connection(root, auth_mode)
    required_paths = []
    for value in private_paths:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("private paths must be absolute")
        try:
            required_paths.append(path.resolve(strict=True))
        except OSError as exc:
            raise ValueError("private path must exist") from exc
    url = server.rstrip("/") + "/api/projects/" + quote(project, safe="") + "/agents/" + quote(agent, safe="") + "/sessions"
    body = {"workspace": workspace, "provider": provider, "modelId": model,
            "approvalMode": "deny-all", "sandbox": {"mode": "read-only", "network": "open"}}
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     method="POST", headers=headers)
    try:
        client = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())
        if required_paths:
            masks = _host_masks(client, server, headers)
            covered = []
            for item in masks:
                if isinstance(item, str) and Path(item).is_absolute():
                    try:
                        covered.append(Path(item).resolve(strict=True))
                    except OSError:
                        continue
            for required in required_paths:
                if not any(required == mask or (mask.is_dir() and mask in required.parents)
                           for mask in covered):
                    raise ValueError("required private path is not masked by the host")
        with client.open(request, timeout=15) as response:
            result = json.load(response)
        if required_paths and _host_masks(client, server, headers) != masks:
            raise RuntimeError("host mask configuration changed during session creation")
    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError("unable to create read-only PenguinHarness session") from exc
    session = result.get("session", {}) if isinstance(result, dict) else {}
    return _assert_readonly_session(session, project, agent, require_masks=bool(required_paths))

"""Privacy enforcement: PROVE the local-only claim instead of promising it.

``install_guard()`` patches ``socket.socket.connect`` process-wide so any
outbound connection to a non-local address raises :class:`PrivacyViolation`
(and is logged). Loopback stays open — the local Ollama server and the local
web UI are the only network the agent ever needs. The guard is installed at
server startup unless cloud escalation is explicitly enabled with
``AGENTIC_CAD_ALLOW_CLOUD=1``.

This is a runtime tripwire for the privacy evaluation, not an OS firewall:
it catches every accidental egress path inside this Python process (model
calls, telemetry, package phone-home), which is the threat model here.
"""
from __future__ import annotations

import socket
import threading
from urllib.parse import urlparse

from agentic_cad import config


class PrivacyViolation(RuntimeError):
    """An outbound connection to a non-local address was attempted."""


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}

_state = {
    "installed": False,
    "original_connect": None,
    "allowed": set(),
    "violations": [],       # blocked destinations, for the report / health API
    "lock": threading.Lock(),
}


def _allowed_hosts() -> set[str]:
    hosts = set(_LOCAL_HOSTS)
    try:  # whatever host serves Ollama is part of the local trust boundary
        parsed = urlparse(config.OLLAMA_HOST)
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    except Exception:
        pass
    return hosts


def _host_of(address) -> str | None:
    """The host part of a connect() address, for INET/INET6 tuples."""
    if isinstance(address, tuple) and address:
        return str(address[0]).lower()
    return None  # AF_UNIX etc. — local by construction


def install_guard(extra_allowed: tuple[str, ...] = ()) -> None:
    """Idempotently block non-local outbound connections for this process."""
    with _state["lock"]:
        _state["allowed"] = _allowed_hosts() | {h.lower() for h in extra_allowed}
        if _state["installed"]:
            return
        original = socket.socket.connect

        def guarded_connect(self, address):
            host = _host_of(address)
            if host is not None and host not in _state["allowed"]:
                _state["violations"].append(f"{host}:{address[1] if len(address) > 1 else '?'}")
                raise PrivacyViolation(
                    f"blocked outbound connection to {address!r} — the agent is "
                    "local-only (set AGENTIC_CAD_ALLOW_CLOUD=1 to permit egress)")
            return original(self, address)

        socket.socket.connect = guarded_connect
        _state["original_connect"] = original
        _state["installed"] = True


def uninstall_guard() -> None:
    with _state["lock"]:
        if _state["installed"]:
            socket.socket.connect = _state["original_connect"]
            _state["installed"] = False
            _state["original_connect"] = None


def guard_active() -> bool:
    return _state["installed"]


def violations() -> list[str]:
    """Destinations the guard has blocked so far (most recent last)."""
    return list(_state["violations"])


def status() -> dict:
    return {"guard_active": guard_active(), "allow_cloud": config.ALLOW_CLOUD,
            "allowed_hosts": sorted(_state["allowed"]) if guard_active() else [],
            "blocked_attempts": len(_state["violations"])}

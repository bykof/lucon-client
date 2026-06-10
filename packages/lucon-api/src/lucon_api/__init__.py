"""REST gateway exposing the GEFASOFT LUCON 4C-20A-V over HTTP.

A single running instance owns one long-lived, supervised :class:`lucon.Lucon`
connection to one chain (ADR-0003) and surfaces every device function as typed
HTTP resources plus an SSE event stream (ADR-0004). Built as a separate package
so the core ``lucon`` client stays zero-dependency (ADR-0005).

Entry point: :func:`lucon_api.app.create_app`.
"""

from __future__ import annotations

from lucon_api._version import __version__
from lucon_api.app import create_app

__all__ = ["create_app", "__version__"]

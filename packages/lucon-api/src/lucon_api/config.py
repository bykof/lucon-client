"""Gateway configuration, sourced from ``LUCON_`` environment variables.

A single :class:`Settings` object is built at startup (``create_app``) and held
for the process lifetime; the gateway owns one connection to one chain, so the
device address lives here, not in any URL (ADR-0003).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration. ``LUCON_HOST`` is required; the rest default."""

    model_config = SettingsConfigDict(env_prefix="LUCON_", env_file=".env", extra="ignore")

    # --- device connection ---
    host: str = Field(..., description="Device (master) host/IP — required.")
    port: int = Field(50000, description="Device UDP command port.")
    timeout: float = Field(1.0, gt=0, description="Per-request device timeout (s).")
    retries: int = Field(2, ge=0, description="Device retransmits on timeout.")
    current_tenths: bool = Field(
        False, description="sub-45 mA read interpretation (CONTEXT.md open item #1)."
    )

    # --- HTTP server ---
    bind_host: str = Field("127.0.0.1", description="HTTP bind host.")
    bind_port: int = Field(8000, description="HTTP bind port.")

    # --- security / surface ---
    api_key: str | None = Field(None, description="If set, required on all /v1 routes.")
    enable_raw: bool = Field(False, description="Enable the POST /v1/raw escape hatch.")

    # --- backpressure ---
    queue_depth: int = Field(8, ge=1, description="Max in-flight+queued device ops before 503.")
    request_deadline: float = Field(
        10.0, gt=0, description="Overall per-request deadline (s) before 504."
    )

    # --- supervised reconnect ---
    reconnect_backoff_initial: float = Field(0.5, gt=0, description="Initial reconnect backoff (s).")
    reconnect_backoff_max: float = Field(30.0, gt=0, description="Reconnect backoff ceiling (s).")

    # --- events ---
    event_buffer: int = Field(256, ge=1, description="SSE replay ring-buffer size.")

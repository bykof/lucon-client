"""The guarded raw escape hatch: ``POST /v1/raw`` (opt-in via LUCON_ENABLE_RAW).

Structured fields are built through the codec, so its grammar checks still apply
(channel 0-99, <=16 values) — but every higher-level validation is bypassed.
Disabled by default; returns 404 when off so it is invisible unless enabled.
"""

from __future__ import annotations

from fastapi import APIRouter

from lucon import Lucon, codec
from lucon.codec import Response
from lucon_api.deps import GatewayDepInstance, SettingsDep
from lucon_api.errors import NotFoundError
from lucon_api.schemas import RawIn, RawOut

router = APIRouter(tags=["raw"])


@router.post("/raw", response_model=RawOut)
def raw_command(
    gateway: GatewayDepInstance, settings: SettingsDep, body: RawIn
) -> RawOut:
    """Send a single raw SET/READ command and return the decoded response."""
    if not settings.enable_raw:
        # Truly invisible when disabled: a generic 404, indistinguishable from any
        # unknown route (don't advertise the feature or its env toggle).
        raise NotFoundError("not found")

    if body.verb == "S":
        command = codec.encode_set(body.channel, body.cmd, *body.values)

        def op(lucon: Lucon) -> Response:
            return lucon.send(command)
    else:
        command = codec.encode_read(body.channel, body.cmd, *body.values)

        def op(lucon: Lucon) -> Response:
            return lucon.query(command)

    response = gateway.with_chain(op)
    return RawOut(
        kind=response.kind.value,
        echo=response.echo,
        values=list(response.values),
        message=response.message,
        raw=response.raw.decode("ascii", errors="replace"),
    )

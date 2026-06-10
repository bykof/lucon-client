# lucon-api

A REST gateway that exposes every function of the [`lucon`](../lucon) client for
the GEFASOFT LUCON 4C-20A-V LED light controller over HTTP.

One running instance owns **one** long-lived, supervised connection to **one**
chain (ADR-0003). Run one instance per controller chain.

## Run

```bash
LUCON_HOST=192.168.0.50 lucon-api          # serves on 127.0.0.1:8000
# OpenAPI docs at /docs ; health at /healthz, /readyz
```

## Configuration (environment, `LUCON_` prefix)

| Var | Default | Meaning |
|---|---|---|
| `LUCON_HOST` | *(required)* | Device (master) host/IP |
| `LUCON_PORT` | `50000` | Device UDP command port |
| `LUCON_BIND_HOST` | `127.0.0.1` | HTTP bind host |
| `LUCON_BIND_PORT` | `8000` | HTTP bind port |
| `LUCON_TIMEOUT` | `1.0` | Per-request device timeout (s) |
| `LUCON_RETRIES` | `2` | Device retransmits on timeout |
| `LUCON_CURRENT_TENTHS` | `false` | sub-45 mA read interpretation (CONTEXT.md open item #1) |
| `LUCON_API_KEY` | *(unset)* | If set, required on all `/v1` routes |
| `LUCON_ENABLE_RAW` | `false` | Enable the `POST /v1/raw` escape hatch |
| `LUCON_QUEUE_DEPTH` | `8` | Max in-flight+queued device ops before `503` |
| `LUCON_REQUEST_DEADLINE` | `10.0` | Overall per-request deadline (s) → `504` |
| `LUCON_RECONNECT_BACKOFF_MAX` | `30.0` | Supervisor reconnect backoff ceiling (s) |
| `LUCON_EVENT_BUFFER` | `256` | SSE replay ring-buffer size |

See `docs/adr/` (ADR-0003 gateway shape, ADR-0004 SSE events, ADR-0005 packaging).

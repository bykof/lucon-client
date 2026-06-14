# lucon

Python tooling for the GEFASOFT **LUCON® 4C-20A-V** LED light controller. A
zero-dependency client speaks the device's UDP/Ethernet ASCII command protocol
directly; a thin REST gateway exposes the same functionality over HTTP. Together
they cover every general and per-channel command in the manufacturer's manual.

> ⚠️ **This commands real hardware.** A LUCON controller energizes LED outputs —
> up to 3 A in continuous mode and 20 A in pulse/switch mode. Per-channel current
> and voltage limits exist to protect the lighting and the controller; the gateway
> also exposes network reconfiguration, restart, and factory-reset. Point it only
> at devices you intend to drive, and review limits before energizing an output.

## Packages

This is a monorepo with two independently installable packages:

| Package | What it is |
|---|---|
| [`lucon`](packages/lucon) | The core client — a zero-dependency, fully-typed UDP protocol library. |
| [`lucon-api`](packages/lucon-api) | A FastAPI REST gateway that exposes the client over HTTP (one instance per chain). |

## How it fits together

The protocol is modeled as a nested **Chain → Controller → Channel** tree (see
[`CONTEXT.md`](CONTEXT.md) for the full glossary):

- A **Chain** is one master Controller plus 0–23 slaves, reached as a unit
  through the master's single Ethernet interface — this is the `Lucon` object
  you connect to.
- Each **Controller** has 4 independent **Channels** (LED outputs), addressed by
  a global `1..96` number across the chain.

`lucon-api` wraps a single `Lucon` connection: one running gateway instance owns
one long-lived, supervised connection to one chain ([ADR-0003](docs/adr/0003-rest-gateway-stateful-single-chain-service.md)).

## Quickstart

### Library (`lucon`)

```python
from lucon import Lucon

with Lucon("192.168.0.50") as lucon:        # connect to the master; tree is auto-built
    print(lucon.firmware(), lucon.serial())  # general (master-only) identity reads

    ch = lucon.channel(1)                    # global channel 1..96
    ch.set_continuous(100)                   # drive 100 mA (continuous mode, max 3 A)
    print(ch.mode(), ch.current_flow())      # read back mode and live current
    ch.save()                                # promote to Permanent memory (survives reboot)
```

Every write lands in **Temporary memory** first; call `save()` to persist it.

### REST gateway (`lucon-api`)

```bash
LUCON_HOST=192.168.0.50 lucon-api            # serves on 127.0.0.1:8000 by default
```

```bash
curl http://127.0.0.1:8000/v1/chain          # chain identity (firmware, serial, …)
# Interactive OpenAPI docs at /docs ; health at /healthz, /readyz
```

See the [`lucon-api` README](packages/lucon-api/README.md) for the full
environment-variable configuration table (host, port, API key, timeouts, …).

## Development

Requires Python ≥ 3.11. Both packages install editable into one virtualenv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e "packages/lucon[dev]" -e "packages/lucon-api[dev]"
```

Type-check and test each package (both are `mypy --strict` clean):

```bash
( cd packages/lucon     && python -m mypy && python -m pytest -q )
( cd packages/lucon-api && python -m mypy && python -m pytest -q )
```

## Documentation

- [`CONTEXT.md`](CONTEXT.md) — domain glossary, the Chain/Controller/Channel
  model, and hardware-verified behaviors (confirmed against firmware 0.5.0).
- [`docs/adr/`](docs/adr) — architecture decision records.
- [`docs/`](docs) — the manufacturer datasheet and manual (Rev. 1.0).
- Licensed under the MIT [`LICENSE`](LICENSE).

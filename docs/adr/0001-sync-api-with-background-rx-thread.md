# Synchronous API backed by a background RX thread

## Status

accepted

## Context

The LUCON 4C-20A-V speaks an ASCII command protocol over **UDP**. It is not a clean request/response channel: besides solicited replies (echo + `>`), the device emits **unsolicited** datagrams on the same socket — an overtemperature `:E` during operation and a `:S RUNNING` notice after boot — and it only sends these to the **last remote station that contacted it**. So the client must (a) demultiplex solicited replies from spontaneous notifications, and (b) transmit at least once before it can receive any unsolicited message at all.

## Decision

Expose a **synchronous** public API. Internally, open a *connected* UDP socket to the master and run **one daemon RX thread** that reads continuously and demuxes: a single in-flight request (serialized by a lock) gets its reply handed back to the calling thread via an event/queue handshake; unsolicited `:S`/`:E` are routed to `on_error`/`on_event` callbacks (fired on the RX thread) with a fallback queue. Opening a `Lucon` performs a **mandatory `R00F` handshake** to register as the remote station and verify reachability. Timeout 1 s, 2 retries (safe — every command is idempotent).

## Considered options

- **Pure synchronous, single-threaded** — simplest, but cannot drain the socket while idle, so an overtemp `:E` is read as stale data on the next call.
- **Pure asyncio** — idiomatic for this I/O, but forces `async`/`await` on all callers; most machine-vision/automation consumers are synchronous.
- **Both sync + async** — most flexible, ~2× the surface to build/test/document.

## Consequences

- Callback handlers run on the RX thread and must be thread-safe; the fallback queue is the thread-safe alternative.
- Opening a connection does network I/O (the handshake) and can raise immediately — intentional.
- An async variant can be added later over the shared codec without changing this core.

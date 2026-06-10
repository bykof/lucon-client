# Unsolicited events fan out server-side over SSE

## Status

accepted

## Context

The device emits unsolicited `:E` (e.g. overtemperature — safety-relevant) and `:S` (boot `RUNNING`) datagrams (ADR-0001). `lucon` surfaces them through `on_event`/`on_error` callbacks and a single fallback queue drained by `poll_events`, which is **single-consumer**. A REST API may have several clients at once — a status dashboard and an automation loop — that all want these notices.

## Decision

The gateway registers `on_event`/`on_error` and pushes every notice into an internal pub/sub. Clients subscribe via `GET /v1/events` as **Server-Sent Events** (`text/event-stream`); a bounded in-memory ring buffer plus `Last-Event-ID` lets a reconnecting client replay recent notices (also `GET /v1/events/recent`). That same internal stream drives the supervised reconnect / tree-rebuild on `:S RUNNING`.

`:E` is carried as a generic `kind: "error"` with the raw bytes and verbatim message — **never** a presumptive `"overtemp"` label, because the transport documents that `:E` text is firmware/locale-dependent and that a solicited rejection cannot be reliably distinguished from a spontaneous fault.

## Considered options

- **Mirror `poll_events` as a long-poll endpoint**: simplest and closest to the library, but the underlying queue is single-consumer — two polling clients would steal each other's notices. Fan-out would still have to be built server-side (per-client buffers), so the long-poll surface adds latency without removing the real work.
- **WebSocket**: also supports server-side fan-out, but it is bidirectional and we never need client→server traffic on this channel; the extra framing/keepalive/reconnect complexity buys nothing over SSE.

## Consequences

- Event fan-out is owned by the gateway; clients never touch `poll_events` directly.
- SSE is unidirectional and proxy-friendly, matching "events only flow device→client."
- Event classification is deliberately coarse (`error` / `status` + raw text); richer typing would require hardware-confirmed `:E` semantics we do not have.

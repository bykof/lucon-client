# REST gateway is a stateful, single-chain, supervised service

## Status

accepted

## Context

We want a REST API exposing every `lucon` function. The instinct behind "REST" is a stateless, multi-client, possibly multi-device service — but the device and the client library make that a poor fit:

- `lucon` is **stateful**: `open()` performs a mandatory `R00F` handshake and builds the Controller/Channel tree from `R00RT`, then keeps a *connected* UDP socket and a daemon RX thread alive (ADR-0001).
- The device sends unsolicited `:E`/`:S` datagrams **only to the last remote station that contacted it**, so only one process can reliably receive a given chain's events.
- `lucon`'s event delivery (`poll_events`) is **single-consumer**.
- Opening costs network round-trips (handshake + tree build), so a per-request connect is slow and re-registers the remote station on every call.

## Decision

Model the service as a **stateful single-chain gateway**: one running instance owns exactly one long-lived `Lucon` connection to one chain, with the device address taken from config (no device id in URLs). The connection is **supervised** — opened at startup, reconnected in the background with backoff; `/readyz` reflects `Lucon.is_open`; device-touching requests return `503` while disconnected; `POST /v1/device/reconnect` forces a fresh `open()` + tree-rebuild; and the unsolicited `:S RUNNING` triggers an automatic tree rebuild. Run **one instance per chain**.

## Considered options

- **Multi-device broker** (device id in every URL, a pool of open connections): more flexible, but only one process can receive a given device's `:E`/`:S`, so the broker still funnels each device's events through a single owner — the multi-tenancy buys little while multiplying the supervised-connection and event-fan-out logic per device.
- **Stateless / per-request connect**: genuinely stateless and simplest to reason about, but pays the handshake + tree-build (seconds) on every call, re-registers the remote station each time, and **fundamentally cannot receive unsolicited events** — an overtemperature `:E` would be unobservable, which is disqualifying for a safety-relevant light controller.

## Consequences

- The service holds device state and a background thread; it is **not** horizontally scalable by running more replicas against the same chain (they would fight over the remote-station binding). Scale is one-instance-per-chain.
- "REST" here is resource-shaped HTTP over a stateful backend, not a stateless application; readiness depends on a live device connection.
- Device reboots deliberately triggered through the API (`restart` / `save_and_restart`) are expected to drop and re-establish the connection; the supervisor plus the `:S`-driven rebuild absorb this.

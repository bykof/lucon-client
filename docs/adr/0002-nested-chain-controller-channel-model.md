# Nested Chain → Controller → Channel object model

## Status

accepted

## Context

Up to 24 LUCON 4C-20A-V units chain over a cross-connector bus and are reached through the **master's single Ethernet interface**, exposing up to 96 channels. Channel numbering is positional: global channel = `offset × 4 + (1…4)`. The protocol splits addressing into general `00` commands and per-channel `01–99` commands. Crucially, **general `00` commands physically reach only the master** — a *slave's* identity (serial, firmware, PCB revision, its own offset) is **not readable over UDP**; slave presence is only inferred from `R00RT`'s list of online channels.

## Decision

Model the topology explicitly: top-level **`Lucon`** (the connection; owns all general `00` commands, the socket/RX thread, callbacks, and a global `channel(1–96)` shortcut) → **`Controller`** (one per offset; `is_master`; local `channel(1–4)`) → **`Channel`** (all per-channel ops). The tree is auto-built from `R00RT` on open. General/device-identity reads live on `Lucon` (= the master), reflecting that they aren't slave-addressable.

## Considered options

- **Flat: one endpoint → channels 1–96** — trivial single-unit ergonomics, but erases the physical-unit grouping that the offset/`00` semantics make real.
- **Single-unit only (channels 1–4)** — smallest, but punts on a headline product feature (96-channel chains) and pushes chained setups to the raw escape hatch.

## Consequences

- Single-unit users navigate one extra layer (`lucon.controller(0).channel(1)`); mitigated by the global `lucon.channel(n)` shortcut.
- `Controller` objects for slaves expose channel operations but not device-identity reads (documented), because the protocol cannot provide them.
- The positional `offset × 4 + n` mapping is encoded once and surfaced as read-only metadata on each `Channel`.

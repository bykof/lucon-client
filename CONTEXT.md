# lucon_py

A Python client library for the GEFASOFT **LUCON® 4C-20A-V** LED light controller, communicating over its UDP/Ethernet command protocol. Scope: a full protocol client covering every general and per-channel command.

## Language

The vocabulary below is taken from the manufacturer's manual (Rev. 1.0). API names should stay faithful to it.

### Device topology

**Chain**:
The assembled set of one master **Controller** plus 0–23 slaves on the cross-connector bus, reached as a unit through the master's Ethernet interface. This is the concept embodied by the top-level `Lucon` object you connect to. (A lone unit is a Chain of one.)
_Avoid_: network, composite, bus (the manual uses all three).

**Controller**:
One physical LUCON 4C-20A-V unit. Has 4 **Channels**, one **Controller offset**, and (only when it is the master) the reachable set of general parameters.
_Avoid_: device, module (the manual says "module" but we standardize on Controller).

**Channel**:
One of the 4 independent LED outputs on a Controller, addressed `01`–`99` across a chain. Holds its own mode, limits, and trigger config.

**Controller offset**:
The position of a Controller in a master/slave chain (master = 0, first slave = 1, …). Shifts the channel-number range a Controller answers to, so up to 24 Controllers / 96 Channels share one Ethernet interface.

**Remote station**:
The IP+port the Controller has learned to reply to — captured from the first UDP datagram it receives, overwritten by any later sender.

### Operating modes

**Continuous mode** (a.k.a. software mode):
Channel is switched on/off purely by command; drives the set current until told to stop. Max 3 A. (`CM`=2)
_Avoid_: software mode (use as parenthetical only).

**Pulse mode**:
Channel emits one fixed-duration current pulse per qualifying trigger edge. Max 20 A. (`CM`=4)

**Switch mode**:
Channel is on for as long as the trigger is in its active state. Max 20 A. (`CM`=3)

**None mode** (a.k.a. idle):
Output and trigger evaluation disabled; used to hold stored parameters without driving anything. (`CM`=0)

**Error mode**:
Channel has faulted (e.g. overtemperature); no operation possible until cleared. (`CM`=7)

### Parameters & limits

**Current limit**:
Per-channel ceiling protecting the *lighting*. Separate values for continuous (`L`) and pulse/switch (`LP`), in mA.

**Voltage limit**:
Per-channel ceiling protecting the *Controller* from overheating (`V`), in mV.

**Trigger input**:
Per-channel hardware input (camera → Controller) that fires Pulse/Switch mode. Polarity configurable (rising/falling/both).

**Trigger output**:
Per-channel hardware output (Controller → camera) emitted on a configurable source/edge/delay/length.

### Protocol & persistence

**Command**:
One request line: `('S'|'R') + <2-digit channel> + <cmd> + ('|'<value>)* + <delimiter>`. `S`=SET, `R`=READ. Channel `00` = **general** (device-wide); `01`–`99` = **channel-specific**.

**Temporary memory**:
Where every write lands first; lost on restart.

**Permanent memory**:
Non-volatile store; written only by an explicit save command (`S00S`/`S01S`, scopes 0/1/2). General and channel parameters persist in separate permanent stores.

## Relationships

- A **Controller** has exactly 4 **Channels** and one **Controller offset**.
- A **Chain** has one master **Controller** (offset 0) and up to 23 slaves connected over the cross-connector bus; the whole Chain is reached through the master's single Ethernet interface and shares one **Remote station** binding.
- **Channel numbering is positional:** global channel number = `offset × 4 + (1…4)`. Offset 0 → channels 1–4, offset 1 → 5–8, …, offset 23 → 93–96.
- The object model is **nested**: Chain → Controller (by offset) → Channel. A Controller exposes channels by *local* index 1–4; the Chain offers a *global* 1–96 shortcut.
- **General (`00`) commands physically address the master only.** Read-backs of device identity (firmware, serial, MAC, PCB revisions, supply voltage, offset) are therefore available **only for the master** over UDP — there is no documented way to read a *slave's* serial/firmware via UDP. Save/reset/restart `00` commands have a scope value (0/1/2) that can fan out to all slaves.
- Slave presence is discovered indirectly: `R00RT` lists online channel numbers, from which the set of live Controllers/offsets is inferred. The master must boot last (or be restarted) to detect slaves.
- A **Channel** is in exactly one **Operating mode** at a time.
- A **Channel**'s switch/pulse drive current may not exceed its pulse/switch **Current limit** (`SC ≤ LP`); the device rejects a violating write ("Switch current higher then current limit.").
- Every write targets **Temporary memory**; promoting it to **Permanent memory** is a separate, explicit step.
- A **Command** addresses either general params (`00`, = master) or one **Channel** (`01`–`99`).

## Example dialogue

> **Dev:** "When I set the current with `S01MC|100`, is that saved if the Controller reboots?"
> **Domain expert:** "No — that only touches Temporary memory. You have to send `S01S` (or `S00S` for general params) to promote it to Permanent memory."
> **Dev:** "And if I never save, the channel reverts to its last permanent config on power-up?"
> **Domain expert:** "Right. Stand-alone operation relies entirely on what's in Permanent memory."

## Flagged ambiguities

- "module" / "device" in the manual both mean **Controller** — standardized on Controller.
- "software mode" is an alias for **Continuous mode** — standardized on Continuous mode.
- Pulse "delay" appears in two units: `MD`/`Y` use **ms**, `MDU`/`PDU` use **µs** — the API standardizes on **µs** internally (`MDU`/`PDU`/`D`) and never exposes a bare "delay".
- **Resolved (confirmed on fw 0.5.0):** sub-45 mA currents. A **driven** current (e.g. switch current `SC`) reads back as **decimal mA** — `S01SC|10.9` → `R01SC` returns `"10.9"` (and a 1 mA value reads as `"1.0"`). So `current_tenths=False` (the default) is correct; the integer-tenths `"354"` form does **not** occur on this firmware. By contrast, the **limit** fields `L`/`LP` are **whole mA**: `S01L|10.9` reads back `"10"` (the fraction is *truncated*, not rounded), so limits carry no 0.1 mA resolution.
- **Resolved (confirmed on fw 0.5.0):** the `OTS` (output trigger source) value is `0` = INPUT and `1` = LIGHTING in **both** directions; writing `2` is **rejected** ("out of range"). The manual's SET table (§7.4.2.3) showing `2` is wrong for this firmware; the READ table's `1` is correct. `OutputTriggerSource` now sends `1` for LIGHTING and tolerates a stray `2` on READ defensively.

> Note: behaviours above were confirmed against a unit reporting firmware **0.5.0** (the manual documents Rev 1.0). Re-verify if targeting materially different firmware.

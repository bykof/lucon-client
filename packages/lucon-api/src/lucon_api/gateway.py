"""The supervised single-chain connection (ADR-0003).

:class:`Gateway` owns one long-lived :class:`lucon.Lucon`. A daemon supervisor
thread keeps it connected (reconnect with backoff), and the device's unsolicited
``:S RUNNING`` triggers a reconnect/tree-rebuild. Every device-touching call goes
through :meth:`execute`, which enforces readiness, bounded-queue backpressure,
and a per-request deadline against ``lucon``'s single serialized transport.

The device's identity (serial/firmware/MAC/offset) is cached on connect: it is
stable, master-only, and the serial backs the destructive-op confirmation guard.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

from lucon import Lucon, LuconConnectionError, LuconError, LuconTimeoutError
from lucon.codec import Response, ResponseKind

from lucon_api.config import Settings
from lucon_api.errors import BusyError, DeadlineError, DeviceUnavailableError, NotFoundError
from lucon_api.events import EventHub

_LOG = logging.getLogger("lucon_api.gateway")

T = TypeVar("T")


class Gateway:
    """Owns and supervises the one connection; serializes and bounds device I/O."""

    def __init__(self, settings: Settings, hub: EventHub) -> None:
        self._settings = settings
        self._hub = hub
        self._lucon = Lucon(
            settings.host,
            settings.port,
            timeout=settings.timeout,
            retries=settings.retries,
            current_tenths=settings.current_tenths,
            on_error=self._on_device_error,
            on_event=self._on_device_event,
        )

        # Serializes device ops against each other and against reconnect, and is
        # the point where the per-request deadline is enforced (acquire timeout).
        self._device_lock = threading.Lock()
        # Bounds how many threads may be in execute() at once (backpressure).
        self._counter_lock = threading.Lock()
        self._inflight = 0

        # Connection state.
        self._state_lock = threading.Lock()
        self._ready = False
        self._connect_count = 0
        self._serial: str | None = None
        self._firmware: str | None = None
        self._mac: str | None = None
        self._offset: int | None = None

        # Supervisor coordination.
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._rebuild_requested = False
        self._supervisor: threading.Thread | None = None

    # --- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Launch the supervisor thread (it performs the first connect)."""
        self._stop.clear()
        self._wake.clear()  # discard any leftover signal from a prior stop()
        self._supervisor = threading.Thread(
            target=self._supervise, name="lucon-api-supervisor", daemon=True
        )
        self._supervisor.start()

    def stop(self) -> None:
        """Stop the supervisor and close the connection. Idempotent."""
        self._stop.set()
        self._wake.set()
        thread = self._supervisor
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        try:
            self._lucon.close()
        except LuconError:
            _LOG.debug("error closing lucon during stop", exc_info=True)

    # --- state accessors ------------------------------------------------

    @property
    def ready(self) -> bool:
        """True when a live, handshaken connection with cached identity exists."""
        with self._state_lock:
            return self._ready

    @property
    def connect_count(self) -> int:
        """Number of successful (re)connects — observable for tests/diagnostics."""
        with self._state_lock:
            return self._connect_count

    @property
    def serial(self) -> str | None:
        """Cached master serial (the destructive-op confirmation token)."""
        with self._state_lock:
            return self._serial

    @property
    def firmware(self) -> str | None:
        """Cached master firmware string."""
        with self._state_lock:
            return self._firmware

    @property
    def mac(self) -> str | None:
        """Cached master MAC address."""
        with self._state_lock:
            return self._mac

    @property
    def controller_offset(self) -> int | None:
        """Cached master controller offset."""
        with self._state_lock:
            return self._offset

    @property
    def host(self) -> str:
        return self._settings.host

    @property
    def port(self) -> int:
        return self._settings.port

    @property
    def current_tenths(self) -> bool:
        """Live sub-45 mA read interpretation (CONTEXT.md open item #1)."""
        return self._lucon.current_tenths

    def set_current_tenths(self, value: bool) -> None:
        """Toggle the sub-45 mA read interpretation at runtime (no device I/O)."""
        self._lucon.current_tenths = value

    # --- device callbacks (run on lucon's RX thread) --------------------

    def _on_device_event(self, response: Response) -> None:
        self._publish(response)
        message = (response.message or "").upper()
        if response.kind is ResponseKind.STATUS and "RUNNING" in message:
            # The device just (re)booted; rebuild the tree to rediscover slaves
            # and re-register as its remote station. Defer the actual reconnect
            # to the supervisor — we are on the RX thread that close() joins.
            with self._state_lock:
                self._rebuild_requested = True
            self._wake.set()

    def _on_device_error(self, response: Response) -> None:
        self._publish(response)

    def _publish(self, response: Response) -> None:
        kind = "status" if response.kind is ResponseKind.STATUS else "error"
        raw = response.raw.decode("ascii", errors="replace") if response.raw else None
        try:
            self._hub.publish(kind=kind, message=response.message, raw=raw)
        except Exception:  # pragma: no cover - defensive; never kill the RX thread
            _LOG.exception("event publish failed")

    # --- execution ------------------------------------------------------

    def execute(self, fn: Callable[[Lucon], T]) -> T:
        """Run ``fn(lucon)`` under readiness, backpressure, and a deadline.

        Raises :class:`DeviceUnavailableError` (503) when disconnected,
        :class:`BusyError` (503) when the queue is full, and
        :class:`DeadlineError` (504) when the wait for the serialized transport
        exceeds ``request_deadline``. A :class:`LuconConnectionError` from the op
        marks the connection unhealthy and triggers a reconnect.
        """
        if not self.ready:
            raise DeviceUnavailableError("device is not connected")

        with self._counter_lock:
            if self._inflight >= self._settings.queue_depth:
                raise BusyError(
                    "device command queue is full", headers={"Retry-After": "1"}
                )
            self._inflight += 1
        try:
            acquired = self._device_lock.acquire(timeout=self._settings.request_deadline)
            if not acquired:
                raise DeadlineError("timed out waiting for the device transport")
            try:
                if not self.ready:
                    raise DeviceUnavailableError("device is not connected")
                return fn(self._lucon)
            except (LuconConnectionError, LuconTimeoutError):
                # A timeout means the device went silent (the codebase treats it
                # as whole-device unreachability, see _reads._PROPAGATE). Mark
                # unhealthy so the supervisor reconnects — otherwise the gateway
                # stays ready=True and 504s every request with no auto-recovery.
                self._mark_unhealthy()
                raise
            finally:
                self._device_lock.release()
        finally:
            with self._counter_lock:
                self._inflight -= 1

    def with_channel(self, channel_num: int, fn: Callable[..., T]) -> T:
        """Resolve a global channel (404 if not online) and run ``fn(channel)``."""

        def op(lucon: Lucon) -> T:
            try:
                channel = lucon.channel(channel_num)
            except LuconError as exc:
                raise NotFoundError(str(exc))
            return fn(channel)

        return self.execute(op)

    def with_controller(self, offset: int, fn: Callable[..., T]) -> T:
        """Resolve a controller by offset (404 if absent) and run ``fn(controller)``."""

        def op(lucon: Lucon) -> T:
            try:
                controller = lucon.controller(offset)
            except LuconError as exc:
                raise NotFoundError(str(exc))
            return fn(controller)

        return self.execute(op)

    def with_chain(self, fn: Callable[[Lucon], T]) -> T:
        """Run a general (master/``00``) operation against the live connection."""
        return self.execute(fn)

    # --- topology (in-memory; built on connect, no device I/O) ----------

    def _read_tree(self, fn: Callable[[Lucon], T]) -> T:
        """Read the in-memory tree under ``_device_lock``.

        The lock excludes a concurrent reconnect (``_connect`` rebuilds the tree
        while holding it), so reads never observe a half-rebuilt ``_controllers``
        dict — otherwise ``Lucon.controllers``' non-atomic offsets-then-index read
        could raise a spurious ``KeyError`` mid-reconnect.
        """
        with self._device_lock:
            if not self._ready:
                raise DeviceUnavailableError("device is not connected")
            return fn(self._lucon)

    def topology(self) -> list[dict[str, Any]]:
        """All controllers as ``{offset, is_master, channels:[global_num,...]}``."""
        return self._read_tree(lambda lucon: [self._controller_dict(c) for c in lucon.controllers])

    def controller_view(self, offset: int) -> dict[str, Any]:
        """One controller's topology (404 if no such offset)."""

        def fn(lucon: Lucon) -> dict[str, Any]:
            try:
                controller = lucon.controller(offset)
            except LuconError as exc:
                raise NotFoundError(str(exc))
            return self._controller_dict(controller)

        return self._read_tree(fn)

    def online_channels(self) -> list[int]:
        """Global channel numbers currently online, sorted."""
        return self._read_tree(
            lambda lucon: sorted(ch.channel_num for c in lucon.controllers for ch in c.channels)
        )

    def offsets(self) -> list[int]:
        """Discovered controller offsets, sorted."""
        return self._read_tree(lambda lucon: lucon.offsets)

    @staticmethod
    def _controller_dict(controller: object) -> dict[str, Any]:
        # ``controller`` is a lucon.Controller; typed loosely to avoid importing
        # the concrete class just for an isinstance-free projection.
        return {
            "offset": controller.offset,  # type: ignore[attr-defined]
            "is_master": controller.is_master,  # type: ignore[attr-defined]
            "channels": [ch.channel_num for ch in controller.channels],  # type: ignore[attr-defined]
        }

    # --- reconnection ---------------------------------------------------

    def request_reconnect(self) -> None:
        """Force a reconnect+rebuild now (e.g. POST /v1/device/reconnect)."""
        with self._state_lock:
            self._rebuild_requested = True
            self._ready = False
        self._wake.set()

    def note_disruption(self) -> None:
        """Signal that an issued command will drop the link (restart/save+restart)."""
        self._mark_unhealthy()

    def _mark_unhealthy(self) -> None:
        with self._state_lock:
            self._ready = False
        self._wake.set()

    def _supervise(self) -> None:
        backoff = self._settings.reconnect_backoff_initial
        while not self._stop.is_set():
            with self._state_lock:
                need_connect = (not self._ready) or self._rebuild_requested
            if need_connect:
                if self._connect():
                    backoff = self._settings.reconnect_backoff_initial
                else:
                    self._wake.wait(timeout=backoff)
                    self._wake.clear()
                    backoff = min(backoff * 2, self._settings.reconnect_backoff_max)
                    continue
            # Connected and stable: sleep until woken (stop / disruption / :S).
            self._wake.wait()
            self._wake.clear()

    def _connect(self) -> bool:
        """(Re)open the connection and refresh cached identity. Returns success."""
        with self._device_lock:
            with self._state_lock:
                self._ready = False
                self._rebuild_requested = False
            try:
                if self._lucon.is_open:
                    self._lucon.close()
                self._lucon.open()
                serial = self._lucon.serial()
                firmware = self._lucon.firmware()
                mac = self._lucon.mac()
                offset = self._lucon.controller_offset()
            except LuconError as exc:
                # exc_info keeps a LuconCommandError's command/raw bytes in the
                # log (str() carries only the terse device message).
                _LOG.warning(
                    "connect to %s:%s failed: %s", self.host, self.port, exc, exc_info=True
                )
                try:
                    self._lucon.close()
                except LuconError:
                    pass
                return False
            with self._state_lock:
                self._serial = serial
                self._firmware = firmware
                self._mac = mac
                self._offset = offset
                self._ready = True
                self._connect_count += 1
        _LOG.info("connected to %s:%s (serial=%s)", self.host, self.port, serial)
        return True

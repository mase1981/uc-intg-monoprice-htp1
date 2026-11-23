"""
Monoprice HTP-1 device implementation for Unfolded Circle integration.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio
import copy
import json
import logging
import math
from enum import Enum
import re
import time
from typing import Any

import aiohttp
import websockets

from ucapi_framework import WebSocketDevice, DeviceEvents
from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.displayvalues import sound_mode_display_values, sound_mode_native_values

_LOG = logging.getLogger(__name__)

FILTER_TYPE_MAP = {"PeakingEQ": 0, "LowShelf": 1, "HighShelf": 2}
BEQ_SLOT_START = 0
BEQ_SLOT_END = 15
KEEPALIVE_INTERVAL = 20
KEEPALIVE_TIMEOUT = 10
PROBE_TIMEOUT = 5.0
STATE_REFRESH_TIMEOUT = 5.0
VOLUME_STATE_MAX_AGE = 0.5
VOLUME_STATE_PATHS = ("/volume", "/cal/vpl", "/cal/vph", "/cal/zeroPoint")
WRITE_CONVERGENCE_TIMEOUT = 2.0
DIRAC_SWITCH_TIMEOUT = 15.0


class DiracLifecycleState(Enum):
    """Integration-owned lifecycle for one receiver Dirac slot request."""

    IDLE = "idle"
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    APPLYING = "applying"
    COMPLETE = "complete"
    FAILED = "failed"


_DIRAC_LIFECYCLE_TRANSITIONS = {
    DiracLifecycleState.IDLE: {DiracLifecycleState.REQUESTED},
    DiracLifecycleState.REQUESTED: {
        DiracLifecycleState.ACCEPTED,
        DiracLifecycleState.FAILED,
    },
    DiracLifecycleState.ACCEPTED: {
        DiracLifecycleState.APPLYING,
        DiracLifecycleState.COMPLETE,
        DiracLifecycleState.FAILED,
    },
    DiracLifecycleState.APPLYING: {
        DiracLifecycleState.COMPLETE,
        DiracLifecycleState.FAILED,
    },
    DiracLifecycleState.COMPLETE: {DiracLifecycleState.REQUESTED},
    DiracLifecycleState.FAILED: {DiracLifecycleState.REQUESTED},
}
SEAT_SHAKER_TRIM_MIN = -24
SEAT_SHAKER_TRIM_MAX = 6


def _as_dict(value: Any) -> dict:
    """Return a nested state object only when it has the expected shape."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    """Return a nested state array only when it has the expected shape."""
    return value if isinstance(value, list) else []


def _is_finite_number(value: Any) -> bool:
    """Check a JSON numeric value without leaking conversion overflow."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _number(value: Any, default: float = 0) -> float:
    """Read a finite numeric state value without accepting booleans or strings."""
    return value if _is_finite_number(value) else default


async def probe_htp1(
    host: str, timeout: float = PROBE_TIMEOUT, *, port: int = 80
) -> str | None:
    """Open a WebSocket to the HTP-1, request its state and close again.

    :return: None on success, otherwise a short human readable error.
    """
    url = f"ws://{host}:{port}/ws/controller"

    async def _probe() -> None:
        async with websockets.connect(url, ping_interval=None, close_timeout=2) as ws:
            await ws.send("getmso")
            while True:
                message = await ws.recv()
                if isinstance(message, str) and message.startswith("mso "):
                    return

    try:
        await asyncio.wait_for(_probe(), timeout=timeout)
        return None
    except asyncio.TimeoutError:
        return f"No response from {host}:{port} within {int(timeout)} seconds"
    except Exception as err:  # pylint: disable=broad-exception-caught
        return f"Could not connect to {host}:{port}: {err}"


class HTP1Device(WebSocketDevice):
    """Monoprice HTP-1 implementation using WebSocketDevice."""

    def __init__(self, device_config: HTP1Config, **kwargs):
        # Keepalive is done by the websockets library (see create_websocket), which
        # closes a dead connection so recv() returns. The framework ping loop would
        # only call a no-op send_ping, so it is disabled.
        super().__init__(device_config, reconnect=True, ping_interval=0, **kwargs)
        self._device_config = device_config
        self._state: dict[str, Any] | None = None
        self._volume_state_timestamps: dict[str, float] = {}
        self._volume_state_max_age = VOLUME_STATE_MAX_AGE
        self._clock = time.monotonic
        self._state_ready = asyncio.Event()
        self._state_update_event = asyncio.Event()
        self._state_update_paths: set[str] = set()
        self._transaction_error: str | None = None
        self._write_convergence_timeout = WRITE_CONVERGENCE_TIMEOUT
        self._dirac_switch_timeout = DIRAC_SWITCH_TIMEOUT
        self._dirac_request_id: str | None = None
        self._dirac_request_slot: int | None = None
        self._dirac_update_event = asyncio.Event()
        self._dirac_error: str | None = None
        self._dirac_lifecycle_state = DiracLifecycleState.IDLE
        self._dirac_target_slot_name: str | None = None
        self._dirac_failure_reason: str | None = None
        self._dirac_acceptance_timeout = 2.0
        self._dirac_operation_lock = asyncio.Lock()
        self._dirac_task: asyncio.Task | None = None
        self._dirac_volume_capture_active = False
        self._dirac_volume_snapshot: dict[str, float] | None = None
        self._dirac_volume_delta = 0
        self._dirac_volume_absolute_db: float | None = None
        self._ws: Any | None = None
        self._send_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()
        self._connection_generation = 0

        self.events.on(DeviceEvents.CONNECTED, self._on_connected)
        self.events.on(DeviceEvents.DISCONNECTED, self._on_disconnected)

        self._sensor_data: dict[str, str] = {}
        self.current_source: str = ""
        self.source_list: list[str] = []
        self.slot_names: list[str] = []
        self._slot_index: dict[str, int] = {}
        self.dirac_slot_name: str = ""
        self.sound_mode_display: str = ""
        self.surround_mode: str = ""
        self.volume_db: int = -30
        self.muted: bool = False
        self.power: bool = False
        self.vpl: int = -80
        self.vph: int = 12
        self.zp: int = 0
        self.ss_mute = "off"
        self.ss_preset = 0
        self.ss_trim = 0
        self.beq_active: str = ""

    async def _on_connected(self, identifier: str) -> None:
        _LOG.info("[%s] WebSocket connected", self.log_id)
        self._connection_generation += 1
        self._state = None
        self._volume_state_timestamps.clear()
        self._state_ready.clear()
        await asyncio.sleep(0.1)
        await self.send_message("getmso")
        try:
            await asyncio.wait_for(self._state_ready.wait(), timeout=5.0)
            _LOG.info("[%s] Initial state received", self.log_id)
            self._parse_state()
            self.push_update()
        except asyncio.TimeoutError:
            _LOG.warning("[%s] Timeout waiting for initial state", self.log_id)


    async def _on_disconnected(self, identifier: str) -> None:
        _LOG.info("[%s] WebSocket disconnected", self.log_id)
        self._connection_generation += 1
        self._state = None
        self._volume_state_timestamps.clear()
        self._state_ready.set()
        self._state_update_event.set()
        self._dirac_update_event.set()
        self._sensor_data = {}
        self.push_update()

    @property
    def identifier(self) -> str:
        return self._device_config.identifier

    @property
    def name(self) -> str:
        return self._device_config.name

    @property
    def address(self) -> str:
        return self._device_config.host

    @property
    def log_id(self) -> str:
        return f"{self.name} ({self.address})"

    @property
    def websocket_url(self) -> str:
        return f"ws://{self._device_config.host}:{self._device_config.port}/ws/controller"

    def get_sensor_value(self, key: str) -> str:
        return self._sensor_data.get(key, "")

    async def create_websocket(self) -> Any:
        _LOG.info("[%s] Creating WebSocket connection to %s", self.log_id, self.websocket_url)
        logging.getLogger("websockets").setLevel(logging.INFO)
        self._ws = await websockets.connect(
            self.websocket_url,
            ping_interval=KEEPALIVE_INTERVAL,
            ping_timeout=KEEPALIVE_TIMEOUT,
            close_timeout=5,
        )
        return self._ws

    async def close_websocket(self) -> None:
        if self._ws:
            await self._ws.close()

    async def receive_message(self) -> str | None:
        if not self._ws:
            return None
        while True:
            try:
                message = await self._ws.recv()
            except websockets.ConnectionClosed:
                _LOG.info("[%s] WebSocket connection closed", self.log_id)
                self._on_connection_lost()
                return None
            except Exception as err:
                _LOG.error("[%s] Error receiving message: %s", self.log_id, err)
                self._on_connection_lost()
                return None
            if isinstance(message, str):
                return message

    def _on_connection_lost(self) -> None:
        """Mark the device offline right away so entities show UNAVAILABLE."""
        self._is_connected = False
        self._connection_generation += 1
        self._state = None
        self._volume_state_timestamps.clear()
        # Wake in-flight state and Dirac lifecycle waiters on disconnect.
        self._state_ready.set()
        self._dirac_update_event.set()
        self._sensor_data = {}
        self.push_update()

    async def handle_message(self, message: str) -> None:
        if " " not in message:
            return

        cmd, payload = message.split(" ", 1)

        # The receiver uses plain-text error frames for rejected commands.
        # Handle these before JSON decoding so the actual reason is retained.
        if cmd == "error":
            error = payload.strip() or "HTP-1 rejected the command"
            if self._dirac_lifecycle_state is DiracLifecycleState.REQUESTED:
                self._dirac_error = error
                self._dirac_update_event.set()
            else:
                self._transaction_error = error
                self._state_update_event.set()
            _LOG.warning("[%s] HTP-1 command error: %s", self.log_id, error)
            return

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as err:
            _LOG.error("[%s] Failed to decode JSON payload: %s", self.log_id, err)
            if cmd in {"mso", "msoupdate"}:
                self._invalidate_state("malformed JSON payload")
                await self.send_message("getmso")
            return

        if cmd == "mso":
            if not isinstance(data, dict):
                _LOG.error("[%s] Ignoring invalid full-state payload (%s)", self.log_id, type(data).__name__)
                self._invalidate_state("invalid full-state payload")
                await self.send_message("getmso")
                return
            self._state = data
            self._record_volume_state_observation()
            self._state_ready.set()
            self._dirac_update_event.set()
            _LOG.debug("[%s] Received full state", self.log_id)
            self._parse_state()
            self.push_update()

        elif cmd == "msoupdate":
            if self._state is None:
                # getmso is sent once on connect; the full state replaces these anyway
                _LOG.debug("[%s] Ignoring msoupdate before initial state", self.log_id)
                return
            if not isinstance(data, list):
                data = [data]

            # Apply the whole update to a copy so malformed batches cannot
            # leave a partially-mutated live receiver snapshot.
            candidate_state = copy.deepcopy(self._state)
            try:
                for piece in data:
                    if not isinstance(piece, dict):
                        raise ValueError("patch entry must be an object")
                    op = piece.get("op")
                    path_str = piece.get("path", "")
                    if not isinstance(op, str) or not isinstance(path_str, str):
                        raise ValueError("patch operation and path must be strings")
                    apply_json_patch(candidate_state, op, path_str, piece.get("value"))
            except (KeyError, IndexError, TypeError, ValueError) as err:
                _LOG.error("[%s] Rejecting incomplete msoupdate; requesting fresh state: %s", self.log_id, err)
                self._invalidate_state("malformed msoupdate patch batch")
                await self.send_message("getmso")
                return

            # Preserve the receiver-generated request ID even if a later
            # update in the same receive burst restores the previous switch
            # snapshot (as happens for a standby refusal).
            for piece in data:
                if not isinstance(piece, dict) or piece.get("path") != "/cal/diracSwitch":
                    continue
                switch = piece.get("value")
                if (
                    isinstance(switch, dict)
                    and switch.get("state") == "requested"
                    and isinstance(switch.get("id"), str)
                    and isinstance(switch.get("slot"), int)
                    and not isinstance(switch.get("slot"), bool)
                ):
                    self._dirac_request_id = switch["id"]
                    self._dirac_request_slot = switch["slot"]

            self._state = candidate_state
            self._record_volume_state_observation(
                piece["path"]
                for piece in data
                if isinstance(piece, dict) and isinstance(piece.get("path"), str)
            )
            self._state_update_paths.update(
                piece["path"]
                for piece in data
                if isinstance(piece, dict) and isinstance(piece.get("path"), str)
            )
            self._state_update_event.set()
            self._dirac_update_event.set()
            self._parse_state()
            self.push_update()

    def _invalidate_state(self, reason: str) -> None:
        """Discard an untrustworthy snapshot until a new full mso arrives."""
        if self._state is not None:
            _LOG.warning("[%s] Invalidating cached HTP-1 state: %s", self.log_id, reason)
        self._state = None
        self._volume_state_timestamps.clear()
        self._state_ready.clear()
        # Wake waiters so they can observe that the current snapshot is no
        # longer usable instead of continuing with stale cached values.
        self._state_update_event.set()
        self._dirac_update_event.set()

    def _record_volume_state_observation(self, paths=None) -> None:
        """Record monotonic observation times for volume dependencies only."""
        now = self._clock()
        if paths is None:
            for path in VOLUME_STATE_PATHS:
                self._volume_state_timestamps[path] = now
            return

        calibration_paths = {"/cal/vpl", "/cal/vph", "/cal/zeroPoint"}
        for path in paths:
            if path == "/volume":
                self._volume_state_timestamps["/volume"] = now
            elif path == "/cal":
                for dependency in calibration_paths:
                    self._volume_state_timestamps[dependency] = now
            elif path in calibration_paths:
                self._volume_state_timestamps[path] = now

    def _volume_state_is_fresh(self) -> bool:
        """Require every relative-volume dependency to have a recent observation."""
        if any(path not in self._volume_state_timestamps for path in VOLUME_STATE_PATHS):
            return False
        now = self._clock()
        return all(
            0 <= now - self._volume_state_timestamps[path] < self._volume_state_max_age
            for path in VOLUME_STATE_PATHS
        )

    def _volume_state_is_valid(self) -> bool:
        """Return whether the full snapshot contains safe relative-volume inputs."""
        state = self._state
        if not isinstance(state, dict):
            return False
        volume = state.get("volume")
        cal = state.get("cal")
        if (
            isinstance(volume, bool)
            or not isinstance(volume, int)
            or not isinstance(cal, dict)
        ):
            return False
        values = (cal.get("vpl"), cal.get("vph"), cal.get("zeroPoint"))
        if not all(_is_finite_number(value) for value in values):
            return False
        return cal["vpl"] < cal["vph"]

    def _parse_state(self) -> None:
        """Orchestrate the parsing of the HTP-1 state dictionary."""
        if not self._state:
            return

        parsers = (
            self._parse_power_and_volume,
            self._parse_seat_shakers,
            self._parse_inputs,
            self._parse_audio_and_dirac,
            self._parse_video_stats,
            self._update_sensor_data,
        )
        for parser in parsers:
            try:
                parser()
            except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError) as err:
                _LOG.warning("[%s] Ignoring malformed state section in %s: %s", self.log_id, parser.__name__, err)

    def _parse_power_and_volume(self) -> None:
        state = _as_dict(self._state)
        power = state.get("powerIsOn", False)
        muted = state.get("muted", False)
        self.power = power if isinstance(power, bool) else False
        self.muted = muted if isinstance(muted, bool) else False
        cal = _as_dict(state.get("cal"))
        self.zp = _number(cal.get("zeroPoint"), 0)
        self.vpl = _number(cal.get("vpl"), -80)
        self.vph = _number(cal.get("vph"), 12)
        self.volume_db = _number(state.get("volume"), 0) - self.zp
    def _parse_seat_shakers(self) -> None:
        shaker = _as_dict(_as_dict(self._state).get("shaker"))
        mute = shaker.get("mute", "off")
        self.ss_mute = mute if isinstance(mute, str) else "off"
        active = _number(shaker.get("activePreset"), -1)
        active_index = int(active)
        self.ss_preset = active_index + 1 if 0 <= active_index <= 5 else 0
        presets = _as_dict(shaker.get("presets"))
        preset = _as_dict(presets.get(str(active_index)))
        # The top-level shaker trim is the currently applied value. Preset
        # trim is only the saved value for that preset and may lag until saved.
        self.ss_trim = _number(shaker.get("trim"), _number(preset.get("trim"), 0))

    @staticmethod
    def _is_eligible_input(info: Any) -> bool:
        """Return whether an input can be selected directly by the user.

        Add-on audio sources are started by their owning player and are
        intentionally hidden from manual input selection, even if malformed
        or older state reports them as visible.
        """
        return (
            isinstance(info, dict)
            and info.get("visible") is True
            and not info.get("addon")
        )

    def _parse_inputs(self) -> None:
        state = _as_dict(self._state)
        input_id = state.get("input", "")
        self.source_list = []
        self.current_source = ""
        for inp_id, info in _as_dict(state.get("inputs")).items():
            if not isinstance(info, dict):
                continue
            label = info.get("label", inp_id)
            if not isinstance(label, str):
                continue
            if self._is_eligible_input(info):
                self.source_list.append(label)
            if inp_id == input_id:
                self.current_source = label

    def _parse_audio_and_dirac(self) -> None:
        state = _as_dict(self._state)
        audio = _as_dict(state.get("status"))
        codec = audio.get("DECSourceProgram", "")
        channels = audio.get("DECProgramFormat", "")
        codec = codec if isinstance(codec, str) else ""
        channels = channels if isinstance(channels, str) else ""
        self.audio_format = f"{channels} {codec}".strip() if codec else (channels or "none")
        out_codec = audio.get("SurroundMode", "")
        out_channels = audio.get("ENCListeningFormat", "")
        out_codec = out_codec if isinstance(out_codec, str) else ""
        out_channels = out_channels if isinstance(out_channels, str) else ""
        self.output_audio_format = f"{out_channels} {out_codec}".strip() if out_codec else out_channels
        upmix = _as_dict(state.get("upmix"))
        selected = upmix.get("select", "")
        self.surround_mode = selected if isinstance(selected, str) else ""
        self.sound_mode_display = sound_mode_display_values.get(self.surround_mode, self.surround_mode)
        cal = _as_dict(state.get("cal"))
        dirac_status = cal.get("diracactive", False)
        self.slot_names = []
        self._slot_index = {}
        slots = _as_list(cal.get("slots"))
        for idx, slot in enumerate(slots):
            if not isinstance(slot, dict) or slot.get("valid") is not True:
                continue
            name = slot.get("name", "")
            if isinstance(name, str) and name:
                self.slot_names.append(name)
                self._slot_index.setdefault(name, idx)
        # The HTP-1 changes currentdiracslot before its correction has
        # finished applying. Keep the last confirmed display value until the
        # background lifecycle monitor verifies the final receiver snapshot.
        if not self._dirac_display_held:
            self.dirac_slot_name = "Dirac Off"
            slot_idx = cal.get("currentdiracslot", 0)
            if dirac_status == "on" and isinstance(slot_idx, int) and not isinstance(slot_idx, bool):
                if 0 <= slot_idx < len(slots):
                    name = _as_dict(slots[slot_idx]).get("name", "Unknown")
                    self.dirac_slot_name = name if isinstance(name, str) else "Unknown"
            elif dirac_status == "bypass":
                self.dirac_slot_name = "Dirac Bypass"
            else:
                self.dirac_slot_name = "Dirac Off"

    def _parse_video_stats(self) -> None:
        vi = _as_dict(_as_dict(self._state).get("videostat"))
        keys = ("VideoResolution", "HDRstatus", "VideoColorSpace", "VideoMode", "VideoBitDepth")
        parts = [vi.get(key, "") for key in keys]
        self.video_mode = " ".join(p for p in parts if isinstance(p, str) and p) or "-----"

    def _update_sensor_data(self) -> None:
        state = _as_dict(self._state)
        loudness = state.get("loudness", "off")
        night = state.get("night", "off")
        peq = _as_dict(state.get("peq"))
        active = peq.get("beqActive", "")
        self.beq_active = active if isinstance(active, str) else ""
        self._sensor_data = {
            "input": self.current_source,
            "volume": str(self.volume_db),
            "mute": "On" if self.muted else "Off",
            "ss_trim": str(self.ss_trim),
            "ss_mute": self.ss_mute.capitalize(),
            "ss_preset": self.ss_preset,
            "loudness": loudness.capitalize() if isinstance(loudness, str) else ("On" if loudness is True else "Off"),
            "night_mode": night.capitalize() if isinstance(night, str) else ("On" if night is True else "Off"),
            "peq": "On" if peq.get("peqsw") is True else "Off",
            "sound_mode": self.sound_mode_display,
            "audio_format": getattr(self, "audio_format", "none"),
            "output_audio_format": getattr(self, "output_audio_format", ""),
            "dirac_slot": self.dirac_status_text,
            "video_mode": getattr(self, "video_mode", "-----"),
            "connection": "Connected" if self.is_connected else "Disconnected",
            "beq_active": self.beq_active or "None",
        }

    async def send_message(self, message: str) -> bool:
        """Send one complete WebSocket message without overlapping other writes."""
        try:
            async with self._send_lock:
                if self._ws and self.is_connected:
                    await self._ws.send(message)
                    _LOG.debug("[%s] Sent: %s", self.log_id, message[:200])
                    return True
                return False
        except websockets.ConnectionClosed:
            _LOG.warning("[%s] Send failed: WebSocket connection is closed", self.log_id)
            return False
        except Exception as err:
            _LOG.error("[%s] Send error: %s", self.log_id, err)
            return False

    async def _refresh_state_locked(self) -> bool:
        """Request a fresh receiver snapshot while holding the transaction lock."""
        if not self._ws or not self.is_connected:
            self._invalidate_state("cannot refresh while disconnected")
            return False

        generation = self._connection_generation
        self._state_ready.clear()
        if not await self.send_message("getmso"):
            self._invalidate_state("failed to request fresh receiver state")
            return False
        waiter = asyncio.create_task(self._state_ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {waiter}, timeout=STATE_REFRESH_TIMEOUT
            )
            if not done:
                _LOG.warning(
                    "[%s] Timed out waiting for fresh HTP-1 state", self.log_id
                )
                self._invalidate_state("timed out waiting for fresh receiver state")
                return False
            await waiter
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)

        refreshed = (
            self.is_connected
            and self._connection_generation == generation
            and isinstance(self._state, dict)
        )
        if not refreshed:
            self._invalidate_state("connection changed during state refresh")
        return refreshed

    _MISSING = object()

    @staticmethod
    def _json_pointer_value(state: Any, path: str) -> Any:
        """Read a JSON Pointer path, returning a sentinel when it is absent."""
        missing = HTP1Device._MISSING
        if not isinstance(path, str) or not path.startswith("/"):
            return missing
        current = state
        for raw_segment in path[1:].split("/"):
            if re.search(r"~(?![01])", raw_segment):
                return missing
            segment = raw_segment.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                if segment not in current:
                    return missing
                current = current[segment]
            elif isinstance(current, list):
                if not segment.isdecimal():
                    return missing
                index = int(segment)
                if index >= len(current):
                    return missing
                current = current[index]
            else:
                return missing
        return current

    def _transaction_mismatch_path(
        self, expected_state: dict[str, Any], operations: list[dict[str, Any]]
    ) -> str | None:
        """Return the first touched path that differs, without logging transient state."""
        if not isinstance(self._state, dict):
            return "<no state>"
        for path in dict.fromkeys(operation["path"] for operation in operations):
            expected = self._json_pointer_value(expected_state, path)
            actual = self._json_pointer_value(self._state, path)
            if expected is self._MISSING or actual is self._MISSING:
                if expected is not actual:
                    return path
            elif expected != actual:
                return path
        return None

    def _transaction_matches_state(
        self, expected_state: dict[str, Any], operations: list[dict[str, Any]]
    ) -> bool:
        """Quiet predicate used while waiting for live receiver-state convergence."""
        return self._transaction_mismatch_path(expected_state, operations) is None

    @staticmethod
    def _paths_overlap(first: str, second: str) -> bool:
        """Return whether two JSON Pointer paths refer to overlapping state."""
        return (
            first == second
            or first.startswith(second.rstrip("/") + "/")
            or second.startswith(first.rstrip("/") + "/")
        )

    async def _wait_for_transaction_update_locked(
        self, expected_state: dict[str, Any], operations: list[dict[str, Any]]
    ) -> None:
        """Wait briefly for a relevant live patch; the full snapshot remains authoritative."""
        paths = [operation["path"] for operation in operations]
        if self._transaction_matches_state(expected_state, operations):
            # An idempotent write still receives a confirming getmso below.
            return

        deadline = asyncio.get_running_loop().time() + self._write_convergence_timeout
        while True:
            if self._transaction_error:
                return
            relevant_update = any(
                self._paths_overlap(updated, target)
                for updated in self._state_update_paths
                for target in paths
            )
            if relevant_update and self._transaction_matches_state(expected_state, operations):
                return

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                _LOG.warning(
                    "[%s] Timed out waiting for live state convergence; confirming with getmso",
                    self.log_id,
                )
                return

            self._state_update_event.clear()
            try:
                await asyncio.wait_for(self._state_update_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                _LOG.warning(
                    "[%s] Timed out waiting for live state convergence; confirming with getmso",
                    self.log_id,
                )
                return

    async def _execute_transaction_locked(
        self,
        operations: list[dict[str, Any]],
        *,
        allow_during_dirac: bool = False,
    ) -> bool:
        """Write, await live convergence, then confirm one receiver snapshot."""
        if not operations:
            return True
        if self._dirac_commands_blocked() and not allow_during_dirac:
            _LOG.warning("[%s] Rejecting state write while Dirac is applying", self.log_id)
            return False
        if not isinstance(self._state, dict):
            return False

        expected_state = copy.deepcopy(self._state)
        try:
            for operation in operations:
                apply_json_patch(
                    expected_state,
                    operation["op"],
                    operation["path"],
                    operation.get("value"),
                )
        except (KeyError, IndexError, TypeError, ValueError) as err:
            _LOG.error("[%s] Cannot prepare transaction readback: %s", self.log_id, err)
            return False

        payload = json.dumps(operations, separators=(",", ":"))
        self._state_update_paths.clear()
        self._state_update_event.clear()
        self._transaction_error = None
        if not await self.send_message(f"changemso {payload}"):
            return False

        await self._wait_for_transaction_update_locked(expected_state, operations)
        if self._transaction_error:
            _LOG.warning(
                "[%s] Receiver rejected state transaction: %s",
                self.log_id,
                self._transaction_error,
            )
            return False
        if not await self._refresh_state_locked():
            return False
        if self._transaction_error:
            _LOG.warning(
                "[%s] Receiver rejected state transaction: %s",
                self.log_id,
                self._transaction_error,
            )
            return False
        mismatch_path = self._transaction_mismatch_path(expected_state, operations)
        if mismatch_path is None:
            return True
        if mismatch_path == "<no state>":
            _LOG.error("[%s] Receiver did not apply transaction path: no state returned", self.log_id)
        else:
            expected = self._json_pointer_value(expected_state, mismatch_path)
            actual = self._json_pointer_value(self._state, mismatch_path)
            if expected is self._MISSING or actual is self._MISSING:
                _LOG.error(
                    "[%s] Receiver did not apply transaction path %s",
                    self.log_id,
                    mismatch_path,
                )
            else:
                _LOG.error(
                    "[%s] Receiver state mismatch after transaction at %s",
                    self.log_id,
                    mismatch_path,
                )
        return False

    def _set_dirac_lifecycle_state(self, state: DiracLifecycleState) -> None:
        """Apply a legal lifecycle transition and publish visible status changes."""
        current = getattr(self, "_dirac_lifecycle_state", DiracLifecycleState.IDLE)
        if state is current:
            return
        if state not in _DIRAC_LIFECYCLE_TRANSITIONS[current]:
            raise ValueError(
                f"Invalid Dirac lifecycle transition: {current.value} -> {state.value}"
            )

        previous_text = self.dirac_status_text
        if state is DiracLifecycleState.REQUESTED:
            self._dirac_failure_reason = None
        elif state is DiracLifecycleState.COMPLETE:
            self._dirac_failure_reason = None
            self._dirac_target_slot_name = None
        elif state is DiracLifecycleState.FAILED and not self._dirac_failure_reason:
            self._dirac_failure_reason = "Dirac slot change failed"

        self._dirac_lifecycle_state = state
        status_text = self.dirac_status_text
        if status_text != previous_text:
            self._sensor_data["dirac_slot"] = status_text
            self.push_update()

    @property
    def dirac_status_text(self) -> str:
        """Return the user-facing status for the existing Dirac slot sensor."""
        state = getattr(self, "_dirac_lifecycle_state", DiracLifecycleState.IDLE)
        if state in {
            DiracLifecycleState.REQUESTED,
            DiracLifecycleState.ACCEPTED,
            DiracLifecycleState.APPLYING,
        }:
            target = getattr(self, "_dirac_target_slot_name", None)
            return f"Loading {target}" if target else "Loading calibration"
        if state is DiracLifecycleState.FAILED:
            reason = getattr(self, "_dirac_failure_reason", None) or "unknown error"
            normalized = reason.lower()
            if "standby" in normalized or "processor starts" in normalized:
                reason = "standby"
            elif "busy" in normalized or "applying a correction" in normalized:
                reason = "busy"
            elif "timed out" in normalized or "timeout" in normalized:
                reason = "timeout"
            else:
                reason = reason.strip().rstrip(".")
                if len(reason) > 80:
                    reason = f"{reason[:77]}..."
            return f"Not applied: {reason}"
        return self.dirac_slot_name or "Unknown"

    @property
    def _dirac_display_held(self) -> bool:
        """Keep the last confirmed slot visible until success is read back."""
        return getattr(self, "_dirac_lifecycle_state", DiracLifecycleState.IDLE) in {
            DiracLifecycleState.REQUESTED,
            DiracLifecycleState.ACCEPTED,
            DiracLifecycleState.APPLYING,
            DiracLifecycleState.FAILED,
        }

    def _dirac_commands_blocked(self) -> bool:
        """Return whether ordinary state mutations must wait for Dirac."""
        return getattr(self, "_dirac_lifecycle_state", DiracLifecycleState.IDLE) in {
            DiracLifecycleState.REQUESTED,
            DiracLifecycleState.ACCEPTED,
            DiracLifecycleState.APPLYING,
        }

    def _discard_dirac_volume_capture(self) -> None:
        """Discard volume requests captured for a Dirac operation."""
        self._dirac_volume_capture_active = False
        self._dirac_volume_snapshot = None
        self._dirac_volume_delta = 0
        self._dirac_volume_absolute_db = None

    def _begin_dirac_volume_capture(self) -> None:
        """Capture the confirmed volume coordinate system before a Dirac apply."""
        state = _as_dict(self._state)
        cal = _as_dict(state.get("cal"))
        volume = state.get("volume")
        values = {"volume": volume, "vpl": cal.get("vpl"), "vph": cal.get("vph"), "zp": cal.get("zeroPoint")}
        valid = (
            isinstance(volume, int) and not isinstance(volume, bool)
            and all(_is_finite_number(values[key]) for key in ("vpl", "vph", "zp"))
            and values["vpl"] < values["vph"]
            and values["vpl"] <= volume <= values["vph"]
        )
        self._dirac_volume_snapshot = {key: float(value) for key, value in values.items()} if valid else None
        self._dirac_volume_delta = 0
        self._dirac_volume_absolute_db = None
        self._dirac_volume_capture_active = True
        if not valid:
            _LOG.warning(
                "[%s] Cannot safely defer volume during Dirac: initial volume calibration state is invalid",
                self.log_id,
            )

    def _capture_dirac_volume_step(self, delta: int) -> bool | None:
        """Capture a relative volume request; None means no Dirac capture is active."""
        if not self._dirac_volume_capture_active:
            return None
        snapshot = self._dirac_volume_snapshot
        if snapshot is None:
            return False
        base_db = (
            self._dirac_volume_absolute_db
            if self._dirac_volume_absolute_db is not None
            else snapshot["volume"] - snapshot["zp"]
        )
        minimum_db = snapshot["vpl"] - snapshot["zp"]
        maximum_db = snapshot["vph"] - snapshot["zp"]
        target_db = max(minimum_db, min(maximum_db, base_db + self._dirac_volume_delta + delta))
        self._dirac_volume_delta = int(round(target_db - base_db))
        _LOG.debug(
            "[%s] Captured volume step %+d during Dirac (pending delta %+d dB)",
            self.log_id,
            delta,
            self._dirac_volume_delta,
        )
        return True

    def _capture_dirac_volume_raw(self, volume: int) -> bool | None:
        """Capture an absolute receiver-coordinate volume request during Dirac."""
        if not self._dirac_volume_capture_active:
            return None
        snapshot = self._dirac_volume_snapshot
        if snapshot is None or not snapshot["vpl"] <= volume <= snapshot["vph"]:
            return False
        return self._capture_dirac_volume_absolute(volume - snapshot["zp"])

    def _capture_dirac_volume_level(self, level: float) -> bool | None:
        """Capture a normalized absolute volume request during Dirac."""
        if not self._dirac_volume_capture_active:
            return None
        snapshot = self._dirac_volume_snapshot
        if snapshot is None:
            return False
        normalized = max(0.0, min(1.0, level))
        target_raw = int(round(snapshot["vpl"] + normalized * (snapshot["vph"] - snapshot["vpl"])))
        return self._capture_dirac_volume_absolute(target_raw - snapshot["zp"])

    def _capture_dirac_volume_absolute(self, target_db: float) -> bool | None:
        """Capture an absolute displayed-dB target during Dirac."""
        if not self._dirac_volume_capture_active:
            return None
        snapshot = self._dirac_volume_snapshot
        if snapshot is None:
            return False
        minimum_db = snapshot["vpl"] - snapshot["zp"]
        maximum_db = snapshot["vph"] - snapshot["zp"]
        if not minimum_db <= target_db <= maximum_db:
            return False
        self._dirac_volume_absolute_db = target_db
        self._dirac_volume_delta = 0
        _LOG.debug("[%s] Captured absolute volume target %.1f dB during Dirac", self.log_id, target_db)
        return True

    async def _set_volume_level_locked(
        self, level: float, *, allow_during_dirac: bool = False
    ) -> bool:
        """Apply a normalized volume target while the transaction lock is held."""
        if isinstance(level, bool) or not _is_finite_number(level) or not self._state or "cal" not in self._state:
            return False
        span = self.vph - self.vpl
        if span <= 0:
            return False
        level = max(0.0, min(1.0, level))
        target_db = int(round(self.vpl + (level * span)))
        target_db = max(int(self.vpl), min(int(self.vph), target_db))
        current_volume = self._state.get("volume")
        if isinstance(current_volume, bool) or not isinstance(current_volume, int):
            return False
        volume_delta = abs(target_db - current_volume)
        max_safe_jump = 5
        if volume_delta > max_safe_jump:
            target_db = current_volume + max_safe_jump if target_db > current_volume else current_volume - max_safe_jump
            _LOG.warning("[%s] Volume jump clamped to %d dB", self.log_id, target_db)
        operations = [{"op": "replace", "path": "/volume", "value": target_db}]
        if allow_during_dirac:
            return await self._execute_transaction_locked(
                operations, allow_during_dirac=True
            )
        return await self._execute_transaction_locked(operations)

    async def _apply_deferred_dirac_volume_locked(self) -> bool:
        """Apply aggregated volume intent after Dirac confirmation, under the transaction lock."""
        snapshot = self._dirac_volume_snapshot
        pending_delta = self._dirac_volume_delta
        absolute_db = self._dirac_volume_absolute_db
        self._dirac_volume_capture_active = False
        try:
            if snapshot is None or not isinstance(self._state, dict):
                return False
            if absolute_db is None and pending_delta == 0:
                return True
            target_display_db = (
                absolute_db
                if absolute_db is not None
                else snapshot["volume"] - snapshot["zp"] + pending_delta
            )
            if not all(_is_finite_number(value) for value in (self.zp, self.vpl, self.vph)) or self.vph <= self.vpl:
                return False
            # Preserve displayed dB across a calibration zero-point change.
            target_raw = int(round(target_display_db + self.zp))
            target_raw = max(int(self.vpl), min(int(self.vph), target_raw))
            current_volume = self._state.get("volume")
            if isinstance(current_volume, bool) or not isinstance(current_volume, int):
                return False
            if target_raw == current_volume:
                return True
            level = (target_raw - self.vpl) / (self.vph - self.vpl)
            return await self._set_volume_level_locked(level, allow_during_dirac=True)
        finally:
            self._discard_dirac_volume_capture()

    async def _send_transaction(self, operations: list[dict[str, Any]]) -> bool:
        """Serialize, refresh, send, and verify one receiver state transaction."""
        if self._dirac_commands_blocked():
            _LOG.warning("[%s] Rejecting state change while Dirac is applying", self.log_id)
            return False
        async with self._transaction_lock:
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked():
                return False
            return await self._execute_transaction_locked(operations)

    async def turn_on(self) -> bool:
        _LOG.info("[%s] Turning on", self.log_id)
        return await self._send_transaction([
            {"op": "replace", "path": "/powerIsOn", "value": True}
        ])

    async def turn_off(self) -> bool:
        _LOG.info("[%s] Turning off", self.log_id)
        return await self._send_transaction([
            {"op": "replace", "path": "/powerIsOn", "value": False}
        ])
    
    async def toggle_power(self) -> bool:
        if self._dirac_commands_blocked():
            return False
        _LOG.info("[%s] Toggling power", self.log_id)
        async with self._transaction_lock:
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked():
                return False
            new_state = not self.power
            return await self._execute_transaction_locked([
                {"op": "replace", "path": "/powerIsOn", "value": new_state}
            ])

    async def set_volume(self, volume: int) -> bool:
        if isinstance(volume, bool) or not isinstance(volume, int):
            _LOG.warning("[%s] Rejecting invalid volume value: %r", self.log_id, volume)
            return False
        captured = self._capture_dirac_volume_raw(volume)
        if captured is not None:
            return captured
        async with self._transaction_lock:
            captured = self._capture_dirac_volume_raw(volume)
            if captured is not None:
                return captured
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked() or not isinstance(self._state, dict):
                return False
            cal = _as_dict(self._state.get("cal"))
            vpl = cal.get("vpl")
            vph = cal.get("vph")
            if not _is_finite_number(vpl) or not _is_finite_number(vph) or vpl > vph or not vpl <= volume <= vph:
                _LOG.warning("[%s] Rejecting out-of-range volume value: %r", self.log_id, volume)
                return False
            _LOG.info("[%s] Setting volume to %d", self.log_id, volume)
            return await self._execute_transaction_locked([{"op": "replace", "path": "/volume", "value": volume}])

    async def set_volume_level(self, level: float) -> bool:
        if isinstance(level, bool) or not _is_finite_number(level):
            return False
        captured = self._capture_dirac_volume_level(level)
        if captured is not None:
            return captured
        async with self._transaction_lock:
            captured = self._capture_dirac_volume_level(level)
            if captured is not None:
                return captured
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked():
                return False
            return await self._set_volume_level_locked(level)

    async def volume_up(self) -> bool:
        captured = self._capture_dirac_volume_step(1)
        if captured is not None:
            return captured
        async with self._transaction_lock:
            captured = self._capture_dirac_volume_step(1)
            if captured is not None:
                return captured
            if self._dirac_commands_blocked():
                return False
            if not self._volume_state_is_valid() or not self._volume_state_is_fresh():
                if not await self._refresh_state_locked():
                    return False
            if not self._volume_state_is_valid() or not self._volume_state_is_fresh():
                return False
            current = self._state["volume"]
            if not _is_finite_number(self.vph):
                return False
            if current >= self.vph:
                return True
            return await self._execute_transaction_locked([{"op": "replace", "path": "/volume", "value": current + 1}])

    async def volume_down(self) -> bool:
        captured = self._capture_dirac_volume_step(-1)
        if captured is not None:
            return captured
        async with self._transaction_lock:
            captured = self._capture_dirac_volume_step(-1)
            if captured is not None:
                return captured
            if self._dirac_commands_blocked():
                return False
            if not self._volume_state_is_valid() or not self._volume_state_is_fresh():
                if not await self._refresh_state_locked():
                    return False
            if not self._volume_state_is_valid() or not self._volume_state_is_fresh():
                return False
            current = self._state["volume"]
            if not _is_finite_number(self.vpl) or not _is_finite_number(self.zp):
                return False
            limit = self.vpl - self.zp
            if current - self.zp <= limit:
                return True
            return await self._execute_transaction_locked([{"op": "replace", "path": "/volume", "value": current - 1}])

    async def mute_toggle(self, muted: bool) -> bool:
        if not isinstance(muted, bool):
            return False
        _LOG.info("[%s] Setting mute to %s", self.log_id, muted)
        return await self._send_transaction([
            {"op": "replace", "path": "/muted", "value": muted}
        ])

    async def ss_mute_toggle(self, muted: bool) -> bool:
        if not isinstance(muted, bool):
            return False
        _LOG.info("[%s] Setting seat shaker mute to %s", self.log_id, muted)
        status = "on" if muted else "off"
        return await self._send_transaction([
            {"op": "replace", "path": "/shaker/mute", "value": status}
        ])
    
    async def set_ss_trim(self, trim: int) -> bool:
        if (
            isinstance(trim, bool)
            or not isinstance(trim, int)
            or not SEAT_SHAKER_TRIM_MIN <= trim <= SEAT_SHAKER_TRIM_MAX
        ):
            _LOG.warning("[%s] Rejecting invalid seat shaker trim: %r", self.log_id, trim)
            return False
        _LOG.info("[%s] Setting seat shaker trim to %d", self.log_id, trim)
        return await self._send_transaction([
            {"op": "replace", "path": "/shaker/trim", "value": trim}
        ])

    async def select_source(self, source: str) -> bool:
        if not isinstance(source, str) or not source.strip() or self._dirac_commands_blocked():
            return False
        _LOG.info("[%s] Selecting source: %s", self.log_id, source)
        async with self._transaction_lock:
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked():
                return False
            inputs = _as_dict(self._state.get("inputs"))
            for inp_id, inp_info in inputs.items():
                if (
                    self._is_eligible_input(inp_info)
                    and inp_info.get("label") == source
                ):
                    return await self._execute_transaction_locked([
                        {"op": "replace", "path": "/input", "value": inp_id}
                    ])
        _LOG.warning("[%s] Source not found in fresh receiver state: %s", self.log_id, source)
        return False

    async def select_sound_mode(self, sound_mode: str) -> bool:
        _LOG.info("[%s] Selecting sound mode: %s", self.log_id, sound_mode)
        native = sound_mode_native_values.get(sound_mode)
        if native is None:
            _LOG.warning("[%s] Unsupported sound mode: %s", self.log_id, sound_mode)
            return False
        return await self._send_transaction([
            {"op": "replace", "path": "/upmix/select", "value": native}
        ])
    
    async def select_ss_preset(self, preset_index: int) -> bool:
        if isinstance(preset_index, bool) or not isinstance(preset_index, int):
            return False
        if not 0 <= preset_index <= 5 or self._dirac_commands_blocked():
            return False
        _LOG.info("[%s] Selecting seat shaker preset: %d", self.log_id, preset_index)
        async with self._transaction_lock:
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked():
                return False
            if not isinstance(self._state.get("shaker"), dict):
                return False
            return await self._execute_transaction_locked([
                {"op": "replace", "path": "/shaker/activePreset", "value": preset_index}
            ])

    def _dirac_refusal(
        self, request_id: str, previous_reply_id: str | None
    ) -> str | None:
        """Return a refusal message correlated to this Dirac request."""
        cal = _as_dict(_as_dict(self._state).get("cal"))
        switch = _as_dict(cal.get("diracSwitch"))
        if switch.get("id") == request_id and switch.get("state") in {
            "refused", "failed", "error"
        }:
            return str(switch.get("message") or "The HTP-1 refused the Dirac slot change")

        reply = _as_dict(cal.get("diracSwitchReply"))
        reply_id = reply.get("id")
        if (
            reply.get("state") in {"refused", "failed", "error"}
            and reply_id == request_id
            and reply_id != previous_reply_id
        ):
            return str(reply.get("message") or "The HTP-1 refused the Dirac slot change")
        return None

    async def _wait_for_dirac_acceptance(
        self,
        previous_id: str | None,
        previous_reply_id: str | None,
        slot_idx: int,
        connection_generation: int,
    ) -> tuple[str | None, str | None]:
        """Wait only until the receiver accepts this request, not for DSP completion."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._dirac_acceptance_timeout
        while True:
            if self._connection_generation != connection_generation:
                return None, "HTP-1 connection was lost before Dirac accepted the slot change"

            request_id = (
                self._dirac_request_id
                if self._dirac_request_slot == slot_idx
                and self._dirac_request_id != previous_id
                else None
            )
            cal = _as_dict(_as_dict(self._state).get("cal"))
            switch = _as_dict(cal.get("diracSwitch"))
            switch_id = switch.get("id")
            if request_id is None and (
                isinstance(switch_id, str)
                and switch_id
                and switch_id != previous_id
                and switch.get("slot") == slot_idx
            ):
                request_id = switch_id

            if request_id is not None:
                refusal = self._dirac_refusal(request_id, previous_reply_id)
                if refusal:
                    return None, refusal
                if switch.get("id") == request_id and switch.get("state") in {
                    "requested", "running", "complete"
                }:
                    return request_id, None

            if self._dirac_error:
                return None, self._dirac_error

            remaining = deadline - loop.time()
            if remaining <= 0:
                return None, "Timed out waiting for the HTP-1 to accept the Dirac slot change"

            self._dirac_update_event.clear()
            # Recheck after clearing so a receiver update cannot be lost at the wait boundary.
            cal = _as_dict(_as_dict(self._state).get("cal"))
            switch = _as_dict(cal.get("diracSwitch"))
            if self._dirac_error or (
                self._dirac_request_id is not None
                and self._dirac_request_slot == slot_idx
                and self._dirac_request_id != previous_id
            ) or (
                isinstance(switch.get("id"), str)
                and switch.get("id") != previous_id
                and switch.get("slot") == slot_idx
                and switch.get("state") in {"requested", "running", "complete", "refused", "failed", "error"}
            ):
                continue
            try:
                await asyncio.wait_for(self._dirac_update_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return None, "Timed out waiting for the HTP-1 to accept the Dirac slot change"

    async def _wait_for_dirac_completion(
        self,
        request_id: str,
        previous_reply_id: str | None,
        connection_generation: int,
    ) -> str | None:
        """Monitor the accepted request independently of ordinary device transactions."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._dirac_switch_timeout
        while True:
            if (
                not self.is_connected
                or self._connection_generation != connection_generation
            ):
                return "HTP-1 connection was lost while applying the Dirac correction"

            cal = _as_dict(_as_dict(self._state).get("cal"))
            switch = _as_dict(cal.get("diracSwitch"))
            refusal = self._dirac_refusal(request_id, previous_reply_id)
            if refusal:
                return refusal
            if switch.get("id") == request_id:
                if switch.get("state") == "complete":
                    return None
                if switch.get("state") == "running":
                    self._set_dirac_lifecycle_state(DiracLifecycleState.APPLYING)
                if switch.get("state") in {"refused", "failed", "error"}:
                    return str(switch.get("message") or "The HTP-1 refused the Dirac slot change")

            remaining = deadline - loop.time()
            if remaining <= 0:
                return "Timed out waiting for the HTP-1 to complete the Dirac slot change"

            self._dirac_update_event.clear()
            # Recheck terminal state after clearing the event.
            cal = _as_dict(_as_dict(self._state).get("cal"))
            switch = _as_dict(cal.get("diracSwitch"))
            refusal = self._dirac_refusal(request_id, previous_reply_id)
            if refusal:
                return refusal
            if switch.get("id") == request_id and switch.get("state") == "complete":
                return None
            if switch.get("id") == request_id and switch.get("state") == "running":
                self._set_dirac_lifecycle_state(DiracLifecycleState.APPLYING)
            try:
                await asyncio.wait_for(self._dirac_update_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return "Timed out waiting for the HTP-1 to complete the Dirac slot change"

    async def _monitor_dirac_switch(
        self,
        request_id: str,
        slot_idx: int,
        previous_reply_id: str | None,
        connection_generation: int,
    ) -> None:
        """Finish a receiver-accepted Dirac operation without blocking other commands."""
        verified = False
        try:
            failure = await self._wait_for_dirac_completion(
                request_id, previous_reply_id, connection_generation
            )
            if failure:
                self._dirac_failure_reason = failure
                _LOG.warning("[%s] Dirac slot change failed: %s", self.log_id, failure)
                return

            # Serialize only the final snapshot with ordinary writes. The long
            # receiver DSP lifecycle above does not own this transaction lock.
            async with self._transaction_lock:
                if (
                    not self.is_connected
                    or self._connection_generation != connection_generation
                    or not await self._refresh_state_locked()
                ):
                    self._dirac_failure_reason = "Could not confirm the completed Dirac slot change"
                    _LOG.warning(
                        "[%s] Could not confirm the completed Dirac slot change",
                        self.log_id,
                    )
                    return
                cal = _as_dict(_as_dict(self._state).get("cal"))
                switch = _as_dict(cal.get("diracSwitch"))
                verified = (
                    switch.get("id") == request_id
                    and switch.get("state") == "complete"
                    and switch.get("slot") == slot_idx
                    and cal.get("currentdiracslot") == slot_idx
                )
                if not verified:
                    self._dirac_failure_reason = "Final slot readback did not match the requested calibration"
                    _LOG.warning(
                        "[%s] Dirac request %s completed but final slot readback did not match",
                        self.log_id,
                        request_id,
                    )
                    return

                volume_applied = await self._apply_deferred_dirac_volume_locked()
                if not volume_applied:
                    _LOG.warning("[%s] Dirac completed, but deferred volume could not be applied safely", self.log_id)
                # The receiver snapshot and request ID have both been verified;
                # release the held display using the confirmed target name.
                if self._dirac_target_slot_name:
                    self.dirac_slot_name = self._dirac_target_slot_name
                self._set_dirac_lifecycle_state(DiracLifecycleState.COMPLETE)
                self._parse_state()
                self.push_update()
                _LOG.info(
                    "[%s] Dirac slot change to index %d completed and confirmed",
                    self.log_id,
                    slot_idx,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._dirac_failure_reason = "Unexpected error while monitoring the Dirac slot change"
            _LOG.exception("[%s] Unexpected error monitoring Dirac slot change", self.log_id)
        finally:
            if verified:
                if self._dirac_lifecycle_state not in {
                    DiracLifecycleState.COMPLETE,
                    DiracLifecycleState.FAILED,
                }:
                    self._set_dirac_lifecycle_state(DiracLifecycleState.COMPLETE)
            else:
                if not self._dirac_failure_reason:
                    self._dirac_failure_reason = "Dirac slot change was not confirmed"
                if self._dirac_lifecycle_state is not DiracLifecycleState.FAILED:
                    self._set_dirac_lifecycle_state(DiracLifecycleState.FAILED)
                self._discard_dirac_volume_capture()
            if self._dirac_operation_lock.locked():
                self._dirac_operation_lock.release()
            self._dirac_task = None

    def _on_dirac_task_done(self, task: asyncio.Task) -> None:
        """Clean up even when a newly-created monitor is cancelled before it starts."""
        if self._dirac_task is not task:
            return
        if self._dirac_lifecycle_state in {
            DiracLifecycleState.REQUESTED,
            DiracLifecycleState.ACCEPTED,
            DiracLifecycleState.APPLYING,
        }:
            self._dirac_failure_reason = "Dirac operation monitoring stopped unexpectedly"
            self._set_dirac_lifecycle_state(DiracLifecycleState.FAILED)
            self._discard_dirac_volume_capture()
        if self._dirac_operation_lock.locked():
            self._dirac_operation_lock.release()
        self._dirac_task = None

    async def select_calibration(self, slot_name: str) -> bool:
        """Start a Dirac slot change and return once the receiver accepts it."""
        if not isinstance(slot_name, str) or not slot_name:
            return False
        if self._dirac_operation_lock.locked():
            _LOG.warning("[%s] Rejecting Dirac selection; another slot change is active", self.log_id)
            return False

        await self._dirac_operation_lock.acquire()
        handed_off = False
        try:
            async with self._transaction_lock:
                if not await self._refresh_state_locked():
                    return False
                slot_idx = self._slot_index.get(slot_name)
                if slot_idx is None:
                    return False

                cal = _as_dict(_as_dict(self._state).get("cal"))
                current_switch = _as_dict(cal.get("diracSwitch"))
                if current_switch.get("locked") is True or cal.get("diracApplying") is True:
                    _LOG.warning(
                        "[%s] Cannot change Dirac slot while the receiver is applying a correction",
                        self.log_id,
                    )
                    return False

                previous_id = current_switch.get("id")
                previous_id = previous_id if isinstance(previous_id, str) else None
                current_reply = _as_dict(cal.get("diracSwitchReply"))
                previous_reply_id = current_reply.get("id")
                previous_reply_id = previous_reply_id if isinstance(previous_reply_id, str) else None

                self._dirac_update_event.clear()
                self._dirac_error = None
                self._dirac_target_slot_name = slot_name
                self._dirac_failure_reason = None
                self._dirac_request_id = None
                self._dirac_request_slot = None
                self._begin_dirac_volume_capture()
                connection_generation = self._connection_generation
                self._set_dirac_lifecycle_state(DiracLifecycleState.REQUESTED)
                operation = {
                    "op": "replace",
                    "path": "/cal/currentdiracslot",
                    "value": slot_idx,
                }
                payload = json.dumps([operation], separators=(",", ":"))
                if not await self.send_message(f"changemso {payload}"):
                    self._dirac_failure_reason = "Could not send the Dirac slot change request"
                    self._set_dirac_lifecycle_state(DiracLifecycleState.FAILED)
                    self._discard_dirac_volume_capture()
                    self._parse_state()
                    self.push_update()
                    return False

                request_id, failure = await self._wait_for_dirac_acceptance(
                    previous_id, previous_reply_id, slot_idx, connection_generation
                )
                if failure or request_id is None:
                    _LOG.warning(
                        "[%s] Dirac slot change was not accepted: %s",
                        self.log_id,
                        failure or "No receiver request ID was observed",
                    )
                    self._dirac_failure_reason = failure or "No receiver request ID was observed"
                    self._set_dirac_lifecycle_state(DiracLifecycleState.FAILED)
                    self._discard_dirac_volume_capture()
                    self._parse_state()
                    self.push_update()
                    return False

                self._set_dirac_lifecycle_state(DiracLifecycleState.ACCEPTED)
                self._dirac_task = asyncio.create_task(
                    self._monitor_dirac_switch(
                        request_id,
                        slot_idx,
                        previous_reply_id,
                        connection_generation,
                    )
                )
                self._dirac_task.add_done_callback(self._on_dirac_task_done)
                handed_off = True
                _LOG.info(
                    "[%s] Dirac slot request %s accepted; monitoring completion in background",
                    self.log_id,
                    request_id,
                )
                return True
        finally:
            if not handed_off and self._dirac_operation_lock.locked():
                if self._dirac_lifecycle_state in {
                    DiracLifecycleState.REQUESTED,
                    DiracLifecycleState.ACCEPTED,
                    DiracLifecycleState.APPLYING,
                }:
                    if not self._dirac_failure_reason:
                        self._dirac_failure_reason = "Dirac slot change was not completed"
                    self._set_dirac_lifecycle_state(DiracLifecycleState.FAILED)
                self._discard_dirac_volume_capture()
                self._dirac_operation_lock.release()

    async def send_command(self, command: str) -> bool:
        if self._dirac_commands_blocked():
            return False
        _LOG.info("[%s] Sending menu command: %s", self.log_id, command)
        avcui_commands = {
            "send_avcui: hpe": "send_avcui: hpe",
        }
        htp1_command = avcui_commands.get(command)
        if not htp1_command:
            _LOG.warning("[%s] Unknown menu command: %s", self.log_id, command)
            return False
        return await self.send_message(htp1_command)

    async def send_http_command(self, command: str) -> bool:
        if self._dirac_commands_blocked():
            return False
        if (
            not isinstance(command, str)
            or len(command) != 4
            or any(char not in "0123456789abcdefABCDEF" for char in command)
        ):
            _LOG.warning("[%s] Rejecting invalid IR command code", self.log_id)
            return False
        _LOG.info("[%s] Sending http command: %s", self.log_id, command)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://{self.address}:{self._device_config.port}/ircmd?code={command}", timeout=5) as response:
                    return response.status == 200
        except asyncio.TimeoutError:
            _LOG.error("[%s] HTTP command timed out", self.log_id)
            return False
        except aiohttp.ClientError as err:
            _LOG.error("[%s] HTTP command connection error: %s", self.log_id, err)
            return False
        except Exception as err:
            _LOG.error("[%s] HTTP command error: %s", self.log_id, err)
            return False

    def _get_sub_channels(self) -> list[str]:
        """Return the single LFE path used for BEQ before Dirac processing.

        PEQ Pre is required for BEQ compatibility. In the HTP-1 MSO this
        signal is represented by the sub1 PEQ channel; it must not be copied
        independently to the post-Dirac subwoofer outputs.
        """
        return ["sub1"]

    def _find_empty_peq_slot(self, start_slot: int = BEQ_SLOT_START, ch: str | None = None) -> int | None:
        """Find a reusable band with a complete PEQ record for this channel."""
        if (
            not self._state
            or isinstance(start_slot, bool)
            or not isinstance(start_slot, int)
            or not isinstance(ch, str)
        ):
            return None

        peq = _as_dict(self._state.get("peq"))
        slots = _as_list(peq.get("slots"))
        required = ("Fc", "gaindB", "Q", "FilterType")

        for index in range(max(0, start_slot), min(BEQ_SLOT_END + 1, len(slots))):
            slot = _as_dict(slots[index])
            channels = _as_dict(slot.get("channels"))
            channel_data = _as_dict(channels.get(ch))
            if not all(key in channel_data for key in required):
                continue

            freq = channel_data.get("Fc")
            gain = channel_data.get("gaindB")
            q_value = channel_data.get("Q")
            filter_type = channel_data.get("FilterType")
            if (
                not _is_finite_number(freq)
                or not _is_finite_number(gain)
                or not _is_finite_number(q_value)
                or isinstance(filter_type, bool)
                or not isinstance(filter_type, int)
                or filter_type not in FILTER_TYPE_MAP.values()
            ):
                continue
            if gain == 0 or channel_data.get("beq") is True:
                return index

        return None

    def _get_peq_path(self, slot_idx: int, channel: str, attribute: str) -> str:
        """Helper to construct the JSON path for PEQ modifications."""
        return f"/peq/slots/{slot_idx}/channels/{channel}/{attribute}"

    def _build_clear_beq_ops(self) -> list[dict[str, Any]] | None:
        """Reset every BEQ-tagged band, including legacy subwoofer tags."""
        if not self._state:
            return None

        peq = self._state.get("peq")
        if not isinstance(peq, dict):
            return None
        slots = peq.get("slots")
        if not isinstance(slots, list) or not slots:
            return None

        ops: list[dict[str, Any]] = []
        required = ("Fc", "gaindB", "Q", "FilterType")
        for index, slot in enumerate(slots[:BEQ_SLOT_END + 1]):
            channels = _as_dict(_as_dict(slot).get("channels"))
            for channel, raw_channel_data in channels.items():
                if not isinstance(channel, str):
                    continue
                channel_data = _as_dict(raw_channel_data)
                if channel_data.get("beq") is not True:
                    continue
                filter_type = channel_data.get("FilterType")
                if (
                    not all(key in channel_data for key in required)
                    or any(not _is_finite_number(channel_data.get(key)) for key in ("Fc", "gaindB", "Q"))
                    or isinstance(filter_type, bool)
                    or not isinstance(filter_type, int)
                    or filter_type not in FILTER_TYPE_MAP.values()
                ):
                    _LOG.error(
                        "[%s] Cannot clear malformed BEQ slot %d for %s",
                        self.log_id, index, channel,
                    )
                    return None
                ops.extend([
                    {"op": "replace", "path": self._get_peq_path(index, channel, "Fc"), "value": 100},
                    {"op": "replace", "path": self._get_peq_path(index, channel, "gaindB"), "value": 0},
                    {"op": "replace", "path": self._get_peq_path(index, channel, "Q"), "value": 1},
                    {"op": "replace", "path": self._get_peq_path(index, channel, "FilterType"), "value": 0},
                    {"op": "remove", "path": self._get_peq_path(index, channel, "beq")},
                ])

        if "beqActive" in peq:
            ops.append({"op": "remove", "path": "/peq/beqActive"})
        return ops

    async def clear_beq(self) -> bool:
        """Clear BEQ-tagged bands, including tags left on legacy sub channels."""
        if self._dirac_commands_blocked():
            return False
        async with self._transaction_lock:
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked():
                return False
            ops = self._build_clear_beq_ops()
            if ops is None:
                return False
            return await self._execute_transaction_locked(ops)

    async def load_beq(self, title: str, filters: list[dict]) -> bool:
        """Refresh, preflight, apply, and verify one complete BEQ transaction."""
        if self._dirac_commands_blocked():
            return False
        async with self._transaction_lock:
            if self._dirac_commands_blocked():
                return False
            if not await self._refresh_state_locked():
                return False
            return await self._load_beq_locked(title, filters)

    async def _load_beq_locked(self, title: str, filters: list[dict]) -> bool:
        """Build and apply a BEQ transaction while holding the transaction lock."""
        if not self._state or not isinstance(title, str) or not title.strip():
            return False
        if not isinstance(filters, list) or not 1 <= len(filters) <= BEQ_SLOT_END + 1:
            return False

        # Match the HTP-1 BEQ application path: normalize numeric values to
        # the processor limits rather than rejecting imported catalogue data.
        normalized_filters: list[dict[str, Any]] = []
        for filt in filters:
            if not isinstance(filt, dict):
                return False
            filter_type = filt.get("type", "PeakingEQ")
            if not isinstance(filter_type, str) or filter_type not in FILTER_TYPE_MAP:
                return False
            values = {key: filt.get(key, default) for key, default in (
                ("freq", 100), ("gain", 0), ("q", 1)
            )}
            if any(
                not _is_finite_number(value)
                for value in values.values()
            ):
                return False
            normalized_filters.append({
                "type": filter_type,
                "freq": min(max(values["freq"], 10), 20000),
                "gain": min(max(values["gain"], -99), 20),
                "q": min(max(values["q"], 0.1), 10),
            })

        peq = self._state.get("peq")
        if not isinstance(peq, dict) or not isinstance(peq.get("peqsw"), bool):
            _LOG.warning("[%s] BEQ requires a valid PEQ state", self.log_id)
            return False
        slots = peq.get("slots")
        if not isinstance(slots, list) or not slots:
            return False
        if peq.get("location") != "pre":
            _LOG.warning(
                "[%s] BEQ requires PEQ Location to be Pre-Dirac; current placement is %r",
                self.log_id, peq.get("location"),
            )
            return False

        # BEQ must run before Dirac so it modifies the LFE channel signal,
        # not individual post-Dirac subwoofer outputs. In the HTP-1 MSO,
        # sub1 is the Pre-PEQ LFE path.
        sub_channels = self._get_sub_channels()

        next_slot = BEQ_SLOT_START
        filter_ops: list[dict[str, Any]] = []
        for filt in normalized_filters:
            last_slot = None
            for channel in sub_channels:
                slot_idx = self._find_empty_peq_slot(next_slot, channel)
                if slot_idx is None:
                    _LOG.warning(
                        "[%s] Not enough free PEQ slots for BEQ '%s' on %s (%d filters)",
                        self.log_id, title, channel, len(filters),
                    )
                    return False

                filter_ops.extend([
                    {"op": "replace", "path": self._get_peq_path(slot_idx, channel, "Fc"), "value": filt["freq"]},
                    {"op": "replace", "path": self._get_peq_path(slot_idx, channel, "gaindB"), "value": filt["gain"]},
                    {"op": "replace", "path": self._get_peq_path(slot_idx, channel, "Q"), "value": filt["q"]},
                    {"op": "replace", "path": self._get_peq_path(slot_idx, channel, "FilterType"), "value": FILTER_TYPE_MAP[filt["type"]]},
                    {"op": "add", "path": self._get_peq_path(slot_idx, channel, "beq"), "value": True},
                ])
                last_slot = slot_idx if last_slot is None else max(last_slot, slot_idx)
            if last_slot is None:
                return False
            next_slot = last_slot + 1

        clear_ops = self._build_clear_beq_ops()
        if clear_ops is None:
            return False

        # Send cleanup and replacement together. A preflight or send failure
        # no longer clears the current BEQ in a separate earlier transaction.
        ops = clear_ops + filter_ops + [
            {"op": "add", "path": "/peq/beqActive", "value": title.strip()},
            {"op": "replace", "path": "/peq/peqsw", "value": True},
        ]
        success = await self._execute_transaction_locked(ops)
        if success:
            self.beq_active = title.strip()
            _LOG.info("[%s] BEQ loaded: %s (%d filters)", self.log_id, title.strip(), len(filters))
        return success


def apply_json_patch(target: dict | list, op: str, path_str: str, value: Any = None) -> None:
    """Apply a single JSON patch operation to a target dictionary/list."""
    if not isinstance(target, (dict, list)) or not isinstance(path_str, str) or not path_str.startswith("/"):
        raise ValueError("Invalid JSON patch target or path")
    if op not in ("add", "remove", "replace"):
        raise ValueError(f"Unsupported JSON patch operation: {op}")

    raw_path = path_str[1:].split("/")
    path = []
    for segment in raw_path:
        if re.search(r"~(?![01])", segment):
            raise ValueError("Invalid JSON pointer escape")
        path.append(segment.replace("~1", "/").replace("~0", "~"))
    final_key = path.pop()

    current = target
    for node in path:
        if isinstance(current, list):
            if not node.isdecimal():
                raise ValueError("Invalid JSON patch array index")
            index = int(node)
            if index >= len(current):
                raise IndexError("JSON patch array index out of range")
            current = current[index]
        elif isinstance(current, dict):
            current = current[node]
        else:
            raise TypeError("JSON patch path traverses a scalar value")

    if isinstance(current, list):
        if op == "add" and final_key == "-":
            current.append(value)
            return
        if not final_key.isdecimal():
            raise ValueError("Invalid JSON patch array index")
        index = int(final_key)
        if op == "add":
            if index > len(current):
                raise IndexError("JSON patch insertion index out of range")
            current.insert(index, value)
        elif index >= len(current):
            raise IndexError("JSON patch array index out of range")
        elif op == "remove":
            del current[index]
        else:
            current[index] = value
    elif isinstance(current, dict):
        if op == "remove":
            current.pop(final_key, None)
        elif op == "replace":
            if final_key not in current:
                raise KeyError(final_key)
            current[final_key] = value
        else:
            current[final_key] = value
    else:
        raise TypeError("JSON patch target is a scalar value")


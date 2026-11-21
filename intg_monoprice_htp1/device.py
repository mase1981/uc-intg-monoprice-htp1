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
import re
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


async def probe_htp1(host: str, timeout: float = PROBE_TIMEOUT) -> str | None:
    """Open a WebSocket to the HTP-1, request its state and close again.

    :return: None on success, otherwise a short human readable error.
    """
    url = f"ws://{host}/ws/controller"

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
        return f"No response from {host} within {int(timeout)} seconds"
    except Exception as err:  # pylint: disable=broad-exception-caught
        return f"Could not connect to {host}: {err}"


class HTP1Device(WebSocketDevice):
    """Monoprice HTP-1 implementation using WebSocketDevice."""

    def __init__(self, device_config: HTP1Config, **kwargs):
        # Keepalive is done by the websockets library (see create_websocket), which
        # closes a dead connection so recv() returns. The framework ping loop would
        # only call a no-op send_ping, so it is disabled.
        super().__init__(device_config, reconnect=True, ping_interval=0, **kwargs)
        self._device_config = device_config
        self._state: dict[str, Any] | None = None
        self._state_ready = asyncio.Event()
        self._ws: Any | None = None

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
        self._state = None
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
        self._state = None
        self._state_ready.clear()
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
        return f"ws://{self._device_config.host}/ws/controller"

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
        self._state = None
        self._state_ready.clear()
        self._sensor_data = {}
        self.push_update()

    async def handle_message(self, message: str) -> None:
        if " " not in message:
            return

        cmd, payload = message.split(" ", 1)
        
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as err:
            _LOG.error("[%s] Failed to decode JSON payload: %s", self.log_id, err)
            return

        if cmd == "mso":
            if not isinstance(data, dict):
                _LOG.error("[%s] Ignoring invalid full-state payload (%s)", self.log_id, type(data).__name__)
                return
            self._state = data
            self._state_ready.set()
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
                await self.send_message("getmso")
                return

            self._state = candidate_state
            self._parse_state()
            self.push_update()

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
        self.ss_trim = _number(preset.get("trim"), 0)

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
            if info.get("visible") is True:
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
        self.dirac_slot_name = "Dirac Off"
        slots = _as_list(cal.get("slots"))
        for idx, slot in enumerate(slots):
            if not isinstance(slot, dict) or slot.get("valid") is not True:
                continue
            name = slot.get("name", "")
            if isinstance(name, str) and name:
                self.slot_names.append(name)
                self._slot_index.setdefault(name, idx)
        slot_idx = cal.get("currentdiracslot", 0)
        if dirac_status == "on" and isinstance(slot_idx, int) and not isinstance(slot_idx, bool):
            if 0 <= slot_idx < len(slots):
                name = _as_dict(slots[slot_idx]).get("name", "Unknown")
                self.dirac_slot_name = name if isinstance(name, str) else "Unknown"
        elif dirac_status == "bypass":
            self.dirac_slot_name = "Dirac Bypass"

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
            "dirac_slot": self.dirac_slot_name,
            "video_mode": getattr(self, "video_mode", "-----"),
            "connection": "Connected" if self.is_connected else "Disconnected",
            "beq_active": self.beq_active or "None",
        }

    async def send_message(self, message: str) -> bool:
        try:
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

    async def _send_transaction(self, operations: list[dict[str, Any]]) -> bool:
        payload = json.dumps(operations, separators=(",", ":"))
        return await self.send_message(f"changemso {payload}")

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
        _LOG.info("[%s] Toggling power", self.log_id)
        new_state = not self.power
        return await self._send_transaction([
            {"op": "replace", "path": "/powerIsOn", "value": new_state}
        ])  

    async def set_volume(self, volume: int) -> bool:
        if (
            isinstance(volume, bool)
            or not isinstance(volume, int)
            or not _is_finite_number(self.vpl)
            or not _is_finite_number(self.vph)
            or self.vpl > self.vph
            or not self.vpl <= volume <= self.vph
        ):
            _LOG.warning("[%s] Rejecting invalid volume value: %r", self.log_id, volume)
            return False
        _LOG.info("[%s] Setting volume to %d", self.log_id, volume)
        return await self._send_transaction([
            {"op": "replace", "path": "/volume", "value": volume}
        ])

    async def set_volume_level(self, level: float) -> bool:
        if isinstance(level, bool) or not _is_finite_number(level) or not self._state or "cal" not in self._state:
            return False

        span = self.vph - self.vpl
        if span <= 0:
            return False

        level = max(0.0, min(1.0, level))
        target_db = int(round(self.vpl + (level * span)))
        target_db = max(int(self.vpl), min(int(self.vph), target_db))

        current_volume = self._state.get("volume", 0)
        volume_delta = abs(target_db - current_volume)
        max_safe_jump = 5

        if volume_delta > max_safe_jump:
            if target_db > current_volume:
                target_db = current_volume + max_safe_jump
            else:
                target_db = current_volume - max_safe_jump
            _LOG.warning("[%s] Volume jump clamped to %d dB", self.log_id, target_db)

        return await self.set_volume(target_db)

    async def volume_up(self) -> bool:
        if not self._state:
            return False
        current = self._state.get("volume", 0)
        if current >= self.vph:
            return True
        return await self.set_volume(current + 1)

    async def volume_down(self) -> bool:
        if not self._state:
            return False
        current = self._state.get("volume", 0)
        limit = self.vpl - self.zp
        if current - self.zp <= limit:
            return True
        return await self.set_volume(current - 1)

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
        if isinstance(trim, bool) or not isinstance(trim, int):
            return False
        _LOG.info("[%s] Setting seat shaker trim to %d", self.log_id, trim)
        return await self._send_transaction([
            {"op": "replace", "path": "/shaker/trim", "value": trim}
        ])

    async def select_source(self, source: str) -> bool:
        if not isinstance(source, str) or not source.strip() or not self._state:
            return False
        _LOG.info("[%s] Selecting source: %s", self.log_id, source)
        inputs = _as_dict(self._state.get("inputs"))
        for inp_id, inp_info in inputs.items():
            if isinstance(inp_info, dict) and inp_info.get("label") == source:
                return await self._send_transaction([
                    {"op": "replace", "path": "/input", "value": inp_id}
                ])
        _LOG.warning("[%s] Source not found: %s", self.log_id, source)
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
        _LOG.info("[%s] Selecting seat shaker preset: %d", self.log_id, preset_index)
        if not self._state or "shaker" not in self._state or not 0 <= preset_index <= 5:
            return False
        return await self._send_transaction([
            {"op": "replace", "path": "/shaker/activePreset", "value": preset_index}
        ])

    async def select_calibration(self, slot_name: str) -> bool:
        _LOG.info("[%s] Selecting calibration: %s", self.log_id, slot_name)
        slot_idx = self._slot_index.get(slot_name)
        if slot_idx is None:
            return False
        return await self._send_transaction([
            {"op": "replace", "path": "/cal/currentdiracslot", "value": slot_idx}
        ])

    async def send_command(self, command: str) -> bool:
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
                async with session.get(f"http://{self.address}/ircmd?code={command}", timeout=5) as response:
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
        """Return the subwoofer PEQ channels used by the established BEQ path.

        The HTP-1's Pre placement targets Sub1 only. Other PEQ placements
        target each configured subwoofer output, matching the original
        Python integration behavior.
        """
        if not self._state:
            return ["sub1"]

        peq = _as_dict(self._state.get("peq"))
        if peq.get("location") == "pre":
            return ["sub1"]

        speakers = _as_dict(self._state.get("speakers"))
        groups = _as_dict(speakers.get("groups"))
        channels = [
            key
            for key, value in groups.items()
            if isinstance(key, str)
            and key.startswith("sub")
            and isinstance(value, dict)
            and value.get("present") is True
        ]
        return channels or ["sub1"]

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
        ops = self._build_clear_beq_ops()
        if ops is None:
            return False
        return await self._send_transaction(ops) if ops else True

    async def load_beq(self, title: str, filters: list[dict]) -> bool:
        """Load a BEQ using available PEQ bands without disturbing it on preflight failure."""
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

        sub_channels = self._get_sub_channels()
        if not sub_channels:
            return False

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
        success = await self._send_transaction(ops)
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


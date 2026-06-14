"""
Monoprice HTP-1 device implementation for Unfolded Circle integration.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp
import websockets
from websockets.client import WebSocketClientProtocol

from ucapi_framework import WebSocketDevice, DeviceEvents
from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.displayvalues import sound_mode_display_values, sound_mode_native_values

_LOG = logging.getLogger(__name__)

FILTER_TYPE_MAP = {"PeakingEQ": 0, "LowShelf": 1, "HighShelf": 2}
BEQ_SLOT_START = 0
BEQ_SLOT_END = 15


def apply_json_patch(target: dict | list, op: str, path_str: str, value: Any = None) -> None:
    """Apply a single JSON patch operation to a target dictionary/list."""
    if not path_str.startswith("/"):
        return
        
    path = path_str[1:].split("/")
    final_key = path.pop()

    current = target
    for node in path:
        if isinstance(current, list):
            node = int(node)
        current = current[node]

    if op == "remove":
        if isinstance(current, dict):
            current.pop(final_key, None)
        elif isinstance(current, list):
            del current[int(final_key)]
    elif op in ("add", "replace"):
        if isinstance(current, list):
            current[int(final_key)] = value
        else:
            current[final_key] = value


class HTP1Device(WebSocketDevice):
    """Monoprice HTP-1 implementation using WebSocketDevice."""

    def __init__(self, device_config: HTP1Config, **kwargs):
        super().__init__(device_config, reconnect=True, ping_interval=30, **kwargs)
        self._device_config = device_config
        self._state: dict[str, Any] | None = None
        self._state_ready = asyncio.Event()
        self._ws: WebSocketClientProtocol | None = None

        self.events.on(DeviceEvents.CONNECTED, self._on_connected)
        self.events.on(DeviceEvents.DISCONNECTED, self._on_disconnected)

        self._sensor_data: dict[str, str] = {}
        self.current_source: str = ""
        self.source_list: list[str] = []
        self.slot_names: list[str] = []
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

        if os.getenv("INVOCATION_ID"):
            _LOG.info("[%s] Running On Remote", self.log_id)
        else:
            _LOG.info("[%s] Not Running on Remote Pre-fetch BEQ Catalogue", self.log_id)
            asyncio.create_task(self._prefetch_beq_catalogue())

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

    async def create_websocket(self) -> WebSocketClientProtocol:
        _LOG.info("[%s] Creating WebSocket connection to %s", self.log_id, self.websocket_url)
        logging.getLogger("websockets").setLevel(logging.INFO)
        self._ws = await websockets.connect(
            self.websocket_url,
            ping_interval=None,
            close_timeout=5,
        )
        return self._ws

    async def close_websocket(self) -> None:
        if self._ws:
            await self._ws.close()

    async def receive_message(self) -> str | None:
        if not self._ws:
            return None
        try:
            message = await self._ws.recv()
            return message if isinstance(message, str) else None
        except websockets.ConnectionClosed:
            _LOG.debug("[%s] WebSocket connection closed by remote", self.log_id)
            return None
        except Exception as err:
            _LOG.error("[%s] Error receiving message: %s", self.log_id, err)
            return None

    async def handle_message(self, message: str) -> None:
        if self._state is None:
            _LOG.info("[%s] First message received, requesting initial state", self.log_id)
            await self.send_message("getmso")

        if " " not in message:
            return

        cmd, payload = message.split(" ", 1)
        
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as err:
            _LOG.error("[%s] Failed to decode JSON payload: %s", self.log_id, err)
            return

        if cmd == "mso":
            self._state = data
            self._state_ready.set()
            _LOG.debug("[%s] Received full state", self.log_id)
            self._parse_state()
            self.push_update()

        elif cmd == "msoupdate":
            if not isinstance(data, list):
                data = [data]

            for piece in data:
                op = piece.get("op")
                path_str = piece.get("path", "")
                value = piece.get("value")
                
                try:
                    apply_json_patch(self._state, op, path_str, value)
                except (KeyError, IndexError, TypeError) as err:
                    _LOG.error("[%s] Failed to apply JSON patch %s: %s", self.log_id, piece, err)

            self._parse_state()
            self.push_update()

    def _parse_state(self) -> None:
        """Orchestrate the parsing of the HTP-1 state dictionary."""
        if not self._state:
            return

        self._parse_power_and_volume()
        self._parse_seat_shakers()
        self._parse_inputs()
        self._parse_audio_and_dirac()
        self._parse_video_stats()
        self._update_sensor_data()

    def _parse_power_and_volume(self) -> None:
        self.power = self._state.get("powerIsOn", False)
        self.muted = self._state.get("muted", False)

        volume = self._state.get("volume", 0)
        cal = self._state.get("cal", {})
        
        self.zp = cal.get("zeroPoint", 0)
        self.vpl = cal.get("vpl", -80)
        self.vph = cal.get("vph", 12)
        
        self.volume_db = volume - self.zp

    def _parse_seat_shakers(self) -> None:
        shaker = self._state.get("shaker", {})
        if not shaker:
            self.ss_mute = "off"
            self.ss_preset = 0
            self.ss_trim = 0
            return

        self.ss_mute = shaker.get("mute", "off")
        self.ss_preset = shaker.get("activePreset", 0) + 1
        
        presets = shaker.get("presets", {})
        active_preset_str = str(shaker.get("activePreset", ""))
        current_preset = presets.get(active_preset_str, {})
        
        self.ss_trim = current_preset.get("trim", 0)

    def _parse_inputs(self) -> None:
        input_id = self._state.get("input", "")
        self.source_list = []
        self.current_source = ""
        
        inputs = self._state.get("inputs", {})
        for inp_id, inp_info in inputs.items():
            if not isinstance(inp_info, dict):
                continue
                
            label = inp_info.get("label", inp_id)
            if inp_info.get("visible", False):
                self.source_list.append(label)
                
            if inp_id == input_id:
                self.current_source = label

    def _parse_audio_and_dirac(self) -> None:
        audio_info = self._state.get("status", {})
        codec = audio_info.get("DECSourceProgram", "")
        channels = audio_info.get("DECProgramFormat", "")
        self.audio_format = f"{channels} {codec}".strip() if codec else (channels or "none")

        output_codec = audio_info.get("SurroundMode", "")
        output_channels = audio_info.get("ENCListeningFormat", "")
        self.output_audio_format = f"{output_channels} {output_codec}".strip() if output_codec else output_channels

        upmix = self._state.get("upmix", {})
        self.surround_mode = upmix.get("select", "")
        self.sound_mode_display = sound_mode_display_values.get(self.surround_mode, self.surround_mode)

        cal = self._state.get("cal", {})
        dirac_status = cal.get("diracactive", False)
        self.slot_names = []
        self.dirac_slot_name = "Dirac Off"
        
        slots = cal.get("slots", [])
        for slot in slots:
            if isinstance(slot, dict) and slot.get("valid", False):
                self.slot_names.append(slot.get("name", ""))

        if dirac_status == "on":
            slot_idx = cal.get("currentdiracslot", 0)
            if slots and 0 <= slot_idx < len(slots):
                self.dirac_slot_name = slots[slot_idx].get("name", "Unknown")
        elif dirac_status == "bypass":
            self.dirac_slot_name = "Dirac Bypass"

    def _parse_video_stats(self) -> None:
        vi = self._state.get("videostat", {})
        parts = [
            vi.get("VideoResolution", ""),
            vi.get("HDRstatus", ""),
            vi.get("VideoColorSpace", ""),
            vi.get("VideoMode", ""),
            vi.get("VideoBitDepth", "")
        ]
        self.video_mode = " ".join(p for p in parts if p) or "-----"

    def _update_sensor_data(self) -> None:
        loudness_state = self._state.get("loudness", "off")
        night_mode_state = self._state.get("night", "off")
        
        peq = self._state.get("peq", {})
        peq_sw = peq.get("peqsw", False)
        self.beq_active = peq.get("beqActive", "")

        self._sensor_data = {
            "input": self.current_source,
            "volume": str(self.volume_db),
            "mute": "On" if self.muted else "Off",
            "ss_trim": str(self.ss_trim),
            "ss_mute": self.ss_mute.capitalize(),
            "ss_preset": self.ss_preset,
            "loudness": str(loudness_state).capitalize() if isinstance(loudness_state, str) else ("On" if loudness_state else "Off"),
            "night_mode": str(night_mode_state).capitalize() if isinstance(night_mode_state, str) else ("On" if night_mode_state else "Off"),
            "peq": "On" if peq_sw else "Off",
            "sound_mode": self.sound_mode_display,
            "audio_format": getattr(self, 'audio_format', 'none'),
            "output_audio_format": getattr(self, 'output_audio_format', ''),
            "dirac_slot": self.dirac_slot_name,
            "video_mode": self.video_mode,
            "connection": "Connected" if self.is_connected else "Disconnected",
            "beq_active": self.beq_active or "None",
        }

    @staticmethod
    async def _prefetch_beq_catalogue() -> None:
        from intg_monoprice_htp1.browser import prefetch_catalogue, start_refresh_loop
        await prefetch_catalogue()
        # await start_refresh_loop()

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
        _LOG.info("[%s] Setting volume to %d", self.log_id, volume)
        return await self._send_transaction([
            {"op": "replace", "path": "/volume", "value": volume}
        ])

    async def set_volume_level(self, level: float) -> bool:
        if not self._state or "cal" not in self._state:
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
        _LOG.info("[%s] Setting mute to %s", self.log_id, muted)
        return await self._send_transaction([
            {"op": "replace", "path": "/muted", "value": muted}
        ])

    async def ss_mute_toggle(self, muted: bool) -> bool:
        _LOG.info("[%s] Setting seat shaker mute to %s", self.log_id, muted)
        status = "on" if self.ss_mute == "off" else "off"
        return await self._send_transaction([
            {"op": "replace", "path": "/shaker/mute", "value": status}
        ])
    
    async def set_ss_trim(self, trim: int) -> bool:
        _LOG.info("[%s] Setting seat shaker trim to %d", self.log_id, trim)
        return await self._send_transaction([
            {"op": "replace", "path": "/shaker/trim", "value": trim}
        ])

    async def select_source(self, source: str) -> bool:
        _LOG.info("[%s] Selecting source: %s", self.log_id, source)
        if not self._state or "inputs" not in self._state:
            return False
        for inp_id, inp_info in self._state["inputs"].items():
            if inp_info.get("label") == source:
                return await self._send_transaction([
                    {"op": "replace", "path": "/input", "value": inp_id}
                ])
        _LOG.warning("[%s] Source not found: %s", self.log_id, source)
        return False

    async def select_sound_mode(self, sound_mode: str) -> bool:
        _LOG.info("[%s] Selecting sound mode: %s", self.log_id, sound_mode)
        native = sound_mode_native_values.get(sound_mode, sound_mode)
        return await self._send_transaction([
            {"op": "replace", "path": "/upmix/select", "value": native}
        ])
    
    async def select_ss_preset(self, preset_index: int) -> bool:
        _LOG.info("[%s] Selecting seat shaker preset: %d", self.log_id, preset_index)
        if not self._state or "shaker" not in self._state:
            return False
        return await self._send_transaction([
            {"op": "replace", "path": "/shaker/activePreset", "value": preset_index}
        ])

    async def select_calibration(self, slot_name: str) -> bool:
        _LOG.info("[%s] Selecting calibration: %s", self.log_id, slot_name)
        if slot_name not in self.slot_names:
            return False
        return await self._send_transaction([
            {"op": "replace", "path": "/cal/currentdiracslot", "value": self.slot_names.index(slot_name)}
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
        if not self._state:
            return ["sub1"]

        peq_location = self._state.get("peq", {}).get("location", "")
        if peq_location == "pre":
            return ["sub1"]

        speakers = self._state.get("speakers", {}).get("groups", {})
        subs = []
        for key, val in speakers.items():
            if key.startswith("sub") and isinstance(val, dict):
                if val.get("present", False):
                    subs.append(key)
        return subs or ["sub1"]

    def _find_empty_peq_slot(self, start_slot: int = BEQ_SLOT_START, ch: str | None = None) -> int | None:
        if not self._state:
            return None
        peq = self._state.get("peq", {})
        slots = peq.get("slots", [])

        for i in range(start_slot, min(BEQ_SLOT_END + 1, len(slots))):
            channels = slots[i].get("channels", {})
            ch_data = channels.get(ch, {})
            if ch_data.get("gaindB", 0) == 0 or ch_data.get("beq"):
                return i
        return None

    def _get_peq_path(self, slot_idx: int, channel: str, attribute: str) -> str:
        """Helper to construct the JSON path for PEQ modifications."""
        return f"/peq/slots/{slot_idx}/channels/{channel}/{attribute}"

    async def clear_beq(self) -> bool:
        """Clear all BEQ-tagged filters from all PEQ slots on all sub channels."""
        if not self._state:
            return False
        ops: list[dict] = []
        peq = self._state.get("peq", {})
        slots = peq.get("slots", [])
        all_subs = self._get_sub_channels()

        for i in range(min(16, len(slots))):
            channels = slots[i].get("channels", {})
            for ch in all_subs:
                ch_data = channels.get(ch, {})
                if ch_data.get("beq"):
                    ops.extend([
                        {"op": "replace", "path": self._get_peq_path(i, ch, "Fc"), "value": 100},
                        {"op": "replace", "path": self._get_peq_path(i, ch, "gaindB"), "value": 0},
                        {"op": "replace", "path": self._get_peq_path(i, ch, "Q"), "value": 1},
                        {"op": "replace", "path": self._get_peq_path(i, ch, "FilterType"), "value": 0},
                        {"op": "remove", "path": self._get_peq_path(i, ch, "beq")},
                    ])

        if "beqActive" in peq:
            ops.append({"op": "remove", "path": "/peq/beqActive"})

        if ops:
            return await self._send_transaction(ops)
        return True

    async def load_beq(self, title: str, filters: list[dict]) -> bool:
        if not self._state:
            return False

        await self.clear_beq()

        sub_channels = self._get_sub_channels()
        if not sub_channels:
            return False

        ops = []
        next_slot = BEQ_SLOT_START

        for filt in filters:
            ft = FILTER_TYPE_MAP.get(filt.get("type", "PeakingEQ"), 0)
            freq = filt.get("freq", 100)
            gain = filt.get("gain", 0)
            q = filt.get("q", 1)

            for ch in sub_channels:
                slot_idx = self._find_empty_peq_slot(next_slot, ch)
                if slot_idx is None:
                    _LOG.warning("[%s] No empty PEQ slot for BEQ filter", self.log_id)
                    break

                ops.extend([
                    {"op": "replace", "path": self._get_peq_path(slot_idx, ch, "Fc"), "value": freq},
                    {"op": "replace", "path": self._get_peq_path(slot_idx, ch, "gaindB"), "value": gain},
                    {"op": "replace", "path": self._get_peq_path(slot_idx, ch, "Q"), "value": q},
                    {"op": "replace", "path": self._get_peq_path(slot_idx, ch, "FilterType"), "value": ft},
                    {"op": "add", "path": self._get_peq_path(slot_idx, ch, "beq"), "value": True},
                ])
            next_slot = slot_idx + 1

        ops.extend([
            {"op": "add", "path": "/peq/beqActive", "value": title},
            {"op": "replace", "path": "/peq/peqsw", "value": True},
        ])

        success = await self._send_transaction(ops)
        if success:
            self.beq_active = title
            _LOG.info("[%s] BEQ loaded: %s (%d filters)", self.log_id, title, len(filters))
        return success
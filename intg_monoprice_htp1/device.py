"""
Monoprice HTP-1 device implementation for Unfolded Circle integration.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio
import json
import logging
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

    async def create_websocket(self) -> WebSocketClientProtocol:
        _LOG.info(
            "[%s] Creating WebSocket connection to %s", self.log_id, self.websocket_url
        )
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
            return None
        except Exception as err:
            _LOG.error("[%s] Error receiving message: %s", self.log_id, err)
            return None

    async def handle_message(self, message: str) -> None:
        if self._state is None:
            _LOG.info("[%s] First message received, requesting initial state", self.log_id)
            await self.send_message("getmso")

        try:
            if " " not in message:
                return

            cmd, payload = message.split(" ", 1)
            data = json.loads(payload)

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
                    path = piece.get("path", "")[1:].split("/")
                    target = self._state
                    final = path.pop()

                    if op == "remove":
                        for node in path:
                            if isinstance(target, list):
                                node = int(node)
                            target = target[node]
                        if isinstance(target, dict):
                            target.pop(final, None)
                        elif isinstance(target, list):
                            del target[int(final)]
                        continue

                    if op not in ("add", "replace"):
                        continue

                    for node in path:
                        if isinstance(target, list):
                            node = int(node)
                        target = target[node]

                    value = piece.get("value")
                    target[final] = value

                self._parse_state()
                self.push_update()

        except Exception as err:
            _LOG.error("[%s] Message processing error: %s", self.log_id, err)

    def _parse_state(self) -> None:
        if not self._state:
            return

        self.power = self._state.get("powerIsOn", False)
        self.muted = self._state.get("muted", False)

        volume = self._state.get("volume", 0)
        if "cal" in self._state:
            cal = self._state["cal"]
            self.zp = cal.get("zeroPoint", 0)
            self.vpl = cal.get("vpl", -80)
            self.vph = cal.get("vph", 12)
            volume -= self.zp
        self.volume_db = volume

        input_id = self._state.get("input")
        source_list = []
        source = ""
        if "inputs" in self._state:
            for inp_id, inp_info in self._state["inputs"].items():
                if inp_info.get("visible"):
                    source_list.append(inp_info.get("label", inp_id))
                if inp_id == input_id:
                    source = inp_info.get("label", inp_id)
        self.current_source = source
        self.source_list = source_list

        loudness_state = self._state.get("loudness", "off")
        night_mode_state = self._state.get("night", "off")
        peq_sw = self._state.get("peq", {}).get("peqsw", False)
        self.beq_active = self._state.get("peq", {}).get("beqActive", "")

        sound_mode = ""
        if "upmix" in self._state:
            sound_mode = self._state["upmix"].get("select", "")
        self.surround_mode = sound_mode
        self.sound_mode_display = sound_mode_display_values.get(sound_mode, sound_mode)

        audio_format = "none"
        if "status" in self._state:
            audio_info = self._state["status"]
            codec = audio_info.get("DECSourceProgram", "")
            channels = audio_info.get("DECProgramFormat", "")
            if channels:
                audio_format = f"{channels} {codec}".strip() if codec else channels

        output_audio_format = ""
        if "status" in self._state:
            output_info = self._state["status"]
            output_codec = output_info.get("SurroundMode", "")
            output_channels = output_info.get("ENCListeningFormat", "")
            if output_channels:
                output_audio_format = f"{output_channels} {output_codec}".strip() if output_codec else output_channels

        dirac_slot_name = "None"
        available_slots = []
        if "cal" in self._state:
            cal = self._state["cal"]
            dirac_status = cal.get("diracactive", False)
            if dirac_status == "on":
                slot_idx = cal.get("currentdiracslot", 0)
                slots = cal.get("slots", [])
                if slots and slot_idx < len(slots):
                    dirac_slot_name = slots[slot_idx].get("name", "")
            elif dirac_status == "bypass":
                dirac_slot_name = "Dirac Bypass"
            else:
                dirac_slot_name = "Dirac Off"
            for slot in cal.get("slots", []):
                if slot.get("valid", False):
                    available_slots.append(slot.get("name", ""))
        self.dirac_slot_name = dirac_slot_name
        self.slot_names = available_slots

        video_mode = "-----"
        if "videostat" in self._state:
            vi = self._state["videostat"]
            parts = [vi.get("VideoResolution", "")]
            if vi.get("HDRstatus"):
                parts.append(vi["HDRstatus"])
            if vi.get("VideoColorSpace"):
                parts.append(vi["VideoColorSpace"])
            if vi.get("VideoMode"):
                parts.append(vi["VideoMode"])
            if vi.get("VideoBitDepth"):
                parts.append(vi["VideoBitDepth"])
            video_mode = " ".join(p for p in parts if p) or "-----"

        self._sensor_data = {
            "input": source,
            "volume": f"{self.volume_db}",
            "mute": "On" if self.muted else "Off",
            "loudness": str(loudness_state).capitalize() if isinstance(loudness_state, str) else ("On" if loudness_state else "Off"),
            "night_mode": str(night_mode_state).capitalize() if isinstance(night_mode_state, str) else ("On" if night_mode_state else "Off"),
            "peq": "On" if peq_sw else "Off",
            "sound_mode": self.sound_mode_display,
            "audio_format": audio_format,
            "output_audio_format": output_audio_format,
            "dirac_slot": dirac_slot_name,
            "video_mode": video_mode,
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
                async with session.get(f"http://{self.address}/ircmd?code={command}") as response:
                    return response.status == 200
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
                        {"op": "replace", "path": f"/peq/slots/{i}/channels/{ch}/Fc", "value": 100},
                        {"op": "replace", "path": f"/peq/slots/{i}/channels/{ch}/gaindB", "value": 0},
                        {"op": "replace", "path": f"/peq/slots/{i}/channels/{ch}/Q", "value": 1},
                        {"op": "replace", "path": f"/peq/slots/{i}/channels/{ch}/FilterType", "value": 0},
                        {"op": "remove", "path": f"/peq/slots/{i}/channels/{ch}/beq"},
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
                    {"op": "replace", "path": f"/peq/slots/{slot_idx}/channels/{ch}/Fc", "value": freq},
                    {"op": "replace", "path": f"/peq/slots/{slot_idx}/channels/{ch}/gaindB", "value": gain},
                    {"op": "replace", "path": f"/peq/slots/{slot_idx}/channels/{ch}/Q", "value": q},
                    {"op": "replace", "path": f"/peq/slots/{slot_idx}/channels/{ch}/FilterType", "value": ft},
                    {"op": "add", "path": f"/peq/slots/{slot_idx}/channels/{ch}/beq", "value": True},
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

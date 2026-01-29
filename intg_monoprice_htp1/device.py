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
from ucapi.media_player import Attributes as MediaAttributes, States as MediaStates
from ucapi.sensor import Attributes as SensorAttributes
from ucapi.remote import Attributes as RemoteAttributes
from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.displayvalues import sound_mode_display_values 


_LOG = logging.getLogger(__name__)

class HTP1Device(WebSocketDevice):
    """Monoprice HTP-1 implementation using WebSocketDevice."""

    def __init__(self, device_config: HTP1Config, **kwargs):
        super().__init__(device_config, reconnect=True, ping_interval=30, **kwargs)
        self._device_config = device_config
        self._state: dict[str, Any] | None = None
        self._state_ready = asyncio.Event()
        self._ws: WebSocketClientProtocol | None = None

        # Listen for connection events
        self.events.on(DeviceEvents.CONNECTED, self._on_connected)
        self.events.on(DeviceEvents.DISCONNECTED, self._on_disconnected)

    async def _on_connected(self, identifier: str) -> None:
        """Handle connection established."""
        _LOG.info("[%s] WebSocket connected", self.log_id)
        self._state = None
        self._state_ready.clear()

        # Request initial state
        await asyncio.sleep(0.1)  # Small delay to ensure connection is stable
        await self.send_message("getmso")

        # Wait for initial state with timeout
        try:
            await asyncio.wait_for(self._state_ready.wait(), timeout=5.0)
            _LOG.info("[%s] Initial state received", self.log_id)
            self._emit_update()
        except asyncio.TimeoutError:
            _LOG.warning("[%s] Timeout waiting for initial state", self.log_id)

    async def _on_disconnected(self, identifier: str) -> None:
        """Handle disconnection."""
        _LOG.info("[%s] WebSocket disconnected", self.log_id)
        self._state = None
        self._state_ready.clear()
        self._emit_update()

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
        """Return the WebSocket URL."""
        return f"ws://{self._device_config.host}/ws/controller"

    async def create_websocket(self) -> WebSocketClientProtocol:
        """Create WebSocket connection."""
        _LOG.info("[%s] Creating WebSocket connection to %s", self.log_id, self.websocket_url)
        self._ws = await websockets.connect(
            self.websocket_url,
            ping_interval=None,  # We handle pings ourselves
            close_timeout=5,
        )
        return self._ws

    async def close_websocket(self) -> None:
        """Close WebSocket connection."""
        if self._ws:
            await self._ws.close()

    async def receive_message(self) -> str | None:
        """Receive message from WebSocket."""
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
        """Process incoming WebSocket message."""
        # On first message, request initial state if we don't have it
        if self._state is None:
            _LOG.info("[%s] First message received, requesting initial state", self.log_id)
            await self.send_message("getmso")


        try:
            if " " not in message:
                return

            cmd, payload = message.split(" ", 1)
            data = json.loads(payload)

            if cmd == "mso":
                # Full state update
                self._state = data
                self._state_ready.set()
                _LOG.debug("[%s] Received full state", self.log_id)
                self._emit_update()

            elif cmd == "msoupdate":
                # Incremental state update
                if not isinstance(data, list):
                    data = [data]

                for piece in data:
                    op = piece.get("op")
                    path = piece.get("path", "")[1:].split("/")
                    target = self._state
                    final = path.pop()

                    if op not in ("add", "replace"):
                        continue

                    for node in path:
                        if isinstance(target, list):
                            node = int(node)
                        target = target[node]

                    value = piece.get("value")
                    target[final] = value

                self._emit_update()

        except Exception as err:
            _LOG.error("[%s] Message processing error: %s", self.log_id, err)

    def _emit_update(self) -> None:
        """Emit entity update events for all entities."""
        media_player_id = f"media_player.{self.identifier}"
        remote_id = f"remote.{self.identifier}"

        # Sensor IDs
        input_sensor_id = f"sensor.{self.identifier}_input"
        volume_sensor_id = f"sensor.{self.identifier}_volume"
        loudness_sensor_id = f"sensor.{self.identifier}_loudness"
        peq_sensor_id = f"sensor.{self.identifier}_peq"
        mute_sensor_id = f"sensor.{self.identifier}_mute"
        sound_mode_sensor_id = f"sensor.{self.identifier}_sound_mode"
        audio_format_sensor_id = f"sensor.{self.identifier}_audio_format"
        output_audio_format_sensor_id = f"sensor.{self.identifier}_output_audio_format"
        current_dirac_slot_name_sensor_id = f"sensor.{self.identifier}_current_dirac_slot_name"
        video_mode_sensor_id = f"sensor.{self.identifier}_video_mode"
        connection_sensor_id = f"sensor.{self.identifier}_connection"

        if not self._state or not self.is_connected:
            # Device unavailable - update all entities
            self.events.emit(
                DeviceEvents.UPDATE,
                media_player_id,
                {MediaAttributes.STATE: MediaStates.UNAVAILABLE}
            )
            self.events.emit(
                DeviceEvents.UPDATE,
                remote_id,
                {RemoteAttributes.STATE: "UNAVAILABLE"}
            )
            self.events.emit(
                DeviceEvents.UPDATE,
                connection_sensor_id,
                {
                    SensorAttributes.STATE: "Disconnected",
                    SensorAttributes.VALUE: "disconnected",
                }
            )
            return

        # Extract state information
        power = self._state.get("powerIsOn", False)
        volume = self._state.get("volume", 0)
        muted = self._state.get("muted", False)
        input_id = self._state.get("input")

        # Get input name
        source = None
        source_list = []
        if "inputs" in self._state:
            inputs = self._state["inputs"]
            for inp_id, inp_info in inputs.items():
                if inp_info.get("visible"):
                    source_list.append(inp_info.get("label", inp_id))
                if inp_id == input_id:
                    source = inp_info.get("label", inp_id)

        # Get Loudness state
        loudness_state = "off"
        if "loudness" in self._state:
            loudness_state = self._state["loudness"]

        # Get PEQ state
        peq_state = "off"
        if "peq" in self._state:
            peq_data = self._state["peq"]
            peq_state = peq_data.get("peqsw", "off")

        # Get sound mode (upmix)
        sound_mode = None
        sound_mode_list = []
        if "upmix" in self._state:
            upmix_data = self._state["upmix"]
            sound_mode = upmix_data.get("select")
            sound_mode_list = [
                k for k, v in upmix_data.items()
                if k != "select" and isinstance(v, dict) and v.get("homevis")
            ]

        # Get audio format
        audio_format = "none"
        if "status" in self._state:
            audio_info = self._state["status"]
            codec = audio_info.get("DECSourceProgram", "")
            channels = audio_info.get("DECProgramFormat", "")
            if channels:
                audio_format = f"{channels}"
                if codec:
                    audio_format += f" {codec}"

        # Get output audio format
        output_audio_format = ""
        if "status" in self._state:
            output_audio_info = self._state["status"]
            output_codec = output_audio_info.get("SurroundMode", "")
            output_channels = audio_info.get("ENCListeningFormat", "")
            if output_channels:
                output_audio_format = f"{output_channels}"
                if output_codec:
                    output_audio_format += f" {output_codec}"
        
        # Get current Calibration slot name
        current_dirac_slot = "None"
        current_dirac_slot_name = "None"
        if "cal" in self._state:
            cal = self._state["cal"]
            diracstatus = cal.get("diracactive", False)
            if diracstatus == "on":
                current_dirac_slot = cal.get("currentdiracslot", "")
                current_dirac_slot_name = cal.get("slots", "")[current_dirac_slot].get("name", "")
            elif diracstatus == "bypass":
                current_dirac_slot_name = "Dirac Bypass"
            else :
                current_dirac_slot_name = "Dirac Off"

        # Get valid slots
        available_slots = []
        if "cal" in self._state:
            cal = self._state["cal"]
            for slot in cal.get("slots", False):
                if slot.get("valid", False):
                    available_slots.append(slot)
        

            
        # Get video mode
        video_mode = "-----"
        if "videostat" in self._state:
            video_info = self._state["videostat"]
            resolution = video_info.get("VideoResolution", "")
            hdr = video_info.get("HDRstatus", "")
            colospace = video_info.get("VideoColorSpace", "")
            videomode = video_info.get("VideoMode", "")
            videobitdepth = video_info.get("VideoBitDepth", "")     

            if resolution:
                video_mode = resolution
                if hdr:
                    video_mode += f" {hdr}"
                if colospace:
                    video_mode += f" {colospace}"
                if videomode:
                    video_mode += f" {videomode}"
                if videobitdepth:
                    video_mode += f" {videobitdepth}"


        # Calculate volume level (0..1) based on calibration
        volume_level = None
        if "cal" in self._state:
            cal = self._state["cal"]
            vpl = cal.get("vpl", -80)
            vph = cal.get("vph", 12)
            span = vph - vpl
            if span > 0:
                volume_level = max(0.0, min(1.0, (volume - vpl) / span))

        state = MediaStates.ON if power else MediaStates.OFF

        # Update Media Player
        media_player_attrs = {
            MediaAttributes.STATE: state,
            MediaAttributes.VOLUME: volume,
            MediaAttributes.MUTED: muted,
            MediaAttributes.SOURCE: source,
            MediaAttributes.SOURCE_LIST: source_list,
        }

        if sound_mode:
            media_player_attrs[MediaAttributes.SOUND_MODE] = sound_mode
        if sound_mode_list:
            media_player_attrs[MediaAttributes.SOUND_MODE_LIST] = sound_mode_list

        _LOG.debug("[%s] Emitting update: %s", self.log_id, state)
        self.events.emit(DeviceEvents.UPDATE, media_player_id, media_player_attrs)

        # Update Remote
        remote_state = "ON" if power else "OFF"
        self.events.emit(
            DeviceEvents.UPDATE,
            remote_id,
            {RemoteAttributes.STATE: remote_state}
        )

        # Update Sensors
        # Input Sensor
        if source:
            self.events.emit(
                DeviceEvents.UPDATE,
                input_sensor_id,
                {
                    SensorAttributes.STATE: source,
                    SensorAttributes.VALUE: source,
                }
            )

        # Volume Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            volume_sensor_id,
            {
                SensorAttributes.STATE: f"{volume} dB",
                SensorAttributes.VALUE: volume,
            }
        )

        # Mute Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            mute_sensor_id,
            {
                SensorAttributes.STATE: "On" if muted else "Off",
                SensorAttributes.VALUE: "On" if muted else "Off",
            }
        )

        # Loudness Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            loudness_sensor_id,
            {
                SensorAttributes.STATE: loudness_state,
                SensorAttributes.VALUE: loudness_state.capitalize(),
            }
        )  

        # PEQ Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            peq_sensor_id,
            {
                 SensorAttributes.STATE: "On" if peq_state else "Off",
                SensorAttributes.VALUE: "On" if peq_state else "Off",
            }
        )  

        # Sound Mode Sensor
        if sound_mode:
            self.events.emit(
                DeviceEvents.UPDATE,
                sound_mode_sensor_id,
                {
                    SensorAttributes.STATE: sound_mode,
                    SensorAttributes.VALUE: sound_mode_display_values.get(sound_mode, sound_mode),
                }
            )

        # Audio Format Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            audio_format_sensor_id,
            {
                SensorAttributes.STATE: audio_format,
                SensorAttributes.VALUE: audio_format,
            }
        )

         # Output Audio Format Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            output_audio_format_sensor_id,
            {
                SensorAttributes.STATE: output_audio_format,
                SensorAttributes.VALUE: output_audio_format,
            }
        )

        # Current Dirac Slot Name Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            current_dirac_slot_name_sensor_id,
            {
                SensorAttributes.STATE: current_dirac_slot_name,
                SensorAttributes.VALUE: current_dirac_slot_name,
            }
        )

        # Video Mode Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            video_mode_sensor_id,
            {
                SensorAttributes.STATE: video_mode,
                SensorAttributes.VALUE: video_mode,
            }
        )

        # Connection Sensor
        self.events.emit(
            DeviceEvents.UPDATE,
            connection_sensor_id,
            {
                SensorAttributes.STATE: "Connected",
                SensorAttributes.VALUE: "connected",
            }
        )

    async def send_message(self, message: str) -> bool:
        """Send message via WebSocket."""
        try:
            if self._ws and self.is_connected:
                await self._ws.send(message)
                _LOG.debug("[%s] Sent: %s", self.log_id, message)
                return True
            return False
        except Exception as err:
            _LOG.error("[%s] Send error: %s", self.log_id, err)
            return False

    async def _send_transaction(self, operations: list[dict[str, Any]]) -> bool:
        """Send a transaction with multiple operations."""
        payload = json.dumps(operations, separators=(",", ":"))
        return await self.send_message(f"changemso {payload}")

    async def turn_on(self) -> bool:
        """Turn on the receiver."""
        _LOG.info("[%s] Turning on", self.log_id)
        return await self._send_transaction([
            {"op": "replace", "path": "/powerIsOn", "value": True}
        ])

    async def turn_off(self) -> bool:
        """Turn off the receiver."""
        _LOG.info("[%s] Turning off", self.log_id)
        return await self._send_transaction([
            {"op": "replace", "path": "/powerIsOn", "value": False}
        ])

    async def set_volume(self, volume: int) -> bool:
        """Set volume level (in dB)."""
        _LOG.info("[%s] Setting volume to %d", self.log_id, volume)
        return await self._send_transaction([
            {"op": "replace", "path": "/volume", "value": volume}
        ])

    async def set_volume_level(self, level: float) -> bool:
        """Set volume level (0..1) with safety protection against large jumps."""
        if not self._state or "cal" not in self._state:
            return False

        cal = self._state["cal"]
        vpl = cal.get("vpl", -80)
        vph = cal.get("vph", 12)
        span = vph - vpl

        if span <= 0:
            return False

        level = max(0.0, min(1.0, level))
        target_db = vpl + (level * span)
        target_db = int(round(target_db))
        target_db = max(int(vpl), min(int(vph), target_db))

        # Safety protection: prevent large volume jumps that could damage speakers
        current_volume = self._state.get("volume", 0)
        volume_delta = abs(target_db - current_volume)
        max_safe_jump = 5  # Maximum safe volume change in dB

        if volume_delta > max_safe_jump:
            # Clamp to safe incremental change
            if target_db > current_volume:
                clamped_db = current_volume + max_safe_jump
            else:
                clamped_db = current_volume - max_safe_jump

            _LOG.warning(
                "[%s] Volume jump protection: Requested change from %d dB to %d dB (%+d dB) exceeds safe limit. "
                "Clamping to %d dB (%+d dB) to prevent speaker damage.",
                self.log_id,
                current_volume,
                target_db,
                target_db - current_volume,
                clamped_db,
                clamped_db - current_volume
            )
            target_db = clamped_db

        return await self.set_volume(target_db)

    async def volume_up(self) -> bool:
        """Increase volume."""
        if not self._state:
            return False
        current = self._state.get("volume", 0)
        return await self.set_volume(current + 1)

    async def volume_down(self) -> bool:
        """Decrease volume."""
        if not self._state:
            return False
        current = self._state.get("volume", 0)
        return await self.set_volume(current - 1)

    async def mute(self, muted: bool) -> bool:
        """Set mute state."""
        _LOG.info("[%s] Setting mute to %s", self.log_id, muted)
        return await self._send_transaction([
            {"op": "replace", "path": "/muted", "value": muted}
        ])

    async def select_source(self, source: str) -> bool:
        """Select input source."""
        _LOG.info("[%s] Selecting source: %s", self.log_id, source)
        if not self._state or "inputs" not in self._state:
            return False

        # Find input ID by label
        for inp_id, inp_info in self._state["inputs"].items():
            if inp_info.get("label") == source:
                return await self._send_transaction([
                    {"op": "replace", "path": "/input", "value": inp_id}
                ])

        _LOG.warning("[%s] Source not found: %s", self.log_id, source)
        return False

    async def select_sound_mode(self, sound_mode: str) -> bool:
        """Select sound mode (upmix)."""
        _LOG.info("[%s] Selecting sound mode: %s", self.log_id, sound_mode)
        return await self._send_transaction([
            {"op": "replace", "path": "/upmix/select", "value": sound_mode}
        ])
        

    async def send_command(self, command: str) -> bool:
        """Send menu navigation command."""
        _LOG.info("[%s] Sending menu command: %s", self.log_id, command)

        # Map commands to HTP-1 menu operations
        # The HTP-1 uses a menu system accessible via the front panel
        # Commands are sent as button presses
        avcui_command_map = {
            "send_avcui: hpe": "send_avcui: hpe"
        }

        htp1_command = avcui_command_map.get(command)
        if not htp1_command:
                _LOG.warning("[%s] Unknown menu command: %s", self.log_id, command)
                return False
        # Send as a simple command (HTP-1 might use different protocol for menu)
        return await self.send_message(htp1_command)

    async def send_http_command(self, command: str) -> bool:
        """Send HTTP IR command."""
        _LOG.info("[%s] Sending http command: %s", self.log_id, command)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://{self.address}/ircmd?code={command}") as response:
                    return response.status == 200
        except Exception as err:
            _LOG.error("[%s] HTTP command error: %s", self.log_id, err)
            return False
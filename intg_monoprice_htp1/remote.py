"""
Monoprice HTP-1 Remote entity.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

import logging
from typing import Any
from ucapi import StatusCodes
from ucapi.ui import Buttons
from ucapi.remote import (
    Attributes,
    Commands,
    Features,
    Remote,

)
from intg_monoprice_htp1 import media_player
from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)

# Simple command IDs for activities and macros
SIMPLE_COMMANDS = [
    "POWER",
    "VOLUME_UP",
    "VOLUME_DOWN",
    "Mode None",
    "Mode Dolby Sur",
    "Mode Neural-X",
    "Mode Native",
    "Mode Auro",
    "Night Toggle",
    "Dialog Up",
    "Dialog Down",
    "Dirac Toggle",
    "Loudness Toggle",
    "User Input 1",
    "User Input 2",
    "User Input 3",
    "User Input 4",
    "User Input 5",
    "User Input 6",
    "User Input 7",
    "User Input 8",
    "User Input 9",
    "Last Input",
    "BT Pair",
    "HDMI+",
    "SPDIF+",
    "Analog+",
    "Stream+",
    "Red",
    "Green",
    "Yellow",
    "Blue",
    "A",
    "B",
    "C",
    "D",
    "Preset 1",
    "Preset 2",
    "Preset 3",
    "Preset 4",
    "Info",
    "Dim",
    "Loud On",
    "Loud Off",
    "Night On",
    "Night Off",
    "Dirac On",
    "Dirac Off",
    "In USB",
    "In AES",
    "TV Input",
    "In HDMI 1",
    "In HDMI 2",
    "In HDMI 3",
    "In HDMI 4",
    "In HDMI 5",
    "In HDMI 6",
    "In HDMI 7",
    "In HDMI 8",
    "In Bluetooth",
    "In Analog 1",
    "In Analog 2",
    "In Optical 1",
    "In Optical 2",
    "In Optical 3",
    "In Coaxial 1",
    "In Coaxial 2",
    "In Coaxial 3",
    "In Roon",
]


class HTP1Remote(Remote):
    """Remote entity for Monoprice HTP-1."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize with device reference."""
        self._device = device
        self._device_config = device_config

        entity_id = f"remote.{device_config.identifier}"
        entity_name = f"{device_config.name} Remote"

        features = [Features.TOGGLE]
        attributes = {Attributes.STATE: "UNAVAILABLE"}


        # Define button to command mapping
        remote_mapping= [
            {
                "button": Buttons.VOLUME_UP,
                "short_press": {"cmd_id": media_player.Commands.VOLUME_UP},
            },
            {
                "button": Buttons.VOLUME_DOWN,
                "short_press": {"cmd_id": media_player.Commands.VOLUME_DOWN},
            },
            {
                "button": Buttons.CHANNEL_UP,
                "short_press": {"cmd_id": "Dialog Up"},
            },
            {
                "button": Buttons.CHANNEL_DOWN,
                "short_press": {"cmd_id": "Dialog Down"},
            },
            {
                "button": Buttons.MUTE,
                "short_press": {"cmd_id": media_player.Commands.MUTE_TOGGLE},
            },
        ]

        # Define user interface with button layout
        user_interface = {
            "pages": [
                 {
                    "page_id": "main",
                    "name": "Main",
                    "grid": {"width": 9, "height": 5},
                    "items": [
                        # Row 1: 
                        {"type": "text", "text": "1", "command": {"cmd_id": "User Input 1"}, "location": {"x": 0, "y": 0}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "2", "command": {"cmd_id": "User Input 2"}, "location": {"x": 3, "y": 0}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "3", "command": {"cmd_id": "User Input 3"}, "location": {"x": 6, "y": 0}, "size": {"width": 3, "height": 1}},
                         # Row 2: 
                        {"type": "text", "text": "4", "command": {"cmd_id": "User Input 4"}, "location": {"x": 0, "y": 1}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "5", "command": {"cmd_id": "User Input 5"}, "location": {"x": 3, "y": 1}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "6", "command": {"cmd_id": "User Input 6"}, "location": {"x": 6, "y": 1}, "size": {"width": 3, "height": 1}},
                        # Row 3:
                        {"type": "text", "text": "7", "command": {"cmd_id": "User Input 7"}, "location": {"x": 0, "y": 2}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "8", "command": {"cmd_id": "User Input 8"}, "location": {"x": 3, "y": 2}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "9", "command": {"cmd_id": "User Input 9"}, "location": {"x": 6, "y": 2}, "size": {"width": 3, "height": 1}},
                        # Row 4
                        {"type": "text", "text": "INFO", "command": {"cmd_id": "Info"}, "location": {"x": 0, "y": 3}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "LAST", "command": {"cmd_id": "Last Input"}, "location": {"x": 3, "y": 3}, "size": {"width": 3, "height": 1}},
                        {"type": "text", "text": "DIM", "command": {"cmd_id": "Dim"}, "location": {"x": 6, "y": 3}, "size": {"width": 3, "height": 1}},
                        # Row 5
                        {"type": "text", "text": "RED", "command": {"cmd_id": "Red"}, "location": {"x": 0, "y": 4}, "size": {"width": 2, "height": 1}},
                        {"type": "text", "text": "GREEN", "command": {"cmd_id": "Green"}, "location": {"x": 2, "y": 4}, "size": {"width": 2, "height": 1}},
                        {"type": "text", "text": "YELLOW", "command": {"cmd_id": "Yellow"}, "location": {"x": 5, "y": 4}, "size": {"width": 2, "height": 1}},
                        {"type": "text", "text": "BLUE", "command": {"cmd_id": "Blue"}, "location": {"x": 7, "y": 4}, "size": {"width": 2, "height": 1}},
           ],
        },
        {
                    "page_id": "pad",
                    "name": "Pad",
                    "grid": {"width": 3, "height": 3},
                    "items": [
                        # Row 1: Top buttons
                        {"type": "text", "text": "HDMI+", "command": {"cmd_id": "HDMI+"}, "location": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "NATIVE", "command": {"cmd_id": "Mode Native"}, "location": {"x": 1, "y": 0}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "STRM+", "command": {"cmd_id": "Stream+"}, "location": {"x": 2, "y": 0}, "size": {"width": 1, "height": 1}},
                        # Row 2: Middle buttons
                        {"type": "text", "text": "DTS", "command": {"cmd_id": "Mode Neural-X"}, "location": {"x": 0, "y": 1}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "DIRECT", "command": {"cmd_id": "Mode None"}, "location": {"x": 1, "y": 1}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "DOLBY", "command": {"cmd_id": "Mode Dolby Sur"}, "location": {"x": 2, "y": 1}, "size": {"width": 1, "height": 1}},
                        # Row 3: Bottom buttons
                        {"type": "text", "text": "SPID+", "command": {"cmd_id": "SPID+"}, "location": {"x": 0, "y": 2}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "AURO 3D", "command": {"cmd_id": "Mode Auro"}, "location": {"x": 1, "y": 2}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "ANA+", "command": {"cmd_id": "Analog+"}, "location": {"x": 2, "y": 2}, "size": {"width": 1, "height": 1}},
                    ],
                },
                {
                    "page_id": "toggles",
                    "name": "Toggles",
                    "grid": {"width": 3, "height": 4},
                    "items": [
                        # Row 1: 
                        {"type": "text", "text": "DIRAC", "command": {"cmd_id": "Dirac Toggle"}, "location": {"x": 0, "y": 0}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "A", "command": {"cmd_id": "A"}, "location": {"x": 1, "y": 0}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "PRESET 1", "command": {"cmd_id": "PRESET 1"}, "location": {"x": 2, "y": 0}, "size": {"width": 1, "height": 1}},
                         # Row 2: 
                        {"type": "text", "text": "NIGHT", "command": {"cmd_id": "Night Toggle"}, "location": {"x": 0, "y": 1}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "B", "command": {"cmd_id": "B"}, "location": {"x": 1, "y": 1}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "PRESET 2", "command": {"cmd_id": "PRESET 2"}, "location": {"x": 2, "y": 1}, "size": {"width": 1, "height": 1}},
                        # Row 3:
                        {"type": "text", "text": "LOUD", "command": {"cmd_id": "Loudness Toggle"}, "location": {"x": 0, "y": 2}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "C", "command": {"cmd_id": "C"}, "location": {"x": 1, "y": 2}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "PRESET 3", "command": {"cmd_id": "PRESET 3"}, "location": {"x": 2, "y": 2}, "size": {"width": 1, "height": 1}},
                        # Row 4
                        {"type": "text", "text": "BT PAIR", "command": {"cmd_id": "BT PAIR"}, "location": {"x": 0, "y": 3}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "D", "command": {"cmd_id": "D"}, "location": {"x": 1, "y": 3}, "size": {"width": 1, "height": 1}},
                        {"type": "text", "text": "PRESET 4", "command": {"cmd_id": "PRESET 4"}, "location": {"x": 2, "y": 3}, "size": {"width": 1, "height": 1}},
            ],
        }
    ]
}

        # Create remote with UI and simple commands
        super().__init__(
            entity_id,
            entity_name,
            features,
            attributes,
            simple_commands=SIMPLE_COMMANDS,
            button_mapping= remote_mapping,
            ui_pages=user_interface["pages"],
            cmd_handler=self.handle_command,
        )

    async def handle_command(
        self, entity: Remote, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        """Handle remote commands."""
        _LOG.info("[%s] Remote command: %s %s", self.id, cmd_id, params or "")

        try:
            # Map command IDs to device actions
            if cmd_id == "POWER":
                if self._device._state:
                    power_on = self._device._state.get("powerIsOn", False)
                    success = await self._device.turn_off() if power_on else await self._device.turn_on()
                    return StatusCodes.OK if success else StatusCodes.SERVER_ERROR
                return StatusCodes.SERVER_ERROR

            elif cmd_id == "VOLUME_UP":
                success = await self._device.volume_up()
                return StatusCodes.OK if success else StatusCodes.SERVER_ERROR

            elif cmd_id == "VOLUME_DOWN":
                success = await self._device.volume_down()
                return StatusCodes.OK if success else StatusCodes.SERVER_ERROR

            elif cmd_id == "MUTE":
                if self._device._state:
                    muted = self._device._state.get("muted", False)
                    success = await self._device.mute(not muted)
                    return StatusCodes.OK if success else StatusCodes.SERVER_ERROR
                return StatusCodes.SERVER_ERROR
            
            elif cmd_id == "send_cmd":
                c = params.get("command", "");
                http_cmd = map_http_commands.get(c)
                if not http_cmd:
                    success = await self._device.send_hmand(c)
                else:
                    success = await self._device.send_http_command(http_cmd)
                return StatusCodes.OK if success else StatusCodes.SERVER_ERROR
            
            return StatusCodes.NOT_IMPLEMENTED

        except Exception as err:
            _LOG.error("[%s] Remote command error: %s", self.id, err)
            return StatusCodes.SERVER_ERROR


map_http_commands = {
 "Vol Down": "09f6",
"Mute Toggle": "0af5",
"Vol Up": "0bf4",
"Mode None": "1be4",
"Mode Dolby Sur": "1ce3",
"Mode Neural-X": "1de2",
"Mode Native": "1ee1",
"Mode Auro": "1fe0",
"Night Toggle": "40bf",
"Dialog Up": "41be",
"Dialog Down": "42bd",
"Dirac Toggle": "47b8",
"Loudness Toggle": "5aa5",
"User Input 1": "609f",
"User Input 2": "619e",
"User Input 3": "629d",
"User Input 4": "639c",
"User Input 5": "649b",
"User Input 6": "659a",
"User Input 7": "6699",
"User Input 8": "6798",
"User Input 9": "6897",
"Last Input": "44bb",
"BT Pair": "59a6",
"HDMI+": "4db2",
"SPDIF+": "4eb1",
"Analog+": "4fb0",
"Stream+": "50af",
"Red": "51ae",
"Green": "52ad",
"Yellow": "53ac",
"Blue": "54ab",
"A": "55aa",
"B": "56a9",
"C": "57a8",
"D": "58a7",
"Preset 1": "03fc",
"Preset 2": "04fb",
"Preset 3": "05fa",
"Preset 4": "06f9",
"Info": "43bc",
"Dim": "45ba",
"Mute On": "4bb4",
"Mute Off": "4cb3",
"Loud On": "3ac5",
"Loud Off": "3bc4",
"Night On": "3cc3",
"Night Off": "3dc2",
"Dirac On": "3ec1",
"Dirac Off": "3fc0",
"In USB": "2dd2",
"In AES": "5ba4",
"TV Input": "0ef1",
"In HDMI 1": "0ff0",
"In HDMI 2": "10ef",
"In HDMI 3": "11ee",
"In HDMI 4": "12ed",
"In HDMI 5": "13ec",
"In HDMI 6": "14eb",
"In HDMI 7": "15ea",
"In HDMI 8": "17e8",
"In Bluetooth": "46b9",
"In Analog 1": "27d8",
"In Analog 2": "28d7",
"In Optical 1": "29d6",
"In Optical 2": "2ad5",
"In Optical 3": "49b6",
"In Coaxial 1": "2bd4",
"In Coaxial 2": "2cd3",
"In Coaxial 3": "48b7",
"In Roon": "4ab5",
}
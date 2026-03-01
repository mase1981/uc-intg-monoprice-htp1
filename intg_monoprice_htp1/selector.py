

import logging
from typing import Any

from ucapi import StatusCodes
from ucapi.select import Attributes, Features, Select, States

from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)

class HTP1CInputSelect(Select):
    """Select entity for Input control."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize select entity."""
        self._device = device
        self._device_config = device_config

        entity_id = f"select.{device_config.identifier}_inputs"
        entity_name = f"{device_config.name} Input"

        attributes = {
            Attributes.STATE: States.UNAVAILABLE,
            Attributes.CURRENT_OPTION: "",
            Attributes.OPTIONS: [""]
        }

        super().__init__(
            entity_id,
            entity_name,
            attributes,
            cmd_handler=self.handle_command,
        )

        _LOG.info("[%s] Input select entity initialized", self.id)

    async def handle_command(
        self, entity: Select, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        """Handle select commands."""
        _LOG.info("[%s] Command: %s %s", self.id, cmd_id, params or "")

        try:
            if cmd_id == "select_option" and params and "option" in params:
                state = params["option"]
                success = await self._device.select_source(state)
                return StatusCodes.OK if success else StatusCodes.SERVER_ERROR

            return StatusCodes.NOT_IMPLEMENTED

        except Exception as err:
            _LOG.error("[%s] Command error: %s", self.id, err)
            return StatusCodes.SERVER_ERROR
 

class HTP1CalibrationSelect(Select):
    """Select entity for Calibration control."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize select entity."""
        self._device = device
        self._device_config = device_config

        entity_id = f"select.{device_config.identifier}_calibration"
        entity_name = f"{device_config.name} Calibration"

        attributes = {
            Attributes.STATE: States.UNAVAILABLE,
            Attributes.CURRENT_OPTION: "",
            Attributes.OPTIONS: [""]
        }

        super().__init__(
            entity_id,
            entity_name,
            attributes,
            cmd_handler=self.handle_command,
        )

        _LOG.info("[%s] Calibration select entity initialized", self.id)

    async def handle_command(
        self, entity: Select, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        """Handle select commands."""
        _LOG.info("[%s] Command: %s %s", self.id, cmd_id, params or "")

        try:
            if cmd_id == "select_option" and params and "option" in params:
                state = params["option"]
                success = await self._device.select_calibration(state)
                return StatusCodes.OK if success else StatusCodes.SERVER_ERROR

            return StatusCodes.NOT_IMPLEMENTED

        except Exception as err:
            _LOG.error("[%s] Command error: %s", self.id, err)
            return StatusCodes.SERVER_ERROR
        
class HTP1SurroundModeSelect(Select):
    """Select entity for Surround Mode control."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize select entity."""
        self._device = device
        self._device_config = device_config

        entity_id = f"select.{device_config.identifier}_surround_mode"
        entity_name = f"{device_config.name} Surround Mode"

        attributes = {
            Attributes.STATE: States.UNAVAILABLE,
            Attributes.CURRENT_OPTION: "",
            Attributes.OPTIONS: ["DIRECT", "DOLBY SURROUND", "DTS NEURAL:X", "AURO-3D", "NATIVE", "STEREO"]
        }

        super().__init__(
            entity_id,
            entity_name,
            attributes,
            cmd_handler=self.handle_command,
        )

        _LOG.info("[%s] Surround Mode select entity initialized", self.id)

    async def handle_command(
        self, entity: Select, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        """Handle select commands."""
        _LOG.info("[%s] Command: %s %s", self.id, cmd_id, params or "")

        try:
            if cmd_id == "select_option" and params and "option" in params:
                selection = params["option"]
                success = await self._device.select_sound_mode(selection)
                return StatusCodes.OK if success else StatusCodes.SERVER_ERROR

            return StatusCodes.NOT_IMPLEMENTED

        except Exception as err:
            _LOG.error("[%s] Command error: %s", self.id, err)
            return StatusCodes.SERVER_ERROR
"""
Monoprice HTP-1 Select entities.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ucapi import StatusCodes
from ucapi.select import Attributes, Commands, States
from ucapi_framework import SelectEntity

if TYPE_CHECKING:
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)


class HTP1Select(SelectEntity):
    """Generic HTP-1 select entity using subscribe/sync_state pattern."""

    def __init__(
        self,
        entity_id: str,
        name: str,
        device: HTP1Device,
        get_options_fn: Callable[[], list[str]],
        get_current_fn: Callable[[], str],
        command_fn: Callable[[str], Awaitable[bool]],
    ):
        super().__init__(
            entity_id,
            name,
            {
                Attributes.STATE: States.UNKNOWN,
                Attributes.OPTIONS: [],
                Attributes.CURRENT_OPTION: "",
            },
            cmd_handler=self._handle_command,
        )
        self._device = device
        self._get_options = get_options_fn
        self._get_current = get_current_fn
        self._command_fn = command_fn
        self.subscribe_to_device(device)

    async def sync_state(self):
        if not self._device.is_connected:
            self.update({Attributes.STATE: States.UNAVAILABLE})
            return
        self.update({
            Attributes.STATE: States.ON,
            Attributes.OPTIONS: self._get_options(),
            Attributes.CURRENT_OPTION: self._get_current(),
        })

    async def _handle_command(
        self, entity: Any, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        if cmd_id != Commands.SELECT_OPTION:
            return StatusCodes.NOT_IMPLEMENTED
        option = params.get("option") if params else None
        if not option:
            return StatusCodes.BAD_REQUEST
        _LOG.info("[%s] Setting %s to: %s", self._device.log_id, self.name, option)
        success = await self._command_fn(option)
        return StatusCodes.OK if success else StatusCodes.SERVER_ERROR


def create_selects(config: HTP1Config, device: HTP1Device) -> list[HTP1Select]:
    """Create select entities for HTP-1 device."""
    from intg_monoprice_htp1.displayvalues import sound_mode_display_values

    device_id = config.identifier
    name = config.name

    surround_options = list(sound_mode_display_values.values())

    entities = [
        HTP1Select(
            f"select.{device_id}.input",
            f"{name} Input",
            device,
            lambda: device.source_list,
            lambda: device.current_source,
            lambda opt: device.select_source(opt),
        ),
        HTP1Select(
            f"select.{device_id}.calibration",
            f"{name} Calibration",
            device,
            lambda: device.slot_names,
            lambda: device.dirac_slot_name,
            lambda opt: device.select_calibration(opt),
        ),
        HTP1Select(
            f"select.{device_id}.surround_mode",
            f"{name} Surround Mode",
            device,
            lambda opts=surround_options: opts,
            lambda: device.sound_mode_display,
            lambda opt: device.select_sound_mode(opt),
        ),
        HTP1Select(
            f"select.{device_id}.ss_preset",
            f"{name} Seat Shaker Preset",
            device,
            lambda opts=[1, 2, 3, 4, 5, 6]: opts,
            lambda: device.ss_preset,
            lambda opt: device.select_ss_preset(int(opt)-1),
        ),
    ]

    _LOG.info("Created %d select entities for %s", len(entities), name)
    return entities

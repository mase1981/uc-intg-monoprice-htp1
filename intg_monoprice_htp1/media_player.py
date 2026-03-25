"""
Monoprice HTP-1 Media Player entity.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ucapi import StatusCodes
from ucapi.api_definitions import BrowseOptions, BrowseResults, SearchOptions, SearchResults
from ucapi.media_player import (
    Attributes,
    Commands,
    DeviceClasses,
    Features,
    States,
)
from ucapi_framework import MediaPlayerEntity

if TYPE_CHECKING:
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)

FEATURES = [
    Features.ON_OFF,
    Features.VOLUME,
    Features.VOLUME_UP_DOWN,
    Features.MUTE_TOGGLE,
    Features.MUTE,
    Features.UNMUTE,
    Features.SELECT_SOURCE,
    Features.SELECT_SOUND_MODE,
    Features.PLAY_MEDIA,
    Features.BROWSE_MEDIA,
    Features.SEARCH_MEDIA,
]


class HTP1MediaPlayer(MediaPlayerEntity):
    """Media player entity for Monoprice HTP-1."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        self._device = device

        super().__init__(
            f"media_player.{device_config.identifier}",
            device_config.name,
            FEATURES,
            {
                Attributes.STATE: States.UNAVAILABLE,
                Attributes.VOLUME: 0,
                Attributes.MUTED: False,
                Attributes.SOURCE: "",
                Attributes.SOURCE_LIST: [],
                Attributes.SOUND_MODE: "",
                Attributes.SOUND_MODE_LIST: [],
            },
            device_class=DeviceClasses.RECEIVER,
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    async def sync_state(self):
        if not self._device.is_connected:
            self.update({Attributes.STATE: States.UNAVAILABLE})
            return

        from intg_monoprice_htp1.displayvalues import sound_mode_display_values

        state = States.ON if self._device.power else States.OFF
        surround_options = list(sound_mode_display_values.values())

        self.update({
            Attributes.STATE: state,
            Attributes.VOLUME: self._device.volume_db,
            Attributes.MUTED: self._device.muted,
            Attributes.SOURCE: self._device.current_source,
            Attributes.SOURCE_LIST: self._device.source_list,
            Attributes.SOUND_MODE: self._device.sound_mode_display,
            Attributes.SOUND_MODE_LIST: surround_options,
        })

    async def browse(self, options: BrowseOptions) -> BrowseResults | StatusCodes:
        from intg_monoprice_htp1 import browser
        return await browser.browse(self._device, options)

    async def search(self, options: SearchOptions) -> SearchResults | StatusCodes:
        from intg_monoprice_htp1 import browser
        return await browser.search(self._device, options)

    async def _handle_command(
        self, entity: Any, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        _LOG.info("[%s] Command: %s %s", self.id, cmd_id, params or "")

        try:
            if cmd_id == Commands.ON:
                return StatusCodes.OK if await self._device.turn_on() else StatusCodes.SERVER_ERROR

            if cmd_id == Commands.OFF:
                return StatusCodes.OK if await self._device.turn_off() else StatusCodes.SERVER_ERROR

            if cmd_id == Commands.VOLUME:
                if params and "volume" in params:
                    success = await self._device.set_volume_level(float(params["volume"]) / 100.0)
                    return StatusCodes.OK if success else StatusCodes.SERVER_ERROR
                return StatusCodes.BAD_REQUEST

            if cmd_id == Commands.VOLUME_UP:
                return StatusCodes.OK if await self._device.volume_up() else StatusCodes.SERVER_ERROR

            if cmd_id == Commands.VOLUME_DOWN:
                return StatusCodes.OK if await self._device.volume_down() else StatusCodes.SERVER_ERROR

            if cmd_id == Commands.MUTE_TOGGLE:
                return StatusCodes.OK if await self._device.mute_toggle(not self._device.muted) else StatusCodes.SERVER_ERROR

            if cmd_id == Commands.MUTE:
                return StatusCodes.OK if await self._device.mute_toggle(True) else StatusCodes.SERVER_ERROR

            if cmd_id == Commands.UNMUTE:
                return StatusCodes.OK if await self._device.mute_toggle(False) else StatusCodes.SERVER_ERROR

            if cmd_id == Commands.SELECT_SOURCE:
                if params and "source" in params:
                    return StatusCodes.OK if await self._device.select_source(params["source"]) else StatusCodes.SERVER_ERROR
                return StatusCodes.BAD_REQUEST

            if cmd_id == Commands.SELECT_SOUND_MODE:
                if params and "mode" in params:
                    return StatusCodes.OK if await self._device.select_sound_mode(params["mode"]) else StatusCodes.SERVER_ERROR
                return StatusCodes.BAD_REQUEST

            if cmd_id == Commands.PLAY_MEDIA:
                return await self._handle_play_media(params)

            return StatusCodes.NOT_IMPLEMENTED

        except Exception as err:
            _LOG.error("[%s] Command error: %s", self.id, err)
            return StatusCodes.SERVER_ERROR

    async def _handle_play_media(self, params: dict[str, Any] | None) -> StatusCodes:
        if not params:
            return StatusCodes.BAD_REQUEST

        media_id = params.get("media_id", "")
        if not media_id:
            return StatusCodes.BAD_REQUEST

        if media_id == "beq:clear":
            success = await self._device.clear_beq()
            return StatusCodes.OK if success else StatusCodes.SERVER_ERROR

        if media_id.startswith("beq:"):
            beq_data = media_id[4:]
            try:
                entry = json.loads(beq_data)
                title = entry.get("underlying", "Unknown")
                filters = entry.get("filters", [])
                if not filters:
                    return StatusCodes.BAD_REQUEST
                success = await self._device.load_beq(title, filters)
                return StatusCodes.OK if success else StatusCodes.SERVER_ERROR
            except (json.JSONDecodeError, KeyError) as err:
                _LOG.error("[%s] Invalid BEQ data: %s", self.id, err)
                return StatusCodes.BAD_REQUEST

        return StatusCodes.NOT_IMPLEMENTED

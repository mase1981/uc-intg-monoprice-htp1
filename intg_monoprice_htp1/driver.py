"""
Monoprice HTP-1 driver for Unfolded Circle Remote.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

import logging
from ucapi import Entity
from ucapi_framework import BaseIntegrationDriver
from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device
from intg_monoprice_htp1.media_player import HTP1MediaPlayer
from intg_monoprice_htp1.remote import HTP1Remote
from intg_monoprice_htp1.sensor import (
    HTP1InputSensor,
    HTP1VolumeSensor,
    HTP1SoundModeSensor,
    HTP1AudioFormatSensor,
    HTP1OutputAudioFormatSensor,
    HTP1VideoModeSensor,
    HTP1ConnectionSensor,
)

_LOG = logging.getLogger(__name__)


class HTP1Driver(BaseIntegrationDriver[HTP1Device, HTP1Config]):
    """Monoprice HTP-1 integration driver."""

    def __init__(self):
        super().__init__(
            device_class=HTP1Device,
            entity_classes=[
                HTP1MediaPlayer,
                HTP1Remote,
                HTP1InputSensor,
                HTP1VolumeSensor,
                HTP1SoundModeSensor,
                HTP1AudioFormatSensor,
                HTP1OutputAudioFormatSensor,
                HTP1VideoModeSensor,
                HTP1ConnectionSensor,
            ],
            driver_id="monoprice_htp1",
        )

    def create_entities(
        self, device_config: HTP1Config, device: HTP1Device
    ) -> list[Entity]:
        """Create entity instances."""
        _LOG.info("Creating entities for %s", device_config.name)
        return [
            HTP1MediaPlayer(device_config, device),
            HTP1Remote(device_config, device),
            HTP1InputSensor(device_config, device),
            HTP1VolumeSensor(device_config, device),
            HTP1SoundModeSensor(device_config, device),
            HTP1AudioFormatSensor(device_config, device),
            HTP1OutputAudioFormatSensor(device_config, device),
            HTP1VideoModeSensor(device_config, device),
            HTP1ConnectionSensor(device_config, device),
        ]

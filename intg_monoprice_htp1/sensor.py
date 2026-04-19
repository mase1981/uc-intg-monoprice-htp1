"""
Monoprice HTP-1 Sensor entities.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ucapi.sensor import Attributes, DeviceClasses, Options, States
from ucapi_framework import SensorEntity

if TYPE_CHECKING:
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)


class HTP1Sensor(SensorEntity):
    """Generic HTP-1 sensor entity using subscribe/sync_state pattern."""

    def __init__(
        self,
        entity_id: str,
        name: str,
        device: HTP1Device,
        sensor_key: str,
        unit: str,
    ):
        super().__init__(
            entity_id,
            name,
            [],
            {
                Attributes.STATE: States.UNKNOWN,
                Attributes.VALUE: "",
            },
            device_class=DeviceClasses.CUSTOM,
            options={Options.CUSTOM_UNIT: unit},
        )
        self._device = device
        self._sensor_key = sensor_key
        self.subscribe_to_device(device)

    async def sync_state(self):
        if not self._device.is_connected:
            self.update({Attributes.STATE: States.UNAVAILABLE})
            return
        value = self._device.get_sensor_value(self._sensor_key) or "Unknown"
        self.update({
            Attributes.STATE: States.ON,
            Attributes.VALUE: value,
        })


def create_sensors(config: HTP1Config, device: HTP1Device) -> list[HTP1Sensor]:
    """Create sensor entities for HTP-1 device."""
    device_id = config.identifier
    name = config.name

    sensors = [
        HTP1Sensor(f"sensor.{device_id}.input", f"{name} Input", device, "input", ""),
        HTP1Sensor(f"sensor.{device_id}.volume", f"{name} Volume", device, "volume", "dB"),
        HTP1Sensor(f"sensor.{device_id}.mute", f"{name} Mute", device, "mute", ""),
        HTP1Sensor(f"sensor.{device_id}.loudness", f"{name} Loudness", device, "loudness", ""),
        HTP1Sensor(f"sensor.{device_id}.night_mode", f"{name} Night Mode", device, "night_mode", ""),
        HTP1Sensor(f"sensor.{device_id}.peq", f"{name} PEQ", device, "peq", ""),
        HTP1Sensor(f"sensor.{device_id}.sound_mode", f"{name} Sound Mode", device, "sound_mode", ""),
        HTP1Sensor(f"sensor.{device_id}.audio_format", f"{name} Audio Format", device, "audio_format", ""),
        HTP1Sensor(f"sensor.{device_id}.output_audio_format", f"{name} Output Audio Format", device, "output_audio_format", ""),
        HTP1Sensor(f"sensor.{device_id}.dirac_slot", f"{name} Dirac Slot", device, "dirac_slot", ""),
        HTP1Sensor(f"sensor.{device_id}.video_mode", f"{name} Video Mode", device, "video_mode", ""),
        HTP1Sensor(f"sensor.{device_id}.connection", f"{name} Connection", device, "connection", ""),
        HTP1Sensor(f"sensor.{device_id}.beq_active", f"{name} BEQ Filter", device, "beq_active", ""),
    ]

    _LOG.info("Created %d sensor entities for %s", len(sensors), name)
    return sensors

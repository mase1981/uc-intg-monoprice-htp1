"""
Monoprice HTP-1 Sensor entities.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

import logging
from ucapi.sensor import Sensor, Attributes, DeviceClasses
from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device

_LOG = logging.getLogger(__name__)


class HTP1InputSensor(Sensor):
    """Sensor for current input source."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the input sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_input"

        super().__init__(
            entity_id,
            f"{device_config.name} Input",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )


class HTP1VolumeSensor(Sensor):
    """Sensor for current volume level in dB."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the volume sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_volume"

        super().__init__(
            entity_id,
            f"{device_config.name} Volume",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: "dB",
            },
            device_class=DeviceClasses.CUSTOM,
        )

class HTP1MutedSensor(Sensor):
    """Sensor for mute status."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the volume sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_mute"

        super().__init__(
            entity_id,
            f"{device_config.name} Mute",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,

            },
            device_class=DeviceClasses.CUSTOM,
        )

class HTP1LoudnessSensor(Sensor):
    """Sensor for current loudness status."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the sound mode sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_loudness"

        super().__init__(
            entity_id,
            f"{device_config.name} Loudness",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )

class HTP1PEQSensor(Sensor):
    """Sensor for current PEQ status."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the PEQ sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_peq"

        super().__init__(
            entity_id,
            f"{device_config.name} PEQ",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )

class HTP1SoundModeSensor(Sensor):
    """Sensor for current sound mode (upmix)."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the sound mode sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_sound_mode"

        super().__init__(
            entity_id,
            f"{device_config.name} Sound Mode",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )


class HTP1AudioFormatSensor(Sensor):
    """Sensor for current audio format."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the audio format sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_audio_format"

        super().__init__(
            entity_id,
            f"{device_config.name} Audio Format",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )

class HTP1OutputAudioFormatSensor(Sensor):
    """Sensor for current output audio format."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the output audio format sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_output_audio_format"

        super().__init__(
            entity_id,
            f"{device_config.name} Output Audio Format",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )

class HTP1CurrentDiracSlotNameSensor(Sensor):
    """Sensor for current output audio format."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the output audio format sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_current_dirac_slot_name"

        super().__init__(
            entity_id,
            f"{device_config.name} Current Dirac Slot Name",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )

class HTP1VideoModeSensor(Sensor):
    """Sensor for current video mode."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the video mode sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_video_mode"

        super().__init__(
            entity_id,
            f"{device_config.name} Video Mode",
            [],  # No features
            {
                Attributes.STATE: "Unknown",
                Attributes.VALUE: None,
                Attributes.UNIT: None,
            },
        )


class HTP1ConnectionSensor(Sensor):
    """Sensor for connection state."""

    def __init__(self, device_config: HTP1Config, device: HTP1Device):
        """Initialize the connection sensor."""
        self._device = device
        self._device_config = device_config

        entity_id = f"sensor.{device_config.identifier}_connection"

        super().__init__(
            entity_id,
            f"{device_config.name} Connection",
            [],  # No features
            {
                Attributes.STATE: "Disconnected",
                Attributes.VALUE: "disconnected",
                Attributes.UNIT: None,
            },
            device_class=DeviceClasses.CUSTOM,
        )

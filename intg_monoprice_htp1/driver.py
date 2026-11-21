"""
Monoprice HTP-1 driver for Unfolded Circle Remote.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

from ucapi_framework import BaseIntegrationDriver
from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device
from intg_monoprice_htp1.media_player import HTP1MediaPlayer
from intg_monoprice_htp1.remote import HTP1Remote
from intg_monoprice_htp1.sensor import create_sensors
from intg_monoprice_htp1.selector import create_selects


class HTP1Driver(BaseIntegrationDriver[HTP1Device, HTP1Config]):
    """Monoprice HTP-1 integration driver."""

    def __init__(self):
        super().__init__(
            device_class=HTP1Device,
            entity_classes=[
                HTP1MediaPlayer,
                HTP1Remote,
                lambda cfg, dev: create_sensors(cfg, dev),
                lambda cfg, dev: create_selects(cfg, dev),
            ],
            driver_id="monoprice_htp1",
            # Start the persistent HTP-1 WebSocket connection before registering entities.
            require_connection_before_registry=True,
        )

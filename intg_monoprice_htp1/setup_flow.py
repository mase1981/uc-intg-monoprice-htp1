"""
Monoprice HTP-1 setup flow for Unfolded Circle integration.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

import logging
from typing import Any
from ucapi import RequestUserInput
from ucapi_framework import BaseSetupFlow
from intg_monoprice_htp1.config import DEFAULT_PORT, HTP1Config
from intg_monoprice_htp1.device import probe_htp1

_LOG = logging.getLogger(__name__)

DEFAULT_NAME = "Monoprice HTP-1"


class HTP1SetupFlow(BaseSetupFlow[HTP1Config]):
    """Setup flow for Monoprice HTP-1 integration."""

    def _existing_config(self) -> HTP1Config | None:
        """Return the saved config when updating an existing device."""
        if self._add_mode:
            return None
        return self.selected_config_entry

    def _build_form(
        self,
        name: str,
        host: str,
        port: int | str = DEFAULT_PORT,
        error: str | None = None,
    ) -> RequestUserInput:
        settings: list[dict[str, Any]] = []
        if error:
            settings.append(
                {
                    "id": "error",
                    "label": {"en": "Error"},
                    "field": {"label": {"value": {"en": error}}},
                }
            )
        settings.extend(
            [
                {
                    "id": "name",
                    "label": {"en": "Device Name"},
                    "field": {"text": {"value": name}},
                },
                {
                    "id": "host",
                    "label": {"en": "IP Address"},
                    "field": {"text": {"value": host}},
                },
                {
                    "id": "port",
                    "label": {"en": "HTTP / WebSocket Port"},
                    "field": {"text": {"value": str(port)}},
                },
            ]
        )
        return RequestUserInput({"en": "Monoprice HTP-1 Setup"}, settings)

    def get_manual_entry_form(self) -> RequestUserInput:
        """Define manual entry fields, prefilled with saved values on update."""
        existing = self._existing_config()
        if existing:
            return self._build_form(existing.name, existing.host, existing.port)
        return self._build_form(DEFAULT_NAME, "", DEFAULT_PORT)

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> HTP1Config | RequestUserInput:
        """Validate the connection and create the config.

        Errors are returned as the form (with the typed values) so the user can retry.
        """
        host = (input_values.get("host") or "").strip()
        name = (input_values.get("name") or "").strip() or DEFAULT_NAME
        raw_port = input_values.get("port", DEFAULT_PORT)

        if not host:
            return self._build_form(name, host, DEFAULT_PORT, "IP address is required.")

        try:
            if isinstance(raw_port, bool):
                raise ValueError
            port = int(raw_port)
            if isinstance(raw_port, float) and raw_port != port:
                raise ValueError
            if isinstance(raw_port, str) and raw_port.strip() != str(port):
                raise ValueError
            if not 1 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            display_port = raw_port if isinstance(raw_port, str) else DEFAULT_PORT
            return self._build_form(
                name,
                host,
                display_port,
                "Port must be a whole number between 1 and 65535.",
            )

        _LOG.info("Setting up Monoprice HTP-1 at %s:%d", host, port)
        error = await probe_htp1(host, port=port)
        if error:
            _LOG.warning("HTP-1 setup connection test failed: %s", error)
            return self._build_form(
                name,
                host,
                port,
                f"{error}. Check that the HTP-1 is powered on and reachable, then try again.",
            )

        existing = self._existing_config()
        # Keep the identifier on update so entity IDs and activities stay intact.
        identifier = existing.identifier if existing else f"htp1_{host.replace('.', '_')}"

        _LOG.info("Successfully connected to Monoprice HTP-1 at %s:%d", host, port)
        return HTP1Config(identifier=identifier, name=name, host=host, port=port)

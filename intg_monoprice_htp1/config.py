"""
Monoprice HTP-1 configuration for Unfolded Circle integration.

:copyright: (c) 2026 by Meir Miyara.
:license: MPL-2.0, see LICENSE for more details.
"""

from dataclasses import dataclass
from ucapi_framework import BaseConfigManager


DEFAULT_PORT = 80
MIN_PORT = 1
MAX_PORT = 65535


@dataclass
class HTP1Config:
    """Monoprice HTP-1 configuration."""

    identifier: str
    name: str
    host: str
    # Keep a default so configurations saved before configurable ports existed
    # continue to load without migration.
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        """Reject invalid ports in newly created or restored configurations."""
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ValueError("HTP-1 port must be an integer")
        if not MIN_PORT <= self.port <= MAX_PORT:
            raise ValueError(f"HTP-1 port must be between {MIN_PORT} and {MAX_PORT}")


class HTP1ConfigManager(BaseConfigManager[HTP1Config]):
    """Configuration manager with automatic JSON persistence."""

    pass

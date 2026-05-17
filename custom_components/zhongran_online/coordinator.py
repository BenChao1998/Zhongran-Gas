from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZhongranApiClient, ZhongranApiError, ZhongranAuthError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZhongranDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Zhongran API updates."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: ZhongranApiClient,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self.session_valid: bool | None = None
        self.last_error: str | None = None

    @property
    def status(self) -> str:
        """Return the current session validity state."""
        if self.session_valid is True:
            return "valid"
        if self.session_valid is False:
            return "invalid"
        return "unknown"

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.async_fetch_dashboard()
        except ZhongranAuthError as err:
            self.session_valid = False
            self.last_error = str(err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZhongranApiError as err:
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err
        self.session_valid = True
        self.last_error = None
        return data

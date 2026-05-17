from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZhongranApiClient, ZhongranCredentials
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CUST_CODE,
    CONF_CUST_NAME,
    CONF_MINI_OPEN_ID,
    CONF_MOBILE,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_SID,
    CONF_UNION_ID,
    CONF_USER_ID,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ZhongranDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zhongran Gas from a config entry."""
    settings = {**entry.data, **entry.options}
    cust_code = str(settings[CONF_CUST_CODE])
    desired_title = f"Zhongran Gas {cust_code}"
    if entry.title in {f"Zhongran {cust_code}", f"Zhongran Online {cust_code}"}:
        hass.config_entries.async_update_entry(entry, title=desired_title)
    credentials = ZhongranCredentials(
        user_id=str(settings[CONF_USER_ID]),
        access_token=str(settings[CONF_ACCESS_TOKEN]),
        sid=str(settings[CONF_SID]),
        cust_code=cust_code,
        cust_name=str(settings[CONF_CUST_NAME]),
        mobile=str(settings[CONF_MOBILE]) if settings.get(CONF_MOBILE) else None,
        union_id=str(settings[CONF_UNION_ID]) if settings.get(CONF_UNION_ID) else None,
        mini_open_id=str(settings[CONF_MINI_OPEN_ID])
        if settings.get(CONF_MINI_OPEN_ID)
        else None,
    )
    client = ZhongranApiClient(async_get_clientsession(hass), credentials)
    coordinator = ZhongranDataUpdateCoordinator(
        hass,
        client,
        update_interval=timedelta(
            hours=int(settings.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS))
        ),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

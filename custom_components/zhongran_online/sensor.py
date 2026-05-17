from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZhongranDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class ZhongranSensorDescription(SensorEntityDescription):
    """Describe a Zhongran sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[ZhongranSensorDescription, ...] = (
    ZhongranSensorDescription(
        key="qty_balance",
        name="Gas Balance",
        native_unit_of_measurement="m3",
        value_fn=lambda data: _to_float(data.get("customer_info", {}).get("qtyBalance")),
        attrs_fn=lambda data: {
            "meter_no": data.get("customer_info", {}).get("meterNo"),
            "meter_form_name": data.get("customer_info", {}).get("meterFormName"),
            "company": data.get("customer_info", {}).get("compName"),
        },
    ),
    ZhongranSensorDescription(
        key="owe_money",
        name="Gas Owe Money",
        native_unit_of_measurement="CNY",
        value_fn=lambda data: _to_float(data.get("customer_info", {}).get("oweMoney")),
    ),
    ZhongranSensorDescription(
        key="last_record",
        name="Last Meter Reading",
        value_fn=lambda data: _to_int(data.get("customer_info", {}).get("lastRecord")),
    ),
    ZhongranSensorDescription(
        key="last_record_time",
        name="Last Meter Reading Date",
        value_fn=lambda data: data.get("customer_info", {}).get("lastRecordTime"),
    ),
    ZhongranSensorDescription(
        key="latest_consumption_qty",
        name="Latest Gas Consumption",
        native_unit_of_measurement="m3",
        value_fn=lambda data: _to_float(data.get("latest_consumption", {}).get("thisReadGq")),
        attrs_fn=lambda data: {
            "month": data.get("latest_consumption", {}).get("amtYm"),
            "reading_date": data.get("latest_consumption", {}).get("thisYmd"),
            "reading_value": data.get("latest_consumption", {}).get("thisRead"),
            "records": data.get("consumption", []),
        },
    ),
    ZhongranSensorDescription(
        key="latest_remaining_gas",
        name="Latest Remaining Gas",
        native_unit_of_measurement="m3",
        value_fn=lambda data: _to_float(data.get("latest_consumption", {}).get("remainGas")),
    ),
    ZhongranSensorDescription(
        key="latest_payment_amount",
        name="Latest Payment Amount",
        native_unit_of_measurement="CNY",
        value_fn=lambda data: _to_float(data.get("latest_payment", {}).get("amount")),
        attrs_fn=lambda data: {
            "paid_at": data.get("latest_payment", {}).get("timeope"),
            "payment_type": data.get("latest_payment", {}).get("paytypedesc"),
            "record_month": data.get("latest_payment", {}).get("recordmonth"),
            "records": data.get("payments", []),
        },
    ),
    ZhongranSensorDescription(
        key="latest_payment_time",
        name="Latest Payment Time",
        value_fn=lambda data: data.get("latest_payment", {}).get("timeope"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zhongran sensors from a config entry."""
    coordinator: ZhongranDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        ZhongranStatusSensor(coordinator, entry),
        *(ZhongranSensor(coordinator, entry, description) for description in SENSORS),
    ]
    async_add_entities(entities)


class ZhongranSensor(CoordinatorEntity[ZhongranDataUpdateCoordinator], SensorEntity):
    """Represent one Zhongran summary sensor."""

    entity_description: ZhongranSensorDescription

    def __init__(
        self,
        coordinator: ZhongranDataUpdateCoordinator,
        entry: ConfigEntry,
        description: ZhongranSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        cust_code = str(entry.data["cust_code"])
        self._attr_unique_id = f"{cust_code}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cust_code)},
            name=f"Zhongran Gas {cust_code}",
            manufacturer="Zhongran Gas",
            model=coordinator.data.get("customer_info", {}).get("meterFormName", "Gas Account")
            if coordinator.data
            else "Gas Account",
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data or {})


class ZhongranStatusSensor(CoordinatorEntity[ZhongranDataUpdateCoordinator], SensorEntity):
    """Represent Zhongran session validity."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Status"

    def __init__(
        self,
        coordinator: ZhongranDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        cust_code = str(entry.data["cust_code"])
        self._entry = entry
        self._attr_unique_id = f"{cust_code}_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cust_code)},
            name=f"Zhongran Gas {cust_code}",
            manufacturer="Zhongran Gas",
            model=coordinator.data.get("customer_info", {}).get("meterFormName", "Gas Account")
            if coordinator.data
            else "Gas Account",
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        return self.coordinator.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        selected_account = data.get("selected_account", {})
        return {
            "last_error": self.coordinator.last_error,
            "last_update_success": self.coordinator.last_update_success,
            "refreshed_at": data.get("refreshed_at"),
            "cust_code": selected_account.get("custCode") or self._entry.data.get("cust_code"),
            "cust_name": selected_account.get("custName") or self._entry.data.get("cust_name"),
        }


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

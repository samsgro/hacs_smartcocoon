"""Support for SmartCocoon room temperature sensors."""

from __future__ import annotations

import logging

from pysmartcocoon.manager import SmartCocoonManager

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmartCocoonController
from .const import DOMAIN
from .coordinator import RoomTemperatureReading, SmartCocoonRoomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SmartCocoon room temperature sensors."""
    _LOGGER.debug("Starting room temperature sensor setup")

    smartcocoon: SmartCocoonController = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = smartcocoon.room_coordinator
    if coordinator is None:
        _LOGGER.debug("Room temperature coordinator missing; skipping sensors")
        return

    added_room_ids: set[int] = set()

    def _add_discovered_rooms() -> None:
        """Add sensors for rooms discovered during startup or later refreshes."""
        room_ids: set[int] = set()
        if coordinator.data:
            room_ids.update(coordinator.data)
        scmanager = smartcocoon.scmanager
        if scmanager is not None:
            room_ids.update(scmanager.rooms)

        new_room_ids = room_ids - added_room_ids
        if not new_room_ids:
            return

        added_room_ids.update(new_room_ids)
        async_add_entities(
            [
                SmartCocoonRoomTemperatureSensor(coordinator, room_id)
                for room_id in sorted(new_room_ids)
            ]
        )

    config_entry.async_on_unload(coordinator.async_add_listener(_add_discovered_rooms))
    _add_discovered_rooms()

    scmanager = smartcocoon.scmanager
    if scmanager is not None:
        async_add_entities(
            [
                SmartCocoonObservedFanSpeedSensor(coordinator, scmanager, fan_id)
                for fan_id in sorted(scmanager.fans)
            ]
        )

    _LOGGER.debug("Completed room temperature sensor setup")


class SmartCocoonRoomTemperatureSensor(CoordinatorEntity, SensorEntity):  # type: ignore[misc]
    """A SmartCocoon room current-temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_force_update = True
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "room_temperature"

    def __init__(
        self,
        coordinator: SmartCocoonRoomCoordinator,
        room_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._room_id = room_id
        self._attr_unique_id = f"{DOMAIN}_room_temperature_{room_id}".lower()
        reading = self._reading
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"smartcocoon_room_{room_id}")},
            name=reading.name if reading else f"Room {room_id}",
            manufacturer="SmartCocoon",
            model="Room",
        )

    @property
    def _reading(self) -> RoomTemperatureReading | None:
        """Return the coordinator reading for this room, if present."""
        data = self.coordinator.data
        if not data:
            return None
        value = data.get(self._room_id)
        if not isinstance(value, RoomTemperatureReading):
            return None
        return value

    @property
    def native_value(self) -> float | None:
        """Return the current room temperature."""
        reading = self._reading
        if reading is None:
            return None
        return reading.temperature


class SmartCocoonObservedFanSpeedSensor(
    CoordinatorEntity,  # type: ignore[misc]
    SensorEntity,  # type: ignore[misc]
):
    """Actual SmartCocoon fan speed observed from the rooms API."""

    _attr_force_update = True
    _attr_has_entity_name = True
    _attr_name = "Observed Fan Speed"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SmartCocoonRoomCoordinator,
        scmanager: SmartCocoonManager,
        fan_id: str,
    ) -> None:
        """Initialize the observed fan-speed sensor."""
        super().__init__(coordinator)
        self._scmanager = scmanager
        self._fan_id = fan_id
        self._attr_unique_id = f"{DOMAIN}_fan_{fan_id}_observed_speed".lower()
        fan = self._scmanager.fans[fan_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"smartcocoon_fan_{fan_id}")},
            name=f"{fan.room_name}:{fan_id}",
            manufacturer="SmartCocoon",
            model="Smart Vent",
        )

    @property
    def available(self) -> bool:
        """Return whether both the observation stream and fan are available."""
        fan = self._scmanager.fans.get(self._fan_id)
        return (
            super().available
            and fan is not None
            and bool(getattr(fan, "connected", False))
        )

    @property
    def native_value(self) -> int | None:
        """Return effective speed: zero while off, configured power while on."""
        fan = self._scmanager.fans.get(self._fan_id)
        if fan is None:
            return None
        if not bool(getattr(fan, "fan_on", False)):
            return 0
        return int(fan.speed_pct)

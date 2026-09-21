"""Support for SmartCocoon room temperature sensors."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
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
    if coordinator is None or coordinator.data is None:
        _LOGGER.debug("Room temperature coordinator not ready; skipping sensors")
        return

    entities = [
        SmartCocoonRoomTemperatureSensor(coordinator, room_id)
        for room_id in sorted(coordinator.data)
    ]
    async_add_entities(entities)

    _LOGGER.debug("Completed room temperature sensor setup")


class SmartCocoonRoomTemperatureSensor(CoordinatorEntity, SensorEntity):
    """A SmartCocoon room current-temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
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
        self._attr_name = "Temperature"

    @property
    def _reading(self) -> RoomTemperatureReading | None:
        """Return the coordinator reading for this room, if present."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._room_id)

    @property
    def native_value(self) -> float | None:
        """Return the current room temperature."""
        reading = self._reading
        if reading is None:
            return None
        return reading.temperature

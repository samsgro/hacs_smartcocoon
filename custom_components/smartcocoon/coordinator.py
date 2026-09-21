"""DataUpdateCoordinator for SmartCocoon room temperatures."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from pysmartcocoon.errors import RequestError, UnauthorizedError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, ROOM_TEMPERATURE_UPDATE_INTERVAL
from .room_refresh import async_fetch_rooms

if TYPE_CHECKING:
    from pysmartcocoon.manager import SmartCocoonManager

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RoomTemperatureReading:
    """Room temperature exposed to Home Assistant."""

    room_id: int
    name: str
    temperature: float


class SmartCocoonRoomCoordinator(
    DataUpdateCoordinator[dict[int, RoomTemperatureReading]],  # type: ignore[misc]
):
    """Poll SmartCocoon for current room temperatures."""

    def __init__(
        self,
        hass: HomeAssistant,
        scmanager: SmartCocoonManager,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=ROOM_TEMPERATURE_UPDATE_INTERVAL,
            config_entry=config_entry,
        )
        self._scmanager = scmanager

    async def _async_update_data(self) -> dict[int, RoomTemperatureReading]:
        """Fetch fresh room temperatures from the cloud API."""
        try:
            rooms = await async_fetch_rooms(self._scmanager)
        except (UnauthorizedError, RequestError) as err:
            raise UpdateFailed(f"Error fetching SmartCocoon room data: {err}") from err

        return {
            room_id: RoomTemperatureReading(
                room_id=room_id,
                name=room.name,
                temperature=room.temperature,
            )
            for room_id, room in rooms.items()
        }

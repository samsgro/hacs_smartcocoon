"""Refresh SmartCocoon room data without swallowing API errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pysmartcocoon.const import API_URL, EntityType
from pysmartcocoon.errors import RequestError
from pysmartcocoon.room import Room

if TYPE_CHECKING:
    from pysmartcocoon.manager import SmartCocoonManager


@dataclass(frozen=True, slots=True)
class RefreshedRoom:
    """A refreshed room and its best available current temperature."""

    room: Room
    current_temperature: float | None


async def async_fetch_rooms(
    scmanager: SmartCocoonManager,
) -> dict[int, RefreshedRoom]:
    """Fetch rooms from the SmartCocoon API and refresh the manager cache.

    Unlike ``SmartCocoonManager.async_update_rooms``, API failures propagate
    instead of returning stale cached room data.
    """
    entity = EntityType.ROOMS.value
    response = await scmanager._api.async_request(  # noqa: SLF001  # pylint: disable=protected-access
        "GET", f"{API_URL}{entity}"
    )

    if not response or entity not in response:
        msg = "Unexpected SmartCocoon rooms API response"
        raise RequestError(msg)

    rooms: dict[int, Room] = {}
    refreshed_rooms: dict[int, RefreshedRoom] = {}
    for item in response[entity]:
        room = Room(data=item)
        rooms[room.identifier] = room
        for fan_payload in item.get("fans", []):
            fan_id = str(fan_payload.get("fan_id", ""))
            fan = scmanager.fans.get(fan_id)
            if fan is not None:
                await fan.async_update_api_data(fan_payload)
        external_sensor = item.get("external_sensor")
        external_temperature = (
            external_sensor.get("temperature")
            if isinstance(external_sensor, dict)
            else None
        )
        temperature = (
            external_temperature
            if external_temperature is not None
            else room.temperature
        )
        refreshed_rooms[room.identifier] = RefreshedRoom(
            room=room,
            current_temperature=(
                float(temperature) if temperature is not None else None
            ),
        )

    scmanager._rooms.clear()  # noqa: SLF001  # pylint: disable=protected-access
    scmanager._rooms.update(rooms)  # noqa: SLF001  # pylint: disable=protected-access

    return refreshed_rooms

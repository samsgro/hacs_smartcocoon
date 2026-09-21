"""Refresh SmartCocoon room data without swallowing API errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pysmartcocoon.const import API_URL, EntityType
from pysmartcocoon.errors import RequestError, UnauthorizedError
from pysmartcocoon.room import Room

if TYPE_CHECKING:
    from pysmartcocoon.manager import SmartCocoonManager


async def async_fetch_rooms(scmanager: SmartCocoonManager) -> dict[int, Room]:
    """Fetch rooms from the SmartCocoon API and refresh the manager cache.

    Unlike ``SmartCocoonManager.async_update_rooms``, API failures propagate
    instead of returning stale cached room data.
    """
    entity = EntityType.ROOMS.value
    try:
        response = await scmanager._api.async_request(  # noqa: SLF001
            "GET", f"{API_URL}{entity}"
        )
    except (UnauthorizedError, RequestError):
        raise

    if not response or entity not in response:
        msg = "Unexpected SmartCocoon rooms API response"
        raise RequestError(msg)

    rooms: dict[int, Room] = {}
    for item in response[entity]:
        room = Room(data=item)
        rooms[room.identifier] = room

    scmanager._rooms.clear()  # noqa: SLF001
    scmanager._rooms.update(rooms)  # noqa: SLF001

    return rooms

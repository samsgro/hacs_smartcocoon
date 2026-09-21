"""Tests for SmartCocoon room temperature sensors."""

# pylint: disable=protected-access
# ruff: noqa: SLF001

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from pysmartcocoon.errors import RequestError
import pytest

from custom_components.smartcocoon import (
    PLATFORMS,
    SmartCocoonController,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smartcocoon.const import DOMAIN, ROOM_TEMPERATURE_UPDATE_INTERVAL
from custom_components.smartcocoon.coordinator import (
    RoomTemperatureReading,
    SmartCocoonRoomCoordinator,
)
from custom_components.smartcocoon.fan import SmartCocoonFan
from custom_components.smartcocoon.room_refresh import async_fetch_rooms
from custom_components.smartcocoon.sensor import (
    SmartCocoonRoomTemperatureSensor,
    async_setup_entry as sensor_async_setup_entry,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import MOCK_USER_INPUT

ROOM_API_PAYLOAD = {
    "rooms": [
        {
            "id": 1,
            "name": "Living Room",
            "desired_temperature": 21.0,
            "hvac_mode": "auto",
            "hvac_state": "idle",
            "is_estimating": False,
            "predicted_temperature": 21.5,
            "target_temperature": 21.0,
            "temperature": 20.5,
            "thermostat_id": 10,
        },
        {
            "id": 2,
            "name": "Bedroom",
            "desired_temperature": 19.0,
            "hvac_mode": "auto",
            "hvac_state": "idle",
            "is_estimating": False,
            "predicted_temperature": 19.0,
            "target_temperature": 19.0,
            "temperature": 18.25,
            "thermostat_id": 11,
        },
    ]
}


def _config_entry() -> ConfigEntry:
    return ConfigEntry(
        version=1,
        domain=DOMAIN,
        title="test@example.com",
        data=MOCK_USER_INPUT,
        source="user",
        options={},
        unique_id="test@example.com",
        minor_version=1,
        discovery_keys=set(),
        subentries_data=[],
    )


def _mock_scmanager() -> MagicMock:
    scmanager = MagicMock()
    scmanager._api = MagicMock()
    scmanager._rooms = {}
    scmanager.fans = {"fan_1": MagicMock(room_name="Living Room", connected=True)}
    return scmanager


@pytest.mark.parametrize(
    ("room_id", "expected"),
    [(1, 20.5), (2, 18.25)],
)
async def test_room_temperature_sensor_value(
    hass: HomeAssistant, room_id: int, expected: float
) -> None:
    """Room sensors report current temperature from coordinator data."""
    scmanager = _mock_scmanager()
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, _config_entry())
    coordinator.async_set_updated_data(
        {
            1: RoomTemperatureReading(1, "Living Room", 20.5),
            2: RoomTemperatureReading(2, "Bedroom", 18.25),
        }
    )

    sensor = SmartCocoonRoomTemperatureSensor(coordinator, room_id)

    assert sensor.native_value == expected
    assert sensor.unique_id == f"{DOMAIN}_room_temperature_{room_id}".lower()


async def test_sensor_does_not_expose_target_or_humidity(hass: HomeAssistant) -> None:
    """Room sensors only expose current temperature, not targets or humidity."""
    scmanager = _mock_scmanager()
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, _config_entry())
    coordinator.async_set_updated_data(
        {1: RoomTemperatureReading(1, "Living Room", 20.5)}
    )

    sensor = SmartCocoonRoomTemperatureSensor(coordinator, 1)

    attributes = sensor.extra_state_attributes or {}
    assert "target_temperature" not in attributes
    assert "desired_temperature" not in attributes
    assert "humidity" not in attributes
    assert sensor.native_value == 20.5


async def test_multi_room_sensor_setup(hass: HomeAssistant) -> None:
    """One temperature sensor is created per room."""
    config_entry = _config_entry()
    scmanager = _mock_scmanager()
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, config_entry)
    coordinator.async_set_updated_data(
        {
            1: RoomTemperatureReading(1, "Living Room", 20.5),
            2: RoomTemperatureReading(2, "Bedroom", 18.25),
        }
    )

    controller = MagicMock(spec=SmartCocoonController)
    controller.room_coordinator = coordinator
    controller.scmanager = scmanager
    hass.data[DOMAIN] = {config_entry.entry_id: controller}

    added: list[SmartCocoonRoomTemperatureSensor] = []

    def capture(entities: list[SmartCocoonRoomTemperatureSensor]) -> None:
        added.extend(entities)

    await sensor_async_setup_entry(hass, config_entry, capture)

    assert len(added) == 2
    assert {entity._room_id for entity in added} == {1, 2}


async def test_sensor_setup_uses_manager_rooms_when_refresh_not_ready(
    hass: HomeAssistant,
) -> None:
    """Sensors can be created from startup room cache before a successful refresh."""
    config_entry = _config_entry()
    scmanager = _mock_scmanager()
    scmanager.rooms = {3: MagicMock(name="Office")}
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, config_entry)
    coordinator.last_update_success = False

    controller = MagicMock(spec=SmartCocoonController)
    controller.room_coordinator = coordinator
    controller.scmanager = scmanager
    hass.data[DOMAIN] = {config_entry.entry_id: controller}

    added: list[SmartCocoonRoomTemperatureSensor] = []

    def capture(entities: list[SmartCocoonRoomTemperatureSensor]) -> None:
        added.extend(entities)

    await sensor_async_setup_entry(hass, config_entry, capture)

    assert len(added) == 1
    assert added[0]._room_id == 3
    assert added[0].available is False


async def test_sensor_setup_adds_rooms_discovered_after_platform_setup(
    hass: HomeAssistant,
) -> None:
    """The first coordinator refresh can discover rooms after platform setup."""
    config_entry = _config_entry()
    scmanager = _mock_scmanager()
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, config_entry)

    controller = MagicMock(spec=SmartCocoonController)
    controller.room_coordinator = coordinator
    controller.scmanager = scmanager
    hass.data[DOMAIN] = {config_entry.entry_id: controller}

    added: list[SmartCocoonRoomTemperatureSensor] = []

    def capture(entities: list[SmartCocoonRoomTemperatureSensor]) -> None:
        added.extend(entities)

    await sensor_async_setup_entry(hass, config_entry, capture)
    assert not added

    coordinator.async_set_updated_data(
        {1: RoomTemperatureReading(1, "Living Room", 20.5)}
    )

    assert len(added) == 1
    assert added[0]._room_id == 1
    assert added[0].native_value == 20.5

    coordinator.async_set_updated_data(
        {1: RoomTemperatureReading(1, "Living Room", 21.0)}
    )
    assert len(added) == 1


async def test_coordinator_poll_interval(hass: HomeAssistant) -> None:
    """Room coordinator refreshes on a 60-second interval."""
    scmanager = _mock_scmanager()
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, _config_entry())

    assert coordinator.update_interval == ROOM_TEMPERATURE_UPDATE_INTERVAL
    assert coordinator.update_interval == timedelta(seconds=60)


async def test_coordinator_refresh_updates_data(hass: HomeAssistant) -> None:
    """Coordinator refresh stores fresh room temperatures."""
    scmanager = _mock_scmanager()
    scmanager._api.async_request = AsyncMock(return_value=ROOM_API_PAYLOAD)
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, _config_entry())

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data is not None
    assert coordinator.data[1].temperature == 20.5
    assert coordinator.data[2].temperature == 18.25


async def test_api_failure_marks_coordinator_stale(hass: HomeAssistant) -> None:
    """Failed room API calls must not look like a successful refresh."""
    scmanager = _mock_scmanager()
    scmanager._api.async_request = AsyncMock(
        side_effect=RequestError("rooms unavailable")
    )
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, _config_entry())
    coordinator.async_set_updated_data(
        {1: RoomTemperatureReading(1, "Living Room", 20.5)}
    )

    await coordinator.async_refresh()
    assert coordinator.last_update_success is False


async def test_async_fetch_rooms_does_not_update_cache_on_failure() -> None:
    """Strict room fetch leaves manager cache untouched when the API fails."""
    scmanager = _mock_scmanager()
    scmanager._rooms[99] = MagicMock(name="stale")
    scmanager._api.async_request = AsyncMock(
        side_effect=RequestError("rooms unavailable")
    )

    with pytest.raises(RequestError):
        await async_fetch_rooms(scmanager)

    assert 99 in scmanager._rooms


async def test_async_fetch_rooms_replaces_cache_on_success() -> None:
    """Successful room fetch replaces the manager room cache."""
    scmanager = _mock_scmanager()
    scmanager._rooms[99] = MagicMock(name="stale")
    scmanager._api.async_request = AsyncMock(return_value=ROOM_API_PAYLOAD)

    rooms = await async_fetch_rooms(scmanager)

    assert set(rooms) == {1, 2}
    assert set(scmanager._rooms) == {1, 2}
    assert 99 not in scmanager._rooms


async def test_sensor_unavailable_when_refresh_fails(hass: HomeAssistant) -> None:
    """Sensors become unavailable when the coordinator refresh fails."""
    scmanager = _mock_scmanager()
    scmanager._api.async_request = AsyncMock(
        side_effect=RequestError("rooms unavailable")
    )
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, _config_entry())
    coordinator.async_set_updated_data(
        {1: RoomTemperatureReading(1, "Living Room", 20.5)}
    )

    await coordinator.async_refresh()

    sensor = SmartCocoonRoomTemperatureSensor(coordinator, 1)
    assert sensor.available is False


async def test_fan_stays_available_when_room_refresh_fails(
    hass: HomeAssistant,
) -> None:
    """A post-setup room refresh failure must not affect fan availability."""
    scmanager = _mock_scmanager()
    fan_data = scmanager.fans["fan_1"]
    fan_data.connected = True
    fan_data.room_name = "Living Room"
    fan_data.firmware_version = "1.0"
    fan_data.fan_on = True
    fan_data.speed_pct = 50
    fan_data.mode = "auto"

    controller = MagicMock(spec=SmartCocoonController)
    controller.scmanager = scmanager
    controller.enable_preset_modes = False
    controller.connection_monitor = None

    scmanager._api.async_request = AsyncMock(
        side_effect=RequestError("rooms unavailable")
    )
    coordinator = SmartCocoonRoomCoordinator(hass, scmanager, _config_entry())
    controller.room_coordinator = coordinator
    coordinator.async_set_updated_data(
        {1: RoomTemperatureReading(1, "Living Room", 20.5)}
    )

    await coordinator.async_refresh()

    fan = SmartCocoonFan(hass, controller, "fan_1")
    sensor = SmartCocoonRoomTemperatureSensor(coordinator, 1)

    assert fan.available is True
    assert sensor.available is False


async def test_async_stop_does_not_shutdown_coordinator(hass: HomeAssistant) -> None:
    """Normal stop only halts the connection monitor."""
    controller = SmartCocoonController(
        username="user",
        password="pass",
        enable_preset_modes=False,
        hass=hass,
    )
    monitor = MagicMock()
    monitor.stop_monitoring = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()

    controller._connection_monitor = monitor
    controller.set_room_coordinator(coordinator)

    await controller.async_stop()

    monitor.stop_monitoring.assert_awaited_once()
    coordinator.async_shutdown.assert_not_called()


async def test_async_stop_after_failed_setup_awaits_shutdown(
    hass: HomeAssistant,
) -> None:
    """Failed setup must await coordinator shutdown and stop monitoring."""
    controller = SmartCocoonController(
        username="user",
        password="pass",
        enable_preset_modes=False,
        hass=hass,
    )
    monitor = MagicMock()
    monitor.stop_monitoring = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()

    controller._connection_monitor = monitor
    controller.set_room_coordinator(coordinator)

    await controller.async_stop_after_failed_setup()

    coordinator.async_shutdown.assert_awaited_once()
    monitor.stop_monitoring.assert_awaited_once()
    assert controller.room_coordinator is None


async def test_unload_stops_connection_monitor(hass: HomeAssistant) -> None:
    """Unload stops the connection monitor via async_stop."""
    config_entry = _config_entry()
    controller = MagicMock(spec=SmartCocoonController)
    controller.async_stop = AsyncMock()
    hass.data[DOMAIN] = {config_entry.entry_id: controller}

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=True
    ) as mock_unload:
        result = await async_unload_entry(hass, config_entry)

    assert result is True
    mock_unload.assert_called_once_with(config_entry, PLATFORMS)
    controller.async_stop.assert_awaited_once()
    assert config_entry.entry_id not in hass.data[DOMAIN]


async def test_setup_loads_fans_when_initial_room_refresh_fails(
    hass: HomeAssistant,
) -> None:
    """Rooms API failure during setup must not block the fan platform."""
    config_entry = _config_entry()
    forwarded: list[str] = []

    async def fake_start(self: SmartCocoonController) -> bool:
        self._scmanager = _mock_scmanager()
        self._scmanager.rooms = {1: MagicMock(name="Living Room")}
        return True

    async def capture_forward(_entry: ConfigEntry, platforms: list[str]) -> None:
        forwarded.extend(platforms)

    with (
        patch.object(SmartCocoonController, "async_start", fake_start),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=capture_forward,
        ),
        patch(
            "custom_components.smartcocoon.coordinator.async_fetch_rooms",
            side_effect=RequestError("rooms unavailable"),
        ),
        patch(
            "custom_components.smartcocoon._async_register_services",
            new=AsyncMock(),
        ),
    ):
        result = await async_setup_entry(hass, config_entry)

    assert result is True
    assert forwarded == PLATFORMS
    controller = hass.data[DOMAIN][config_entry.entry_id]
    assert controller.room_coordinator is not None
    assert controller.room_coordinator.last_update_success is False


async def test_setup_failure_after_start_cleans_up(hass: HomeAssistant) -> None:
    """Setup failures after async_start stop the monitor and coordinator."""
    config_entry = _config_entry()

    async def fake_start(self: SmartCocoonController) -> bool:
        self._scmanager = _mock_scmanager()
        self._connection_monitor = MagicMock()
        self._connection_monitor.stop_monitoring = AsyncMock()
        return True

    with (
        patch.object(SmartCocoonController, "async_start", fake_start),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=RuntimeError("platform setup failed"),
        ),
        patch(
            "custom_components.smartcocoon.SmartCocoonRoomCoordinator"
        ) as coordinator_class,
    ):
        mock_coordinator = MagicMock()
        mock_coordinator.async_shutdown = AsyncMock()
        coordinator_class.return_value = mock_coordinator

        with pytest.raises(RuntimeError, match="platform setup failed"):
            await async_setup_entry(hass, config_entry)

    mock_coordinator.async_shutdown.assert_awaited_once()
    assert config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_setup_entry_starts_room_coordinator(hass: HomeAssistant) -> None:
    """Config entry setup creates the room coordinator and refreshes after platforms."""
    config_entry = _config_entry()

    async def fake_start(self: SmartCocoonController) -> bool:
        self._scmanager = _mock_scmanager()
        return True

    with (
        patch.object(SmartCocoonController, "async_start", fake_start),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", return_value=None
        ),
        patch(
            "custom_components.smartcocoon.SmartCocoonRoomCoordinator"
        ) as coordinator_class,
        patch(
            "custom_components.smartcocoon._async_register_services",
            new=AsyncMock(),
        ),
    ):
        mock_coordinator = MagicMock()
        mock_coordinator.async_refresh = AsyncMock()
        coordinator_class.return_value = mock_coordinator

        result = await async_setup_entry(hass, config_entry)

    assert result is True
    coordinator_class.assert_called_once()
    mock_coordinator.async_refresh.assert_awaited_once()
    controller = hass.data[DOMAIN][config_entry.entry_id]
    assert controller.room_coordinator is mock_coordinator

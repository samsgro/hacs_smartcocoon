# Independent review: SmartCocoon room-temperature

**Reviewer:** dispatched worker `task_7b4718a882cc`  
**Scope:** full diff vs `cfb1080` (main), architecture, HA conventions, tests/lint  
**Edits:** none  
**Recommendation:** **NO-SHIP**

Diff: 8 files, +521/−6 (`f448133`). Fan platform source is untouched; setup coupling is not.

---

## Checklist vs the stated requirements

| Requirement | Verdict | Notes |
|---|---|---|
| HA entity/coordinator conventions | Mixed | Coordinator + `CoordinatorEntity` + device class/unit/state class are right. First refresh is a hard setup gate. `async_shutdown()` is not awaited. |
| 60-second refresh | Pass | `ROOM_TEMPERATURE_UPDATE_INTERVAL = timedelta(seconds=60)`; coordinator stores it; test asserts both. |
| Unload cleanup | Mixed | Unload now calls `async_stop`. Coordinator is also registered on `config_entry.async_on_unload`. Explicit `async_shutdown()` is an unawaited coroutine. Failed first refresh leaks the connection monitor. |
| Stable IDs | Pass | `smartcocoon_room_temperature_{room_id}` matches fan `smartcocoon_fan_{fan_id}`. Device id `smartcocoon_room_{room_id}`. Room id can change if the room is re-added (library caveat). |
| No target/humidity exposure | Pass | `RoomTemperatureReading` only has `room_id`, `name`, `temperature`. Sensor has no `extra_state_attributes`. Payload fixtures include target/desired but they are not mapped. Library `Room` has no humidity field. |
| Fan behavior preserved | **Fail** | `async_config_entry_first_refresh()` before `async_forward_entry_setups` makes a rooms-API failure `ConfigEntryNotReady` for the **entire** entry, including fans. |
| Truthful availability/staleness on API failure | Pass after setup; fail at setup | After load, `UpdateFailed` → `last_update_success is False` → `CoordinatorEntity.available` is False; cache is not rewritten. At setup, a rooms failure blocks fans instead of marking sensors unavailable. |

---

## P0 — must fix before ship

### 1. Rooms first-refresh is a hard gate for fans

```218:227:custom_components/smartcocoon/__init__.py
    room_coordinator = SmartCocoonRoomCoordinator(
        hass, smartcocoon.scmanager, config_entry
    )
    await room_coordinator.async_config_entry_first_refresh()
    smartcocoon.set_room_coordinator(room_coordinator)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry.entry_id] = smartcocoon

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
```

HA 2026.2.3 `DataUpdateCoordinator.async_config_entry_first_refresh` raises `ConfigEntryNotReady` when `last_update_success` is False (`.venv/.../update_coordinator.py` 309–349). That happens **before** platforms are forwarded, so fans never load.

This is a regression against `pysmartcocoon` itself: `SmartCocoonManager.async_update_rooms` swallows `UnauthorizedError`/`RequestError` and returns the cache (`manager.py` 121–130). `async_start_services` already used that soft path. The new strict fetch undoes it at setup.

A rooms outage or empty/unexpected rooms payload now puts the config entry in `SETUP_RETRY`. Users lose vent control until rooms recover.

**Fix direction:** start fans regardless of rooms. Use `async_refresh()` (not first-refresh) or catch `ConfigEntryNotReady` and still forward platforms. Sensors already skip when `coordinator.data is None` (`sensor.py` 36–38).

**Test gap:** no test that a rooms `RequestError` during setup still loads fans. Existing setup tests patch the coordinator class and always succeed first refresh (`tests/test_temperature_sensor.py` 239–266, `tests/test_final.py` 741–786).

### 2. CI will fail on this branch

Full pytest: **98 passed** (`pytest -q --no-cov`). Production ruff: clean. That is not enough; CI runs `pre-commit run --all-files` (`.github/workflows/tests.yaml`).

**ruff** on `tests/test_temperature_sensor.py`:

- `I001` unsorted imports (missing blank line before `from .const`)
- `RUF100` unused `noqa: SLF001` at lines 133 and 244

**mypy --strict** on the new modules (7 errors):

- `__init__.py:166` `unused-coroutine` — `async_shutdown()` not awaited
- `coordinator.py:33` unused `# type: ignore[misc]`
- `coordinator.py:52` `_async_update_data` return type incompatible with untyped `DataUpdateCoordinator` (should be `DataUpdateCoordinator[dict[int, RoomTemperatureReading]]`)
- `sensor.py:41` `room_id` inferred as `str` because coordinator data is untyped
- `sensor.py:80–81` unreachable / `dict.get` overload mismatch (same root cause)

**pylint** on `room_refresh.py`: `W0706` try-except-raise; `W0212` protected `_api` / `_rooms` (3×). Pre-commit pylint disables `import-error` and `unused-argument` only, so `W0706`/`W0212` fail the hook.

---

## P1 — should fix before ship

### 3. `async_shutdown()` is not awaited

```163:168:custom_components/smartcocoon/__init__.py
    async def async_stop(self) -> None:
        """Stop the SmartCocoon Manager and cleanup resources."""
        if self._room_coordinator:
            self._room_coordinator.async_shutdown()
            self._room_coordinator = None
```

HA 2026.2.3 defines `async def async_shutdown` (`.venv/.../update_coordinator.py` 202–207). Calling it without `await` creates a coroutine that never runs. mypy reports this as `unused-coroutine`.

Normal HA unload still works: coordinator `__init__` registers `config_entry.async_on_unload(self.async_shutdown)` (update_coordinator.py 148–149), and `ConfigEntry.async_unload` awaits those callbacks after `async_unload_entry` returns (`config_entries.py` 1002–1004). The explicit call is still wrong (RuntimeWarning / double-intent) and the tests do not prove it.

`test_unload_shuts_down_room_coordinator` (`tests/test_temperature_sensor.py` 218–236) mocks `controller.async_stop` entirely. It never asserts `coordinator.async_shutdown`. The `coordinator.async_shutdown = MagicMock()` assignment is unused.

### 4. Failed first refresh leaks the connection monitor

Order in `async_setup_entry`:

1. `await smartcocoon.async_start()` starts `ConnectionMonitor`
2. Coordinator is constructed (registers `async_on_unload(async_shutdown)`)
3. `async_config_entry_first_refresh()` raises `ConfigEntryNotReady`
4. `hass.data` is never written; `async_stop` is never called

On `ConfigEntryNotReady`, HA still runs `_async_process_on_unload` in `finally` because `result` stays `False` (`config_entries.py` 757–767, 795–835, 865–867). That **does** shut down the coordinator. It does **not** stop the connection monitor. Each setup retry starts another monitor.

This is new: first-refresh is a new failure point after `async_start`.

---

## P2 — should fix soon

### 5. No runtime room discovery

Sensors are created once from the first snapshot (`sensor.py` 40–44). A room added later never gets an entity until reload. A room removed later stays registered; `native_value` becomes `None` while `available` stays True if the last refresh succeeded. Prefer `async_add_entities` on coordinator updates, or mark missing rooms unavailable.

### 6. Private `pysmartcocoon` coupling

`room_refresh.py` 23–39 uses `scmanager._api.async_request` and mutates `scmanager._rooms`. Justified (library swallows errors) but brittle. The try/except that immediately re-raises (lines 26–27) is dead (`pylint W0706`).

Successful replace of `_rooms` is event-loop atomic (no await between `clear` and `update`). Fan `room_name` is cached on the fan object, so a later fan refresh that looks up a missing room gets `"Unknown"` (`manager.py` 178–192).

### 7. `iot_class` still `cloud_push`

`manifest.json` line 7. The integration now polls rooms every 60s. Quality-scale / HACS metadata should be `cloud_polling` or document the mixed push+poll model.

### 8. Entity name vs translation

`sensor.py` sets `_attr_has_entity_name`, `_attr_translation_key = "room_temperature"`, **and** `_attr_name = "Temperature"`. The hardcoded name wins; `translations/en.json` is unused. Drop `_attr_name`.

### 9. Auth failures are not `ConfigEntryAuthFailed`

`UnauthorizedError` is wrapped as `UpdateFailed` (`coordinator.py` 56–57). After setup, sensors go unavailable (good). At first refresh, HA retries rather than starting reauth (`update_coordinator.py` 474–489). Raise `ConfigEntryAuthFailed` for 401/403.

---

## P3 — nits

- Coordinator is not generic: `DataUpdateCoordinator[dict[int, RoomTemperatureReading]]` would remove the `# type: ignore` and the sensor mypy errors.
- `sensor.py` 36–38 silent skip is dead after a successful first refresh (`data` is a dict, possibly empty). It becomes useful only if first-refresh is no longer a gate.
- Unique id does not include `config_entry.entry_id`. Matches existing fan IDs; fine while config unique_id is username.
- Device name is frozen at init (`sensor.py` 68–73). Room rename in the app does not update the HA device name.
- No test that `extra_state_attributes` is empty / that target and desired temperatures never appear.
- `test_sensor_unavailable_when_refresh_fails` constructs the sensor after the failed refresh; it does not add it as a listener. The `available` property still reads `last_update_success`, so the assertion is valid.

---

## What is done well

Architecture is the right HA shape: one `DataUpdateCoordinator`, `CoordinatorEntity` sensors, Celsius + `SensorDeviceClass.TEMPERATURE` + `SensorStateClass.MEASUREMENT`, `has_entity_name`.

Post-setup failure handling is truthful:

- `_async_update_data` raises `UpdateFailed` on `UnauthorizedError`/`RequestError`
- `async_fetch_rooms` does not touch `_rooms` on failure (`tests/test_temperature_sensor.py` 174–185)
- `last_update_success is False` (`159–171`)
- `sensor.available is False` via `CoordinatorEntity.available` (`201–215`)
- Unexpected exceptions are also marked failed by HA (`update_coordinator.py` 418–505)
- Stale `coordinator.data` is retained but not shown as current because the entity is unavailable

Fan.py is unchanged. Target/desired/predicted/humidity are not exposed on the new sensor. IDs follow the existing domain prefix pattern. Interval is 60s. Unload now invokes `async_stop` (previously it only popped `hass.data`).

---

## Test evidence

```
$ .venv/bin/python -m pytest -q --tb=line --no-cov
98 passed

$ .venv/bin/ruff check custom_components/smartcocoon/{coordinator,sensor,room_refresh,__init__,const}.py
All checks passed

$ .venv/bin/ruff check tests/test_temperature_sensor.py
I001, RUF100 ×2  (FAIL)

$ .venv/bin/pylint custom_components/smartcocoon/room_refresh.py
W0706, W0212 ×3  (exit 4)

$ .venv/bin/mypy custom_components/smartcocoon/{coordinator,sensor,room_refresh,__init__}.py --strict ...
7 errors (FAIL), including unused-coroutine at __init__.py:166
```

Missing coverage that would have caught P0/P1:

- rooms API failure during `async_setup_entry` still forwards the fan platform
- `SmartCocoonController.async_stop` actually awaits `coordinator.async_shutdown`
- ConfigEntryNotReady after `async_start` stops the connection monitor
- no target/humidity attributes on the sensor

---

## Ship / no-ship

**NO-SHIP.**

The runtime availability path after a successful load is correct and tested. The setup path is not: a rooms-cloud failure now takes fans with it, retries leak a connection monitor, `async_shutdown` is not awaited, and pre-commit (ruff / pylint / mypy) will fail CI.

Minimum to reconsider ship:

1. Decouple fan setup from rooms first-refresh.
2. `await` coordinator shutdown; stop the connection monitor if setup fails after `async_start`.
3. Make pre-commit green on the new files.
4. Add tests for (1) and (2).

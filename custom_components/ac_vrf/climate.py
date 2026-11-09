"""Climate platform for AC VRF Controller."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import socket
import time
import urllib.parse
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, PlatformNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ac_vrf"

# Mode mappings from API
MODE_TO_HVAC = {
    1: HVACMode.COOL,
    2: HVACMode.DRY,
    4: HVACMode.FAN_ONLY,  # Fresh Air
    8: HVACMode.HEAT,
}

HVAC_TO_MODE = {
    HVACMode.COOL: 1,
    HVACMode.DRY: 2,
    HVACMode.FAN_ONLY: 4,  # Fresh Air
    HVACMode.HEAT: 8,
}

FAN_MODE_MAP = {
    4: "low",
    2: "medium",
    1: "high",
    8: "auto",
}

FAN_MODE_REVERSE = {v: k for k, v in FAN_MODE_MAP.items()}

# The controller's status endpoint is paged; these are the pages we read.
STATUS_PAGES = (0, 1)

# One poll of the controller per interval, shared by all units.
UPDATE_INTERVAL = timedelta(seconds=3)

# Pause after each command before the next one is sent to the controller.
COMMAND_GAP_SECONDS = 0.3

# After a command, the controller's status can lag behind for a few seconds.
# During this window a poll that doesn't match what was commanded is treated as
# stale and doesn't overwrite the entity's state.
COMMAND_GRACE_SECONDS = 10

SOCKET_TIMEOUT = 10

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
) -> None:
    """Set up the AC VRF climate platform from configuration.yaml."""
    host = config[CONF_HOST]
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

    coordinator = ACVRFCoordinator(hass, host, username, password)
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        # Home Assistant retries platform setup with back-off, so the
        # entities appear once the controller becomes reachable.
        raise PlatformNotReady(f"No AC units could be read from controller at {host}")

    _LOGGER.info(
        "AC VRF: Discovered %d units on controller at %s", len(coordinator.data), host
    )
    async_add_entities(
        ACVRFClimate(coordinator, idx, host) for idx in coordinator.data
    )


class ACVRFCoordinator(DataUpdateCoordinator[dict[Any, dict[str, Any]]]):
    """Polls the controller once per interval for the status of all units.

    `data` maps each unit's `idx` to the unit dict returned by the controller.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name=f"AC VRF {host}",
            update_interval=UPDATE_INTERVAL,
        )
        self.host = host
        self.username = username
        self.password = password if password is not None else ""
        # Pages that failed on the previous poll, so a persistent failure is
        # logged once rather than on every poll.
        self._failed_pages: set[int] = set()
        # The controller gets one command at a time.
        self._command_lock = asyncio.Lock()

    async def async_send_command(self, idx: Any, build_params: Callable[[], dict[str, Any]]) -> None:
        """Send a command for one unit.

        `build_params` is called once the command lock is held, so the command
        carries the unit's latest desired state even if other commands for the
        same unit were queued ahead of it.
        """
        async with self._command_lock:
            params = build_params()
            url = (
                f"http://{self.host}/cgi-bin/api.html?"
                f"f=18&on={params['on']}&mode={params['mode']}"
                f"&tempSet={params['tempSet']}&fan={params['fan']}"
                f"&FlowDirection1={params['FlowDirection1']}"
                f"&FlowDirection2={params['FlowDirection2']}"
                f"&idx={idx}"
            )
            try:
                text = await self.hass.async_add_executor_job(self._fetch_raw, url)
            finally:
                await asyncio.sleep(COMMAND_GAP_SECONDS)

        _LOGGER.debug("AC VRF: unit %s <- %s. Response: %s", idx, params, text[:100])
        try:
            result = self._parse_status(text)
        except ValueError:
            return  # no JSON body; nothing to check
        if result.get("err", 0) != 0:
            _LOGGER.warning(
                "AC VRF: Controller returned error code %r for command to unit %s",
                result.get("err"),
                idx,
            )

    def _fetch_raw(self, url: str) -> str:
        """Fetch data using a raw (blocking) socket.

        The controller answers with HTTP/0.9-style responses (no status line or
        headers), which aiohttp rejects, so the request is made by hand. This
        is blocking I/O and must run via `hass.async_add_executor_job()`.
        """
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path
        if parsed.query:
            path += f"?{parsed.query}"

        request = f"GET {path} HTTP/1.0\r\n"
        request += f"Host: {host}\r\n"

        if self.username:
            credentials = f"{self.username}:{self.password}"
            b64_credentials = base64.b64encode(credentials.encode()).decode()
            request += f"Authorization: Basic {b64_credentials}\r\n"

        request += "Connection: close\r\n\r\n"

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(SOCKET_TIMEOUT)

        try:
            sock.connect((host, port))
            sock.sendall(request.encode())

            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk

            response_text = response.decode("utf-8", errors="ignore")

            # The response may not have proper HTTP headers; return from the
            # first JSON object onwards if there is one.
            json_start = response_text.find("{")
            if json_start >= 0:
                return response_text[json_start:]

            return response_text

        finally:
            sock.close()

    @staticmethod
    def _parse_status(text: str) -> dict[str, Any]:
        """Parse the first JSON object in a status response.

        Some controllers append extra bytes after the JSON object, which makes
        `json.loads()` fail with "Extra data", so only the first object is
        decoded and anything after it is ignored.
        """
        raw = text.strip()
        first_brace = raw.find("{")
        if first_brace > 0:
            raw = raw[first_brace:]
        data, end = json.JSONDecoder().raw_decode(raw)
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        trailing = raw[end:].strip()
        if trailing:
            _LOGGER.debug(
                "AC VRF: Ignoring %d trailing bytes after JSON payload: %r",
                len(trailing),
                trailing[:120],
            )
        return data

    async def _async_fetch_page(self, page: int) -> list[dict[str, Any]]:
        """Fetch one status page and return its units."""
        url = f"http://{self.host}/cgi-bin/api.html?f=17&p={page}"
        text = await self.hass.async_add_executor_job(self._fetch_raw, url)
        _LOGGER.debug("AC VRF: Page %d response (first 200 chars): %s", page, text[:200])

        try:
            data = self._parse_status(text)
        except ValueError as err:
            raise ValueError(f"invalid JSON ({err}); response: {text[:200]!r}") from err

        if data.get("err") != 0:
            raise ValueError(f"controller returned error code {data.get('err')!r}")
        if "unit" not in data:
            raise ValueError("response has no 'unit' list")
        return data["unit"]

    async def _async_update_data(self) -> dict[Any, dict[str, Any]]:
        """Fetch the status of all units from the controller."""
        units: dict[Any, dict[str, Any]] = {}
        errors: list[str] = []

        for page in STATUS_PAGES:
            try:
                page_units = await self._async_fetch_page(page)
            except (OSError, ValueError) as err:
                errors.append(f"page {page}: {err}")
                if page not in self._failed_pages:
                    self._failed_pages.add(page)
                    _LOGGER.warning(
                        "AC VRF: Failed to read status page %d from %s: %s",
                        page,
                        self.host,
                        err,
                    )
                continue

            if page in self._failed_pages:
                self._failed_pages.discard(page)
                _LOGGER.info("AC VRF: Status page %d from %s recovered", page, self.host)

            for unit in page_units:
                if isinstance(unit, dict) and "idx" in unit:
                    units[unit["idx"]] = unit
            _LOGGER.debug("AC VRF: Page %d returned %d units", page, len(page_units))

        if not units:
            # Marks every entity unavailable; the coordinator logs this once
            # and logs again when the controller recovers.
            raise UpdateFailed(
                f"No AC units read from {self.host}: " + ("; ".join(errors) or "empty response")
            )
        return units


class ACVRFClimate(CoordinatorEntity[ACVRFCoordinator], ClimateEntity):
    """Representation of an AC VRF unit."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ]
    _attr_fan_modes = list(FAN_MODE_REVERSE)
    _attr_min_temp = 16
    _attr_max_temp = 30
    _attr_target_temperature_step = 1

    def __init__(self, coordinator: ACVRFCoordinator, idx: Any, host: str) -> None:
        """Initialize the climate device."""
        super().__init__(coordinator)
        self._idx = idx
        self._host = host
        # Must stay in this format: it ties the entities to the existing
        # entity registry entries (entity IDs, names, customizations).
        self._attr_unique_id = f"ac_vrf_{host}_{idx}"

        # Desired state: what the unit should be doing. Every command sends
        # the full state, built from these, never from the last poll. That
        # way two commands in a row (e.g. Versatile Thermostat sending
        # set_hvac_mode then set_temperature) can't undo each other while the
        # controller's status is still catching up. Raw controller values are
        # kept so unknown modes/fan codes are sent back unchanged.
        self._on: int | None = None
        self._mode: Any = None
        self._temp: int | None = None
        self._fan: Any = None
        self._flow1: Any = 0
        self._flow2: Any = 0
        # Until this time (monotonic), polls that don't match the desired
        # state are considered stale.
        self._grace_until = 0.0

        self._sync_from_unit(force=True)

    @property
    def available(self) -> bool:
        """Return True if the last poll succeeded and included this unit."""
        return super().available and self._idx in self.coordinator.data

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._sync_from_unit()
        super()._handle_coordinator_update()

    @staticmethod
    def _read_unit(unit: dict[str, Any]) -> dict[str, Any] | None:
        """Extract the fields we use from a unit dict, or None if malformed."""
        try:
            return {
                "name": unit["nm"],
                "on": 1 if unit["on"] == 1 else 0,
                "mode": unit["mode"],
                "temp": int(float(unit["tempSet"])),
                "temp_in": float(unit["tempIn"]),
                "fan": unit["fan"],
                "flow1": unit.get("FlowDirection1", 0),
                "flow2": unit.get("FlowDirection2", 0),
            }
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("AC VRF: Unexpected status data for unit %s (%s): %s", unit.get("idx"), err, unit)
            return None

    def _matches(self, status: dict[str, Any]) -> bool:
        """Return True if the controller reports the desired state."""
        return (
            status["on"] == self._on
            and status["mode"] == self._mode
            and status["temp"] == self._temp
            and status["fan"] == self._fan
        )

    def _sync_from_unit(self, force: bool = False) -> None:
        """Update state from this unit's entry in the coordinator data."""
        unit = self.coordinator.data.get(self._idx)
        status = self._read_unit(unit) if unit is not None else None
        if status is None:
            return

        self._attr_name = status["name"]
        self._attr_current_temperature = status["temp_in"]

        in_grace = time.monotonic() < self._grace_until
        if in_grace and not force:
            if not self._matches(status):
                # The controller hasn't caught up with the last command yet;
                # keep showing (and sending) the commanded state.
                return
            self._grace_until = 0.0

        self._on = status["on"]
        self._mode = status["mode"]
        self._temp = status["temp"]
        self._fan = status["fan"]
        self._flow1 = status["flow1"]
        self._flow2 = status["flow2"]
        self._update_attrs()

    def _update_attrs(self) -> None:
        """Derive the Home Assistant attributes from the desired state."""
        self._attr_hvac_mode = (
            MODE_TO_HVAC.get(self._mode, HVACMode.COOL) if self._on else HVACMode.OFF
        )
        self._attr_target_temperature = float(self._temp) if self._temp is not None else None
        self._attr_fan_mode = FAN_MODE_MAP.get(self._fan, "auto")

    async def _push(self) -> None:
        """Send the desired state to the controller and show it right away."""
        if self._on is None or self._temp is None:
            raise HomeAssistantError(f"State of AC unit {self.name} is not known yet")

        self._grace_until = time.monotonic() + COMMAND_GRACE_SECONDS
        self._update_attrs()
        self.async_write_ha_state()

        try:
            await self.coordinator.async_send_command(
                self._idx,
                lambda: {
                    "on": self._on,
                    "mode": self._mode,
                    "tempSet": self._temp,
                    "fan": self._fan,
                    "FlowDirection1": self._flow1,
                    "FlowDirection2": self._flow2,
                },
            )
        except OSError as err:
            # Nothing reached the controller: go back to its last known state.
            self._grace_until = 0.0
            self._sync_from_unit(force=True)
            self.async_write_ha_state()
            raise HomeAssistantError(
                f"Failed to send command to AC unit {self.name}: {err}"
            ) from err

        await self.coordinator.async_request_refresh()

    def _set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            self._on = 0
        else:
            self._on = 1
            self._mode = HVAC_TO_MODE.get(hvac_mode, 1)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        self._set_hvac_mode(hvac_mode)
        await self._push()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature (and optionally the hvac mode).

        Without an hvac_mode the unit's on/off state is preserved: setting a
        temperature on a unit that is off stores the setpoint without turning
        it on.
        """
        temperature = kwargs.get(ATTR_TEMPERATURE)
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if temperature is None and hvac_mode is None:
            return
        if hvac_mode is not None:
            self._set_hvac_mode(hvac_mode)
        if temperature is not None:
            # The controller takes whole degrees; round (Versatile Thermostat
            # sends fractional setpoints) and keep within the unit's range.
            # (half-up, so 24.5 -> 25; Python's round() would give 24).
            self._temp = max(
                self._attr_min_temp,
                min(self._attr_max_temp, math.floor(float(temperature) + 0.5)),
            )
        await self._push()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        fan = FAN_MODE_REVERSE.get(fan_mode)
        if fan is not None:
            self._fan = fan
            await self._push()

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        self._on = 1
        await self._push()

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        self._on = 0
        await self._push()

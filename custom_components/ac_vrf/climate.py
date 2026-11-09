"""Climate platform for AC VRF Controller."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import urllib.parse
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
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

# Give the controller a moment to apply a command before re-reading status.
COMMAND_SETTLE_DELAY = 0.5

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

        # Raw controller values, sent back unchanged in commands.
        self._current_mode = None
        self._current_fan = None
        self._current_flow_dir1 = 0
        self._current_flow_dir2 = 0

        self._update_from_unit()

    @property
    def available(self) -> bool:
        """Return True if the last poll succeeded and included this unit."""
        return super().available and self._idx in self.coordinator.data

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_from_unit()
        super()._handle_coordinator_update()

    def _update_from_unit(self) -> None:
        """Update state from this unit's entry in the coordinator data."""
        unit = self.coordinator.data.get(self._idx)
        if unit is None:
            return

        try:
            name = unit["nm"]
            is_on = unit["on"] == 1
            mode = unit["mode"]
            temp_set = float(unit["tempSet"])
            temp_in = float(unit["tempIn"])
            fan = unit["fan"]
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug(
                "AC VRF: Unexpected status data for unit %s (%s): %s", self._idx, err, unit
            )
            return

        self._attr_name = name
        self._attr_hvac_mode = (
            MODE_TO_HVAC.get(mode, HVACMode.COOL) if is_on else HVACMode.OFF
        )
        self._attr_target_temperature = temp_set
        self._attr_current_temperature = temp_in
        self._attr_fan_mode = FAN_MODE_MAP.get(fan, "auto")

        self._current_mode = mode
        self._current_fan = fan
        self._current_flow_dir1 = unit.get("FlowDirection1", 0)
        self._current_flow_dir2 = unit.get("FlowDirection2", 0)

    async def _send_command(
        self,
        on: int,
        mode: int | None = None,
        temp_set: float | None = None,
        fan: int | None = None,
    ) -> None:
        """Send a command to the AC unit, then refresh status."""
        # The controller sets every field at once, so unspecified fields are
        # sent with their current values.
        if mode is None:
            mode = self._current_mode
        if temp_set is None:
            temp_set = self._attr_target_temperature
        if fan is None:
            fan = self._current_fan

        url = (
            f"http://{self._host}/cgi-bin/api.html?"
            f"f=18&on={on}&mode={mode}&tempSet={int(temp_set)}&fan={fan}"
            f"&FlowDirection1={self._current_flow_dir1}"
            f"&FlowDirection2={self._current_flow_dir2}"
            f"&idx={self._idx}"
        )

        try:
            text = await self.hass.async_add_executor_job(
                self.coordinator._fetch_raw, url
            )
        except OSError as err:
            raise HomeAssistantError(
                f"Failed to send command to AC unit {self.name}: {err}"
            ) from err

        _LOGGER.debug(
            "AC VRF: Command sent to unit %s: on=%s, mode=%s, temp=%s, fan=%s. Response: %s",
            self._idx,
            on,
            mode,
            temp_set,
            fan,
            text[:100],
        )

        await asyncio.sleep(COMMAND_SETTLE_DELAY)
        await self.coordinator.async_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            await self._send_command(on=0)
        else:
            mode = HVAC_TO_MODE.get(hvac_mode, 1)
            await self._send_command(on=1, mode=mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature.

        The unit's on/off state is preserved: setting a temperature on a unit
        that is off stores the setpoint without turning it on.
        """
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            is_on = 1 if self._attr_hvac_mode != HVACMode.OFF else 0
            await self._send_command(on=is_on, temp_set=temperature)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        fan = FAN_MODE_REVERSE.get(fan_mode)
        if fan is not None:
            is_on = 1 if self._attr_hvac_mode != HVACMode.OFF else 0
            await self._send_command(on=is_on, fan=fan)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        await self._send_command(on=1)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self._send_command(on=0)

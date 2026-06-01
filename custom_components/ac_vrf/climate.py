"""Climate platform for AC VRF Controller."""
import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import aiohttp
import async_timeout
from aiohttp import BasicAuth

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ac_vrf"

MODE_TO_HVAC = {
    1: HVACMode.COOL,
    2: HVACMode.DRY,
    4: HVACMode.FAN_ONLY,
    8: HVACMode.HEAT,
}

HVAC_TO_MODE = {
    HVACMode.COOL: 1,
    HVACMode.DRY: 2,
    HVACMode.FAN_ONLY: 4,
    HVACMode.HEAT: 8,
    HVACMode.OFF: 1,
}

FAN_MODE_MAP = {
    1: "low",
    2: "medium-low",
    3: "medium",
    4: "high",
    5: "auto",
}

FAN_MODE_REVERSE = {v: k for k, v in FAN_MODE_MAP.items()}

SCAN_INTERVAL = timedelta(seconds=2)


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
):
    """Set up the AC VRF climate platform from configuration.yaml."""
    host = config.get("host")
    username = config.get("username")
    password = config.get("password")

    if not host:
        _LOGGER.error("AC VRF: 'host' is required in configuration")
        return False

    coordinator = ACVRFCoordinator(hass, host, username, password)
    await coordinator.async_refresh()

    if coordinator.data:
        async_add_entities(
            [ACVRFClimate(coordinator, unit["idx"]) for unit in coordinator.data],
            True,
        )
    else:
        _LOGGER.warning("AC VRF: No units discovered from controller at %s", host)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up AC VRF climate from a config entry."""
    host = entry.data["host"]
    username = entry.data.get("username")
    password = entry.data.get("password")

    coordinator = ACVRFCoordinator(hass, host, username, password)
    await coordinator.async_config_entry_first_refresh()

    async_add_entities(
        [ACVRFClimate(coordinator, unit["idx"]) for unit in coordinator.data],
        True,
    )


class ACVRFCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch data from AC VRF controller."""

    def __init__(self, hass: HomeAssistant, host: str, username: str = None, password: str = None):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="AC VRF",
            update_interval=SCAN_INTERVAL,
        )
        self.host = host
        self._auth = BasicAuth(username, password or "") if username else None

    async def _async_update_data(self) -> list:
        """Fetch data from API. Called once per interval for all entities."""
        session = async_get_clientsession(self.hass)
        all_units = []

        for page in [0, 1]:
            url = f"http://{self.host}/cgi-bin/api.html?f=17&p={page}"
            try:
                async with async_timeout.timeout(10):
                    async with session.get(url, auth=self._auth) as response:
                        if response.status == 401:
                            raise UpdateFailed(
                                f"Authentication failed for {self.host}. Check username and password."
                            )
                        text = await response.text()
                        try:
                            data = json.loads(text)
                            if data.get("err") == 0 and "unit" in data:
                                all_units.extend(data["unit"])
                            elif data.get("err") != 0:
                                _LOGGER.error("API returned error code: %s", data.get("err"))
                        except json.JSONDecodeError as e:
                            raise UpdateFailed(
                                f"Failed to parse JSON from {url}: {e}. Response: {text[:200]}"
                            )
            except aiohttp.ClientError as e:
                raise UpdateFailed(f"Client error fetching from {url}: {e}")

        _LOGGER.debug("Successfully fetched %d AC units", len(all_units))
        return all_units


class ACVRFClimate(CoordinatorEntity, ClimateEntity):
    """Representation of an AC VRF unit."""

    def __init__(self, coordinator: ACVRFCoordinator, idx: int):
        """Initialize the climate device."""
        super().__init__(coordinator)
        self._idx = idx
        self._session = async_get_clientsession(coordinator.hass)
        self._auth = coordinator._auth
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        self._attr_hvac_modes = [
            HVACMode.OFF,
            HVACMode.COOL,
            HVACMode.HEAT,
            HVACMode.DRY,
            HVACMode.FAN_ONLY,
        ]
        self._attr_fan_modes = list(FAN_MODE_REVERSE.keys())
        self._attr_min_temp = 16
        self._attr_max_temp = 30
        self._attr_target_temperature_step = 1
        self._update_from_coordinator()

    def _update_from_coordinator(self):
        """Update state from coordinator data."""
        for unit in (self.coordinator.data or []):
            if unit["idx"] == self._idx:
                self._attr_name = unit["nm"]
                self._attr_unique_id = f"ac_vrf_{self.coordinator.host}_{self._idx}"

                is_on = unit["on"] == 1
                mode = unit["mode"]

                self._attr_hvac_mode = (
                    MODE_TO_HVAC.get(mode, HVACMode.COOL) if is_on else HVACMode.OFF
                )
                self._attr_target_temperature = float(unit["tempSet"])
                self._attr_current_temperature = float(unit["tempIn"])
                self._attr_fan_mode = FAN_MODE_MAP.get(unit["fan"], "auto")

                self._current_mode = mode
                self._current_fan = unit["fan"]
                self._current_flow_dir1 = unit.get("FlowDirection1", 0)
                self._current_flow_dir2 = unit.get("FlowDirection2", 0)
                break

    @callback
    def _handle_coordinator_update(self):
        """Handle updated data from coordinator."""
        self._update_from_coordinator()
        self.async_write_ha_state()

    async def _send_command(
        self,
        on: int,
        mode: int = None,
        temp_set: float = None,
        fan: int = None,
    ):
        """Send command to AC unit."""
        if mode is None:
            mode = self._current_mode
        if temp_set is None:
            temp_set = self._attr_target_temperature
        if fan is None:
            fan = self._current_fan

        url = (
            f"http://{self.coordinator.host}/cgi-bin/api.html?"
            f"f=18&on={on}&mode={mode}&tempSet={int(temp_set)}&fan={fan}"
            f"&FlowDirection1={self._current_flow_dir1}"
            f"&FlowDirection2={self._current_flow_dir2}"
            f"&idx={self._idx}"
        )

        try:
            async with async_timeout.timeout(10):
                async with self._session.get(url, auth=self._auth) as response:
                    text = await response.text()

                    if response.status == 401:
                        _LOGGER.error(
                            "Authentication failed for AC unit %s. Check username and password.",
                            self._idx,
                        )
                        return False
                    elif response.status != 200:
                        _LOGGER.error(
                            "Error sending command to AC unit %s: HTTP %s",
                            self._idx,
                            response.status,
                        )
                        return False

                    _LOGGER.debug(
                        "Command sent to unit %s: on=%s, mode=%s, temp=%s, fan=%s",
                        self._idx, on, mode, temp_set, fan,
                    )

            await self.coordinator.async_request_refresh()
            return True

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout sending command to AC unit %s", self._idx)
            return False
        except Exception as err:
            _LOGGER.error("Error sending command to AC unit %s: %s", self._idx, err)
            return False

    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            await self._send_command(on=0)
        else:
            await self._send_command(on=1, mode=HVAC_TO_MODE.get(hvac_mode, 1))

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            is_on = 1 if self._attr_hvac_mode != HVACMode.OFF else 0
            await self._send_command(on=is_on, temp_set=temperature)

    async def async_set_fan_mode(self, fan_mode: str):
        """Set new target fan mode."""
        fan = FAN_MODE_REVERSE.get(fan_mode)
        if fan is not None:
            is_on = 1 if self._attr_hvac_mode != HVACMode.OFF else 0
            await self._send_command(on=is_on, fan=fan)

    async def async_turn_on(self):
        """Turn the entity on."""
        await self._send_command(on=1)

    async def async_turn_off(self):
        """Turn the entity off."""
        await self._send_command(on=0)

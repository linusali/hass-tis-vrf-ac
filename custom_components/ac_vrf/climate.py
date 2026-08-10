"""Climate platform for AC VRF Controller."""
import asyncio
import logging
from datetime import timedelta

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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

SCAN_INTERVAL = timedelta(seconds=3)  # Poll every 3 seconds


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info=None,
):
    """Set up the AC VRF climate platform from configuration.yaml."""
    _LOGGER.info("AC VRF: Starting platform setup")
    _LOGGER.debug("AC VRF: Config received: %s", {k: v for k, v in config.items() if k != 'password'})
    
    host = config.get("host")
    username = config.get("username")
    password = config.get("password")
    
    if not host:
        _LOGGER.error("AC VRF: 'host' is required in configuration")
        return False
    
    _LOGGER.info("AC VRF: Connecting to controller at %s", host)
    if username:
        _LOGGER.info("AC VRF: Using authentication with username: %s", username)
    
    entities = []
    
    # Fetch initial status to discover units
    coordinator = ACVRFCoordinator(hass, host, username, password)
    _LOGGER.info("AC VRF: Fetching initial status from controller...")
    await coordinator.async_update()
    
    _LOGGER.info("AC VRF: Discovered %d units from controller", len(coordinator.data))
    
    if coordinator.data:
        for unit in coordinator.data:
            _LOGGER.info("AC VRF: Creating entity for unit: %s (idx=%s)", unit.get('nm'), unit.get('idx'))
            entities.append(ACVRFClimate(coordinator, unit["idx"], host))
    else:
        _LOGGER.warning("AC VRF: No units discovered from controller at %s", host)
        _LOGGER.warning("AC VRF: Please check network connectivity and controller status")
    
    _LOGGER.info("AC VRF: Adding %d entities to Home Assistant", len(entities))
    async_add_entities(entities, True)
    _LOGGER.info("AC VRF: Platform setup completed")
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up AC VRF climate from a config entry."""
    host = entry.data["host"]
    username = entry.data.get("username")
    password = entry.data.get("password")
    entities = []
    
    # Fetch initial status to discover units
    coordinator = ACVRFCoordinator(hass, host, username, password)
    await coordinator.async_update()
    
    if coordinator.data:
        for unit in coordinator.data:
            entities.append(ACVRFClimate(coordinator, unit["idx"], host))
    
    async_add_entities(entities, True)


class ACVRFCoordinator:
    """Coordinator to fetch data from AC VRF controller."""

    def __init__(self, hass: HomeAssistant, host: str, username: str = None, password: str = None):
        """Initialize the coordinator."""
        self.hass = hass
        self.host = host
        self.username = username
        self.password = password if password is not None else ""
        self.data = []

    def _fetch_raw(self, url: str) -> str:
        """Fetch data using a raw (blocking) socket.

        This helper intentionally stays synchronous because it uses the stdlib
        socket module (blocking I/O). It must be executed via
        `hass.async_add_executor_job()` from the event loop.
        """
        import urllib.parse
        import socket
        
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path
        if parsed.query:
            path += f"?{parsed.query}"
        
        # Build HTTP request
        request = f"GET {path} HTTP/1.0\r\n"
        request += f"Host: {host}\r\n"
        
        if self.username:
            import base64
            credentials = f"{self.username}:{self.password}"
            b64_credentials = base64.b64encode(credentials.encode()).decode()
            request += f"Authorization: Basic {b64_credentials}\r\n"
        
        request += "Connection: close\r\n\r\n"
        
        # Create socket connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        try:
            sock.connect((host, port))
            sock.sendall(request.encode())
            
            # Read response
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            
            # Decode response
            response_text = response.decode('utf-8', errors='ignore')
            
            # Try to find JSON in response (might not have proper HTTP headers)
            # Look for the JSON starting with {
            json_start = response_text.find('{')
            if json_start >= 0:
                return response_text[json_start:]
            
            return response_text
            
        finally:
            sock.close()

    async def async_update(self):
        """Fetch data from API."""
        _LOGGER.debug("AC VRF: Starting status update from %s", self.host)
        try:
            # The status endpoint returns data in pages (p=0, p=1, etc.)
            # We'll fetch both pages to get all units
            all_units = []
            
            for page in [0, 1]:
                url = f"http://{self.host}/cgi-bin/api.html?f=17&p={page}"
                _LOGGER.debug("AC VRF: Fetching from URL: %s", url)
                
                try:
                    # Use raw fetch for controllers without proper HTTP headers
                    text = await self.hass.async_add_executor_job(self._fetch_raw, url)
                    _LOGGER.debug("AC VRF: Response text (first 200 chars): %s", text[:200])
                    
                    # Try to parse as JSON.
                    #
                    # Some controllers return a valid JSON object but also
                    # append extra bytes after it (e.g., garbage, an HTML
                    # footer, or a second payload). `json.loads()` fails with
                    # "Extra data" in that case. Use `raw_decode()` to parse
                    # the first JSON object and ignore any trailing bytes.
                    import json
                    try:
                        raw = text.strip()
                        # If we accidentally captured HTTP headers or other
                        # noise, start from the first JSON object.
                        first_brace = raw.find('{')
                        if first_brace > 0:
                            raw = raw[first_brace:]

                        decoder = json.JSONDecoder()
                        data, end = decoder.raw_decode(raw)

                        # Log trailing non-whitespace for troubleshooting.
                        trailing = raw[end:].strip()
                        if trailing:
                            _LOGGER.debug(
                                "AC VRF: Trailing non-JSON data after payload (len=%d) from %s: %r",
                                len(trailing),
                                url,
                                trailing[:120],
                            )
                        _LOGGER.debug("AC VRF: Parsed JSON successfully for page %d", page)
                        
                        if data.get("err") == 0 and "unit" in data:
                            units_found = len(data["unit"])
                            _LOGGER.info("AC VRF: Found %d units on page %d", units_found, page)
                            all_units.extend(data["unit"])
                            for unit in data["unit"]:
                                _LOGGER.debug("  - Unit: %s (idx=%s, on=%s, temp=%s)", 
                                            unit.get('nm'), unit.get('idx'), 
                                            unit.get('on'), unit.get('tempIn'))
                        elif data.get("err") != 0:
                            _LOGGER.error("API returned error code: %s", data.get("err"))
                        else:
                            _LOGGER.warning("AC VRF: No 'unit' key in response for page %d", page)
                    except json.JSONDecodeError as e:
                        _LOGGER.error(
                            "Failed to parse JSON from %s: %s. Response: %s",
                            url,
                            e,
                            text[:200]
                        )
                        
                except Exception as e:
                    _LOGGER.error("Error fetching from %s: %s", url, e)
                    import traceback
                    _LOGGER.debug("Traceback: %s", traceback.format_exc())
            
            self.data = all_units
            if all_units:
                _LOGGER.info("AC VRF: Successfully fetched %d total AC units", len(all_units))
            else:
                _LOGGER.warning("AC VRF: No units found from controller at %s", self.host)
            
        except Exception as err:
            _LOGGER.error("Error fetching AC VRF status: %s", err)
            import traceback
            _LOGGER.debug("Traceback: %s", traceback.format_exc())


class ACVRFClimate(ClimateEntity):
    """Representation of an AC VRF unit."""

    def __init__(self, coordinator: ACVRFCoordinator, idx: int, host: str):
        """Initialize the climate device."""
        _LOGGER.info("AC VRF: Initializing climate entity for idx=%s", idx)
        self._coordinator = coordinator
        self._idx = idx
        self._host = host
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
        
        # Initialize attributes
        self._update_from_coordinator()
        _LOGGER.info("AC VRF: Climate entity initialized: %s (unique_id=%s)", 
                    getattr(self, '_attr_name', 'Unknown'), 
                    getattr(self, '_attr_unique_id', 'Unknown'))

    def _update_from_coordinator(self):
        """Update state from coordinator data."""
        for unit in self._coordinator.data:
            if unit["idx"] == self._idx:
                self._attr_name = unit["nm"]
                self._attr_unique_id = f"ac_vrf_{self._host}_{self._idx}"
                
                # Update current state
                is_on = unit["on"] == 1
                mode = unit["mode"]
                
                if not is_on:
                    self._attr_hvac_mode = HVACMode.OFF
                else:
                    self._attr_hvac_mode = MODE_TO_HVAC.get(mode, HVACMode.COOL)
                
                self._attr_target_temperature = float(unit["tempSet"])
                self._attr_current_temperature = float(unit["tempIn"])
                self._attr_fan_mode = FAN_MODE_MAP.get(unit["fan"], "auto")
                
                # Store current settings for API calls
                self._current_mode = mode
                self._current_fan = unit["fan"]
                self._current_flow_dir1 = unit.get("FlowDirection1", 0)
                self._current_flow_dir2 = unit.get("FlowDirection2", 0)
                
                break

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return len(self._coordinator.data) > 0

    async def async_update(self):
        """Update the entity."""
        await self._coordinator.async_update()
        self._update_from_coordinator()

    async def _send_command(
        self,
        on: int,
        mode: int = None,
        temp_set: float = None,
        fan: int = None,
    ):
        """Send command to AC unit."""
        # Use current values if not specified
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
            # Use raw fetch for controllers without proper HTTP headers
            text = await self._coordinator.hass.async_add_executor_job(
                self._coordinator._fetch_raw, url
            )
            
            # Log successful command
            _LOGGER.debug(
                "Command sent to unit %s: on=%s, mode=%s, temp=%s, fan=%s. Response: %s",
                self._idx, on, mode, temp_set, fan, text[:100]
            )
            
            # Update state immediately and then refresh
            await asyncio.sleep(0.5)
            await self.async_update()
            return True
            
        except Exception as err:
            _LOGGER.error("Error sending command to AC unit %s: %s", self._idx, err)
            return False

    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            await self._send_command(on=0)
        else:
            mode = HVAC_TO_MODE.get(hvac_mode, 1)
            await self._send_command(on=1, mode=mode)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            # If currently off, turn on when setting temperature
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

# AC VRF Controller Integration for Home Assistant

This custom integration allows you to control your AC VRF units through Home Assistant.

## Installation

1. Copy the `ac_vrf` folder to your Home Assistant's `custom_components` directory:
   ```
   /config/custom_components/ac_vrf/
   ```

2. Restart Home Assistant

## Configuration

Add the following to your `configuration.yaml`:

```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251          # Required: Your AC controller IP address
    username: admin               # Optional: HTTP Basic Auth username
    password: your_password       # Optional: HTTP Basic Auth password
```

**Configuration Options:**
- `host` (required): IP address of your AC VRF controller
- `username` (optional): Username for HTTP Basic Authentication
- `password` (optional): Password for HTTP Basic Authentication (can be empty string)

**Configuration Examples:**

With username and password:
```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251
    username: admin
    password: your_password
```

With username but empty password:
```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251
    username: admin
    password: ""
```

Without authentication:
```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251
```

After adding the configuration, restart Home Assistant. The integration will automatically discover all AC units connected to your VRF controller.

## Features

- **Automatic Unit Discovery**: All AC units are automatically discovered from the controller
- **Real-time Status**: Updates every 2 seconds to reflect current state
- **Full Control**:
  - Turn on/off
  - Set temperature (16-30°C)
  - Change HVAC mode (Cool, Heat, Dry, Fan Only)
  - Adjust fan speed (Low, Medium, High, Auto)
- **Current Temperature**: Displays the current room temperature from each unit

## Discovered Entities

Based on your configuration, the following climate entities will be created:
- `climate.maidsroom`
- `climate.majlis`
- `climate.living`
- `climate.kitchen`
- `climate.m_bedroom`
- `climate.ahzab`
- `climate.ehsan`
- `climate.office`

## API Mapping

The integration maps Home Assistant's climate entity features to your AC controller's API:

### HVAC Modes
- **Cool**: `mode=1`
- **Heat**: `mode=8`
- **Dry**: `mode=2`
- **Fan Only** (Fresh Air): `mode=4`
- **Off**: `on=0`

### Fan Modes
- **Low**: `fan=4`
- **Medium**: `fan=2`
- **High**: `fan=1`
- **Auto**: `fan=8`

## Troubleshooting

### Units not appearing
- Check that your AC controller is accessible at the configured IP address
- Verify the controller is responding to status requests
- Check Home Assistant logs for any error messages

### Commands not working
- Ensure your network allows communication with the AC controller
- Check that the controller is not locked or in a restricted mode
- Review Home Assistant logs for timeout or connection errors

### Viewing Logs
Enable debug logging by adding to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.ac_vrf: debug
```

## Example Automations

### Turn on AC when arriving home
```yaml
automation:
  - alias: "AC on when home"
    trigger:
      - platform: state
        entity_id: person.your_name
        to: "home"
    action:
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.living
        data:
          hvac_mode: cool
      - service: climate.set_temperature
        target:
          entity_id: climate.living
        data:
          temperature: 24
```

### Energy-saving night mode
```yaml
automation:
  - alias: "AC night mode"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: climate.set_temperature
        target:
          entity_id: 
            - climate.m_bedroom
            - climate.ahzab
            - climate.ehsan
        data:
          temperature: 26
      - service: climate.set_fan_mode
        target:
          entity_id: 
            - climate.m_bedroom
            - climate.ahzab
            - climate.ehsan
        data:
          fan_mode: "low"
```

## Support

For issues or questions, please check the Home Assistant logs and verify your controller's API is functioning correctly.

## Technical Details

- **Polling Interval**: 2 seconds (as per your requirement)
- **Timeout**: 10 seconds for API calls
- **Dependencies**: None (uses built-in aiohttp)
- **Integration Type**: Local Polling

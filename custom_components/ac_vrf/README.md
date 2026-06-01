# AC VRF Controller Integration for Home Assistant

A custom integration to control TIS VRF AC units through Home Assistant via the controller's local HTTP API.

## Installation

1. Copy the `ac_vrf` folder to your Home Assistant `custom_components` directory:
   ```
   /config/custom_components/ac_vrf/
   ```
2. Restart Home Assistant.

## Configuration

Add the following to your `configuration.yaml`:

```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251          # Required: IP address of your AC controller
    username: admin               # Optional: HTTP Basic Auth username
    password: your_password       # Optional: HTTP Basic Auth password (can be empty string)
```

After adding the configuration, restart Home Assistant. The integration will automatically discover all AC units connected to the controller and create a `climate` entity for each one.

## Features

- Automatic discovery of all connected indoor units
- Status updates every 2 seconds
- Control: on/off, temperature (16–30°C), HVAC mode, fan speed
- Current room temperature display

## HVAC Modes

| Home Assistant | API value |
|---|---|
| Cool | `mode=1` |
| Dry | `mode=2` |
| Fan Only | `mode=4` |
| Heat | `mode=8` |
| Off | `on=0` |

## Fan Modes

| Home Assistant | API value |
|---|---|
| low | `fan=1` |
| medium-low | `fan=2` |
| medium | `fan=3` |
| high | `fan=4` |
| auto | `fan=5` |

## Debug Logging

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
          entity_id: climate.living_room
        data:
          hvac_mode: cool
      - service: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 24
```

### Night mode

```yaml
automation:
  - alias: "AC night mode"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: climate.set_temperature
        target:
          entity_id: climate.bedroom
        data:
          temperature: 26
      - service: climate.set_fan_mode
        target:
          entity_id: climate.bedroom
        data:
          fan_mode: "low"
```

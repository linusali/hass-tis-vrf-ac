# AC VRF Controller Integration for Home Assistant

A custom integration that controls the indoor units of a TIS AC VRF controller from Home Assistant. Every indoor unit connected to the controller shows up as a `climate` entity.

## Installation

**HACS:** add `https://github.com/linusali/hass-tis-vrf-ac` as a custom repository (category *Integration*), install **AC VRF Controller**, then restart Home Assistant.

**Manual:** copy `custom_components/ac_vrf/` into your Home Assistant `config/custom_components/` directory and restart. See [INSTALLATION.md](INSTALLATION.md) for details.

## Configuration

The integration is configured in `configuration.yaml`:

```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251          # Required: controller IP address
    username: admin              # Optional: HTTP Basic Auth username
    password: your_password      # Optional: HTTP Basic Auth password
```

| Option | Required | Description |
|---|---|---|
| `host` | yes | IP address (or `ip:port`) of the VRF controller |
| `username` | no | HTTP Basic Auth username. Leave it out if the controller has no authentication |
| `password` | no | HTTP Basic Auth password. May be an empty string (`password: ""`) |

Restart Home Assistant after changing the configuration. A full restart is needed; reloading YAML is not enough.

Give the controller a fixed IP address (e.g. a DHCP reservation). Entities are identified by the controller's address, so if it changes, Home Assistant creates a new set of entities.

## Features

- **Automatic discovery:** one climate entity per indoor unit, named as configured on the controller (for example, a unit named "Living" becomes `climate.living`).
- **Status polling:** the controller is polled once every 3 seconds for all units together.
- **Control:**
  - Turn on/off
  - Set target temperature (16–30 °C, 1 °C steps)
  - HVAC mode: Cool, Heat, Dry, Fan Only
  - Fan speed: Low, Medium, High, Auto
- **Current room temperature** reported by each unit.
- **Availability:** a unit is shown as *unavailable* when the controller can't be reached or doesn't report that unit, rather than showing stale values. Entities recover on their own when the controller comes back.
- If the controller is unreachable when Home Assistant starts, setup is retried automatically.

Setting a temperature or fan speed on a unit that is off stores the setting without turning the unit on.

### Command handling

Each command sends the unit's full state (on/off, mode, setpoint, fan). The integration builds every command from the state you last asked for, not from the last poll, and sends them to the controller one at a time. The controller's status can take a few seconds to catch up. During that time (up to 10 s after a command) a poll that doesn't match yet is treated as out of date, so the entity doesn't flip back to its old state. This makes the entities safe to drive from [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat), which sends several commands in a row (e.g. set the mode, then the temperature).

Setpoints are whole degrees: fractional values are rounded half-up (24.5 → 25) and limited to 16–30 °C.

## API mapping

The controller exposes a CGI API at `http://<host>/cgi-bin/api.html`:

- Status: `?f=17&p=<page>` (pages 0 and 1 are read)
- Command: `?f=18&on=..&mode=..&tempSet=..&fan=..&FlowDirection1=..&FlowDirection2=..&idx=<unit>`

The controller replies with HTTP/0.9-style responses (no status line or headers), which standard HTTP clients reject, so the integration sends its requests over a plain TCP socket.

### HVAC modes

| Home Assistant | `mode` |
|---|---|
| Cool | `1` |
| Dry | `2` |
| Fan Only (Fresh Air) | `4` |
| Heat | `8` |
| Off | `on=0` |

### Fan modes

| Home Assistant | `fan` |
|---|---|
| High | `1` |
| Medium | `2` |
| Low | `4` |
| Auto | `8` |

## Example automations

### Turn on the AC when arriving home

```yaml
automation:
  - alias: "AC on when home"
    triggers:
      - trigger: state
        entity_id: person.your_name
        to: "home"
    actions:
      - action: climate.set_hvac_mode
        target:
          entity_id: climate.living
        data:
          hvac_mode: cool
      - action: climate.set_temperature
        target:
          entity_id: climate.living
        data:
          temperature: 24
```

### Night mode for the bedrooms

```yaml
automation:
  - alias: "AC night mode"
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id:
            - climate.bedroom_1
            - climate.bedroom_2
        data:
          temperature: 26
      - action: climate.set_fan_mode
        target:
          entity_id:
            - climate.bedroom_1
            - climate.bedroom_2
        data:
          fan_mode: "low"
```

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md). To see what the integration is doing, enable debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.ac_vrf: debug
```

## Technical details

- **Integration type:** local polling (`configuration.yaml` only, no UI config flow)
- **Polling interval:** 3 seconds, one status poll shared by all units
- **Socket timeout:** 10 seconds per request
- **Dependencies:** none beyond Home Assistant itself

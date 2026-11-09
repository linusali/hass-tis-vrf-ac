# Troubleshooting Guide

## Quick checklist

- Files are in `/config/custom_components/ac_vrf/`
- `configuration.yaml` has correct indentation (spaces, not tabs)
- Home Assistant was fully restarted (not just a configuration reload)
- Controller IP is correct and reachable from the Home Assistant host
- Username/password are correct (if the controller requires authentication)

## Step 1: Enable debug logging

Add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.ac_vrf: debug
```

Restart and check **Settings → System → Logs** for messages containing `ac_vrf`. With debug logging enabled you'll see each status response and every command sent to the controller.

## Step 2: Test the controller with the test script

`test_ac_vrf_connection.py` talks to the controller the same way the integration does (raw socket, HTTP/0.9-tolerant). It needs only Python 3, no extra packages. Run it from the Home Assistant host or any machine on the same network:

```bash
# No authentication
python3 test_ac_vrf_connection.py 192.168.1.251

# With authentication (empty password is fine)
python3 test_ac_vrf_connection.py 192.168.1.251 --username admin --password ""
```

It lists every unit found on each status page, or prints the raw response if something is wrong.

## Step 3: Test raw network access

From the Home Assistant host:

```bash
ping 192.168.1.251

# The controller replies with HTTP/0.9, so curl needs --http0.9
curl --http0.9 "http://192.168.1.251/cgi-bin/api.html?f=17&p=0"

# With authentication
curl --http0.9 -u admin: "http://192.168.1.251/cgi-bin/api.html?f=17&p=0"
```

If `curl` without the flag says `Received HTTP/0.9 when not allowed`, that confirms the controller uses HTTP/0.9; the integration handles this automatically.

If these commands fail entirely, the problem is the network or firewall, not the integration.

## Common log messages

| Message | Cause | Fix |
|---|---|---|
| `Integration 'ac_vrf' not found` / `Platform not found` | Files not installed correctly | Check Step 1 of [INSTALLATION.md](INSTALLATION.md) |
| `No AC units could be read from controller ... Retrying` | Controller unreachable at startup | Home Assistant keeps retrying; check IP, power and network |
| `Failed to read status page N` | One status page timed out or returned bad data | Units on that page show as unavailable until it recovers; logged once per outage |
| `Error fetching AC VRF ... data` | No units could be read at all | All entities become unavailable; check the controller and network |
| `Failed to send command to AC unit` | Command couldn't reach the controller | Check the controller is reachable |
| `invalid JSON` / `controller returned error code` | Unexpected response | Run the test script and inspect the raw response; check credentials |
| YAML error on startup | Config formatting issue | Use **Developer Tools → YAML → Check Configuration** |

## Entities show as "Unavailable"

The integration loaded but couldn't read those units on the last poll. Check:

- The controller is powered on and connected to the network
- No firewall rules block traffic from the Home Assistant host
- The credentials haven't changed

If only some units are unavailable, the controller is failing to return one of its status pages; the debug log shows which one. Entities recover automatically once the controller responds again.

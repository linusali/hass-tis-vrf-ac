# Troubleshooting Guide

## Quick checklist

- Files are in `/config/custom_components/ac_vrf/`
- `configuration.yaml` has correct indentation (spaces, not tabs)
- Home Assistant was fully restarted (not just config reload)
- Controller IP is correct and reachable from the HA host
- Username/password are correct (if authentication is required)

## Step 1: Enable debug logging

Add to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.ac_vrf: debug
```

Restart and check **Settings → System → Logs** for messages containing `ac_vrf`.

## Step 2: Test API connectivity

Run the included test script from the HA host or any machine on the same network:

1. Edit `test_ac_vrf_connection.py` with your settings:
   ```python
   HOST = "192.168.1.251"
   USERNAME = "admin"   # or None
   PASSWORD = ""        # or None
   ```

2. Run:
   ```bash
   python3 test_ac_vrf_connection.py
   ```

The output will show all discovered units or a clear error message.

## Step 3: Test raw network access

From the HA host, verify the controller is reachable:

```bash
# Test ping
ping 192.168.1.251

# Test HTTP access (note: controller uses HTTP/0.9 — use --http0.9 flag)
curl --http0.9 "http://192.168.1.251/cgi-bin/api.html?f=17&p=0"

# With authentication
curl --http0.9 -u admin: "http://192.168.1.251/cgi-bin/api.html?f=17&p=0"
```

If `curl` returns `Received HTTP/0.9 when not allowed` without the flag, that confirms the controller uses the older HTTP/0.9 protocol — the integration handles this automatically.

If these commands fail entirely, the issue is network/firewall, not the integration.

## Common errors

| Error | Cause | Fix |
|---|---|---|
| `Platform not found` | Files not installed correctly | Verify Step 1 of INSTALLATION.md |
| `Authentication failed` / `HTTP 401` | Wrong credentials | Check username/password in config |
| `Timeout` / `Connection refused` | Controller unreachable | Check IP address and network |
| `No units discovered` | API not returning data | Run test script to inspect raw response |
| `YAML error` on startup | Config formatting issue | Use **Developer Tools → YAML → Check Configuration** |

## Entities appear as "Unavailable"

This means the integration loaded but can't reach the controller at runtime. Check:
- Controller is powered on and connected to the network
- No firewall rules blocking traffic from the HA host
- Credentials haven't changed

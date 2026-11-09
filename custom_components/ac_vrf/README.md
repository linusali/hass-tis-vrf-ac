# AC VRF Controller

Home Assistant `climate` platform for TIS AC VRF controllers.

Configure it in `configuration.yaml`:

```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251
    username: admin        # optional
    password: ""           # optional
```

Full documentation: https://github.com/linusali/hass-tis-vrf-ac

# Installation Guide

## Step 1: Copy the component

Copy the `ac_vrf` folder into your Home Assistant `custom_components` directory:

```
config/
├── configuration.yaml
└── custom_components/
    └── ac_vrf/
        ├── __init__.py
        ├── climate.py
        ├── manifest.json
        └── README.md
```

If `custom_components` doesn't exist, create it first.

## Step 2: Configure

Add to your `configuration.yaml`:

```yaml
climate:
  - platform: ac_vrf
    host: 192.168.1.251       # Your controller's IP address
    username: admin            # Optional: remove if no auth required
    password: your_password    # Optional: remove if no auth required
```

## Step 3: Restart Home Assistant

Go to **Settings → System → Restart**. A full restart is required — reloading configuration is not sufficient.

## Step 4: Verify

After restart, go to **Settings → Devices & Services → Entities** and filter by "climate". You should see one entity per indoor unit discovered from the controller.

If nothing appears, see [TROUBLESHOOTING.md](../TROUBLESHOOTING.md).

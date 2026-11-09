# Installation Guide

## Step 1: Install the component

### Option A: HACS

1. In HACS, open the menu → **Custom repositories**.
2. Add `https://github.com/linusali/hass-tis-vrf-ac` with category **Integration**.
3. Find **AC VRF Controller** in HACS and install it.

### Option B: Manual

Copy the `custom_components/ac_vrf` folder from this repository into your Home Assistant `config` directory:

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

Use **Developer Tools → YAML → Check Configuration** to catch mistakes before restarting.

## Step 3: Restart Home Assistant

Go to **Settings → System → Restart**. A full restart is required; reloading the YAML configuration is not enough.

## Step 4: Verify

After the restart, go to **Settings → Devices & Services → Entities** and filter by "climate". You should see one entity per indoor unit on the controller.

If the controller couldn't be reached during startup, Home Assistant retries the setup in the background and the entities appear once it responds.

If nothing appears, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Upgrading

Replace the `ac_vrf` folder (or update through HACS) and restart Home Assistant. Entity IDs, history and customizations are kept.

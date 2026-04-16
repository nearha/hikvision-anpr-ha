# Hikvision ANPR for Home Assistant

Custom integration for Hikvision ANPR cameras using ISAPI in **listening mode**.

## Features

- Config flow via the Home Assistant UI
- Auto-configures the camera callback endpoint during setup/reconfigure
- Parses ANPR events and stores images in `/media/hikvison_anpr`
- Exposes sensors for the latest event
- Exposes three image entities:
  - last license plate image
  - last vehicle image
  - last detection image
- Fires a Home Assistant bus event: `hikvision_anpr_event`
- Exposes a native Home Assistant event entity with attributes from the latest ANPR event

## Installation with HACS

1. Push this repository to GitHub.
2. In Home Assistant, open **HACS → Integrations → Custom repositories**.
3. Add your repository URL and select **Integration** as the category.
4. Install **Hikvision ANPR** from HACS.
5. Restart Home Assistant.
6. Go to **Settings → Devices & Services → Add Integration** and search for **Hikvision ANPR**.

## Inputs

The integration asks only for camera-side connection information:

- camera host / IP
- port
- HTTP or HTTPS
- username
- password
- auth mode
- verify SSL
- channel
- HTTP host ID
- media directory

The integration discovers the Home Assistant base URL automatically and configures the camera callback path using the config entry ID.

## Media storage

By default, event images are saved under:

```text
/media/hikvison_anpr
```

## Bus event payload

The integration fires `hikvision_anpr_event` with fields such as:

- `event_id`
- `event_time`
- `plate`
- `confidence`
- `direction`
- `list_result`
- `country`
- `brand`
- `type`
- `color`
- `license_plate_image_path`
- `vehicle_image_path`
- `detection_image_path`

## Notes before publishing

Before publishing publicly, replace these placeholders inside `custom_components/hikvision_anpr/manifest.json`:

- `YOUR_GITHUB_USERNAME`
- repository URLs in `documentation` and `issue_tracker`
- `codeowners`

## Repository structure

```text
custom_components/
  hikvision_anpr/
    __init__.py
    manifest.json
    config_flow.py
    const.py
    manager.py
    parser.py
    mappings.py
    sensor.py
    image.py
    event.py
    button.py
    view.py
    strings.json
```

# Hikvision ANPR

Custom integration for Home Assistant that receives Hikvision ANPR events in listening mode (`httpHosts`).

## Features

- Auto-configures `httpHosts/1` on setup
- Auto-tests callback after configuration
- Receives ANPR events by callback instead of polling
- Creates sensors for the latest ANPR data
- Creates image entities for plate, vehicle, and detection images
- Saves media files under Home Assistant media storage
- Supports HTTP or HTTPS to the camera

## Installation with HACS

1. Add this repository as a custom repository in HACS, category **Integration**.
2. Install **Hikvision ANPR**.
3. Restart Home Assistant.
4. Add the integration from **Settings → Devices & Services**.

## Configuration

Provide only the camera connection details in the UI. The integration configures the callback on the camera automatically.

## Notes

- For self-signed camera certificates, leave SSL verification disabled.
- The camera callback target is derived automatically from the Home Assistant instance URL.
- Media is stored under `/media/hikvison_anpr`.

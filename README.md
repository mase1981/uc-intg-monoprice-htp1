# Monoprice HTP-1 Integration for Unfolded Circle Remote 2/3

![Monoprice HTP-1](https://img.shields.io/badge/Monoprice-HTP--1-red)
[![GitHub Release](https://img.shields.io/github/v/release/mase1981/uc-intg-monoprice-htp1?style=flat-square)](https://github.com/mase1981/uc-intg-monoprice-htp1/releases)
![License](https://img.shields.io/badge/license-MPL--2.0-blue?style=flat-square)
[![GitHub issues](https://img.shields.io/github/issues/mase1981/uc-intg-monoprice-htp1?style=flat-square)](https://github.com/mase1981/uc-intg-monoprice-htp1/issues)
[![Community Forum](https://img.shields.io/badge/community-forum-blue?style=flat-square)](https://unfolded.community/)
[![Discord](https://badgen.net/discord/online-members/zGVYf58)](https://discord.gg/zGVYf58)
![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/mase1981/uc-intg-monoprice-htp1/total?style=flat-square)
[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg?style=flat-square)](https://buymeacoffee.com/meirmiyara)
[![PayPal](https://img.shields.io/badge/PayPal-donate-blue.svg?style=flat-square)](https://paypal.me/mmiyara)
[![Github Sponsors](https://img.shields.io/badge/GitHub%20Sponsors-30363D?&logo=GitHub-Sponsors&logoColor=EA4AAA&style=flat-square)](https://github.com/sponsors/mase1981)

Control your Monoprice HTP-1 from an Unfolded Circle Remote. The integration provides media-player controls, a customizable remote layout, live receiver status, and access to the hosted Bass EQ (BEQ) catalogue.

## Features

### Media player

- Turn the HTP-1 on and off.
- Adjust volume with the slider or volume buttons.
- Mute and unmute.
- Select from inputs configured and made visible on the HTP-1.
- Select the receiver's available surround modes.
- View the current input, volume, mute state, and audio information.

The volume slider maps to the volume range reported by your HTP-1. To help prevent sudden changes, a single slider update is limited to a maximum 5 dB change.

### BEQ catalogue

Browse and search the hosted BEQ catalogue from the Remote. Search matches both titles and authors. The catalogue listing is kept separate from the filter data, so the integration downloads a filter when you select it rather than downloading the entire catalogue to the Remote.

You need an internet connection from the Remote or Docker host to use the hosted catalogue. Docker is no longer required just to browse or load BEQ filters.

**Loading and clearing BEQ filters**

- Selecting a BEQ loads it into the HTP-1's PEQ, with placement handled automatically based on your HTP-1 configuration.
- Loading another BEQ replaces the previously loaded BEQ filters. Other PEQ bands are left alone.
- The integration turns PEQ on when it loads a BEQ.
- Clearing BEQ resets the bands marked as BEQ to neutral settings. It does not clear unrelated PEQ bands or turn PEQ off.
- A BEQ can contain up to 16 filters. The HTP-1 must have enough available PEQ bands on every channel the filter needs. If there is not enough room, the new filter is not loaded and the existing BEQ is left in place.
- Catalogue values are prepared for the HTP-1's supported PEQ ranges. The supported ranges are 10–20,000 Hz, -99 to +20 dB, and Q 0.1–10.

### Remote entity

The Remote entity includes three built-in pages:

- **Main** – numbered input buttons, Info, Last Input, Dim, and the color buttons.
- **Pad** – input cycling and sound-mode shortcuts.
- **Toggles** – Dirac, Night, Loudness, Bluetooth pairing, and presets.

The standard Remote buttons control volume, mute, and dialog level. The Remote entity also exposes a list of commands that can be used in activities and custom macros, including input selection, sound modes, Dirac, Night Mode, Loudness, PEQ, and Seat Shaker controls.

### Select entities

The integration creates these selectable controls:

- **Input** – choose a visible HTP-1 input.
- **Calibration** – choose a valid saved Dirac calibration slot.
- **Surround Mode** – choose an available surround or upmix mode.
- **Seat Shaker Preset** – choose preset 1 through 6.

### Sensor entities

The integration provides live status for:

| Sensor | What it reports |
|---|---|
| Input | Current input |
| Volume | Current volume in dB |
| Mute | Mute on/off |
| Seat Shaker Trim | Current Seat Shaker trim |
| Seat Shaker Mute | Seat Shaker mute state |
| Seat Shaker Preset | Current Seat Shaker preset |
| Loudness | Loudness on/off |
| Night Mode | Night Mode on/off |
| PEQ | Parametric EQ on/off |
| Sound Mode | Current surround or upmix mode |
| Audio Format | Incoming audio format |
| Output Audio Format | Current output audio format |
| Dirac Slot | Active calibration, Dirac Bypass, Dirac Off, or lifecycle status |
| Video Mode | Video resolution and available HDR/video details |
| Connection | Receiver connection status |
| BEQ Filter | Name of the loaded BEQ filter, or None |

**Dirac Slot status**

The **Dirac Slot** sensor reports the receiver's confirmed Dirac state and also makes an in-progress calibration change visible:

- **Calibration name** – the active, confirmed Dirac calibration.
- **Dirac Bypass** – Dirac is bypassed.
- **Dirac Off** – Dirac is off.
- **Loading <target>** – the HTP-1 has accepted a calibration change and is applying the requested slot. If the target name is not available, the sensor displays **Loading calibration**.
- **Not applied: <reason>** – the requested change was refused or could not be completed.

The HTP-1 can report the requested slot index before its DSP has finished applying that calibration. During this interval, the sensor displays **Loading <target>** rather than presenting the requested slot as already active. After the change completes, the integration reads the receiver's state and displays the confirmed result. If the change fails, the sensor displays **Not applied: <reason>**—it does not continue to show the previous calibration name. The integration retains the last confirmed calibration internally, so the failed target is not treated as active. Common reasons include **standby**, **busy**, and **timeout**; other failures display the available error text.

## Requirements and network access

- An Unfolded Circle Remote 2 or Remote 3.
- A Monoprice HTP-1 connected to your network and powered on during setup.
- The Remote (or the Docker host) must be able to reach the HTP-1's IP address.
- The HTP-1 control connection uses WebSocket on **port 80**, at `/ws/controller`.
- The hosted BEQ catalogue requires outbound internet access to GitHub Pages over HTTPS.

The Remote and HTP-1 do not have to be on the same subnet if your network routes traffic between them and the firewall allows the connection. A DHCP reservation for the HTP-1 is recommended so its address does not change.

**BEQ catalogue security:** The catalogue is downloaded over HTTPS with certificate-chain and hostname verification enabled. The integration uses the CA bundle provided by `certifi`, which is included in the packaged driver. If certificate verification cannot be completed, the catalogue request fails; TLS verification is not disabled.

## Installation

### Option 1: Install on the Remote

1. Open the [integration Releases page](https://github.com/mase1981/uc-intg-monoprice-htp1/releases).
2. Download the latest `uc-intg-monoprice-htp1-<version>-aarch64.tar.gz` package.
3. Open the Remote's web interface at `http://<remote-ip>`.
4. Go to **Settings → Integrations → Add Integration**.
5. Upload the downloaded package.

### Option 2: Run with Docker

Docker is an alternative installation method, not a requirement for BEQ browsing.

The published image is available from GitHub Container Registry:

```text
ghcr.io/mase1981/uc-intg-monoprice-htp1:latest
```

**Docker Compose**

```yaml
services:
  uc-intg-monoprice-htp1:
    image: ghcr.io/mase1981/uc-intg-monoprice-htp1:latest
    container_name: uc-intg-monoprice-htp1
    network_mode: host
    volumes:
      - ./htp1-data:/data
    environment:
      - UC_CONFIG_HOME=/data
      - UC_INTEGRATION_HTTP_PORT=9090
      - UC_INTEGRATION_INTERFACE=0.0.0.0
      - PYTHONPATH=/app
    restart: unless-stopped
```

**Docker Run**

```bash
docker run -d --name uc-htp1 --restart unless-stopped --network host \
  -v htp1-config:/app/config \
  -e UC_CONFIG_HOME=/app/config \
  -e UC_INTEGRATION_INTERFACE=0.0.0.0 \
  -e UC_INTEGRATION_HTTP_PORT=9090 \
  -e PYTHONPATH=/app \
  ghcr.io/mase1981/uc-intg-monoprice-htp1:latest
```

The Docker host must be able to reach the HTP-1 and access the internet for the hosted BEQ catalogue.

## Configuration

1. Make sure the HTP-1 is powered on and connected to your network.
2. Install and start the integration.
3. In the Remote web interface, open **Settings → Integrations** and select the Monoprice HTP-1 integration.
4. Choose **Configure**.
5. Enter a device name and the HTP-1's IP address.
6. The integration checks the connection before saving the configuration. If the check fails, confirm the address, network access, and that the HTP-1 is powered on, then try again.

After setup, the integration creates a media player, a Remote entity, select entities, and sensor entities for the receiver.

## Using the integration

### Media player

Use the media player to control power, volume, mute, input, and surround mode. The volume slider uses the range reported by the receiver and limits each slider update to a 5 dB step to avoid sudden jumps.

### BEQ browsing

1. Open the media browser on the HTP-1 media player.
2. Open **BEQ Catalogue** and choose a category, or use search.
3. Select a filter to load it into the HTP-1.
4. To remove the currently loaded BEQ, choose **Clear BEQ** in the browser.

An internet connection is required to browse the hosted catalogue and retrieve a selected filter. If the catalogue cannot be reached and no cached catalogue is available, browsing will be unavailable until the connection is restored.

### Remote commands and activities

Use the Remote entity's built-in pages or its available commands in Remote activities and macros. Commands include power, volume, mute, input shortcuts, sound modes, Dirac, Night Mode, Loudness, PEQ, and Seat Shaker functions. The Seat Shaker commands include mute, preset selection, and trim up/down.

### During a Dirac calibration change

A Dirac change is acknowledged once the HTP-1 accepts it; the integration then monitors the receiver until the change completes. While the receiver is applying the calibration:

- Volume up/down and absolute volume requests are held and consolidated rather than sent individually. After successful completion and state confirmation, at most one volume adjustment is sent, using the receiver's calibration limits and the normal 5 dB maximum-change protection.
- Other state-changing commands—including power, mute, input, surround mode, BEQ, Seat Shaker, menu, and IR commands—are temporarily rejected. Retry them after the Dirac change completes.
- If the receiver refuses the change, times out, or disconnects, the held volume requests are discarded. The last confirmed calibration remains displayed.

This avoids competing writes while the HTP-1's DSP is busy and prevents a burst of queued volume presses from becoming a sudden volume jump.

## ❤️ Support Development ❤️
If you find this integration useful, consider supporting development:
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-pink?style=for-the-badge&logo=github)](https://github.com/sponsors/mase1981)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/meirmiyara)
[![PayPal](https://img.shields.io/badge/PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/mmiyara)
Your support helps maintain this integration. Thank you! ❤️

## Credits

- **Developer**: Meir Miyara
- **Monoprice**: High-performance HTP-1 AV processor
- **Unfolded Circle**: Remote 2/3 integration framework (ucapi)
- **Protocol**: Monoprice HTP-1 WebSocket API
- **Community**: Testing and feedback from UC community

## License

This project is licensed under the Mozilla Public License 2.0 (MPL-2.0) - see LICENSE file for details.

## Support & Community

- **GitHub Issues**: [Report bugs and request features](https://github.com/mase1981/uc-intg-monoprice-htp1/issues)
- **UC Community Forum**: [General discussion and support](https://unfolded.community/)
- **Developer**: [Meir Miyara](https://www.linkedin.com/in/meirmiyara)
- **Monoprice Support**: [Official Monoprice Support](https://www.monoprice.com/pages/support)

---

**Made with ❤️ for the Unfolded Circle and Monoprice Communities**



**Thank You**: Meir Miyara

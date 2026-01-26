# Monoprice HTP-1 Integration for Unfolded Circle Remote 2/3

Control your Monoprice HTP-1 AV receiver directly from your Unfolded Circle Remote 2 or Remote 3 with comprehensive media player control, **full remote UI layout**, **real-time state monitoring**, and **complete WebSocket-based control**.

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


## Features

This integration provides comprehensive control of Monoprice HTP-1 AV receivers through the native WebSocket API, delivering seamless integration with your Unfolded Circle Remote for complete home theater control.

---
## ❤️ Support Development ❤️

If you find this integration useful, consider supporting development:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-pink?style=for-the-badge&logo=github)](https://github.com/sponsors/mase1981)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/meirmiyara)
[![PayPal](https://img.shields.io/badge/PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/mmiyara)

Your support helps maintain this integration. Thank you! ❤️
---

### 🎵 **Media Player Control**

#### **Power Management**
- **Power On/Off** - Complete power control
- **Power Toggle** - Quick power state switching
- **State Feedback** - Real-time power state monitoring

#### **Volume Control**
- **Volume Up/Down** - Precise volume adjustment
- **Set Volume** - Direct volume control (-127.5dB to 0dB)
- **Volume Slider** - Visual volume control (0-100 scale)
- **Mute Toggle** - Quick mute/unmute
- **Unmute** - Explicit unmute control
- **Real-time Updates** - Instant volume feedback via WebSocket

#### **Source Selection**
Control all available input sources:
- **HDMI Inputs** - All configured HDMI inputs
- **Analog Inputs** - Stereo and multichannel analog
- **Digital Inputs** - Coaxial and optical
- **Other Sources** - USB, network streaming, etc.
- **Custom Names** - Uses your configured input names

#### **Sound Mode Control**
- **Upmix Selection** - Choose from available upmix modes
- **DTS Neural:X** - Immersive audio processing
- **Dolby Surround** - Dolby upmixing
- **Stereo/Native** - Direct audio modes
- **Real-time Feedback** - Current mode displayed

### 🎮 **Remote Entity**

#### **Full UI Layout**
Pre-configured button layout optimized for Remote UI:
- **Power Control** - Toggle power button
- **Volume Controls** - Volume up/down, mute buttons
- **Activity Support** - All buttons available as simple commands

#### **Activity Integration**
- Works seamlessly with Remote activities
- Simple command support for custom macros
- Pre-configured button mappings

### 📊 **Sensor Entities**

Real-time monitoring of receiver state:

- **Input Sensor** - Currently selected input source
- **Volume Sensor** - Current volume level in dB
- **Loudness Sensor** -State of Loudness
- **Mute Sensor** - Stae of Mute
- **PEQ Status Sensor** - State of PEQ
- **Audio Format Sensor** - Detected audio codec and channels
- **Output Audio Format Sensor** - Detected output audio codec and output channels
- **Current Calibration Sensor** - Displays the Current Dirac Calibration Name, Dirac Bybass, or Dirac Off 
- **Video Mode Sensor** - Current video resolution and HDR format
- **Connection Sensor** - Integration connection status

### **Protocol Requirements**

- **Protocol**: Monoprice HTP-1 WebSocket API
- **WebSocket Port**: 80 (HTTP) or 443 (HTTPS)
- **WebSocket Path**: `/ws/controller`
- **Network Access**: Receiver must be on same local network
- **Connection**: Persistent WebSocket with automatic reconnection
- **Real-time Updates**: Bidirectional communication for instant state changes

### **Network Requirements**

- **Local Network Access** - Integration requires same network as HTP-1 receiver
- **HTTP Protocol** - WebSocket over HTTP (port 80) or HTTPS (port 443)
- **Static IP Recommended** - Receiver should have static IP or DHCP reservation
- **Firewall** - Must allow HTTP/WebSocket traffic

## Installation

### Option 1: Remote Web Interface (Recommended)
1. Navigate to the [**Releases**](https://github.com/mase1981/uc-intg-monoprice-htp1/releases) page
2. Download the latest `uc-intg-monoprice-htp1-<version>-aarch64.tar.gz` file
3. Open your remote's web interface (`http://your-remote-ip`)
4. Go to **Settings** → **Integrations** → **Add Integration**
5. Click **Upload** and select the downloaded `.tar.gz` file

### Option 2: Docker (Advanced Users)

The integration is available as a pre-built Docker image from GitHub Container Registry:

**Image**: `ghcr.io/mase1981/uc-intg-monoprice-htp1:latest`

**Docker Compose:**
```yaml
services:
  uc-intg-monoprice-htp1:
    image: ghcr.io/mase1981/uc-intg-monoprice-htp1:latest
    container_name: uc-intg-monoprice-htp1
    network_mode: host
    volumes:
      - </local/path>:/data
    environment:
      - UC_CONFIG_HOME=/data
      - UC_INTEGRATION_HTTP_PORT=9090
      - UC_INTEGRATION_INTERFACE=0.0.0.0
      - PYTHONPATH=/app
    restart: unless-stopped
```

**Docker Run:**
```bash
docker run -d --name uc-htp1 --restart unless-stopped --network host -v htp1-config:/app/config -e UC_CONFIG_HOME=/app/config -e UC_INTEGRATION_INTERFACE=0.0.0.0 -e UC_INTEGRATION_HTTP_PORT=9090 -e PYTHONPATH=/app ghcr.io/mase1981/uc-intg-monoprice-htp1:latest
```

## Configuration

### Step 1: Prepare Your HTP-1 Receiver

**IMPORTANT**: HTP-1 receiver must be powered on and connected to your network before adding the integration.

#### Verify Network Connection:
1. Check that receiver is connected to network (Ethernet recommended)
2. Note the IP address from receiver's network settings menu
3. Ensure receiver firmware is up to date
4. Verify WebSocket API is accessible (enabled by default)

#### Network Setup:
- **Wired Connection**: Recommended for stability
- **Static IP**: Recommended via DHCP reservation
- **Firewall**: Allow HTTP traffic (port 80)
- **Network Isolation**: Must be on same subnet as Remote

### Step 2: Setup Integration

1. After installation, go to **Settings** → **Integrations**
2. The Monoprice HTP-1 integration should appear in **Available Integrations**
3. Click **"Configure"** to begin setup:

#### **Configuration:**
- **Device Name**: Friendly name (e.g., "Living Room HTP-1")
- **IP Address**: Enter receiver IP (e.g., 192.168.1.100)
- Click **Complete Setup**

#### **Connection Test:**
- Integration verifies receiver connectivity
- WebSocket connection established
- Setup fails if receiver unreachable

4. Integration will create entities:
   - **Media Player**: `media_player.htp1_[device_name]`
   - **Remote**: `remote.htp1_[device_name]`
   - **Sensors**: Multiple sensor entities for state monitoring

## Using the Integration

### Media Player Entity

The media player entity provides complete control:

- **Power Control**: On/Off/Toggle with state feedback
- **Volume Control**: Volume slider (-127.5dB to 0dB mapped to 0-100)
- **Volume Buttons**: Up/Down with real-time feedback
- **Mute Control**: Toggle, Mute, Unmute
- **Source Selection**: Dropdown with all available inputs
- **Sound Mode Selection**: Choose upmix/audio mode
- **State Display**: Current power, volume, source, mute, and audio format

### Remote Entity

The remote entity provides:
- **Power Button**: Toggle power
- **Volume Buttons**: Up, Down, Mute
- **Activity Support**: All buttons work in activities

### Sensor Entities

| Sensor | Description |
|--------|-------------|
| Input Sensor | Currently selected input source |
| Volume Sensor | Current volume level in dB |
| Sound Mode Sensor | Active sound mode/upmix |
| Audio Format Sensor | Current audio codec and channel count |
| Output Audio Format Sensor | Current output audio codec and output channel count |
| Video Mode Sensor | Video resolution and HDR format |
| Connection Sensor | WebSocket connection status |

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

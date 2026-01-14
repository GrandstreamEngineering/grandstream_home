# Grandstream Home Integration

[English](README.md) | [简体中文](README_zh.md)

A powerful Home Assistant custom integration that provides comprehensive support for Grandstream devices,
including GDS series door access devices and GNS series NAS devices. This integration enables local control
and monitoring of your Grandstream devices directly through Home Assistant.

## Key Features

### Device Support

- **GDS372X**: Real-time status monitoring, call status detection, device control
- **GNS5004E/GNS5004R**: Storage monitoring, system performance monitoring, temperature detection, device control

### Monitoring Metrics

- **GDS Devices**: Phone status, available accounts, call status, ringing status
- **GNS Devices**: CPU usage, memory usage, storage pool status, disk health status, network traffic,
  temperature monitoring

### Device Control

- **Reboot Device**: Support for GDS and GNS device reboot
- **Power Management**: GNS device sleep, wake, shutdown functions
- **Camera Support**: GDS device RTSP streaming and snapshot functionality

### Integration Features

- **Real-time Updates**: Local push notifications for instant device status changes
- **Camera Integration**: RTSP stream support for compatible devices
- **Device Actions**: Control devices, custom device automations and services
- **Automatic Discovery**: Local network automatic device detection (Zeroconf)

## Installation Methods

### Method 1: HACS Installation (Recommended)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. In Home Assistant, go to HACS → Integrations
3. Click the "+" button and search for "Grandstream Home"
4. Click "Download" and follow the prompts
5. Restart Home Assistant
6. In Home Assistant, go to Settings → Devices & Services → Add Integration
7. Search for "Grandstream Home" and follow the setup wizard

### Method 2: Script Installation (Recommended)

The project provides two installation scripts that support automatic detection and installation:

#### Using Full Installation Script (install.sh)

```bash
# Automatically detect Home Assistant configuration directory
./install.sh

# Or specify configuration directory
HA_CONFIG_DIR=/config ./install.sh

# Uninstall integration
./install.sh --uninstall

# View help
./install.sh --help
```

#### Using Simplified Installation Script (install-simple.sh)

```bash
# Need to manually specify Home Assistant configuration directory
./install-simple.sh /config

# Or use other configuration path
./install-simple.sh ~/.homeassistant
```

**Installation Script Features:**

- Automatic detection of Home Assistant configuration directory
- Backup existing installation
- Permission settings
- Installation verification
- Support for uninstall functionality

### Method 3: Manual Installation

1. Download the latest release from the [Releases page](https://github.com/GrandstreamEngineering/grandstream_home/releases)
2. Extract the zip file
3. Copy the `grandstream_home` folder to your `config/custom_components` directory
4. Restart Home Assistant
5. In Home Assistant, go to Settings → Devices & Services → Add Integration
6. Search for "Grandstream Home Integration" and follow the setup wizard

## Configuration Methods

### GDS Device Configuration

1. Ensure your GDS device is connected to the same network as Home Assistant
2. Create a local user account on your GDS device with administrator privileges
3. Default username: `gdsha`
4. During setup, provide:
   - Device IP address
   - Password
   - Optional: Custom port
   - Optional: RTSP streaming configuration (camera functionality)

### GNS NAS Configuration

1. Ensure your GNS is connected to the same network as Home Assistant
2. Enable local API access in the NAS web interface
3. During setup, provide:
   - Device IP address
   - Username
   - Password
   - Optional: Custom port

### Automatic Discovery (Zeroconf)

The integration supports automatic discovery functionality:

- Automatically identifies GDS372X and GNS devices
- Automatically configures device names and ports

### Manual Configuration Method

If automatic discovery doesn't work properly, you can manually configure devices:

1. In Home Assistant, go to Settings → Devices & Services → Add Integration
2. Search for "Grandstream Home Integration"
3. In the configuration interface, select "Manual Configuration"
4. Enter the following information:
   - **Device Type**: Select GDS or GNS
   - **Device IP Address**: Device's IP address on the local network
5. Click "Submit" to proceed with the next configuration step

## Entities and Sensors

### GDS Device Sensors

- **Phone Status**: Displays current device status
  - `unknown` - Unknown
  - `available` - Available accounts present
  - `unavailable` - No available accounts
  - `busy` - Call in progress
  - `preview` - Call preview
  - `ringing` - Ringing

### GNS Sensors

- **CPU Usage**: Real-time CPU usage percentage
- **Memory Usage**: Memory usage percentage and total capacity
- **Storage Pool Status**: Storage pool health status and usage rate
- **Disk Health**: Disk temperature, health status and capacity
- **Network Traffic**: Real-time network receive/send rate
- **Temperature Monitoring**: CPU temperature and system temperature
- **Fan Status**: Fan operation status and mode

## Button Controls

### GDS Device Buttons

- **Reboot Device**: Reboot GDS device

### GNS Device Buttons

- **Reboot Device**: Reboot GNS
- **Sleep Device**: Put GNS into sleep state
- **Wake Device**: Wake GNS from sleep state
- **Shutdown Device**: Safely shutdown GNS

## Camera Support

### GDS Camera Functionality

- **RTSP Streaming**
- **Snapshot Functionality**

## Services

The integration provides the following services:

### Grandstream Home Services

- `grandstream_home.reboot_device`: Reboot a Grandstream device
- `grandstream_home.sleep_device`: Put a GNS device to sleep
- `grandstream_home.wake_device`: Wake up a sleeping GNS device
- `grandstream_home.shutdown_device`: Shutdown a GNS device

### Camera Functionality (for GDS devices)

The integration provides FFmpeg-based RTSP streaming support, mainly for image capture and streaming display

## Troubleshooting

### Device Not Found

- Ensure the device is on the same network as Home Assistant
- Check if the device's local API is enabled
- Verify firewall settings allow communication between Home Assistant and the device
- Try manual IP configuration instead of discovery

### Connection Errors

- Verify the correct username and password
- Check for custom ports configured on the device
- Enable debug mode to view device logs for more specific error information

## Changelog

See the [CHANGELOG.md](CHANGELOG.md) for a detailed history of changes.

## License

Please see the [LICENSE](LICENSE) file for details.

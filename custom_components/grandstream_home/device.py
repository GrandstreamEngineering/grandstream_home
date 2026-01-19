"""Device definitions for Grandstream Home."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.entity import DeviceInfo

from .const import DEVICE_TYPE_GDS, DEVICE_TYPE_GNS_NAS, DOMAIN


class GrandstreamDevice:
    """Grandstream device base class."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        config_entry_id: str,
    ) -> None:
        """Initialize the device."""
        self.hass = hass
        self.name = name
        self.unique_id = unique_id
        self.config_entry_id = config_entry_id
        self.device_type = None  # will be set in subclasses
        self.ip_address = None  # Device IP address
        self.mac_address = None  # Device MAC address
        self.firmware_version = None  # Device firmware version
        self._register_device()

    def set_ip_address(self, ip_address: str) -> None:
        """Set device IP address."""
        self.ip_address = ip_address
        # Update device registry information
        if self.ip_address:
            self._register_device()

    def set_mac_address(self, mac_address: str) -> None:
        """Set device MAC address."""
        self.mac_address = mac_address
        # Update device registry information
        if self.mac_address:
            self._register_device()

    def set_firmware_version(self, firmware_version: str) -> None:
        """Set device firmware version."""
        self.firmware_version = firmware_version
        # Update device registry information
        if self.firmware_version:
            self._register_device()

    def _register_device(self) -> None:
        """Register device in Home Assistant."""
        device_registry = async_get_device_registry(self.hass)

        # Check if device already exists
        existing_device = None
        for dev in device_registry.devices.values():
            for identifier in dev.identifiers:
                if identifier[0] == DOMAIN and identifier[1] == self.unique_id:
                    existing_device = dev
                    break
            if existing_device:
                break

        # Prepare model info (including IP address)
        model_info = self.device_type
        if self.ip_address:
            model_info = f"{self.device_type} (IP: {self.ip_address})"

        # Determine sw_version: prefer firmware version, fallback to integration version
        sw_version = self.firmware_version if self.firmware_version else "unknown"

        # Prepare connections (MAC address)
        connections = set()
        if self.mac_address:
            # Remove separators and convert to lowercase for consistency
            mac_clean = self.mac_address.replace(":", "").replace("-", "").lower()
            connections.add(("mac", mac_clean))

        if existing_device:
            # If device exists, update device info
            update_kwargs = {
                "name": self.name,
                "manufacturer": "Grandstream",
                "model": model_info,
                "suggested_area": "Entry",
                "sw_version": sw_version,
            }
            if connections:
                update_kwargs["merge_connections"] = connections

            device_registry.async_update_device(existing_device.id, **update_kwargs)
        else:
            # If device does not exist, create new device
            create_kwargs = {
                "config_entry_id": self.config_entry_id,
                "identifiers": {(DOMAIN, self.unique_id)},
                "name": self.name,
                "manufacturer": "Grandstream",
                "model": model_info,
                "suggested_area": "Entry",
                "sw_version": sw_version,
            }
            if connections:
                create_kwargs["connections"] = connections

            device_registry.async_get_or_create(**create_kwargs)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        # Prepare model info (including IP address)
        model_info = self.device_type
        if self.ip_address:
            model_info = f"{self.device_type} (IP: {self.ip_address})"

        # Determine sw_version: prefer firmware version, fallback to integration version
        sw_version = self.firmware_version if self.firmware_version else "unknown"

        # Prepare connections (MAC address)
        connections = set()
        if self.mac_address:
            # Remove separators and convert to lowercase for consistency
            mac_clean = self.mac_address.replace(":", "").replace("-", "").lower()
            connections.add(("mac", mac_clean))

        device_info_dict = {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "manufacturer": "Grandstream",
            "model": model_info,
            "suggested_area": "Entry",
            "sw_version": sw_version,
        }

        if connections:
            device_info_dict["connections"] = connections

        return DeviceInfo(**device_info_dict)


class GDSDevice(GrandstreamDevice):
    """GDS device."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        config_entry_id: str,
    ) -> None:
        """Initialize the device."""
        super().__init__(hass, name, unique_id, config_entry_id)
        self.device_type = DEVICE_TYPE_GDS


class GNSNASDevice(GrandstreamDevice):
    """GNS NAS device."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        unique_id: str,
        config_entry_id: str,
    ) -> None:
        """Initialize the device."""
        super().__init__(hass, name, unique_id, config_entry_id)
        self.device_type = DEVICE_TYPE_GNS_NAS

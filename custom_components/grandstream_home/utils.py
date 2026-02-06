"""Utility functions and classes for Grandstream Home integration."""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import logging
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

from homeassistant.helpers import device_registry as dr

from .const import DEFAULT_PORT, DEVICE_TYPE_GDS, DEVICE_TYPE_GNS_NAS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _get_encryption_key(unique_id: str) -> bytes:
    """Generate a consistent encryption key based on unique_id."""
    # Use unique_id + a fixed salt to generate key
    salt = hashlib.sha256(f"grandstream_home_{unique_id}_salt_2026".encode()).digest()
    key_material = (unique_id + "grandstream_home").encode() + salt
    key = hashlib.sha256(key_material).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_password(password: str, unique_id: str) -> str:
    """Encrypt password using Fernet encryption.

    Args:
        password: Plain text password
        unique_id: Device unique ID for key generation

    Returns:
        str: Encrypted password (base64 encoded)
    """
    if not password:
        return ""

    try:
        key = _get_encryption_key(unique_id)
        f = Fernet(key)
        encrypted = f.encrypt(password.encode())
        return base64.b64encode(encrypted).decode()
    except (ValueError, TypeError, OSError) as e:
        _LOGGER.warning("Failed to encrypt password: %s", e)
        return password  # Fallback to plaintext


def decrypt_password(encrypted_password: str, unique_id: str) -> str:
    """Decrypt password using Fernet encryption.

    Args:
        encrypted_password: Encrypted password (base64 encoded)
        unique_id: Device unique ID for key generation

    Returns:
        str: Plain text password
    """
    if not encrypted_password:
        return ""

    # Check if it looks like encrypted data (base64 + reasonable length)
    if not is_encrypted_password(encrypted_password):
        return encrypted_password  # Assume plaintext for backward compatibility

    try:
        key = _get_encryption_key(unique_id)
        f = Fernet(key)
        encrypted_bytes = base64.b64decode(encrypted_password.encode())
        decrypted = f.decrypt(encrypted_bytes)
        return decrypted.decode()
    except (ValueError, TypeError, OSError, binascii.Error, InvalidToken) as e:
        _LOGGER.warning("Failed to decrypt password, using as plaintext: %s", e)
        return encrypted_password  # Fallback to plaintext


def is_encrypted_password(password: str) -> bool:
    """Check if password appears to be encrypted.

    Args:
        password: Password string to check

    Returns:
        bool: True if password appears encrypted
    """
    try:
        # Try to decode as base64, if successful it might be encrypted
        base64.b64decode(password.encode())
        return len(password) > 50  # Encrypted passwords are typically longer
    except (ValueError, TypeError, binascii.Error):
        return False


def get_device_type(
    hass: HomeAssistant,
    device: dr.DeviceEntry,
) -> str | None:
    """Get device type from config entry data or device attributes.

    Args:
        hass: Home Assistant instance
        device: Device registry entry

    Returns:
        Device type string (GDS/GNS) or None if not determinable

    """
    if device is None:
        return None

    # Method 1: Get from hass.data (most reliable)
    domain_data = hass.data.get(DOMAIN, {})
    if getattr(device, "config_entries", None):
        for entry_id in device.config_entries:
            entry_data = domain_data.get(entry_id)
            if isinstance(entry_data, dict) and "device_type" in entry_data:
                device_type = entry_data.get("device_type")
                _LOGGER.debug(
                    "Device type from hass.data: device=%s, type=%s",
                    device.id,
                    device_type,
                )
                return device_type

    # Method 2: Fallback - infer from device name/model
    device_name = (device.name or "").upper()
    device_model = (device.model or "").upper()

    if DEVICE_TYPE_GDS in device_name or DEVICE_TYPE_GDS in device_model:
        _LOGGER.debug(
            "Device type inferred as GDS: device=%s, name=%s, model=%s",
            device.id,
            device.name,
            device.model,
        )
        return DEVICE_TYPE_GDS

    if DEVICE_TYPE_GNS_NAS in device_name or DEVICE_TYPE_GNS_NAS in device_model:
        _LOGGER.debug(
            "Device type inferred as GNS: device=%s, name=%s, model=%s",
            device.id,
            device.name,
            device.model,
        )
        return DEVICE_TYPE_GNS_NAS

    _LOGGER.warning(
        "Cannot determine device type: device=%s, name=%s, model=%s",
        device.id,
        device.name,
        device.model,
    )
    return None


class DeviceMatcher:
    """Helper class to find and match Grandstream devices."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize device matcher.

        Args:
            hass: Home Assistant instance

        """
        self.hass = hass
        self._device_registry = dr.async_get(hass)

    def get_device_by_id(self, device_id: str) -> dr.DeviceEntry | None:
        """Get device by ID.

        Args:
            device_id: Device identifier

        Returns:
            Device entry or None if not found

        """
        return self._device_registry.async_get(device_id)

    def get_all_grandstream_devices(self) -> list[dr.DeviceEntry]:
        """Get all Grandstream devices.

        Returns:
            List of all devices belonging to this integration

        """
        return [
            dev
            for dev in self._device_registry.devices.values()
            if any(identifier[0] == DOMAIN for identifier in dev.identifiers)
        ]

    def find_device_by_type(
        self,
        device_type: str,
        fallback_to_first: bool = True,
    ) -> dr.DeviceEntry | None:
        """Find device by type.

        Args:
            device_type: Device type to search for (GDS/GNS)
            fallback_to_first: If True, return first device when type match fails

        Returns:
            Matching device or None

        """
        devices = self.get_all_grandstream_devices()

        if not devices:
            _LOGGER.error("No Grandstream devices found")
            return None

        # If only one device, return it directly
        if len(devices) == 1:
            device = devices[0]
            _LOGGER.debug(
                "Single Grandstream device found: id=%s, name=%s",
                device.id,
                device.name,
            )
            return device

        # Multiple devices: find by type
        for device in devices:
            if get_device_type(self.hass, device) == device_type:
                _LOGGER.debug(
                    "Found device of type %s: id=%s, name=%s",
                    device_type,
                    device.id,
                    device.name,
                )
                return device

        # Fallback to first device if requested
        if fallback_to_first:
            device = devices[0]
            _LOGGER.warning(
                "No device of type %s found, using first device: id=%s, name=%s",
                device_type,
                device.id,
                device.name,
            )
            return device

        _LOGGER.error("No device of type %s found", device_type)
        return None

    def find_device_for_action(
        self,
        device_id: str | None,
        action_type: str,
    ) -> dr.DeviceEntry | None:
        """Find appropriate device for an action.

        Args:
            device_id: Original device ID (may be None or invalid)
            action_type: Type of action to perform

        Returns:
            Best matching device or None

        """
        # Try to get device by ID first
        if device_id:
            device = self.get_device_by_id(device_id)
            if device and any(
                identifier[0] == DOMAIN for identifier in device.identifiers
            ):
                return device

            _LOGGER.warning(
                "Device %s not found or not a Grandstream device, searching alternatives",
                device_id,
            )

        # Action type to device type mapping
        action_device_type_map = {
            "sleep_device": DEVICE_TYPE_GNS_NAS,
            "wake_device": DEVICE_TYPE_GNS_NAS,
            "shutdown_device": DEVICE_TYPE_GNS_NAS,
        }

        # Find device by action type
        target_type = action_device_type_map.get(action_type)
        if target_type:
            return self.find_device_by_type(target_type, fallback_to_first=True)

        # For generic actions (like reboot), return any device
        return self.find_device_by_type(DEVICE_TYPE_GDS, fallback_to_first=True)

    def validate_device_for_integration(
        self,
        device: dr.DeviceEntry | None,
    ) -> bool:
        """Validate if device belongs to this integration.

        Args:
            device: Device to validate

        Returns:
            True if device is valid, False otherwise

        """
        if device is None:
            return False

        return any(identifier[0] == DOMAIN for identifier in device.identifiers)


class DeviceTypeResolver:
    """Helper to resolve device types for automation components."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize resolver.

        Args:
            hass: Home Assistant instance

        """
        self.hass = hass
        self._device_registry = dr.async_get(hass)

    def get_device_type_for_automation(
        self,
        device_id: str,
        automation_type: str,
    ) -> str | None:
        """Get device type for automation purposes.

        Args:
            device_id: Device ID
            automation_type: Type of automation (action/condition/trigger)

        Returns:
            Device type or None

        """
        device = self._device_registry.async_get(device_id)

        if device is None:
            _LOGGER.error(
                "Device not found for %s: %s",
                automation_type,
                device_id,
            )
            return None

        # Validate device belongs to integration
        if not any(identifier[0] == DOMAIN for identifier in device.identifiers):
            _LOGGER.error(
                "Device does not belong to %s integration: %s",
                DOMAIN,
                device_id,
            )
            return None

        # Get device type
        device_type = get_device_type(self.hass, device)

        _LOGGER.debug(
            "Resolved device type for %s: device_id=%s, name=%s, type=%s",
            automation_type,
            device_id,
            device.name,
            device_type,
        )

        return device_type

    def is_gds_device(self, device_id: str) -> bool:
        """Check if device is GDS type.

        Args:
            device_id: Device identifier

        Returns:
            True if device is GDS type, False otherwise

        """
        return (
            self.get_device_type_for_automation(device_id, "check") == DEVICE_TYPE_GDS
        )

    def is_gns_device(self, device_id: str) -> bool:
        """Check if device is GNS type.

        Args:
            device_id: Device identifier

        Returns:
            True if device is GNS type, False otherwise

        """
        return (
            self.get_device_type_for_automation(device_id, "check")
            == DEVICE_TYPE_GNS_NAS
        )


def format_host_url(host: str) -> str:
    """Format host URL with square brackets for IPv6 addresses."""
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]"
    except ValueError:
        # Not a valid IP address, return as is
        pass
    return host


def generate_unique_id(
    device_name: str, device_type: str, host: str, port: int = DEFAULT_PORT
) -> str:
    """Generate device unique ID.

    Prioritize using device name as the basis for unique ID. If device name is empty, use IP address and port.

    Args:
        device_name: Device name
        device_type: Device type (GDS, GNS_NAS)
        host: Device IP address
        port: Device port

    Returns:
        str: Formatted unique ID

    """
    # Clean device name, remove special characters
    if device_name and device_name.strip():
        # Use device name as the basis for unique ID
        clean_name = (
            device_name.strip().replace(" ", "_").replace("-", "_").replace(".", "_")
        )
        unique_id = f"{clean_name}"
    else:
        # If no device name, use IP address and port
        clean_host = host.replace(".", "_").replace(":", "_")
        unique_id = f"{device_type}_{clean_host}_{port}"

    # Ensure unique ID contains no special characters and convert to lowercase
    return unique_id.replace(" ", "_").replace("-", "_").lower()


__all__ = [
    "DeviceMatcher",
    "DeviceTypeResolver",
    "format_host_url",
    "generate_unique_id",
    "get_device_type",
]
# Add password encryption functions to exports
__all__ += ["decrypt_password", "encrypt_password", "is_encrypted_password"]

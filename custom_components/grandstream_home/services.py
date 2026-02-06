"""Services for Grandstream integration."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DEVICE_TYPE_GDS, DEVICE_TYPE_GNS_NAS, DOMAIN
from .utils import DeviceMatcher

_LOGGER = logging.getLogger(__name__)


class APIResolver:
    """Helper class to resolve API instances for service calls."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize API resolver."""
        self.hass = hass
        self._device_matcher = DeviceMatcher(hass)

    def _get_all_apis(self) -> list[Any]:
        """Get all available API instances."""
        if DOMAIN not in self.hass.data:
            return []

        return [
            entry_data["api"]
            for entry_data in self.hass.data[DOMAIN].values()
            if isinstance(entry_data, dict) and "api" in entry_data
        ]

    def _match_api_by_mac(self, device_mac: str) -> Any | None:
        """Match API by MAC address."""
        for api in self._get_all_apis():
            if (
                hasattr(api, "device_mac")
                and api.device_mac
                and api.device_mac.upper() == device_mac.upper()
            ):
                _LOGGER.debug("Found API by MAC address: %s", device_mac)
                return api
        return None

    def _match_api_by_ip(self, ip_address: str) -> Any | None:
        """Match API by IP address."""
        for api in self._get_all_apis():
            # Both GDS and GNS use host attribute
            if hasattr(api, "host") and api.host == ip_address:
                _LOGGER.debug("Found API by IP address: %s", ip_address)
                return api
        return None

    def _match_api_by_device_name(self, device_name: str) -> Any | None:
        """Match API by device name."""
        if not device_name:
            return None

        for api in self._get_all_apis():
            if hasattr(api, "device_name") and api.device_name:
                if (
                    api.device_name == device_name
                    or device_name.lower() in api.device_name.lower()
                    or api.device_name.lower() in device_name.lower()
                ):
                    _LOGGER.debug("Found API by device name: %s", device_name)
                    return api
        return None

    def _match_api_by_device_type(
        self,
        device_model: str | None,
        device_name: str | None,
    ) -> Any | None:
        """Match API by device type."""
        for api in self._get_all_apis():
            # Match by model
            if hasattr(api, "device_type") and api.device_type and device_model:
                if (
                    api.device_type.lower() in device_model.lower()
                    or device_model.lower() in api.device_type.lower()
                ):
                    _LOGGER.debug("Found API by device type: %s", api.device_type)
                    return api

            # Match by keywords in device name
            if device_name and hasattr(api, "device_type") and api.device_type:
                device_name_upper = device_name.upper()
                api_type_upper = api.device_type.upper()

                if (
                    DEVICE_TYPE_GDS in device_name_upper
                    and DEVICE_TYPE_GDS in api_type_upper
                ):
                    _LOGGER.debug("Found API by GDS keyword match")
                    return api
                if (
                    DEVICE_TYPE_GNS_NAS in device_name_upper
                    and DEVICE_TYPE_GNS_NAS in api_type_upper
                ):
                    _LOGGER.debug("Found API by GNS keyword match")
                    return api
        return None

    def _match_api_by_device(
        self,
        device: Any,
        device_unique_id: str,  # pylint: disable=unused-argument
    ) -> Any | None:
        """Match API by device registry information (MAC, IP, unique_id).

        Args:
            device: Device registry device object
            device_unique_id: Device unique identifier

        Returns:
            Matched API instance or None

        """
        # Extract MAC and IP from device connections
        device_mac = None
        device_ip = None

        for connection_type, connection_value in device.connections:
            if connection_type == "mac":
                device_mac = connection_value
            elif connection_type == "ip":
                device_ip = connection_value

        _LOGGER.debug(
            "Device connections: MAC=%s, IP=%s",
            device_mac or "unknown",
            device_ip or "unknown",
        )

        # Match by MAC address
        if device_mac:
            api = self._match_api_by_mac(device_mac)
            if api:
                return api

        # Match by IP address
        if device_ip:
            for api in self._get_all_apis():
                # Both GDS and GNS use host attribute
                if hasattr(api, "host") and api.host == device_ip:
                    _LOGGER.debug("Found API by host/IP match: %s", device_ip)
                    return api

        return None

    def get_api_for_device(self, device_id: str | None = None) -> Any | None:
        """Get API instance for device.

        Args:
            device_id: Device ID (optional)

        Returns:
            API instance or None

        """
        if DOMAIN not in self.hass.data:
            _LOGGER.error("Integration %s not initialized", DOMAIN)
            return None

        apis = self._get_all_apis()
        if not apis:
            _LOGGER.error("No available API instances found")
            return None

        # If no device_id specified, return first available API
        if not device_id:
            api = apis[0]
            api_mac = getattr(api, "device_mac", "unknown")
            api_ip = getattr(api, "ip_address", "unknown")
            _LOGGER.debug(
                "No device ID specified, using first available API: MAC=%s, IP=%s",
                api_mac,
                api_ip,
            )
            return api

        # Log all available APIs for debugging
        _LOGGER.debug("Available API instances: %d", len(apis))
        for api in apis:
            _LOGGER.debug(
                "  - class=%s, mac=%s, ip=%s, type=%s",
                type(api).__name__,
                getattr(api, "device_mac", "unknown"),
                getattr(api, "ip_address", "unknown"),
                getattr(api, "device_type", "unknown"),
            )

        # Get device from registry
        device = self._device_matcher.get_device_by_id(device_id)
        if not device:
            _LOGGER.error("Device not found: %s, using first available API", device_id)
            return apis[0] if apis else None

        # Validate device belongs to integration
        if not self._device_matcher.validate_device_for_integration(device):
            _LOGGER.error(
                "Device does not belong to %s integration: %s",
                DOMAIN,
                device_id,
            )
            return apis[0]

        # Get device unique_id from identifiers
        device_unique_id = None
        for identifier in device.identifiers:
            if identifier[0] == DOMAIN:
                device_unique_id = identifier[1]
                break

        if not device_unique_id:
            _LOGGER.error(
                "Cannot get device unique_id: %s, using first available API",
                device_id,
            )
            return apis[0] if apis else None

        _LOGGER.debug(
            "Looking for API: device_id=%s, unique_id=%s, name=%s, model=%s",
            device_id,
            device_unique_id,
            device.name,
            device.model,
        )

        # First try to match by device unique_id and device connections (MAC/IP)
        matched_api = self._match_api_by_device(device, device_unique_id)
        if matched_api:
            return matched_api

        # Try different matching strategies in order
        matchers = [
            lambda: self._match_api_by_device_name(device.name or ""),
            lambda: self._match_api_by_device_type(device.model, device.name),
        ]

        for matcher in matchers:
            api = matcher()
            if api:
                return api

        # Fallback to first available API
        _LOGGER.warning(
            "No matching API found for device: %s, using first available",
            device_id,
        )
        return apis[0] if apis else None


# Service data validation schemas
SIMPLE_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): cv.string,
    }
)


async def async_setup_services(hass: HomeAssistant) -> bool:
    """Set up services for Grandstream integration."""
    api_resolver = APIResolver(hass)

    async def _call_api_method(
        call: ServiceCall,
        method_name: str,
        *args,
    ) -> None:
        """Call API methods.

        Args:
            call: Service call data
            method_name: Name of API method to call
            *args: Additional arguments to pass to method

        """
        device_id = call.data.get("device_id")
        api = api_resolver.get_api_for_device(device_id)

        if not api:
            _LOGGER.error(
                "No available device API instance found%s",
                f" for device: {device_id}" if device_id else "",
            )
            return

        # Check if API supports the method
        if not hasattr(api, method_name):
            _LOGGER.error(
                "Device does not support %s (API type: %s)",
                method_name,
                type(api).__name__,
            )
            return

        try:
            _LOGGER.info("Calling %s on %s", method_name, type(api).__name__)
            method = getattr(api, method_name)
            await hass.async_add_executor_job(method, *args)
            _LOGGER.info("%s command sent successfully", method_name)
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as err:
            _LOGGER.exception("Failed to execute %s", method_name, exc_info=err)

    async def async_reboot_device(call: ServiceCall) -> None:
        """Handle reboot device service."""
        await _call_api_method(call, "reboot_device")

    async def async_sleep_device(call: ServiceCall) -> None:
        """Handle NAS sleep service."""
        await _call_api_method(call, "sleep_device")

    async def async_wake_device(call: ServiceCall) -> None:
        """Handle NAS wake service."""
        await _call_api_method(call, "wake_device")

    async def async_shutdown_device(call: ServiceCall) -> None:
        """Handle NAS shutdown service."""
        await _call_api_method(call, "shutdown_device")

    # Service registration mapping
    services = {
        "reboot_device": (async_reboot_device, SIMPLE_DEVICE_SCHEMA),
        "sleep_device": (async_sleep_device, SIMPLE_DEVICE_SCHEMA),
        "wake_device": (async_wake_device, SIMPLE_DEVICE_SCHEMA),
        "shutdown_device": (async_shutdown_device, SIMPLE_DEVICE_SCHEMA),
    }

    # Register all services
    for service_name, (handler, schema) in services.items():
        hass.services.async_register(
            DOMAIN,
            service_name,
            handler,
            schema=schema,
        )

    _LOGGER.info("Grandstream Home services registered successfully")
    return True

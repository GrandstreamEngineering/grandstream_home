"""The Grandstream Home integration."""

import asyncio
import logging
import socket
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .api.gds_api import GDSPhoneAPI
from .api.gns_api import GNSNasAPI
from .const import (
    CONF_COMMAND_WEBHOOK_ID,
    CONF_DEVICE_TYPE,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RTSP_PASSWORD,
    CONF_RTSP_USERNAME,
    CONF_STATUS_WEBHOOK_ID,
    CONF_USE_HTTPS,
    CONF_USERNAME,
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTPS_PORT,
    DEFAULT_PORT,
    DEVICE_TYPE_GDS,
    DEVICE_TYPE_GNS_NAS,
    DOMAIN,
)
from .coordinator import GrandstreamCoordinator
from .device import GDSDevice, GNSNASDevice
from .receiver import GsWebhookCommandReceiver, GsWebhookStatusReceiver
from .services import async_setup_services
from .utils import decrypt_password, generate_unique_id

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.CAMERA, Platform.SENSOR]

# Device type mapping to API classes
DEVICE_API_MAPPING = {
    DEVICE_TYPE_GDS: GDSPhoneAPI,
    DEVICE_TYPE_GNS_NAS: GNSNasAPI,
}

# Device type mapping to device classes
DEVICE_CLASS_MAPPING = {
    DEVICE_TYPE_GDS: GDSDevice,
    DEVICE_TYPE_GNS_NAS: GNSNASDevice,
}


async def _get_ha_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Get Home Assistant URL with fallback strategies."""
    # Try configured URL first
    ha_url = hass.config.internal_url or hass.config.external_url
    if ha_url:
        _LOGGER.info("Using configured Home Assistant URL: %s", ha_url)
        return ha_url

    # Try local network IP
    local_ip_url = _try_get_local_ip_url(hass)
    if local_ip_url:
        return local_ip_url

    # Use fallback URLs
    return _get_fallback_ha_url(hass, entry)


def _try_get_local_ip_url(hass: HomeAssistant) -> str:
    """Try to get local network IP URL."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ha_ip = s.getsockname()[0]

        # Use configured URL scheme if available, default to http
        scheme = _get_url_scheme(hass)
        port = hass.config.api.port if hass.config.api else DEFAULT_PORT

        ha_url = f"{scheme}://{ha_ip}:{port}"
        _LOGGER.info("Using local network IP: %s", ha_url)
    except OSError as e:
        _LOGGER.error("Failed to get local IP: %s", e)
        return ""
    return ha_url


def _get_fallback_ha_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Get fallback Home Assistant URL using device network IP or default configuration."""
    # Use configured URL scheme if available, default to http
    scheme = _get_url_scheme(hass)
    port = hass.config.api.port if hass.config.api else DEFAULT_PORT
    device_ip = entry.data.get(CONF_HOST)

    if device_ip:
        # Try to use device network as fallback
        # Instead of hardcoded .180, try using the device's own IP as HA might be on same network
        ip_parts = device_ip.split(".")
        if len(ip_parts) == 4:
            ha_ip = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.{ip_parts[3]}"
            ha_url = f"{scheme}://{ha_ip}:{port}"
            _LOGGER.warning("Using device network IP: %s", ha_url)
            return ha_url

    # Default fallback
    ha_url = f"{scheme}://{hass.config.api.host if hass.config.api and hass.config.api.host else 'localhost'}:{port}"
    _LOGGER.warning("Using default API config IP: %s", ha_url)
    return ha_url


def _get_url_scheme(hass: HomeAssistant) -> str:
    """Determine URL scheme (http/https) from Home Assistant configuration."""
    # Try to determine scheme from existing URLs
    external_url = hass.config.external_url
    internal_url = hass.config.internal_url

    if external_url:
        return external_url.split("://")[0]
    if internal_url:
        return internal_url.split("://")[0]

    # Default to http for local development, https for production
    # This is a simple heuristic - in production, Home Assistant typically uses https
    return "https" if hass.config.api and hass.config.api.port == 443 else "http"


async def _setup_api(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    """Set up and initialize API."""
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_GDS)

    # Get API class using mapping, default to GDS if unknown type
    api_class = DEVICE_API_MAPPING.get(device_type, GDSPhoneAPI)

    # Create API instance based on device type
    api = _create_api_instance(api_class, device_type, entry)

    # Initialize global API lock if not exists
    hass.data.setdefault(DOMAIN, {})
    if "api_lock" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["api_lock"] = asyncio.Lock()

    # Attempt login with error handling
    await _attempt_api_login(hass, api)

    return api


def _create_api_instance(api_class, device_type: str, entry: ConfigEntry) -> Any:
    """Create API instance based on device type."""
    host = entry.data.get(CONF_HOST, "")
    username = entry.data.get(CONF_USERNAME, "")
    encrypted_password = entry.data.get(CONF_PASSWORD, "")
    password = decrypt_password(encrypted_password, entry.unique_id or "default")
    use_https = entry.data.get(CONF_USE_HTTPS, False)
    if device_type == DEVICE_TYPE_GDS:
        port = entry.data.get(CONF_PORT, DEFAULT_PORT)

        rtsp_username = entry.data.get(CONF_RTSP_USERNAME)
        encrypted_rtsp_password = entry.data.get(CONF_RTSP_PASSWORD)
        rtsp_password = None
        if encrypted_rtsp_password:
            rtsp_password = decrypt_password(encrypted_rtsp_password, entry.unique_id or "default")
            _LOGGER.debug("RTSP password decrypted for device %s", entry.unique_id)

        return api_class(
            host=host,
            username=username,
            password=password,
            use_https=use_https,
            port=port,
            rtsp_username=rtsp_username,
            rtsp_password=rtsp_password
        )

    if device_type == DEVICE_TYPE_GNS_NAS:
        port = entry.data.get(
            CONF_PORT, DEFAULT_HTTPS_PORT if use_https else DEFAULT_HTTP_PORT
        )
        return api_class(host, username, password, port=port, use_https=use_https)

    # Default fallback
    return api_class(host, username, password)


async def _attempt_api_login(hass: HomeAssistant, api: Any) -> None:
    """Attempt to login to device API with error handling."""
    async with hass.data[DOMAIN]["api_lock"]:
        try:
            success = await hass.async_add_executor_job(api.login)
            if not success:
                _LOGGER.warning(
                    "Initial login failed (device may be offline), integration will continue to load"
                )
        except (ImportError, AttributeError, ValueError) as e:
            _LOGGER.warning(
                "API setup encountered error (device may be offline): %s, integration will continue to load",
                e,
            )


async def _setup_device(
    hass: HomeAssistant, entry: ConfigEntry, device_type: str
) -> Any:
    """Set up device instance."""
    # Get device class using mapping, default to GDS if unknown type
    device_class = DEVICE_CLASS_MAPPING.get(device_type, GDSDevice)

    # Extract device basic information
    device_info = {
        "host": entry.data.get("host", ""),
        "port": entry.data.get("port", "80"),
        "name": entry.data.get("name", ""),
    }

    # Get API instance for MAC address retrieval
    api = entry.runtime_data.get("api")

    # Extract MAC address from API if available
    mac_address = _extract_mac_address(api)
    _LOGGER.debug("Extracted MAC address: %s", mac_address)

    # Generate unique ID using global function
    unique_id = generate_unique_id(
        device_info["name"], device_type, device_info["host"], device_info["port"]
    )
    _LOGGER.info(
        "Device unique ID: %s, name: %s, type: %s",
        unique_id,
        device_info["name"],
        device_type,
    )

    # Handle existing device
    await _handle_existing_device(hass, unique_id, device_info["name"], device_type)

    # Create device instance
    device = device_class(
        hass=hass,
        name=device_info["name"],
        unique_id=unique_id,
        config_entry_id=entry.entry_id,
    )

    # Set device network information
    _set_device_network_info(device, api, device_info)

    return device


def _extract_mac_address(api: Any) -> str:
    """Extract MAC address from API if available."""
    if not api or not hasattr(api, "device_mac") or not api.device_mac:
        return ""

    mac_address = api.device_mac.replace(":", "").upper()
    _LOGGER.info("Got MAC address from API: %s", mac_address)
    return mac_address


async def _handle_existing_device(
    hass: HomeAssistant, unique_id: str, name: str, device_type: str
) -> None:
    """Check and update existing device if found."""
    device_registry = dr.async_get(hass)

    for dev in device_registry.devices.values():
        for identifier in dev.identifiers:
            if identifier[0] == DOMAIN and identifier[1] == unique_id:
                _LOGGER.info("Found existing device: %s, name: %s", dev.id, dev.name)

                # Update device attributes
                device_registry.async_update_device(
                    dev.id,
                    name=name,
                    manufacturer="Grandstream",
                    model=device_type,
                )
                return


def _set_device_network_info(
    device: Any, api: Any, device_info: dict[str, str]
) -> None:
    """Set device network information (IP and MAC addresses)."""
    # Set IP address
    if api and hasattr(api, "host") and api.host:
        _LOGGER.info("Setting device IP address: %s", api.host)
        device.set_ip_address(api.host)
    else:
        _LOGGER.info("Using configured host address as IP: %s", device_info["host"])
        device.set_ip_address(device_info["host"])

    # Set MAC address if available
    if api and hasattr(api, "device_mac") and api.device_mac:
        _LOGGER.info("Setting device MAC address: %s", api.device_mac)
        device.set_mac_address(api.device_mac)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Grandstream Home integration."""
    try:
        _LOGGER.debug("Starting integration initialization: %s", entry.entry_id)

        # Extract device type from entry
        device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_GDS)

        # 1. Set up API
        api = await _setup_api_with_error_handling(hass, entry, device_type)

        # Store API in runtime_data (required for Bronze quality scale)
        entry.runtime_data = {"api": api}

        # 2. Create device instance
        device = await _setup_device(hass, entry, device_type)
        _LOGGER.debug(
            "Device created successfully: %s, unique ID: %s",
            device.name,
            device.unique_id,
        )

        # 3. Initialize data storage
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {}

        # 4. Create coordinator
        coordinator = await _setup_coordinator(hass, device_type, entry)

        # 5. Update stored data
        await _update_stored_data(hass, entry, coordinator, device, device_type)

        # 6. Set up platforms, services, and data receivers
        await _setup_platforms(hass, entry)
        await async_setup_services(hass)
        await _set_data_receiver(hass, coordinator, entry)

        # 7. Set up device configuration
        ha_url = await _get_ha_url(hass, entry)
        if api:
            api.ha_ip_address = ha_url

        # 8. Configure GDS devices
        await _configure_gds_device(hass, api, device_type, entry, ha_url)

        # 9. Update device information from API (for GNS devices)
        await _update_device_info_from_api(hass, api, device_type, device)

        _LOGGER.info("Integration initialization completed")
    except Exception as e:
        _LOGGER.exception("Error setting up integration")
        raise ConfigEntryNotReady("Integration setup failed") from e
    return True


async def _setup_api_with_error_handling(
    hass: HomeAssistant, entry: ConfigEntry, device_type: str
) -> Any:
    """Set up API with error handling."""
    _LOGGER.debug("Starting API setup")
    try:
        api = await _setup_api(hass, entry)
    except (ImportError, AttributeError, ValueError) as e:
        _LOGGER.exception("Error during API setup")
        raise ConfigEntryNotReady(f"API setup failed: {e}") from e
    _LOGGER.debug("API setup successful, device type: %s", device_type)
    return api


async def _setup_coordinator(
    hass: HomeAssistant, device_type: str, entry: ConfigEntry
) -> Any:
    """Set up data coordinator."""
    _LOGGER.debug("Starting coordinator creation")
    coordinator = GrandstreamCoordinator(hass, device_type, entry.entry_id)
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.debug("Coordinator initialization completed")
    return coordinator


async def _update_stored_data(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Any,
    device: Any,
    device_type: str,
) -> None:
    """Update stored data in hass.data."""
    _LOGGER.debug("Starting data storage update")
    try:
        # Get API from runtime_data
        api = entry.runtime_data.get("api") if entry.runtime_data else None

        hass.data[DOMAIN][entry.entry_id].update(
            {
                "api": api,
                "coordinator": coordinator,
                "device": device,
                "device_type": device_type,
            }
        )
        _LOGGER.debug("Data storage update successful")
    except (ImportError, AttributeError, ValueError) as e:
        _LOGGER.exception("Error during data update")
        raise ConfigEntryNotReady(f"Data storage update failed: {e}") from e


async def _setup_platforms(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up all platforms."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def _configure_gds_device(
    hass: HomeAssistant, api: Any, device_type: str, entry: ConfigEntry, ha_url: str
) -> None:
    """Configure GDS device with webhook URLs."""
    if device_type != DEVICE_TYPE_GDS or not api:
        return

    # Build webhook URLs
    webhook_config = _build_webhook_config(hass, entry, ha_url)

    # Ensure device information is complete
    await _ensure_device_info(hass, api)

    # Send configuration to device
    await _send_gds_device_config(hass, api, webhook_config, entry)


def _build_webhook_config(
    hass: HomeAssistant, entry: ConfigEntry, ha_url: str
) -> dict[str, str | dict[str, str]]:
    """Build webhook configuration dictionary."""
    status_webhook_id = entry.data.get(CONF_STATUS_WEBHOOK_ID)
    command_webhook_id = entry.data.get(CONF_COMMAND_WEBHOOK_ID)

    # Ensure URL is complete with scheme
    if not ha_url.startswith(("http://", "https://")):
        scheme = _get_url_scheme(hass)
        ha_url = f"{scheme}://{ha_url}"

    status_webhook_url = (
        f"{ha_url}/api/webhook/{status_webhook_id}" if status_webhook_id else ""
    )
    command_webhook_url = (
        f"{ha_url}/api/webhook/{command_webhook_id}" if command_webhook_id else ""
    )

    return {
        "status_webhook_url": status_webhook_url,
        "command_webhook_url": command_webhook_url,
        "config_data": {
            "ha.webhook.status_url": status_webhook_url,
            "ha.webhook.command_url": command_webhook_url,
            "ha.auth.url": ha_url,
        },
    }


async def _ensure_device_info(hass: HomeAssistant, api: Any) -> None:
    """Ensure device information is complete, attempting re-login if needed."""
    if not api or (api.device_mac and api.session_id):
        return

    _LOGGER.warning("Missing device MAC or session ID, attempting re-login")
    async with hass.data[DOMAIN]["api_lock"]:
        success = await hass.async_add_executor_job(api.login)
        if not success:
            _LOGGER.warning(
                "Re-login failed (device may be offline), continuing without device info"
            )
        else:
            _LOGGER.info("Device re-login successful")


async def _send_gds_device_config(
    hass: HomeAssistant,
    api: Any,
    webhook_config: dict[str, str | dict[str, str]],
    entry: ConfigEntry,
) -> None:
    """Send configuration to GDS device."""
    async with hass.data[DOMAIN]["api_lock"]:
        try:
            # Register HA URLs for webhook callbacks
            register_result = await hass.async_add_executor_job(
                api.register_ha_urls,
                webhook_config["status_webhook_url"],
                webhook_config["command_webhook_url"],
                entry.entry_id,
            )
            _LOGGER.info("HA URL registration result: %s", register_result)
        except RuntimeError as e:
            _LOGGER.error("Configuration update failed: %s", e)
            _LOGGER.warning(
                "Integration will continue to load with limited functionality"
            )


async def _update_device_info_from_api(
    hass: HomeAssistant, api: Any, device_type: str, device: Any
) -> None:
    """Update device information from API for GNS devices."""
    if (
        device_type != DEVICE_TYPE_GNS_NAS
        or not api
        or not hasattr(api, "get_system_info")
    ):
        return

    try:
        _LOGGER.debug("Getting additional device info from API")
        system_info = await hass.async_add_executor_job(api.get_system_info)

        if not system_info:
            return

        # Update device name with model if needed
        _update_device_name(device, system_info)

        # Update firmware version if available
        _update_firmware_version(device, api, system_info)

    except (OSError, ValueError, RuntimeError) as e:
        _LOGGER.warning("Failed to get additional device info from API: %s", e)


def _update_device_name(device: Any, system_info: dict[str, str]) -> None:
    """Update device name with model information if needed."""
    product_name = system_info.get("product_name", "")
    current_name = device.name

    # If device name doesn't contain model info, try to add model
    if product_name and not any(model in current_name for model in ("GNS", "GDS")):
        # Construct new device name including model info
        new_name = f"{product_name.upper()}"
        _LOGGER.info(
            "Updating device name from %s to %s with model info", current_name, new_name
        )

        # Update device instance name and registration info
        device.name = new_name
        # Use public method if available instead of accessing private method
        if hasattr(device, "register_device"):
            device.register_device()


def _update_firmware_version(
    device: Any, api: Any, system_info: dict[str, str]
) -> None:
    """Update device firmware version from API or system info."""
    # First try from system info
    product_version = system_info.get("product_version", "")
    if product_version:
        _LOGGER.info("Setting device firmware version: %s", product_version)
        device.set_firmware_version(product_version)
        return

    # Fallback to API version attribute
    if hasattr(api, "version") and api.version:
        _LOGGER.debug("Setting device firmware version from API: %s", api.version)
        device.set_firmware_version(api.version)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry."""
    # Get device type and API instance before unloading
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_GDS)
    api = _get_api_from_hass_data(hass, entry.entry_id)

    # Clear webhook registration on GDS devices before unloading
    await _clear_webhook_registration(hass, api, device_type, entry)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


def _get_api_from_hass_data(hass: HomeAssistant, entry_id: str) -> Any:
    """Get API instance from hass data."""
    if entry_id in hass.data.get(DOMAIN, {}):
        return hass.data[DOMAIN][entry_id].get("api")
    return None


async def _clear_webhook_registration(
    hass: HomeAssistant, api: Any, device_type: str, entry: ConfigEntry
) -> None:
    """Clear webhook registration for GDS devices."""
    if device_type != DEVICE_TYPE_GDS or not api:
        return

    try:
        _LOGGER.info("Clearing webhook registration for device: %s", entry.entry_id)
        # Call register_ha_urls with empty URLs and ha_instance_id
        async with hass.data[DOMAIN]["api_lock"]:
            clear_result = await hass.async_add_executor_job(
                api.register_ha_urls,
                "",  # Empty status_url
                "",  # Empty command_url
                None,  # Empty ha_instance_id
            )
            if clear_result.get("response") == "success":
                _LOGGER.info("Successfully cleared webhook registration")
            else:
                _LOGGER.warning(
                    "Failed to clear webhook registration: %s", clear_result
                )
    except (OSError, ValueError, RuntimeError) as e:
        _LOGGER.warning(
            "Error clearing webhook registration (device may be offline): %s", e
        )


async def _set_data_receiver(
    hass: HomeAssistant, coordinator: GrandstreamCoordinator, entry: ConfigEntry
) -> bool:
    """Set up data receivers for GDS devices - Webhook only."""
    # Only GDS devices need webhook receivers
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_GDS)
    if device_type != DEVICE_TYPE_GDS:
        _LOGGER.debug(
            "Skipping webhook receiver setup for non-GDS device type: %s", device_type
        )
        return True

    # Get Webhook IDs
    status_webhook_id = entry.data.get(CONF_STATUS_WEBHOOK_ID)
    command_webhook_id = entry.data.get(CONF_COMMAND_WEBHOOK_ID)

    # Get Home Assistant URL
    ha_url = await _get_ha_url(hass, entry)

    # Set up Webhook receivers (if Webhook IDs are configured)
    if status_webhook_id:
        webhook_status_receiver = GsWebhookStatusReceiver(
            hass, coordinator, status_webhook_id
        )
        await webhook_status_receiver.async_setup()
        _LOGGER.info("Set up Webhook status receiver: %s", status_webhook_id)
    else:
        _LOGGER.warning(
            "Status webhook ID not configured, skipping status receiver setup"
        )

    if command_webhook_id:
        webhook_cmd_receiver = GsWebhookCommandReceiver(hass, command_webhook_id)
        await webhook_cmd_receiver.async_setup()
        _LOGGER.info("Set up Webhook command receiver: %s", command_webhook_id)
    else:
        _LOGGER.warning(
            "Command webhook ID not configured, skipping command receiver setup"
        )

    # Log receiver information
    status_webhook_url = (
        f"{ha_url}/api/webhook/{status_webhook_id}"
        if status_webhook_id
        else "Not configured"
    )
    command_webhook_url = (
        f"{ha_url}/api/webhook/{command_webhook_id}"
        if command_webhook_id
        else "Not configured"
    )

    _LOGGER.info(
        "Webhook receivers configured for GDS device:"
        "\nStatus Webhook: %s"
        "\nCommand Webhook: %s",
        status_webhook_url,
        command_webhook_url,
    )

    return True

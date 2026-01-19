"""Provides device automations for Grandstream Home."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DEVICE_TYPE_GDS, DEVICE_TYPE_GNS_NAS, DOMAIN
from .utils import DeviceMatcher, DeviceTypeResolver

_LOGGER = logging.getLogger(__name__)

# Action types - matching translation keys
ACTION_TYPES = {
    "reboot_device": "Reboot Device",
    "sleep_device": "Put device to sleep",
    "wake_device": "Wake device from sleep",
    "shutdown_device": "Shutdown device",
}

# Device type to actions mapping - for performance optimization
DEVICE_ACTIONS_MAP = {
    DEVICE_TYPE_GDS: ["reboot_device"],
    DEVICE_TYPE_GNS_NAS: [
        "reboot_device",
        "sleep_device",
        "wake_device",
        "shutdown_device",
    ],
}

# Default actions for unknown device types
DEFAULT_ACTIONS = ["reboot_device"]

# Action to service name mapping - one-to-one correspondence
ACTION_SERVICE_MAP = {
    "reboot_device": "reboot_device",
    "sleep_device": "sleep_device",
    "wake_device": "wake_device",
    "shutdown_device": "shutdown_device",
}

# Action schemas
BASIC_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_DOMAIN): vol.Equal(DOMAIN),
    }
)

ACTION_SCHEMA = BASIC_ACTION_SCHEMA


def _build_action_dict(device_id: str, action_type: str) -> dict[str, str]:
    """Build action dictionary.

    Args:
        device_id: Device identifier
        action_type: Action type identifier

    Returns:
        Action configuration dictionary
    """
    return {
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: device_id,
        CONF_TYPE: action_type,
    }


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Return a list of available device actions.

    Args:
        hass: Home Assistant instance
        device_id: Device identifier

    Returns:
        List of action configurations based on device type
    """
    resolver = DeviceTypeResolver(hass)
    device_type = resolver.get_device_type_for_automation(device_id, "action")

    if device_type is None:
        return []

    _LOGGER.debug(
        "Creating actions for device: ID=%s, type=%s",
        device_id,
        device_type,
    )

    # Get actions from mapping or use default
    action_types = DEVICE_ACTIONS_MAP.get(device_type, DEFAULT_ACTIONS)

    # Build action list using list comprehension for better performance
    return [_build_action_dict(device_id, action_type) for action_type in action_types]


def _validate_action_config(config: ConfigType) -> tuple[str, str]:
    """Validate action configuration and return type and device_id.

    Args:
        config: Action configuration dictionary

    Returns:
        Tuple of (action_type, device_id)

    Raises:
        ValueError: If required fields are missing (logged but not raised)
    """
    if CONF_TYPE not in config:
        _LOGGER.error("Action config missing type field")
        raise ValueError("Action configuration missing type field")

    if CONF_DEVICE_ID not in config:
        _LOGGER.error("Action config missing device ID field")
        raise ValueError("Action configuration missing device_id field")

    return config[CONF_TYPE], config[CONF_DEVICE_ID]


def _build_service_data(
    device_id: str,
    action_type: str,
    config: ConfigType,
) -> dict[str, Any]:
    """Build service data dictionary.

    Args:
        device_id: Device identifier
        action_type: Action type identifier
        config: Original action configuration

    Returns:
        Service data dictionary with device_id and optional parameters
    """
    service_data: dict[str, Any] = {"device_id": device_id}

    return service_data


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context | None,
) -> None:
    """Execute a device action.

    Args:
        hass: Home Assistant instance
        config: Action configuration dictionary
        variables: Template variables (unused but required by interface)
        context: Optional context for the service call
    """
    _LOGGER.debug("Executing device action, config: %s", config)

    try:
        # Validate and extract config
        action_type, device_id = _validate_action_config(config)
    except ValueError:
        # Error already logged in _validate_action_config
        return

    # Find appropriate device
    matcher = DeviceMatcher(hass)
    device = matcher.find_device_for_action(device_id, action_type)

    if device is None:
        _LOGGER.error("No available devices found for action: %s", action_type)
        return

    # Validate device belongs to integration
    if not matcher.validate_device_for_integration(device):
        _LOGGER.error(
            "Device does not belong to %s integration: %s",
            DOMAIN,
            device.id,
        )
        return

    # Map action type to service name
    service = ACTION_SERVICE_MAP.get(action_type)
    if service is None:
        _LOGGER.error("Unsupported action type: %s", action_type)
        return

    # Build service data
    service_data = _build_service_data(device.id, action_type, config)

    _LOGGER.debug("Calling service %s.%s, data: %s", DOMAIN, service, service_data)

    # Call service
    await hass.services.async_call(
        DOMAIN,
        service,
        service_data,
        blocking=True,
        context=context,
    )

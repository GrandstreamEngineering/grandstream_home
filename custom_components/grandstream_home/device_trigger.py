"""Provides device automations for Grandstream Home."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .automation_patterns import (
    GDS_TRIGGER_TYPES,
    GNS_TRIGGER_TYPES,
    TRIGGER_TYPES,
    AutomationConditionChecker,
    AutomationTypeClassifier,
    EntityMatcher,
    IndexCalculator,
)
from .const import DEVICE_TYPE_GDS, DEVICE_TYPE_GNS_NAS, DOMAIN
from .utils import DeviceMatcher, DeviceTypeResolver

_LOGGER = logging.getLogger(__name__)

# Additional configuration constants
CONF_THRESHOLD = "threshold"
CONF_INDEX = "index"


# Trigger schema
TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(CONF_THRESHOLD): vol.Coerce(float),
        vol.Optional(CONF_INDEX): vol.Coerce(int),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """Return list of device triggers.

    Args:
        hass: Home Assistant instance
        device_id: Device identifier

    Returns:
        List of trigger configurations for GDS and GNS devices, empty list otherwise

    """
    resolver = DeviceTypeResolver(hass)

    # Only GDS and GNS devices support triggers
    if not (resolver.is_gds_device(device_id) or resolver.is_gns_device(device_id)):
        _LOGGER.debug(
            "Skipping trigger setup for non-supported device: %s",
            device_id,
        )
        return []

    # Determine which triggers to include based on device type
    if resolver.is_gds_device(device_id):
        valid_triggers = GDS_TRIGGER_TYPES
        device_type = DEVICE_TYPE_GDS
    else:
        valid_triggers = GNS_TRIGGER_TYPES
        device_type = DEVICE_TYPE_GNS_NAS

    _LOGGER.debug(
        "Creating triggers for %s device: %s (valid triggers: %d)",
        device_type,
        device_id,
        len(valid_triggers),
    )

    # Build trigger list using list comprehension for better performance
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in valid_triggers
    ]


def _validate_trigger_config(config: ConfigType) -> tuple[str, str]:
    """Validate trigger configuration and return type and device_id.

    Args:
        config: Trigger configuration dictionary

    Returns:
        Tuple of (trigger_type, device_id)

    Raises:
        ValueError: If required fields are missing

    """
    if CONF_TYPE not in config:
        _LOGGER.error("Trigger config missing type field")
        raise ValueError("Trigger configuration missing type field")

    if CONF_DEVICE_ID not in config:
        _LOGGER.error("Trigger config missing device ID field")
        raise ValueError("Trigger configuration missing device_id field")

    return config[CONF_TYPE], config[CONF_DEVICE_ID]


def _build_trigger_data(
    device_id: str,
    trigger_type: str,
    entity_id: str,
    state_value: str,
    threshold: float | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """Build trigger data dictionary from sensor state.

    Args:
        device_id: Device identifier
        trigger_type: Trigger type identifier
        entity_id: Sensor entity ID
        state_value: Current sensor state value
        threshold: Optional threshold value for numeric triggers
        index: Optional index value for multi-component triggers

    Returns:
        Formatted trigger data dictionary

    """
    trigger_data: dict[str, Any] = {
        "trigger": {
            "platform": "device",
            "domain": DOMAIN,
            "device_id": device_id,
            "entity_id": entity_id,
            "type": trigger_type,
            "description": TRIGGER_TYPES[trigger_type],
            "value": state_value,
        }
    }

    # Add threshold and index if provided
    if threshold is not None:
        trigger_data["trigger"][CONF_THRESHOLD] = threshold
    if index is not None:
        trigger_data["trigger"][CONF_INDEX] = index

    return trigger_data


async def _attach_gds_event_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    device_id: str,
    trigger_type: str,
) -> CALLBACK_TYPE:
    """Attach GDS event-based trigger."""
    _LOGGER.debug("Attaching GDS event trigger: %s", trigger_type)

    # Check if this is a door access trigger
    is_door_access = AutomationTypeClassifier.is_door_access_trigger(trigger_type)

    # Initialize variables
    access_type = None
    event_code = None

    if is_door_access:
        # Door access triggers use type="access" and accessType
        access_type = AutomationTypeClassifier.get_door_access_type(trigger_type)
        _LOGGER.debug(
            "Door access trigger: %s, access_type: %s",
            trigger_type,
            access_type,
        )
    else:
        # Regular alarm triggers use alertType
        event_code = AutomationTypeClassifier.get_gds_event_code(trigger_type)
        _LOGGER.debug(
            "Alarm trigger: %s, event_code: %s",
            trigger_type,
            event_code,
        )

    async def handle_gds_alarm_event(event: Event) -> None:
        """Handle GDS alarm events."""
        try:
            # Validate event data
            if not event.data:
                _LOGGER.warning("Received empty event data: %s", event)
                return

            # Get event type (new parameter name)
            event_type = event.data.get("type")
            if not event_type:
                _LOGGER.warning("Missing type in event data: %s", event.data)
                return

            # Handle door access events
            if is_door_access:
                # Check if this is an access event
                if event_type != "access":
                    return

                # Get access type with validation
                event_access_type = event.data.get("accessType")

                # Validate accessType field exists and is valid
                if event_access_type is None:
                    _LOGGER.warning(
                        "Door access event missing 'accessType' field in event data: %s",
                        event.data,
                    )
                    # Fallback: treat as "unknown" access type but still allow "any" trigger to match
                    if access_type is not None:
                        # Specific access type trigger requires valid accessType
                        _LOGGER.debug(
                            "Skipping specific access type trigger %s due to missing accessType",
                            trigger_type,
                        )
                        return
                    # For "any" trigger, continue with unknown access type
                    event_access_type = "unknown"

                # Validate accessType is a string or can be converted to string
                try:
                    event_access_type = str(event_access_type)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "Invalid accessType value in event data: %s (error: %s)",
                        event_access_type,
                        e,
                    )
                    # Fallback: treat as unknown if conversion fails
                    if access_type is not None:
                        return
                    event_access_type = "unknown"

                # For "any" trigger, match all access types
                if access_type is not None:
                    # Match specific access type
                    if event_access_type != access_type:
                        _LOGGER.debug(
                            "Access type mismatch: expected %s, got %s",
                            access_type,
                            event_access_type,
                        )
                        return
                # else: access_type is None means "any", so don't filter

                _LOGGER.debug(
                    "Door access event matched for trigger %s: accessType=%s",
                    trigger_type,
                    event_access_type,
                )
            else:
                # Handle regular alarm events
                # Skip if this is an access event (should be handled by door access triggers)
                if event_type == "access":
                    return

                # Get alert type (new parameter name)
                alert_type = event.data.get("alertType")
                if not alert_type:
                    _LOGGER.warning("Missing alertType in event data: %s", event.data)
                    return

                # Filter by alert type
                if alert_type != event_code:
                    return

                _LOGGER.debug(
                    "Alarm event matched for trigger %s: alertType=%s",
                    trigger_type,
                    alert_type,
                )

            # Build trigger data
            trigger_data = {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": trigger_type,
                    "description": TRIGGER_TYPES[trigger_type],
                    "timestamp": event.data.get("timestamp"),
                    "datetime": event.data.get("datetime"),
                    "info": event.data.get("info"),
                    "event_type": event_type,
                }
            }

            # Add door access specific fields
            if is_door_access:
                trigger_data["trigger"]["access_type"] = event.data.get("accessType")
            else:
                trigger_data["trigger"]["alert_type"] = event.data.get("alertType")

            # Add optional fields if present
            for field in ("sound_type", "temperature"):
                if field in event.data:
                    trigger_data["trigger"][field] = event.data[field]

            _LOGGER.debug(
                "Executing GDS trigger action with data: %s", trigger_data
            )

            # Execute action
            context = Context()
            result = action(trigger_data, context=context)

            # Check if result is a coroutine and create task if so
            if result is not None:
                hass.async_create_task(result)

            _LOGGER.debug("GDS trigger action submitted: %s", trigger_type)

        except (ValueError, KeyError, RuntimeError) as e:
            _LOGGER.exception(
                "Error executing GDS trigger action for type %s",
                trigger_type,
                exc_info=e,
            )

    if is_door_access:
        _LOGGER.info(
            "Registered door access trigger: %s -> access_type %s",
            trigger_type,
            access_type if access_type else "any",
        )
    else:
        _LOGGER.info(
            "Registered GDS alarm trigger: %s -> alert_type %s",
            trigger_type,
            event_code,
        )

    # Listen for grandstream_alarm events
    return hass.bus.async_listen("grandstream_alarm", handle_gds_alarm_event)


async def _attach_sensor_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    device_id: str,
    trigger_type: str,
) -> CALLBACK_TYPE:
    """Attach sensor-based trigger."""
    threshold = config.get(CONF_THRESHOLD)
    index = config.get(CONF_INDEX)

    # Get entity registry
    registry = er.async_get(hass)

    # Find matching entity for this trigger
    entity_matcher = EntityMatcher(registry)
    entity_id = entity_matcher.find_matching_entity(device_id, trigger_type, index)

    if not entity_id:
        _LOGGER.error(
            "No matching entity found for trigger type %s on device %s",
            trigger_type,
            device_id,
        )
        raise ValueError(f"No matching entity found for trigger type {trigger_type}")

    # Handle sensor-based triggers
    _LOGGER.debug("Attaching sensor-based trigger: %s -> %s", trigger_type, entity_id)

    async def check_and_trigger(state_value: str | None = None) -> None:
        """Check if conditions are met and trigger if so."""
        try:
            # If no state value provided, get current state
            if state_value is None:
                state_obj = hass.states.get(entity_id)
                if not state_obj:
                    _LOGGER.debug("Entity not found: %s", entity_id)
                    return
                state_value = state_obj.state

            # Check if trigger conditions are met
            if not AutomationConditionChecker.should_trigger_fire(
                state_value, trigger_type, threshold
            ):
                _LOGGER.debug(
                    "Trigger conditions not met: type=%s, value=%s, threshold=%s, index=%s",
                    trigger_type,
                    state_value,
                    threshold,
                    index,
                )
                return

            # Build trigger data
            trigger_data = _build_trigger_data(
                device_id, trigger_type, entity_id, state_value, threshold, index
            )

            _LOGGER.debug("Executing trigger action with data: %s", trigger_data)

            # Execute action
            context = Context()
            result = action(trigger_data, context=context)

            # Check if result is a coroutine and create task if so
            if result is not None:
                hass.async_create_task(result)

            _LOGGER.debug("Trigger action submitted: %s", trigger_type)

        except (ValueError, KeyError, RuntimeError) as e:
            _LOGGER.exception(
                "Error executing trigger action for type %s", trigger_type, exc_info=e
            )

    async def handle_state_change(event: Event) -> None:
        """Handle sensor state change event."""
        try:
            # Get the new state
            new_state = event.data.get("new_state")
            if not new_state:
                return

            # Check if this is the correct entity
            if new_state.entity_id != entity_id:
                return

            # Check and trigger with new state value
            await check_and_trigger(new_state.state)

        except (ValueError, KeyError, RuntimeError) as e:
            _LOGGER.exception(
                "Error handling state change for type %s", trigger_type, exc_info=e
            )

    # Check current state on trigger registration
    hass.async_create_task(check_and_trigger())

    _LOGGER.info("Registered sensor-based trigger: %s -> %s", trigger_type, entity_id)

    # Listen for state changes on the specific entity
    return hass.bus.async_listen("state_changed", handle_state_change)


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    _trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach device trigger based on sensor state changes or GDS events.

    Args:
        hass: Home Assistant instance
        config: Trigger configuration
        action: Action to execute when triggered
        _trigger_info: Additional trigger information

    Returns:
        Callback function to remove the trigger listener

    Raises:
        ValueError: If configuration is invalid or device not found

    """
    _LOGGER.debug("Attaching trigger, config: %s", config)

    # Validate action
    if not action or not callable(action):
        _LOGGER.error("Trigger action function is invalid")
        raise ValueError("Action must be a valid callable")

    # Validate and extract config
    trigger_type, device_id = _validate_trigger_config(config)

    # Find device
    matcher = DeviceMatcher(hass)
    device = matcher.get_device_by_id(device_id)

    if device is None:
        _LOGGER.error("Device not found: %s", device_id)
        raise ValueError(f"Device not found: {device_id}")

    # Check if this is a GDS event-based trigger
    if AutomationTypeClassifier.is_gds_event_trigger(trigger_type):
        return await _attach_gds_event_trigger(
            hass, config, action, device_id, trigger_type
        )

    return await _attach_sensor_trigger(
        hass, config, action, device_id, trigger_type
    )


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """List trigger capabilities for configuration UI.

    Args:
        hass: Home Assistant instance
        config: Trigger configuration

    Returns:
        Dictionary with extra fields schema for UI configuration

    """
    trigger_type = config[CONF_TYPE]
    device_id = config.get(CONF_DEVICE_ID)

    # Check if this is a GDS event-based trigger
    if AutomationTypeClassifier.is_gds_event_trigger(trigger_type):
        # GDS event triggers don't require additional configuration
        return {}

    # Get entity registry for dynamic index calculation
    registry = er.async_get(hass)
    index_calculator = IndexCalculator(registry)

    # Calculate actual entity count for this device and trigger type
    max_index = (
        index_calculator.get_max_index_for_device(device_id, trigger_type)
        if device_id
        else 1
    )

    _LOGGER.debug(
        "Using max_index=%d for device %s trigger %s",
        max_index,
        device_id,
        trigger_type,
    )

    # Numeric threshold triggers (CPU, Memory, Pool usage)
    if AutomationTypeClassifier.is_threshold_trigger(trigger_type):
        # CPU/Memory usage - only threshold (no index needed)
        if trigger_type in ["cpu_usage_above", "memory_usage_above"]:
            return {
                "extra_fields": vol.Schema(
                    {
                        vol.Required(CONF_THRESHOLD, default=80): vol.All(
                            vol.Coerce(float), vol.Range(min=0, max=100)
                        )
                    }
                )
            }

        # Temperature thresholds
        if trigger_type in ["system_temperature_above", "cpu_temperature_above"]:
            return {
                "extra_fields": vol.Schema(
                    {
                        vol.Required(CONF_THRESHOLD, default=60): vol.All(
                            vol.Coerce(float), vol.Range(min=0, max=100)
                        )
                    }
                )
            }

        # Indexed threshold triggers (disk temperature, pool usage)
        if trigger_type in ["disk_temperature_above", "pool_usage_above"]:
            schema_fields = {
                vol.Required(
                    CONF_THRESHOLD,
                    default=60 if trigger_type == "disk_temperature_above" else 80,
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100))
            }

            # Add index field only if multiple entities exist
            if max_index > 1:
                schema_fields[vol.Required(CONF_INDEX, default=1)] = vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=max_index)
                )

            return {"extra_fields": vol.Schema(schema_fields)}

    # Status-based triggers that need index only when multiple entities exist
    if trigger_type in ["fan_abnormal", "disk_abnormal", "pool_abnormal"]:
        if max_index > 1:
            # Show index selector as required field when multiple entities exist
            return {
                "extra_fields": vol.Schema(
                    {
                        vol.Required(CONF_INDEX, default=1): vol.All(
                            vol.Coerce(int), vol.Range(min=1, max=max_index)
                        )
                    }
                )
            }

    return {}

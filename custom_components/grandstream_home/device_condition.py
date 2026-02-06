"""Provides device conditions for Grandstream Home."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import (
    CONF_CONDITION,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    condition,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.typing import ConfigType

from .automation_patterns import (
    CONDITION_TYPES,
    AutomationConditionChecker,
    AutomationTypeClassifier,
    EntityMatcher,
    IndexCalculator,
    PatternMatcher,
)
from .const import DEVICE_TYPE_GDS, DEVICE_TYPE_GNS_NAS, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_ABOVE = "above"
CONF_INDEX = "index"

# Condition schema
CONDITION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CONDITION): "device",
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_DOMAIN): vol.Equal(DOMAIN),
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_TYPE): vol.In(CONDITION_TYPES),
        vol.Optional(CONF_ABOVE): vol.Coerce(float),
        vol.Optional(CONF_INDEX): vol.Coerce(int),
    }
)


class DeviceConditionProvider:
    """Provider for device conditions based on device type."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize device condition provider.

        Args:
            hass: Home Assistant instance.

        """
        self.hass = hass
        self.registry = er.async_get(hass)

    async def get_conditions(self, device_id: str) -> list[dict[str, Any]]:
        """List device conditions for Grandstream devices."""
        device_registry = dr.async_get(self.hass)
        conditions: list[dict[str, Any]] = []

        # Get all entities for this device
        entities = er.async_entries_for_device(self.registry, device_id)

        # Determine device type from config entry data
        device_type = None
        device = device_registry.async_get(device_id)

        if device and hasattr(device, "config_entries"):
            domain_data = self.hass.data.get(DOMAIN, {})
            for entry_id in device.config_entries:
                entry_data = domain_data.get(entry_id)
                if isinstance(entry_data, dict) and "device_type" in entry_data:
                    device_type = entry_data.get("device_type")
                    break

        _LOGGER.debug(
            "Getting conditions for device: ID=%s, type=%s (from hass.data), entities=%d",
            device_id,
            device_type,
            len(entities),
        )

        # Add conditions based on device type
        if device_type == DEVICE_TYPE_GNS_NAS:
            conditions.extend(self._get_nas_conditions(device_id, entities))
        elif device_type == DEVICE_TYPE_GDS:
            conditions.extend(self._get_gds_conditions(device_id, entities))
        else:
            # Fallback: determine from entities
            is_nas_device = False
            is_gds_device = False

            for entry in entities:
                if entry.domain == "sensor":
                    if (
                        "cpu_usage" in entry.unique_id
                        or "memory_usage" in entry.unique_id
                    ):
                        is_nas_device = True
                    elif "phone_status" in entry.unique_id:
                        is_gds_device = True

            if is_nas_device:
                conditions.extend(self._get_nas_conditions(device_id, entities))

            if is_gds_device:
                conditions.extend(self._get_gds_conditions(device_id, entities))

        return conditions

    def _get_nas_conditions(
        self, device_id: str, entities: list[er.RegistryEntry]
    ) -> list[dict[str, Any]]:
        """Get conditions for GNS NAS devices."""
        conditions: list[dict[str, Any]] = []

        # Find representative entities for each condition type
        representative_entities = PatternMatcher.find_representative_entities(entities)

        # Create conditions using representative entities
        condition_configs = {
            "cpu_usage_above": {"above": "80"},
            "memory_usage_above": {"above": "80"},
            "cpu_temperature_above": {"above": "70"},
            "system_temperature_above": {"above": "60"},
            "disk_temperature_above": {"above": "50"},
            "pool_usage_above": {"above": "90"},
            "fan_abnormal": {},
            "disk_abnormal": {},
            "pool_abnormal": {},
        }

        for condition_type, config in condition_configs.items():
            entity = representative_entities.get(condition_type)
            if entity:
                condition_dict = {
                    "condition": "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_ENTITY_ID: entity.entity_id,
                    CONF_TYPE: condition_type,
                }
                condition_dict.update(config)
                conditions.append(condition_dict)

        return conditions

    def _get_gds_conditions(
        self, device_id: str, entities: list[er.RegistryEntry]
    ) -> list[dict[str, Any]]:
        """Get conditions for GDS devices."""
        conditions: list[dict[str, Any]] = []

        # Find representative entities for each condition type
        representative_entities = PatternMatcher.find_representative_entities(entities)

        # Create conditions using representative entities
        condition_configs: dict[str, Any] = {"phone_status_is": {}}

        for condition_type, config in condition_configs.items():
            entity = representative_entities.get(condition_type)
            if entity:
                condition_dict = {
                    "condition": "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_ENTITY_ID: entity.entity_id,
                    CONF_TYPE: condition_type,
                }
                condition_dict.update(config)
                conditions.append(condition_dict)

        return conditions


async def async_get_conditions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device conditions for Grandstream devices."""
    provider = DeviceConditionProvider(hass)
    return await provider.get_conditions(device_id)


@callback
def async_condition_from_config(
    hass: HomeAssistant,  # pylint: disable=unused-argument
    config: ConfigType,
) -> condition.ConditionCheckerType:
    """Create a function to test a device condition."""
    condition_type = config[CONF_TYPE]
    initial_entity_id = config[CONF_ENTITY_ID]
    above_value = config.get(CONF_ABOVE)
    index = config.get(CONF_INDEX)
    device_id = config.get(CONF_DEVICE_ID)

    @callback
    def test_condition(
        hass: HomeAssistant, variables: Mapping[str, Any] | None
    ) -> bool:  # pylint: disable=unused-argument
        """Test if condition is met."""
        # Use the initial entity_id as default
        entity_id = initial_entity_id

        # If index is provided, find the correct entity using EntityMatcher
        if index is not None and device_id:
            registry = er.async_get(hass)
            entity_matcher = EntityMatcher(registry)
            actual_entity_id = entity_matcher.find_matching_entity(
                device_id, condition_type, index
            )

            if actual_entity_id:
                entity_id = actual_entity_id
                _LOGGER.debug(
                    "Found entity %s for condition_type=%s with index=%s",
                    actual_entity_id,
                    condition_type,
                    index,
                )
            else:
                _LOGGER.warning(
                    "Could not find entity for condition_type=%s with index=%s, using default entity_id=%s",
                    condition_type,
                    index,
                    entity_id,
                )

        state = hass.states.get(entity_id)

        if state is None:
            _LOGGER.warning("Entity %s not found for condition check", entity_id)
            return False

        _LOGGER.debug(
            "Checking condition: entity_id=%s, condition_type=%s, state_value=%s, above_value=%s, index=%s",
            entity_id,
            condition_type,
            state.state,
            above_value,
            index,
        )

        return AutomationConditionChecker.check_condition(
            state.state, condition_type, above_value, index
        )

    return test_condition


async def async_get_condition_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """List condition capabilities."""
    condition_type = config[CONF_TYPE]
    device_id = config.get(CONF_DEVICE_ID)

    # Get entity registry for dynamic index calculation
    registry = er.async_get(hass)
    index_calculator = IndexCalculator(registry)

    # Calculate actual entity count for this device and condition type
    max_index = (
        index_calculator.get_max_index_for_device(device_id, condition_type)
        if device_id
        else 1
    )

    _LOGGER.debug(
        "Using max_index=%d for device %s condition %s",
        max_index,
        device_id,
        condition_type,
    )

    # Numeric threshold conditions (CPU, Memory, Pool usage)
    if AutomationTypeClassifier.is_threshold_trigger(condition_type):
        # CPU/Memory usage - only threshold (no index needed)
        if condition_type in ["cpu_usage_above", "memory_usage_above"]:
            return {
                "extra_fields": vol.Schema(
                    {
                        vol.Required(CONF_ABOVE, default=80): vol.All(
                            vol.Coerce(float), vol.Range(min=0, max=100)
                        )
                    }
                )
            }

        # Temperature thresholds
        if condition_type in ["system_temperature_above", "cpu_temperature_above"]:
            return {
                "extra_fields": vol.Schema(
                    {
                        vol.Required(
                            CONF_ABOVE,
                            default=(
                                60
                                if condition_type == "system_temperature_above"
                                else 70
                            ),
                        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100))
                    }
                )
            }

        # Indexed threshold triggers (disk temperature, pool usage)
        if condition_type in ["disk_temperature_above", "pool_usage_above"]:
            schema_fields = {
                vol.Required(
                    CONF_ABOVE,
                    default=50 if condition_type == "disk_temperature_above" else 90,
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100))
            }

            # Add index field only if multiple entities exist
            if max_index > 1:
                schema_fields[vol.Required(CONF_INDEX, default=1)] = vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=max_index)
                )

            return {"extra_fields": vol.Schema(schema_fields)}

    # Status-based conditions that need index only when multiple entities exist
    if condition_type in ["fan_abnormal", "disk_abnormal", "pool_abnormal"]:
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

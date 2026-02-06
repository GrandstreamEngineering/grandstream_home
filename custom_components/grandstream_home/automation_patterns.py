"""Shared patterns and utilities for device triggers and conditions."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

# GNS NAS device types
GNS_TRIGGER_TYPES = {
    "cpu_usage_above": "CPU Usage Exceeds Threshold",
    "memory_usage_above": "Memory Usage Exceeds Threshold",
    "system_temperature_above": "System Temperature Exceeds Threshold",
    "cpu_temperature_above": "CPU Temperature Exceeds Threshold",
    "disk_temperature_above": "Disk Temperature Exceeds Threshold",
    "pool_usage_above": "Storage Pool Usage Exceeds Threshold",
    "fan_abnormal": "Fan Status Abnormal",
    "disk_abnormal": "Disk Status Abnormal",
    "pool_abnormal": "Storage Pool Status Abnormal",
}

GNS_CONDITION_TYPES = GNS_TRIGGER_TYPES

# GDS device types
GDS_TRIGGER_TYPES = {
    "personnel_intrusion": "Intrusion/Loitering Alarm",
    "hostage": "Duress Alarm",
    "tamper": "Tamper Alarm",
    "keypad_error": "Remote Unlock Wrong Password Alarm",
    "non_scheduled_access": "Exceeded Access Time Limit Alarm",
    "unauthorized_rfid": "Unauthorized RFID Card/QR Code Access Alarm",
    "abnormal_sound": "Abnormal Sound Alarm",
    "high_temperature": "High Temperature Alarm",
    "di_1": "Digital Input Alarm 1",
    "di_2": "Digital Input Alarm 2",
    "door_opened_any": "Door Opened (Any Method)",
    "door_opened_rfid": "Door Opened by RFID Card",
    "door_opened_sip": "Door Opened by SIP Call",
    "door_opened_common_password": "Door Opened by Common Password",
    "door_opened_personal_password": "Door Opened by Personal Password",
    "door_opened_card_password": "Door Opened by Card and Password",
    "door_opened_temp_password": "Door Opened by Temporary Password",
    "door_opened_forced": "Door Opened by Force",
    "door_opened_remote": "Door Opened Remotely",
    "door_opened_http": "Door Opened by HTTP",
    "door_opened_qrcode": "Door Opened by QR Code",
    "door_opened_ble": "Door Opened by Bluetooth",
    "door_opened_nfc": "Door Opened by NFC",
    "door_opened_pin": "Door Opened by PIN Code",
    "door_opened_guest_qrcode": "Door Opened by Guest QR Code",
    "door_opened_touch_pass": "Door Opened by Touch Pass",
    "phone_busy": "Phone Status Busy",
    "phone_ringing": "Phone Status Ringing",
}

GDS_CONDITION_TYPES: dict[str, str] = {}

# Combined types for backward compatibility
TRIGGER_TYPES = {**GNS_TRIGGER_TYPES, **GDS_TRIGGER_TYPES}
CONDITION_TYPES = {**GNS_CONDITION_TYPES, **GDS_CONDITION_TYPES}

# GDS event code mapping
GDS_EVENT_MAPPING = {
    "personnel_intrusion": "0",
    "hostage": "1",
    "tamper": "2",
    "keypad_error": "3",
    "non_scheduled_access": "4",
    "unauthorized_rfid": "5",
    "abnormal_sound": "6",
    "high_temperature": "7",
    "di_1": "8",
    "di_2": "9",
    "di_3": "10",
}

# Door access type mapping (for door_opened event with type="access")
DOOR_ACCESS_TYPE_MAPPING = {
    "door_opened_any": None,  # Match any access type
    "door_opened_rfid": "0",
    "door_opened_sip": "1",
    "door_opened_common_password": "2",
    "door_opened_personal_password": "3",
    "door_opened_card_password": "4",
    "door_opened_temp_password": "5",
    "door_opened_forced": "6",
    "door_opened_remote": "7",
    "door_opened_http": "8",
    "door_opened_qrcode": "9",
    "door_opened_ble": "10",
    "door_opened_nfc": "11",
    "door_opened_pin": "12",
    "door_opened_guest_qrcode": "13",
    "door_opened_touch_pass": "14",
}

# All door access triggers (for easy checking)
DOOR_ACCESS_TRIGGERS = set(DOOR_ACCESS_TYPE_MAPPING.keys())

# Event-based GDS triggers
GDS_EVENT_TRIGGERS = set(GDS_EVENT_MAPPING.keys())

# Numeric threshold types
THRESHOLD_TYPES = {
    "cpu_usage_above",
    "memory_usage_above",
    "disk_temperature_above",
    "system_temperature_above",
    "cpu_temperature_above",
    "pool_usage_above",
}

# Status-based types
STATUS_TRIGGER_TYPES = {"fan_abnormal", "disk_abnormal", "pool_abnormal"}

STATUS_CONDITION_TYPES = STATUS_TRIGGER_TYPES

# Default values for different types
DEFAULT_THRESHOLDS = {
    "cpu_usage_above": 80,
    "memory_usage_above": 80,
    "system_temperature_above": 60,
    "cpu_temperature_above": 70,
    "disk_temperature_above": 50,
    "pool_usage_above": 90,
}

DEFAULT_CONDITION_THRESHOLDS = DEFAULT_THRESHOLDS


class PatternMatcher:
    """Pattern matcher for entity IDs and unique IDs."""

    # Unique ID patterns for entity matching
    UNIQUE_ID_PATTERNS = {
        "disk_abnormal": ["disk", "status"],
        "pool_abnormal": ["pool", "status"],
        "fan_abnormal": ["fan", "status"],
        "disk_temperature_above": ["disk", "temperature"],
        "pool_usage_above": ["pool", "usage"],
        "cpu_usage_above": ["cpu", "usage", "percent"],
        "memory_usage_above": ["memory", "usage", "percent"],
        "system_temperature_above": ["system", "temperature"],
        "cpu_temperature_above": ["cpu", "temperature"],
        "phone_busy": ["phone", "status"],
        "phone_ringing": ["phone", "status"],
    }

    # Entity ID regex patterns
    REGEX_PATTERNS = {
        "cpu_usage_percent": [
            "^sensor\\.[^.]*_cpu_usage_percent$",
            "^sensor\\.[^.]*cpu.*usage.*percent$",
            "^sensor.*cpu_usage_percent$",
            "^.*cpu_usage_percent$",
        ],
        "memory_usage_percent": [
            "^sensor\\.[^.]*_memory_usage_percent$",
            "^sensor\\.[^.]*memory.*usage.*percent$",
            "^sensor.*memory_usage_percent$",
            "^.*memory_usage_percent$",
        ],
        "system_temperature_c": ["^sensor\\.[^.]*_system_temperature_c$"],
        "cpu_temperature_c": ["^sensor\\.[^.]*_cpu_temperature_c$"],
        "device_status": ["^sensor\\.[^.]*_device_status$"],
        "phone_status": ["^sensor\\.[^.]*_phone_status$"],
        "disk_status": ["^sensor\\.[^.]*_disk_\\d+_status$"],
        "fan_status": [
            "^sensor\\.[^.]*_fan_\\d+_status_\\d+$",
            "^sensor\\.[^.]*_fan_\\d+_status$",
        ],
        "pool_status": ["^sensor\\.[^.]*_pool_\\d+_status$"],
        "disk_temperature": ["^sensor\\.[^.]*_disk_\\d+_temperature$"],
        "pool_usage": ["^sensor\\.[^.]*_pool_\\d+_usage$"],
    }

    # Index extraction patterns
    INDEX_PATTERNS = [
        r"fan_(\d+)_status",  # fan_1_status
        r"disk_(\d+)_status",  # disk_1_status
        r"pool_(\d+)_status",  # pool_1_status
        r"disk_(\d+)_temperature",  # disk_1_temperature
        r"pool_(\d+)_usage",  # pool_1_usage
        r"fan_(\d+)_status_(\d+)",  # fan_1_status_2
        r"disk_(\d+)_status_(\d+)",  # disk_1_status_2
        r"pool_(\d+)_status_(\d+)",  # pool_1_status_2
        r"_(\d+)",  # _1, _2, _3
        r"disk_(\d+)",  # disk_1, disk_2
        r"pool_(\d+)",  # pool_1, pool_2
        r"fan_(\d+)",  # fan_1, fan_2
    ]

    @classmethod
    def extract_index(cls, text: str) -> int | None:
        """Extract index from text using multiple patterns."""
        for pattern in cls.INDEX_PATTERNS:
            match = re.search(pattern, text)
            if match:
                try:
                    # For patterns with two capture groups, use the first one
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue
        return None

    @classmethod
    def matches_unique_id_patterns(cls, entity_id: str, trigger_type: str) -> bool:
        """Check if entity's unique_id matches trigger patterns."""
        patterns = cls.UNIQUE_ID_PATTERNS.get(trigger_type, [])
        if not patterns:
            return False
        return all(pattern in entity_id for pattern in patterns)

    @classmethod
    def matches_regex_patterns(cls, entity_id: str, trigger_key: str) -> bool:
        """Check if entity_id matches any regex pattern for trigger key."""
        patterns = cls.REGEX_PATTERNS.get(trigger_key, [])
        return any(re.match(pattern, entity_id) for pattern in patterns)

    @staticmethod
    def _extract_index_from_unique_id(unique_id: str, pattern_type: str) -> int | None:
        """Extract index from unique_id based on pattern type.

        Args:
            unique_id: The unique ID string to extract index from
            pattern_type: Type of pattern (fan_status, disk_status, pool_status, etc.)

        Returns:
            Extracted index or None if extraction fails

        """
        try:
            # Special handling for fan_status which can have "_status_" format
            if pattern_type == "fan_status" and "_status_" in unique_id:
                return int(unique_id.split("_status_")[-1])

            # For patterns like disk_temperature, pool_usage, fan_status, disk_status, pool_status
            # The unique_id format is like "disk_1_temperature", "pool_1_usage", etc.
            # Extract number between underscores
            match = re.search(r"_(\d+)_", unique_id)
            if match:
                return int(match.group(1))

            # Fallback: extract last number after underscore
            return int(unique_id.split("_")[-1])
        except (ValueError, IndexError):
            return None

    @classmethod
    def _add_indexed_entity(
        cls,
        entry: er.RegistryEntry,
        entity_dict: dict[int, er.RegistryEntry],
        pattern_type: str,
    ) -> None:
        """Add an indexed entity to the dictionary if index extraction succeeds.

        Args:
            entry: Entity registry entry to add
            entity_dict: Dictionary to add the entity to
            pattern_type: Type of pattern for index extraction

        """
        index = cls._extract_index_from_unique_id(entry.unique_id, pattern_type)
        if index is not None and index not in entity_dict:
            entity_dict[index] = entry
            if pattern_type == "fan_status":
                _LOGGER.debug(
                    "Added fan status entity: %s with index %d (unique_id: %s)",
                    entry.entity_id,
                    index,
                    entry.unique_id,
                )

    @classmethod
    def find_representative_entities(
        cls, entities: list[er.RegistryEntry]
    ) -> dict[str, er.RegistryEntry]:
        """Find representative entities for each condition/trigger type."""
        result = {}
        _LOGGER.debug("Finding representative entities for %d entities", len(entities))

        # Single instance entities
        cpu_usage_entity = None
        memory_usage_entity = None
        cpu_temp_entity = None
        system_temp_entity = None
        phone_status_entity = None
        device_status_entity = None

        # Multi-index entities (use dictionaries)
        disk_temp_entities: dict[int, Any] = {}
        pool_usage_entities: dict[int, Any] = {}
        fan_status_entities: dict[int, Any] = {}
        disk_status_entities: dict[int, Any] = {}
        pool_status_entities: dict[int, Any] = {}

        # Sort entities to ensure consistent index ordering
        sorted_entities = sorted(entities, key=lambda e: e.entity_id)

        # Scan through all entities to collect representatives
        for entry in sorted_entities:
            if entry.domain != "sensor" or not entry.unique_id:
                continue

            # CPU usage condition - only add once
            if "cpu_usage_percent" in entry.unique_id and cpu_usage_entity is None:
                cpu_usage_entity = entry

            # Memory usage condition - only add once
            elif (
                "memory_usage_percent" in entry.unique_id
                and memory_usage_entity is None
            ):
                memory_usage_entity = entry

            # CPU temperature condition - only add once
            elif "cpu_temperature" in entry.unique_id and cpu_temp_entity is None:
                cpu_temp_entity = entry

            # System temperature condition - only add once
            elif "system_temperature" in entry.unique_id and system_temp_entity is None:
                system_temp_entity = entry

            # Phone status condition - only add once
            elif "phone_status" in entry.unique_id and phone_status_entity is None:
                phone_status_entity = entry

            # Device status condition - only add once
            elif "device_status" in entry.unique_id and device_status_entity is None:
                device_status_entity = entry

            # Disk temperature conditions - add for each disk
            elif "disk" in entry.unique_id and "temperature" in entry.unique_id:
                cls._add_indexed_entity(entry, disk_temp_entities, "disk_temperature")

            # Pool usage conditions - add for each pool
            elif "pool" in entry.unique_id and "usage" in entry.unique_id:
                cls._add_indexed_entity(entry, pool_usage_entities, "pool_usage")

            # Fan status conditions - add for each fan
            elif "fan" in entry.unique_id and "status" in entry.unique_id:
                cls._add_indexed_entity(entry, fan_status_entities, "fan_status")

            # Disk status conditions - add for each disk
            elif "disk" in entry.unique_id and "status" in entry.unique_id:
                cls._add_indexed_entity(entry, disk_status_entities, "disk_status")

            # Pool status conditions - add for each pool
            elif "pool" in entry.unique_id and "status" in entry.unique_id:
                cls._add_indexed_entity(entry, pool_status_entities, "pool_status")

        # Store single instance entities
        if cpu_usage_entity:
            result["cpu_usage_above"] = cpu_usage_entity
        if memory_usage_entity:
            result["memory_usage_above"] = memory_usage_entity
        if cpu_temp_entity:
            result["cpu_temperature_above"] = cpu_temp_entity
        if system_temp_entity:
            result["system_temperature_above"] = system_temp_entity
        if phone_status_entity:
            result["phone_busy"] = phone_status_entity
            result["phone_ringing"] = phone_status_entity

        # Store multi-index entities (use first one as representative)
        if disk_temp_entities:
            result["disk_temperature_above"] = disk_temp_entities[
                sorted(disk_temp_entities.keys())[0]
            ]
        if pool_usage_entities:
            result["pool_usage_above"] = pool_usage_entities[
                sorted(pool_usage_entities.keys())[0]
            ]
        if fan_status_entities:
            result["fan_abnormal"] = fan_status_entities[
                sorted(fan_status_entities.keys())[0]
            ]
        if disk_status_entities:
            result["disk_abnormal"] = disk_status_entities[
                sorted(disk_status_entities.keys())[0]
            ]
        if pool_status_entities:
            result["pool_abnormal"] = pool_status_entities[
                sorted(pool_status_entities.keys())[0]
            ]

        return result


class EntityMatcher:
    """Matcher for finding entities based on trigger/condition types."""

    def __init__(self, registry: er.EntityRegistry) -> None:
        """Initialize the index calculator.

        Args:
            registry: The entity registry to search in.

        """
        self.registry = registry

    def find_matching_entity(
        self, device_id: str, trigger_type: str, index: int | None = None
    ) -> str | None:
        """Find matching entity ID for trigger/condition type."""
        entities = er.async_entries_for_device(self.registry, device_id)

        _LOGGER.debug(
            "Looking for entity match for type: %s, device: %s, index: %s",
            trigger_type,
            device_id,
            index,
        )
        _LOGGER.debug("Device has %d entities", len(entities))

        # Log all entities for debugging
        for entity in entities:
            _LOGGER.debug(
                "  Entity: %s (domain: %s), unique_id: %s",
                entity.entity_id,
                entity.domain,
                entity.unique_id,
            )

        # Check if this is a GDS event-based trigger (no entity required)
        if trigger_type in GDS_EVENT_TRIGGERS:
            # For GDS event triggers, we don't need a specific entity
            _LOGGER.debug(
                "GDS event trigger %s does not require entity matching", trigger_type
            )
            return "sensor.grandstream_gds_events"

        # Try unique_id matching first (most reliable)
        entity_id = self._match_by_unique_id(entities, trigger_type, index)
        if entity_id:
            return entity_id

        # Try entity_id keyword matching
        entity_id = self._match_by_keyword(entities, trigger_type, index)
        if entity_id:
            return entity_id

        # Try regex pattern matching
        entity_id = self._match_by_regex(entities, trigger_type, index)
        if entity_id:
            return entity_id

        # Final fallback: substring matching
        entity_id = self._match_by_substring(entities, trigger_type, index)
        if entity_id:
            return entity_id

        _LOGGER.debug(
            "No matching entity found for type: %s, device: %s, index: %s",
            trigger_type,
            device_id,
            index,
        )
        return None

    def _match_by_unique_id(
        self,
        entities: list[er.RegistryEntry],
        trigger_type: str,
        index: int | None = None,
    ) -> str | None:
        """Match entities by unique_id patterns."""
        patterns = PatternMatcher.UNIQUE_ID_PATTERNS.get(trigger_type, [])
        if not patterns:
            return None

        _LOGGER.debug("Trying unique_id matching for type %s", trigger_type)

        for entity in entities:
            if entity.domain != "sensor" or not entity.unique_id:
                continue

            # Check if unique_id contains all patterns
            if PatternMatcher.matches_unique_id_patterns(
                entity.unique_id, trigger_type
            ):
                # For indexed triggers, check if index matches
                if index is not None:
                    entity_index = PatternMatcher.extract_index(entity.unique_id)
                    if entity_index is not None and entity_index == index:
                        _LOGGER.debug(
                            "Found matching entity via unique_id: %s (index=%d)",
                            entity.entity_id,
                            index,
                        )
                        return entity.entity_id
                else:
                    # For non-indexed triggers, return first match
                    _LOGGER.debug(
                        "Found matching entity via unique_id: %s", entity.entity_id
                    )
                    return entity.entity_id
        return None

    def _match_by_keyword(
        self,
        entities: list[er.RegistryEntry],
        trigger_type: str,
        index: int | None = None,
    ) -> str | None:
        """Match entities by keyword patterns in entity_id."""
        patterns = PatternMatcher.UNIQUE_ID_PATTERNS.get(trigger_type, [])
        if not patterns:
            return None

        _LOGGER.debug(
            "Trying keyword matching for type %s with keywords: %s",
            trigger_type,
            patterns,
        )

        for entity in entities:
            if entity.domain != "sensor":
                continue

            # Check if entity ID contains all keywords
            if all(keyword in entity.entity_id for keyword in patterns):
                # For indexed triggers, check if index matches
                if index is not None:
                    entity_index = PatternMatcher.extract_index(entity.entity_id)
                    if entity_index is not None and entity_index == index:
                        _LOGGER.debug(
                            "Found matching entity via keyword fallback: %s (index=%d)",
                            entity.entity_id,
                            index,
                        )
                        return entity.entity_id
                else:
                    # For non-indexed triggers, return first match
                    _LOGGER.debug(
                        "Found matching entity via keyword fallback: %s",
                        entity.entity_id,
                    )
                    return entity.entity_id
        return None

    def _match_by_regex(
        self,
        entities: list[er.RegistryEntry],
        trigger_type: str,
        index: int | None = None,
    ) -> str | None:
        """Match entities using regex patterns."""
        # Determine regex patterns key based on trigger type
        regex_key = None
        if "cpu_usage" in trigger_type:
            regex_key = "cpu_usage_percent"
        elif "memory_usage" in trigger_type:
            regex_key = "memory_usage_percent"
        elif "system_temperature" in trigger_type:
            regex_key = "system_temperature_c"
        elif "cpu_temperature" in trigger_type:
            regex_key = "cpu_temperature_c"
        elif "disk_temperature" in trigger_type:
            regex_key = "disk_temperature"
        elif "pool_usage" in trigger_type:
            regex_key = "pool_usage"
        elif "fan_status" in trigger_type or "fan_abnormal" in trigger_type:
            regex_key = "fan_status"
        elif "disk_status" in trigger_type or "disk_abnormal" in trigger_type:
            regex_key = "disk_status"
        elif "pool_status" in trigger_type or "pool_abnormal" in trigger_type:
            regex_key = "pool_status"

        if not regex_key:
            _LOGGER.debug("No regex key found for type: %s", trigger_type)
            return None

        patterns = PatternMatcher.REGEX_PATTERNS.get(regex_key, [])
        if not patterns:
            return None

        _LOGGER.debug(
            "Trying regex pattern matching for type %s with key %s",
            trigger_type,
            regex_key,
        )

        # Adjust patterns for indexed triggers
        if index is not None and regex_key in [
            "disk_status",
            "fan_status",
            "pool_status",
            "disk_temperature",
            "pool_usage",
        ]:
            # For indexed triggers, create a specific pattern
            specific_pattern = patterns[0].replace("\\d+", str(index))
            patterns = [specific_pattern]

        _LOGGER.debug("Trying regex pattern matching with %d patterns", len(patterns))

        for regex_pattern in patterns:
            _LOGGER.debug("Trying regex pattern: %s", regex_pattern)
            for entity in entities:
                if entity.domain != "sensor":
                    continue

                # Match entity ID pattern
                if re.match(regex_pattern, entity.entity_id):
                    _LOGGER.debug(
                        "Found matching entity via regex: %s (pattern: %s)",
                        entity.entity_id,
                        regex_pattern,
                    )
                    return entity.entity_id
        return None

    def _match_by_substring(
        self,
        entities: list[er.RegistryEntry],
        trigger_type: str,
        index: int | None = None,
    ) -> str | None:
        """Match entities using simple substring matching."""
        _LOGGER.debug("Trying substring matching as final fallback")

        for entity in entities:
            if entity.domain != "sensor":
                continue

            unique_id = entity.unique_id or ""
            entity_id = entity.entity_id

            found_match = False

            # CPU and memory usage (no index)
            if trigger_type in ["cpu_usage_above", "memory_usage_above"]:
                if (
                    ("cpu" in unique_id and "usage" in unique_id)
                    or ("memory" in unique_id and "usage" in unique_id)
                    or ("cpu_usage" in entity_id and "usage" in entity_id)
                    or ("memory_usage" in entity_id and "usage" in entity_id)
                ):
                    found_match = True

            # Temperature triggers
            elif trigger_type in ["cpu_temperature_above", "system_temperature_above"]:
                if (
                    "cpu_temperature" in unique_id
                    or "system_temperature" in unique_id
                    or "cpu_temperature_c" in entity_id
                    or "system_temperature_c" in entity_id
                ):
                    found_match = True

            # Disk triggers (with optional index)
            elif trigger_type in ["disk_abnormal", "disk_temperature_above"]:
                if (
                    "disk" in unique_id
                    and ("status" in unique_id or "temperature" in unique_id)
                ) or (
                    "disk" in entity_id
                    and ("status" in entity_id or "temperature" in entity_id)
                ):
                    if (
                        index is None
                        or f"_{index}_" in unique_id
                        or f"disk_{index}_" in unique_id
                        or f"_{index}_" in entity_id
                        or f"disk_{index}_" in entity_id
                    ):
                        found_match = True

            # Fan triggers (with optional index)
            elif trigger_type == "fan_abnormal":
                if ("fan" in unique_id and "status" in unique_id) or (
                    "fan" in entity_id and "status" in entity_id
                ):
                    if (
                        index is None
                        or f"fan_{index}_" in unique_id
                        or f"fan_{index}_status_" in unique_id
                        or f"fan_{index}_" in entity_id
                        or f"fan_{index}_status_" in entity_id
                    ):
                        found_match = True

            # Pool triggers (with optional index)
            elif trigger_type in ["pool_abnormal", "pool_usage_above"]:
                if (
                    "pool" in unique_id
                    and ("status" in unique_id or "usage" in unique_id)
                ) or (
                    "pool" in entity_id
                    and ("status" in entity_id or "usage" in entity_id)
                ):
                    if (
                        index is None
                        or f"_{index}_" in unique_id
                        or f"pool_{index}_" in unique_id
                        or f"_{index}_" in entity_id
                        or f"pool_{index}_" in entity_id
                    ):
                        found_match = True

            if found_match:
                _LOGGER.debug(
                    "Found matching entity via substring fallback: %s", entity.entity_id
                )
                return entity.entity_id
        return None


class IndexCalculator:
    """Calculator for maximum index values for indexed entities."""

    def __init__(self, registry: er.EntityRegistry) -> None:
        """Initialize the index calculator.

        Args:
            registry: The entity registry to search in.

        """
        self.registry = registry

    def get_max_index_for_device(self, device_id: str, trigger_type: str) -> int:
        """Get maximum index value for a device based on existing entities."""
        entities = er.async_entries_for_device(self.registry, device_id)
        max_index = 1
        actual_count = 0

        # Determine patterns to count based on trigger type
        if trigger_type in ["fan_abnormal", "disk_abnormal", "pool_abnormal"]:
            # For status conditions, count entities with matching unique_id patterns
            patterns = {
                "fan_abnormal": ["fan", "status"],
                "disk_abnormal": ["disk", "status"],
                "pool_abnormal": ["pool", "status"],
            }

            match_patterns = patterns.get(trigger_type, [])
            for entity in entities:
                if entity.domain == "sensor" and entity.unique_id:
                    # Check if unique_id contains all required patterns
                    if all(pattern in entity.unique_id for pattern in match_patterns):
                        actual_count += 1
                        _LOGGER.debug(
                            "Found matching entity %s with unique_id %s",
                            entity.entity_id,
                            entity.unique_id,
                        )

        elif trigger_type == "disk_temperature_above":
            # Count disk temperature entities
            for entity in entities:
                if (
                    entity.domain == "sensor"
                    and entity.unique_id
                    and "disk" in entity.unique_id
                    and "temperature" in entity.unique_id
                ):
                    actual_count += 1

        elif trigger_type == "pool_usage_above":
            # Count pool usage entities
            for entity in entities:
                if (
                    entity.domain == "sensor"
                    and entity.unique_id
                    and "pool" in entity.unique_id
                    and "usage" in entity.unique_id
                ):
                    actual_count += 1

        # Use actual count if found, otherwise use a reasonable default
        max_index = actual_count if actual_count > 0 else 1

        _LOGGER.debug(
            "Using max_index=%d for device %s trigger %s based on %d actual entities",
            max_index,
            device_id,
            trigger_type,
            actual_count,
        )

        return max_index


class AutomationTypeClassifier:
    """Classifier for automation types and their properties."""

    @classmethod
    def is_gds_event_trigger(cls, trigger_type: str) -> bool:
        """Check if trigger is a GDS event-based trigger."""
        return trigger_type in GDS_EVENT_TRIGGERS or trigger_type in DOOR_ACCESS_TRIGGERS

    @classmethod
    def is_door_access_trigger(cls, trigger_type: str) -> bool:
        """Check if trigger is a door access trigger."""
        return trigger_type in DOOR_ACCESS_TRIGGERS

    @classmethod
    def is_threshold_trigger(cls, trigger_type: str) -> bool:
        """Check if trigger requires a numeric threshold."""
        return trigger_type in THRESHOLD_TYPES

    @classmethod
    def is_status_trigger(cls, trigger_type: str) -> bool:
        """Check if trigger is status-based."""
        return trigger_type in STATUS_TRIGGER_TYPES

    @classmethod
    def is_status_condition(cls, condition_type: str) -> bool:
        """Check if condition is status-based."""
        return condition_type in STATUS_CONDITION_TYPES

    @classmethod
    def get_gds_event_code(cls, trigger_type: str) -> str | None:
        """Get GDS event code for trigger type."""
        return GDS_EVENT_MAPPING.get(trigger_type)

    @classmethod
    def get_door_access_type(cls, trigger_type: str) -> str | None:
        """Get door access type for trigger type."""
        return DOOR_ACCESS_TYPE_MAPPING.get(trigger_type)


class AutomationConditionChecker:
    """Checker for automation condition evaluation."""

    @classmethod
    def should_trigger_fire(
        cls,
        state_value: str,
        trigger_type: str,
        threshold: float | None = None,
    ) -> bool:
        """Check if a trigger should fire based on sensor state and configuration."""
        # Handle None or empty state values
        if not state_value:
            return False

        # Numeric threshold triggers
        if AutomationTypeClassifier.is_threshold_trigger(trigger_type):
            if threshold is not None:
                try:
                    current_value = float(state_value)
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Invalid numeric value for threshold trigger: %s", state_value
                    )
                    return False
                return current_value >= threshold
            return False

        # Status-based triggers
        state_value_lower = state_value.lower()

        # Phone status triggers
        if trigger_type == "phone_busy":
            _LOGGER.debug(
                "Checking phone busy trigger: state_value='%s' (lower='%s')",
                state_value,
                state_value_lower,
            )
            should_fire = "busy" in state_value_lower
            _LOGGER.debug("Phone busy trigger should fire: %s", should_fire)
            return should_fire

        if trigger_type == "phone_ringing":
            _LOGGER.debug(
                "Checking phone ringing trigger: state_value='%s' (lower='%s')",
                state_value,
                state_value_lower,
            )
            should_fire = "ringing" in state_value_lower or "ring" in state_value_lower
            _LOGGER.debug("Phone ringing trigger should fire: %s", should_fire)
            return should_fire

        if trigger_type == "fan_abnormal":
            _LOGGER.debug(
                "Checking fan abnormal trigger: state_value='%s' (lower='%s')",
                state_value,
                state_value_lower,
            )
            should_fire = "abnormal" in state_value_lower or state_value_lower in [
                "error",
                "failed",
                "off",
                "stopped",
            ]
            _LOGGER.debug("Fan abnormal trigger should fire: %s", should_fire)
            return should_fire

        if trigger_type in ["disk_abnormal", "pool_abnormal"]:
            return state_value_lower not in [
                "normal",
                "ok",
                "healthy",
                "good",
                "online",
                "active",
            ]

        # For GDS alarm triggers, we'll use event-based system
        # These require alarm events from receiver
        return False

    @classmethod
    def check_condition(
        cls,
        state_value: str,
        condition_type: str,
        above_value: float | None = None,
        index: int | None = None,
    ) -> bool:
        """Check if a condition is met based on sensor state and configuration."""
        # Handle None or empty state values
        if not state_value:
            return False

        _LOGGER.debug(
            "Checking condition with index: condition_type=%s, state_value='%s', above_value=%s, index=%s",
            condition_type,
            state_value,
            above_value,
            index,
        )

        # Numeric threshold conditions
        if AutomationTypeClassifier.is_threshold_trigger(condition_type):
            if above_value is not None:
                try:
                    current_value = float(state_value)
                    result = current_value > above_value
                    _LOGGER.debug(
                        "Threshold condition result: %s > %s = %s",
                        current_value,
                        above_value,
                        result,
                    )
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Invalid numeric value for threshold condition: %s", state_value
                    )
                    return False
                return result
            return False

        # Status-based conditions
        state_value_lower = state_value.lower()

        if condition_type == "fan_abnormal":
            _LOGGER.debug(
                "Checking fan abnormal condition: state_value='%s' (lower='%s'), index=%s",
                state_value,
                state_value_lower,
                index,
            )
            # Use string contains matching instead of strict equality for more flexible status handling
            is_abnormal = "abnormal" in state_value_lower
            _LOGGER.debug("Fan abnormal condition result: %s", is_abnormal)
            return is_abnormal

        if condition_type in ["disk_abnormal", "pool_abnormal"]:
            result = state_value_lower not in ["normal", "ok", "healthy", "good"]
            _LOGGER.debug("Status condition result for %s: %s", condition_type, result)
            return result

        return False


__all__ = [
    # Type dictionaries
    "CONDITION_TYPES",
    "DEFAULT_CONDITION_THRESHOLDS",
    "DEFAULT_THRESHOLDS",
    "DOOR_ACCESS_TRIGGERS",
    "DOOR_ACCESS_TYPE_MAPPING",
    "GDS_CONDITION_TYPES",
    "GDS_EVENT_MAPPING",
    "GDS_EVENT_TRIGGERS",
    "GDS_TRIGGER_TYPES",
    "GNS_CONDITION_TYPES",
    "GNS_TRIGGER_TYPES",
    "STATUS_CONDITION_TYPES",
    "STATUS_TRIGGER_TYPES",
    "THRESHOLD_TYPES",
    "TRIGGER_TYPES",
    # Classes
    "AutomationConditionChecker",
    "AutomationTypeClassifier",
    "EntityMatcher",
    "IndexCalculator",
    "PatternMatcher",
]

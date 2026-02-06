"""Data update coordinator for Grandstream devices."""

from datetime import timedelta
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    COORDINATOR_ERROR_THRESHOLD,
    COORDINATOR_UPDATE_INTERVAL,
    DEVICE_TYPE_GNS_NAS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class GrandstreamCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from Grandstream device."""

    last_update_method: str | None = None

    def __init__(self, hass: HomeAssistant, device_type: str, entry_id: str) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            device_type: Type of the device
            entry_id: Configuration entry ID

        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=COORDINATOR_UPDATE_INTERVAL),
        )
        self.device_type = device_type
        self.entry_id = entry_id
        self._error_count = 0
        self._max_errors = COORDINATOR_ERROR_THRESHOLD

    def _process_status(self, status_data: str | dict) -> str:
        """Process status data and ensure it doesn't exceed maximum length.

        Args:
            status_data: Raw status data (string or dict)

        Returns:
            str: Processed status string

        """
        if not status_data:
            return "unknown"

        # If it's a dict, extract status field
        if isinstance(status_data, dict):
            status_data = status_data.get("status", str(status_data))

        # If it's a JSON string, try to parse it
        if isinstance(status_data, str) and status_data.startswith("{"):
            try:
                status_dict = json.loads(status_data)
                status_data = status_dict.get("status", status_data)
            except json.JSONDecodeError:
                pass

        # Convert to string and normalize
        status_str = str(status_data).lower().strip()

        # If status string is too long, truncate it
        if len(status_str) > 250:
            _LOGGER.warning(
                "Status string too long (%d characters), will be truncated",
                len(status_str),
            )
            return status_str[:250] + "..."

        return status_str

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint (polling).

        Returns:
            dict: Updated device data

        """
        try:
            # Try to get API from runtime_data first
            config_entry = None
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.entry_id == self.entry_id:
                    config_entry = entry
                    break

            api = None
            if (
                config_entry
                and hasattr(config_entry, "runtime_data")
                and config_entry.runtime_data
            ):
                api = config_entry.runtime_data.get("api")

            # Fallback to hass.data if runtime_data not available
            if not api:
                api = self.hass.data[DOMAIN][self.entry_id].get("api")
            if not api:
                self._error_count += 1
                if self._error_count >= self._max_errors:
                    return {"phone_status": "unavailable"}
                _LOGGER.error("API not available")
                return {"phone_status": "unknown"}

            # GNS NAS metrics branch
            if self.device_type == DEVICE_TYPE_GNS_NAS and hasattr(
                api, "get_system_metrics"
            ):
                result = await self.hass.async_add_executor_job(api.get_system_metrics)
                if not isinstance(result, dict):
                    self._error_count += 1
                    _LOGGER.error("API call failed (GNS metrics): %s", result)
                    if self._error_count >= self._max_errors:
                        return {"device_status": "unavailable"}
                    return {"device_status": "unknown"}
                self._error_count = 0
                self.last_update_method = "poll"
                result.setdefault("device_status", "online")

                # Update device firmware version if available
                device = self.hass.data[DOMAIN][self.entry_id].get("device")
                if device and result.get("product_version"):
                    device.set_firmware_version(result["product_version"])

                return result

            # Default phone status branch
            result = await self.hass.async_add_executor_job(api.get_phone_status)
            if not isinstance(result, dict) or result.get("response") != "success":
                self._error_count += 1
                error_msg = (
                    result.get("body") if isinstance(result, dict) else str(result)
                )
                _LOGGER.error("API call failed: %s", error_msg)
                if self._error_count >= self._max_errors:
                    return {"phone_status": "unavailable"}
                return {"phone_status": "unknown"}
            self._error_count = 0
            status = result.get("body", "unknown")
            processed_status = self._process_status(status) + " "
            _LOGGER.info("Device status updated: %s", processed_status)
            self.last_update_method = "poll"

            # Update device firmware version if available
            device = self.hass.data[DOMAIN][self.entry_id].get("device")
            if device and api.version:
                device.set_firmware_version(api.version)

        except (RuntimeError, ValueError, OSError, KeyError) as e:
            self._error_count += 1
            _LOGGER.error("Error getting device status: %s", e)
            if self._error_count >= self._max_errors:
                return {"phone_status": "unavailable"}
            return {"phone_status": "unknown"}
        return {"phone_status": processed_status}

    async def async_handle_push_data(self, data: dict[str, Any]) -> None:
        """Handle pushed data.

        Args:
            data: Pushed data from device

        """
        try:
            _LOGGER.debug("Received push data: %s", data)

            # If data is a string, try to parse it as a dictionary
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = {"phone_status": data}

            # If data is a dict but doesn't have phone_status key, try to get from status or state
            if isinstance(data, dict):
                if "phone_status" not in data:
                    status = (
                        data.get("status") or data.get("state") or data.get("value")
                    )
                    if status:
                        data = {"phone_status": status}

            # Process status data
            if "phone_status" in data:
                data["phone_status"] = self._process_status(data["phone_status"])

            self.last_update_method = "push"
            self.async_set_updated_data(data)

        except Exception as e:
            _LOGGER.error("Error processing push data: %s", e)
            raise

    def handle_push_data(self, data: dict[str, Any]) -> None:
        """Handle push data synchronously.

        Args:
            data: Pushed data from device

        """
        try:
            _LOGGER.debug("Processing sync push data: %s", data)

            # If data is a string, try to parse it as a dictionary
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = {"phone_status": data}

            # If data is a dict but doesn't have phone_status key, try to get from status or state
            if isinstance(data, dict):
                if "phone_status" not in data:
                    status = (
                        data.get("status") or data.get("state") or data.get("value")
                    )
                    if status:
                        data = {"phone_status": status}

            # Process status data
            if "phone_status" in data:
                data["phone_status"] = self._process_status(data["phone_status"])

            self.last_update_method = "push"
            self.async_set_updated_data(data)

        except Exception as e:
            _LOGGER.error("Error processing sync push data: %s", e)
            raise

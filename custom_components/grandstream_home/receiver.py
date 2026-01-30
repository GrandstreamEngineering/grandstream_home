"""Grandstream Home Webhook receivers."""

from datetime import datetime
import json
import logging
from typing import Any

from aiohttp import web
from requests.exceptions import RequestException

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

__all__ = ["GsWebhookCommandReceiver", "GsWebhookStatusReceiver"]

# Define alarm event types
ALARM_EVENTS = {
    "0": "Personnel stay/intrusion alarm",
    "1": "Hostage Alarm",
    "2": "Tamper Alarm",
    "3": "Keypad Input Error Alarm",
    "4": "Non-scheduled Access Alarm",
    "5": "Non-authorized RFID Card Access Alarm",
    "6": "Abnormal Sound Alarm",
    "7": "High Temperature Alarm",
    "8": "DI 1",
    "9": "DI 2",
    "10": "DI 3",
}


def normalize_status(status: Any) -> str:
    """Normalize status value.

    Args:
        status: Raw status value

    Returns:
        str: Normalized status string

    """
    if not status:
        return "unknown"

    status_str = str(status).lower().strip()

    # Handle common error states
    if "error" in status_str or "timeout" in status_str or "failed" in status_str:
        return "unavailable"

    return status_str


async def _parse_request_data(request: web.Request) -> dict[str, Any]:
    """Parse request data from either JSON or form data.

    Args:
        request: Web request object

    Returns:
        dict: Parsed request data

    """
    try:
        if request.headers.get("Content-Type", "").startswith("application/json"):
            # Try to parse JSON
            if request.content_length and request.content_length > 0:
                json_data = await request.json()
                # Ensure json_data is a dictionary
                if isinstance(json_data, dict):
                    return json_data
                _LOGGER.warning(
                    "JSON data is not a dictionary, got type: %s", type(json_data)
                )
                return {}
            return {}
        # Try to parse form data
        data = await request.post()
        # Convert MultiDict to regular dict, ensuring all values are strings
        return {k: str(v) for k, v in data.items()}
    except json.JSONDecodeError as e:
        _LOGGER.warning("JSON parsing error: %s", e)
        return {}
    except (ValueError, KeyError, json.JSONDecodeError, RequestException) as e:
        _LOGGER.error("Error parsing request data: %s", e)
        return {}


async def _handle_status_data(
    coordinator: DataUpdateCoordinator, data: dict[str, Any]
) -> dict[str, Any]:
    """Handle status data.

    Args:
        coordinator: Data update coordinator
        data: Status data

    Returns:
        dict: Response data

    """
    try:
        # Get device type, default to 'gds'
        device_type = data.get("type", "gds")
        _LOGGER.info("Received status update (device type: %s): %s", device_type, data)
        result_success: dict[str, Any] | None = None

        # Get status data, support multiple possible key names
        status = data.get("status") or data.get("state") or data.get("value")

        if not status:
            return {
                "success": False,
                "error": "No status data found in request",
                "code": 400,
            }

        # Normalize status value
        status = normalize_status(status)

        # Add device type to status data
        formatted_status = {
            "type": device_type,
            "status": status,
            "data": data,  # Save complete data for subsequent processing
        }

        # Call coordinator method
        if hasattr(coordinator, "handle_push_data"):
            coordinator.handle_push_data(formatted_status)
        else:
            # If no handle_push_data method, use async_set_updated_data
            coordinator.async_set_updated_data(formatted_status)

        result_success = {
            "success": True,
            "message": "Status update successful",
            "code": 200,
            "data": formatted_status,
        }

    except (ValueError, KeyError, json.JSONDecodeError, RequestException) as e:
        _LOGGER.error("Status update error: %s", e)
        return {"success": False, "error": str(e), "code": 500}
    else:
        return result_success


async def _handle_alarm_event(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Handle alarm events.

    Args:
        hass: Home Assistant instance
        data: Alarm event data

    """
    try:
        event = data.get("event")
        timestamp = data.get("timestamp")
        info = data.get("info", "")

        # Convert timestamp to readable time
        event_time = (
            datetime.fromtimestamp(int(timestamp)) if timestamp else datetime.now()
        )

        # Get alarm type description
        event_description = ALARM_EVENTS.get(str(event), "Unknown Alarm")

        # Prepare event data
        event_data = {
            "event_type": event,
            "event_description": event_description,
            "timestamp": timestamp,
            "datetime": event_time.isoformat(),
            "info": info,
        }

        # Add additional information based on different alarm types
        if event == "6":  # Sound alarm
            event_data["sound_type"] = "Gunshot" if info == "0" else "Unknown Sound"
        elif event == "7":  # Temperature alarm
            event_data["temperature"] = float(info) if info.isdigit() else None

        # Trigger Home Assistant event
        hass.bus.async_fire("grandstream_alarm", event_data)

        _LOGGER.info("Alarm event fired: %s, Data: %s", event_description, event_data)

    except (ValueError, KeyError, json.JSONDecodeError, RequestException) as e:
        _LOGGER.error("Error handling alarm event: %s", e)


async def _handle_command_data(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Handle command data.

    Args:
        hass: Home Assistant instance
        data: Command data

    Returns:
        dict: Response data

    """
    try:
        _LOGGER.info("Command received: %s", data)

        # Validate data is a dictionary
        if not isinstance(data, dict):
            _LOGGER.error("Invalid data type received: %s (expected dict)", type(data))
            return {
                "success": False,
                "error": f"Invalid data type: expected dict, got {type(data).__name__}",
                "code": 400,
            }

        result_obj: dict[str, Any] | None = None

        # Get action
        action = data.get("action") or data.get("command")

        if not action:
            return {
                "success": False,
                "error": "No action specified in request",
                "code": 400,
            }

        if action == "reboot_device":
            await hass.services.async_call(DOMAIN, "reboot_device")
            result_obj = {
                "success": True,
                "message": "Device reboot initiated",
                "code": 200,
                "data": {"action": action},
            }
        if action == "alarm":
            await _handle_alarm_event(hass, data)
            result_obj = {
                "success": True,
                "message": "alarm success",
                "code": 200,
                "data": {"action": action},
            }
        if result_obj is None:
            result_obj = {
                "success": False,
                "error": f"Invalid action: {action}",
                "code": 400,
            }
    except (ValueError, KeyError, json.JSONDecodeError, RequestException) as e:
        _LOGGER.error("Command error: %s", e)
        return {"success": False, "error": str(e), "code": 500}
    else:
        return result_obj


# ----------------------------------------------------------
# Webhook receivers
# ----------------------------------------------------------
class GsWebhookStatusReceiver:
    """Handle Webhook status updates."""

    def __init__(
        self, hass: HomeAssistant, coordinator: DataUpdateCoordinator, webhook_id: str
    ) -> None:
        """Initialize the webhook status receiver.

        Args:
            hass: Home Assistant instance
            coordinator: Data update coordinator
            webhook_id: Webhook ID

        """
        self.hass = hass
        self.coordinator = coordinator
        self.webhook_id = webhook_id

    async def async_setup(self) -> None:
        """Set up Webhook."""
        webhook.async_register(
            self.hass,
            "grandstream_home",
            "Grandstream Status",
            self.webhook_id,
            self.handle_webhook,
            local_only=True,  # Restrict to local access only
        )
        _LOGGER.info("Registered status Webhook: %s", self.webhook_id)

    async def handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle Webhook requests.

        Args:
            hass: Home Assistant instance
            webhook_id: Webhook ID
            request: Web request object

        Returns:
            web.Response: JSON response

        """
        try:
            # Parse request data
            data = await _parse_request_data(request)

            if not data:
                # If no data, try to get from query parameters
                data = dict(request.query)

            if not data:
                return web.json_response(
                    {
                        "success": False,
                        "error": "No data provided in request",
                        "code": 400,
                    },
                    status=400,
                )

            result = await _handle_status_data(self.coordinator, data)
            return web.json_response(result, status=result["code"])

        except (ValueError, KeyError, json.JSONDecodeError, RequestException) as e:
            _LOGGER.error("Webhook status update error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e), "code": 500}, status=500
            )


class GsWebhookCommandReceiver:
    """Handle Webhook commands."""

    def __init__(self, hass: HomeAssistant, webhook_id: str) -> None:
        """Initialize the webhook command receiver.

        Args:
            hass: Home Assistant instance
            webhook_id: Webhook ID

        """
        self.hass = hass
        self.webhook_id = webhook_id

    async def async_setup(self) -> None:
        """Set up Webhook."""
        webhook.async_register(
            self.hass,
            "grandstream_home",
            "Grandstream Command",
            self.webhook_id,
            self.handle_webhook,
            local_only=True,  # Restrict to local access only
        )
        _LOGGER.info("Registered command Webhook: %s", self.webhook_id)

    async def handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle Webhook requests.

        Args:
            hass: Home Assistant instance
            webhook_id: Webhook ID
            request: Web request object

        Returns:
            web.Response: JSON response

        """
        try:
            # Parse request data
            data = await _parse_request_data(request)

            if not data:
                # If no data, try to get from query parameters
                data = dict(request.query)

            if not data:
                return web.json_response(
                    {
                        "success": False,
                        "error": "No data provided in request body or query parameters",
                        "code": 400,
                    },
                    status=400,
                )

            result = await _handle_command_data(self.hass, data)
            return web.json_response(result, status=result["code"])

        except (ValueError, KeyError, json.JSONDecodeError, RequestException) as e:
            _LOGGER.error("Webhook command error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e), "code": 500}, status=500
            )

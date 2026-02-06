"""API for GNS NAS devices."""

from __future__ import annotations

from collections.abc import Callable
import functools
import json
import logging
import socket
import time
from typing import Any, TypeVar

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
import requests

# Disable SSL warnings for self-signed certificates
import urllib3

from ..const import (
    CONTENT_TYPE_FORM,
    CONTENT_TYPE_JSON,
    DEFAULT_HTTPS_PORT,
    DEVICE_TYPE_GNS_NAS,
    GNS_DEFAULT_TIMEOUT,
    HEADER_AUTHORIZATION,
    HEADER_CONTENT_TYPE,
    HTTP_METHOD_GET,
    HTTP_METHOD_POST,
    INTEGRATION_VERSION,
)
from ..utils import format_host_url

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LOGGER = logging.getLogger(__name__)

# API response status codes
API_SUCCESS_CODE = 0

# Default app ID
DEFAULT_APP_ID = "com.gs.gns_homeassistant"

# API endpoint constants
ENDPOINT_PUBLIC_KEY = "login/account/public_key"
ENDPOINT_APP_LOGIN = "login/account/app_login_v3"
ENDPOINT_USER_INFO = "auth/base_info"
ENDPOINT_NETWORK_CARDS = "interface/get_network_card_list"
ENDPOINT_STORAGE_POOLS = "pool"
ENDPOINT_STORAGE_DISKS = "disk"
ENDPOINT_DEVICE_REBOOT = "system/device_reboot"
ENDPOINT_DEVICE_SHUTDOWN = "system/device_shutdown"
ENDPOINT_DEVICE_SLEEP = "hardware/power/run_sleep"
ENDPOINT_SYSTEM_INFO = "system/device/system_info"
ENDPOINT_HARDWARE_INFO = "hardware/power/get_hardware_info"
ENDPOINT_NETWORK_DATA = "reporting/netdata_get_data"

# Fan mode mapping
FAN_MODE_MAP = {
    "0": "auto",
    "1": "silent",
    "2": "performance",
    "3": "standard",
}

# WOL constants
WOL_DEFAULT_PORT = 9
WOL_BROADCAST_IP = "255.255.255.255"

# Type variable for decorators
F = TypeVar("F", bound=Callable[..., Any])


def _require_auth(func: F) -> F:
    """Ensure API method is authenticated before execution.

    Args:
        func: Method that requires authentication

    Returns:
        Wrapped method with authentication check

    """

    @functools.wraps(func)
    def wrapper(self: GNSNasAPI, *args: Any, **kwargs: Any) -> Any:
        if not self._ensure_auth():
            _LOGGER.warning(
                "Cannot execute %s: authentication failed (device may be offline)",
                func.__name__,
            )
            return None if func.__annotations__.get("return") in [dict, list] else False
        return func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _handle_session_retry(func: F) -> F:
    """Handle session expiration with automatic re-login.

    If API call returns 401 Unauthorized, automatically re-login once and retry.

    Args:
        func: Method that may encounter session expiration

    Returns:
        Wrapped method with session retry capability

    """

    @functools.wraps(func)
    def wrapper(self: GNSNasAPI, *args: Any, **kwargs: Any) -> Any:
        result = func(self, *args, **kwargs)

        # Check if we got a 401 response (session expired)
        if (
            hasattr(self, "_last_response")
            and self._last_response is not None
            and self._last_response.status_code == 401
        ):
            _LOGGER.warning(
                "Session expired (401), attempting re-login for %s", func.__name__
            )

            # Clear the session and cached credentials, then try to re-login
            self.session_id = None
            self._clear_cached_credentials()
            _LOGGER.debug("Cleared session and cached credentials due to 401 error")

            if self._ensure_auth():
                _LOGGER.info("Re-login successful, retrying %s", func.__name__)
                # Clear last response to avoid infinite retry
                self._last_response = None
                return func(self, *args, **kwargs)
            _LOGGER.error("Re-login failed, cannot retry %s", func.__name__)

        return result

    return wrapper  # type: ignore[return-value]


class GNSNasAPI:
    """GNS NAS API implementation with RSA encryption login.

    This class provides a comprehensive interface for interacting with Grandstream
    GNS NAS devices, including:
    - RSA-encrypted authentication
    - Hardware monitoring (CPU, memory, temperature, fans)
    - Storage management (pools, disks)
    - Network statistics
    - Power management (reboot, shutdown, sleep, WOL)
    - User access control
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        app_id: str = DEFAULT_APP_ID,
        use_https: bool = True,
        port: int = DEFAULT_HTTPS_PORT,
    ) -> None:
        """Initialize GNS NAS API.

        Args:
            host: Device IP address or hostname
            username: Login username
            password: Login password (plain text, will be encrypted)
            app_id: Application ID for app login
            use_https: Use HTTPS protocol (default: True)
            port: Device port (default: DEFAULT_HTTPS_PORT)

        """
        # Connection settings
        self.host: str = host
        self.port: int = port
        self.username: str = username
        self.password: str = password
        self.app_id: str = app_id

        # Device information
        self.device_name: str = DEVICE_TYPE_GNS_NAS
        self.device_mac: str | None = None

        # Session management
        self.session_id: str | None = None
        self.session: requests.Session = requests.Session()
        self.session.verify = (
            False  # Disable SSL verification for self-signed certificates
        )

        # URL configuration
        self._use_https: bool = use_https
        protocol = "https" if use_https else "http"
        host_url = format_host_url(self.host)
        self.base_url: str = f"{protocol}://{host_url}:{port}/api/gs/v1.0/"

        # Authentication state
        self._public_key: bytes | None = None
        self._encrypted_password: str | None = None

        # Device state tracking
        self._is_online: bool = False
        self._is_admin: bool | None = None
        self._user_info: dict[str, Any] | None = None

        # Login failure tracking to prevent account lockout
        self._login_failed_count: int = 0
        self._last_login_attempt: float = 0.0

        # For session retry decorator
        self._last_response: requests.Response | None = None

    def _build_url(self, endpoint: str, use_v2: bool = False) -> str:
        """Build complete API URL from endpoint.

        Args:
            endpoint: API endpoint path
            use_v2: Use v2.0 API version instead of v1.0

        Returns:
            Complete API URL

        """
        protocol = "https" if self._use_https else "http"
        host_url = format_host_url(self.host)
        api_version = "v2.0" if use_v2 else "gs/v1.0"
        return f"{protocol}://{host_url}:{self.port}/api/{api_version}/{endpoint}"

    def _handle_api_request(
        self,
        method: str,
        url: str,
        operation: str,
        timeout: int = GNS_DEFAULT_TIMEOUT,
        **kwargs: Any,
    ) -> tuple[dict[str, Any] | list[Any] | None, bool]:
        """Unified API request handler to eliminate duplicate exception handling code.

        Args:
            method: HTTP method (GET/POST)
            url: API URL
            operation: Operation description for logging
            timeout: Request timeout in seconds
            **kwargs: Additional arguments passed to requests method

        Returns:
            Tuple of (API response data, is_connection_error)
            is_connection_error is True if the failure is due to network connectivity issues

        """
        result = None
        is_connection_error = False
        try:
            # Select appropriate session method
            method_map: dict[str, Any] = {
                HTTP_METHOD_GET: self.session.get,
                HTTP_METHOD_POST: self.session.post,
            }
            session_method = method_map.get(method.upper())

            if not session_method:
                _LOGGER.error("Unsupported HTTP method: %s", method)
                return None, False

            # Execute request
            response = session_method(url, timeout=timeout, **kwargs)
            self._last_response = response  # Store for session retry decorator
            response.raise_for_status()

            # Parse JSON response
            result = response.json()
            _LOGGER.debug("%s response: %s", operation, result)
        except requests.exceptions.ConnectTimeout:
            _LOGGER.warning(
                "Connection timeout during %s (device may be offline)", operation
            )
            self._is_online = False
            is_connection_error = True
        except requests.exceptions.ConnectionError:
            _LOGGER.warning(
                "Connection failed during %s (device may be offline)", operation
            )
            self._is_online = False
            is_connection_error = True
        except requests.RequestException as err:
            _LOGGER.error("Request failed during %s: %s", operation, err)
            self._is_online = False
            is_connection_error = True
        except (ValueError, KeyError, json.JSONDecodeError) as err:
            _LOGGER.error("Failed to parse %s response: %s", operation, err)

        return result, is_connection_error

    def _get_public_key(self) -> bytes | None:
        """Get RSA public key from server.

        Returns:
            PEM formatted public key, or None if failed

        """
        url = f"{self.base_url}{ENDPOINT_PUBLIC_KEY}"
        _LOGGER.debug("Requesting public key from: %s", url)

        result, _ = self._handle_api_request(HTTP_METHOD_GET, url, "get public key")
        if not result or not isinstance(result, dict) or "data" not in result:
            return None

        try:
            public_key_hex = result["data"]
            public_key_bytes = bytes.fromhex(public_key_hex)
            _LOGGER.debug("Successfully retrieved public key")
        except (ValueError, KeyError) as err:
            _LOGGER.error("Invalid public key format: %s", err)
            return None
        return public_key_bytes

    def _encrypt_password(self, password: str) -> str | None:
        """Encrypt password using RSA public key.

        Args:
            password: Plain text password.

        Returns:
            Hex encoded encrypted password, or None if failed.

        """
        try:
            if not self._public_key:
                self._public_key = self._get_public_key()
                if not self._public_key:
                    _LOGGER.warning("Cannot encrypt password: failed to get public key")
                    return None

            public_key = serialization.load_pem_public_key(self._public_key)

            if not isinstance(public_key, RSAPublicKey):
                _LOGGER.error("Public key is not RSA type")
                return None

            hash_algorithm = hashes.SHA512()
            encrypted = public_key.encrypt(
                password.encode(encoding="utf-8"),
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hash_algorithm),
                    algorithm=hash_algorithm,
                    label=None,
                ),
            )

            encrypted_hex = encrypted.hex()
            _LOGGER.debug("Password encrypted successfully")
        except OSError as err:
            _LOGGER.error("Failed to encrypt password: %s", err)
            return None
        return encrypted_hex

    def _get_unknown_metrics(self) -> dict[str, Any]:
        """Get unknown metrics when device is offline or connection failed.

        Returns:
            Dictionary with unknown values for all sensors

        """
        return {
            "device_status": "unknown",
            "cpu_usage_percent": None,
            "memory_usage_percent": None,
            "memory_total_gb": None,
            "memory_used_gb": None,
            "system_temperature_c": None,
            "cpu_temperature_c": None,
            "fan_mode": None,
            "fans": [],
            "fan_count": None,
            "network_received_bytes_per_sec": None,
            "network_sent_bytes_per_sec": None,
            "hostname": None,
            "product_name": None,
            "product_version": None,
            "running_time": None,
            "pools": [],
            "disks": [],
        }

    def _clear_cached_credentials(self) -> None:
        """Clear cached RSA public key and encrypted password."""
        self._public_key = None
        self._encrypted_password = None
        _LOGGER.debug("Cleared cached credentials")

    def _handle_login_failure(
        self,
        reason: str = "Unknown error",
        code: int | None = None,
        auth_failure: bool = True,
    ) -> None:
        """Handle login failure by incrementing count and warning if needed.

        Args:
            reason: Description of failure reason
            code: Error code if available
            auth_failure: Whether this is an authentication failure (default: True)
                         Set to False for connection errors to avoid lockout

        """
        if auth_failure:
            # Clear cached credentials as they may be invalid, especially after device restart
            # which generates new RSA keys
            self._clear_cached_credentials()

            # Increment failure counter
            self._login_failed_count += 1

            # Log error with attempt count
            log_msg = (
                f"GNS NAS app login failed (attempt {self._login_failed_count}): {reason}"
                + (f" (code={code})" if code is not None else "")
            )
            _LOGGER.error(log_msg)

            # Warn if approaching lockout threshold
            if self._login_failed_count >= 2:
                _LOGGER.warning(
                    "Multiple authentication failures detected (%d/2)"
                    "Further failures will require a 15-minute wait",
                    self._login_failed_count,
                )
        else:
            # Log connection error without incrementing counter or clearing credentials
            _LOGGER.warning("Login request failed due to connection issues: %s", reason)

    def _handle_login_success(self, session_data: dict[str, Any]) -> None:
        """Handle successful login by resetting count and setting state.

        Args:
            session_data: Login session data from successful response

        """
        # Reset failure count and set authentication/online state on success
        self._login_failed_count = 0
        self.session_id = session_data.get("dwt")

        if session_data.get("protected", False):
            _LOGGER.warning("Account is protected")

        password_status = session_data.get("password_expiration_status", 0)
        if password_status != 0:
            _LOGGER.warning("Password expiration status: %s", password_status)

        _LOGGER.info(
            "GNS NAS app login successful: token=%s..., locked=%s, protected=%s",
            self.session_id[:20] if self.session_id else None,
            session_data.get("locked"),
            session_data.get("protected"),
        )
        self._is_online = True

        # Get user info to check admin status
        user_info_success = self._fetch_user_info()

        # If failed to get user info, assume admin for 'admin' username
        if not user_info_success and self.username.lower() == "admin":
            _LOGGER.warning(
                "Failed to fetch user info, assuming admin privileges for username 'admin'"
            )
            self._is_admin = True

        # Get MAC address for WOL
        self._fetch_device_mac()

    def login(self, device_id: str = "ha_integration") -> bool:
        """Login to GNS NAS device using app login endpoint.

        Args:
            device_id: Device identifier for the client

        Returns:
            bool: True if login successful, False otherwise

        """
        # Check if we need to wait after multiple failed attempts
        current_time = time.time()
        if self._login_failed_count >= 2:
            # Wait at least 15 minutes before allowing retry after 2+ failures
            time_since_last_attempt = current_time - self._last_login_attempt
            if time_since_last_attempt < 900:  # 15 minutes = 900 seconds
                wait_time = int(900 - time_since_last_attempt)
                _LOGGER.warning("Too many login failures. Will wait %d seconds (%.1f minutes) before retrying",
                    wait_time,
                    wait_time / 60,
                )
                return False
            # Reset counter after waiting period
            self._login_failed_count = 0

        if not self._encrypted_password:
            self._encrypted_password = self._encrypt_password(self.password)
            if not self._encrypted_password:
                _LOGGER.warning("Cannot login: password encryption failed")
                return False

        url = f"{self.base_url}{ENDPOINT_APP_LOGIN}"
        headers = {HEADER_CONTENT_TYPE: CONTENT_TYPE_JSON}
        data = {
            "username": self.username,
            "password": self._encrypted_password,
            "app_id": self.app_id,
            "otp_token": "",
            "client_info": {
                "app_name": "Home Assistant",
                "app_version": INTEGRATION_VERSION,
                "device_category": "integration",
                "os_name": "Home Assistant OS",
                "device_id": device_id,
            },
        }

        # Record this login attempt time
        self._last_login_attempt = time.time()

        _LOGGER.info("Attempting app login to GNS NAS: %s", url)
        _LOGGER.debug("App login request data: %s", {**data, "password": self.password})

        result, is_connection_error = self._handle_api_request(
            HTTP_METHOD_POST, url, "app login", json=data, headers=headers
        )
        if not result or not isinstance(result, dict):
            # Only count authentication failures, not connection errors
            if not is_connection_error:
                self._handle_login_failure("Invalid response from server")
            else:
                _LOGGER.warning(
                    "Login request failed due to connection issues (device may be offline)"
                )
            return False

        _LOGGER.debug("App login response: %s", result)

        if result.get("code") == 0 and "data" in result:
            session_data = result["data"]

            if not session_data.get("successful_login", False):
                reason = str(session_data.get("reason", 0))
                self._handle_login_failure(reason)
                return False

            self.session_id = session_data.get("dwt")

            if session_data.get("locked", False):
                _LOGGER.error("Account is locked")
                self._login_failed_count = 2  # Set to threshold to trigger wait period
                return False

            # Handle successful login
            self._handle_login_success(session_data)
            return True
        error_msg = result.get("msg", "Unknown error")
        error_code = result.get("code")
        # Authentication failed - increment failure count
        self._handle_login_failure(error_msg, error_code)
        return False

    def _ensure_auth(self) -> bool:
        """Ensure authenticated, attempt login if not logged in.

        Returns:
            bool: True if authenticated, False if login failed

        """
        if not self.session_id:
            _LOGGER.info("Not logged in, attempting to login")
            if not self.login():
                _LOGGER.warning("Login failed, device may be offline")
                return False
            _LOGGER.info("Login successful, continuing operation")
        return True

    def _get_auth_headers(self) -> dict[str, str]:
        """Get HTTP headers with authentication.

        Returns:
            Headers with session authentication.

        """
        return {
            HEADER_CONTENT_TYPE: CONTENT_TYPE_JSON,
            HEADER_AUTHORIZATION: f"Bearer {self.session_id}",
        }

    @_require_auth
    @_handle_session_retry
    def _send_power_command(self, endpoint: str, command_name: str) -> bool:
        """Send power management command to GNS NAS.

        Args:
            endpoint: API endpoint path (e.g., "hardware/power/run_sleep")
            command_name: Command name for logging (e.g., "sleep")

        Returns:
            bool: True if command successful, False otherwise

        """
        url = f"{self.base_url}{endpoint}"
        headers = {
            HEADER_CONTENT_TYPE: CONTENT_TYPE_FORM,
            HEADER_AUTHORIZATION: f"Bearer {self.session_id}",
        }

        _LOGGER.info("Sending %s command to GNS NAS: %s", command_name, url)
        _LOGGER.debug(
            "Using session_id: %s",
            self.session_id[:20] if self.session_id else "None",
        )

        result, _ = self._handle_api_request(
            HTTP_METHOD_POST,
            url,
            f"{command_name} command",
            allow_redirects=True,
            headers=headers,
        )

        if not result or not isinstance(result, dict):
            return False

        _LOGGER.debug("%s command response: %s", command_name.capitalize(), result)

        if result.get("code") == API_SUCCESS_CODE:
            _LOGGER.info("GNS NAS %s command successful", command_name)
            self._is_online = True
            return True

        error_msg = result.get("msg", "Unknown error")
        _LOGGER.error(
            "GNS NAS %s command failed: %s (code=%s)",
            command_name,
            error_msg,
            result.get("code"),
        )

        return False

    @staticmethod
    def _build_magic_packet(mac_address: str) -> bytes:
        """Build WOL magic packet.

        Magic packet format: 6 bytes of 0xFF + 16 repetitions of target MAC address

        Args:
            mac_address: MAC address in format "AA:BB:CC:DD:EE:FF" or "AABBCCDDEEFF"

        Returns:
            bytes: Magic packet data

        Raises:
            ValueError: If MAC address format is invalid

        """
        # Clean MAC address (remove separators and convert to uppercase)
        mac_clean = mac_address.replace(":", "").replace("-", "").upper()

        _LOGGER.debug(
            "WOL: Building magic packet - Original MAC: %s, Cleaned: %s",
            mac_address,
            mac_clean,
        )

        if len(mac_clean) != 12:
            raise ValueError(
                f"Invalid MAC address length: {mac_address} (cleaned: {mac_clean}, length: {len(mac_clean)})"
            )

        try:
            mac_bytes = bytes.fromhex(mac_clean)
        except ValueError as e:
            raise ValueError(
                f"Invalid MAC address format: {mac_address} (cleaned: {mac_clean})"
            ) from e

        # Build magic packet: 6 bytes of 0xFF + 16 repetitions of MAC
        magic_packet = b"\xff" * 6 + mac_bytes * 16

        _LOGGER.debug(
            "WOL: Magic packet built - Total length: %d bytes (6 header + %d MAC repetitions)",
            len(magic_packet),
            len(mac_bytes) * 16,
        )
        _LOGGER.debug(
            "WOL: Packet structure - Header (6 bytes): %s, MAC bytes (6 bytes): %s",
            magic_packet[:6].hex(),
            mac_bytes.hex(),
        )

        return magic_packet

    def _get_api_data(
        self,
        endpoint: str,
        operation: str,
        use_v2: bool = False,
        method: str = HTTP_METHOD_GET,
    ) -> Any:
        """Get data from API endpoint with common error handling.

        Args:
            endpoint: API endpoint path
            operation: Operation description for logging
            use_v2: Use v2.0 API version instead of v1.0
            method: HTTP method (default: GET)

        Returns:
            API data, or None if failed

        """
        url = self._build_url(endpoint, use_v2=use_v2)
        headers = self._get_auth_headers()

        result, _ = self._handle_api_request(method, url, operation, headers=headers)
        if not result or not isinstance(result, dict):
            return None

        if result.get("code") == API_SUCCESS_CODE:
            self._is_online = True
            return result.get("data") if "data" in result else result

        error_msg = result.get("msg", "Unknown error")
        _LOGGER.error(
            "Failed to %s: %s (code=%s)", operation, error_msg, result.get("code")
        )
        return None

    @_require_auth
    @_handle_session_retry
    def get_hardware_info(self) -> dict[str, Any] | None:
        """Get hardware information from GNS NAS device.

        API Endpoint: GET /api/v2.0/hardware/power/get_hardware_info

        Returns:
            dict: Hardware information data, or None if failed

        """
        return self._get_api_data(
            ENDPOINT_HARDWARE_INFO, "get hardware info", use_v2=True
        )

    def get_system_metrics(self) -> dict[str, Any]:
        """Get system metrics from GNS NAS device.

        Each interface call is independent and failures do not affect other calls.

        Returns:
            System metrics data with processed hardware information.

        """
        if not self._ensure_auth():
            _LOGGER.warning(
                "Cannot get system metrics: authentication failed (device may be offline)"
            )
            # Return unknown values when authentication fails (network disconnected)
            return self._get_unknown_metrics()

        # Initialize metrics dictionary
        metrics: dict[str, Any] = {
            "device_status": "online" if self._is_online else "offline",
        }

        # Independent calls to each interface, failures use default values
        self._add_hardware_metrics(metrics)
        self._add_storage_metrics(metrics)
        self._add_network_metrics(metrics)
        self._add_system_info_metrics(metrics)

        _LOGGER.debug("Processed system metrics: %s", metrics)

        return metrics

    def _add_hardware_metrics(self, metrics: dict[str, Any]) -> None:
        """Add hardware metrics to the metrics dictionary.

        Args:
            metrics: Target metrics dictionary.

        """
        try:
            hardware_info = self.get_hardware_info()
            if not hardware_info:
                _LOGGER.debug("Hardware info not available, using unknown values")
                self._set_default_hardware_metrics(metrics)
                return

            # CPU metrics
            cpu_percent_str = hardware_info.get("cpu_percent", "0%")
            try:
                metrics["cpu_usage_percent"] = float(cpu_percent_str.rstrip("%"))
            except (ValueError, AttributeError):
                metrics["cpu_usage_percent"] = None

            cpu_temp = hardware_info.get("cpu_temp")
            if cpu_temp is not None:
                metrics["cpu_temperature_c"] = float(cpu_temp)

            # Memory metrics
            memory_percent_str = hardware_info.get("memory_percent", "0%")
            try:
                metrics["memory_usage_percent"] = float(memory_percent_str.rstrip("%"))
            except (ValueError, AttributeError):
                metrics["memory_usage_percent"] = None

            memory_total_str = hardware_info.get("memory_total", "0GB")
            # Convert memory_total to GB
            metrics["memory_total_gb"] = self._parse_memory_size(memory_total_str)
            metrics["memory_used_gb"] = round(
                (metrics["memory_total_gb"] * metrics["memory_usage_percent"]) / 100, 2
            )

            # Temperature metrics
            sys_temp = hardware_info.get("sys_temp")
            if sys_temp is not None:
                metrics["system_temperature_c"] = float(sys_temp)

            # Fan metrics
            fan_mode_str = str(hardware_info.get("fan_mode", "0"))
            metrics["fan_mode"] = FAN_MODE_MAP.get(fan_mode_str, "auto")

            # Fan status
            fans_status = []
            for i in range(3):
                fan_key = f"fan_{i}"
                if fan_key in hardware_info:
                    fan_value = hardware_info[fan_key]
                    fan_status = "abnormal" if fan_value == 1 else "normal"
                    fans_status.append(fan_status)

            metrics["fans"] = fans_status
            metrics["fan_count"] = len(fans_status)

        except (ValueError, KeyError) as err:
            _LOGGER.error("Error adding hardware metrics: %s", err)
            self._set_default_hardware_metrics(metrics)

    def _set_default_hardware_metrics(self, metrics: dict[str, Any]) -> None:
        """Set default hardware metric values.

        Args:
            metrics: Target metrics dictionary.

        """
        metrics.update(
            {
                "cpu_usage_percent": None,
                "memory_usage_percent": None,
                "memory_total_gb": None,
                "memory_used_gb": None,
                "fan_mode": None,
                "fans": [],
                "fan_count": None,
            }
        )

    def _parse_memory_size(self, memory_str: str) -> float:
        """Parse memory size string to GB value.

        Args:
            memory_str: Memory size string (e.g., "16GB", "8192MB").

        Returns:
            GB value.

        """
        try:
            if memory_str.endswith("GB"):
                return float(memory_str.rstrip("GB"))
            if memory_str.endswith("MB"):
                return float(memory_str.rstrip("MB")) / 1024
            if memory_str.endswith("TB"):
                return float(memory_str.rstrip("TB")) * 1024
        except (ValueError, AttributeError):
            _LOGGER.error("Error parsing memory size: %s", memory_str)
        return 0.0

    def _add_storage_metrics(self, metrics: dict[str, Any]) -> None:
        """Add storage metrics to the metrics dictionary.

        Args:
            metrics: Target metrics dictionary.

        """
        try:
            storage_summary = self.get_storage_summary()
            if not storage_summary:
                _LOGGER.debug("Storage summary not available, using unknown values")
                self._set_default_storage_metrics(metrics)
                return

            # Directly use pools and disks from storage_summary (already processed)
            metrics["pools"] = storage_summary.get("pools", []) or []
            metrics["disks"] = storage_summary.get("disks", []) or []

        except (ValueError, KeyError) as err:
            _LOGGER.error("Error adding storage metrics: %s", err)
            self._set_default_storage_metrics(metrics)

    def _set_default_storage_metrics(self, metrics: dict[str, Any]) -> None:
        """Set default storage metric values.

        Args:
            metrics: Target metrics dictionary.

        """
        # For storage, empty lists are appropriate
        metrics.update(
            {
                "pools": [],
                "disks": [],
            }
        )

    def _add_network_metrics(self, metrics: dict[str, Any]) -> None:
        """Add network metrics to the metrics dictionary.

        Args:
            metrics: Target metrics dictionary.

        """
        try:
            network_data = self.get_network_data(duration=GNS_DEFAULT_TIMEOUT)
            if not network_data:
                _LOGGER.debug("Network data not available, using unknown values")
                self._set_default_network_metrics(metrics)
                return

            real_time = network_data.get("real_time", {})
            if real_time:
                metrics["network_received_bytes_per_sec"] = real_time.get(
                    "received_bytes_per_sec", None
                )
                metrics["network_sent_bytes_per_sec"] = real_time.get(
                    "sent_bytes_per_sec", None
                )
            else:
                self._set_default_network_metrics(metrics)

        except (ValueError, KeyError) as err:
            _LOGGER.error("Error adding network metrics: %s", err)
            self._set_default_network_metrics(metrics)

    def _set_default_network_metrics(self, metrics: dict[str, Any]) -> None:
        """Set default network metric values.

        Args:
            metrics: Target metrics dictionary.

        """
        metrics.update(
            {
                "network_received_bytes_per_sec": None,
                "network_sent_bytes_per_sec": None,
            }
        )

    def _add_system_info_metrics(self, metrics: dict[str, Any]) -> None:
        """Add system information metrics to the metrics dictionary.

        Args:
            metrics: Target metrics dictionary.

        """
        try:
            system_info = self.get_system_info()
            if not system_info:
                _LOGGER.debug("System info not available, using unknown values")
                self._set_default_system_info_metrics(metrics)
                return

            metrics["hostname"] = system_info.get("hostname")
            metrics["product_name"] = system_info.get("product_name")
            metrics["product_version"] = system_info.get("product_version")
            running_time_str = system_info.get("running_time", "")
            metrics["running_time"] = self._format_running_time(running_time_str)

        except (ValueError, KeyError) as err:
            _LOGGER.error("Error adding system info metrics: %s", err)
            self._set_default_system_info_metrics(metrics)

    def _set_default_system_info_metrics(self, metrics: dict[str, Any]) -> None:
        """Set default system information metric values.

        Args:
            metrics: Target metrics dictionary.

        """
        metrics.update(
            {
                "hostname": None,
                "product_name": None,
                "product_version": None,
                "running_time": None,
            }
        )

    @_require_auth
    @_handle_session_retry
    def get_storage_pools(self) -> list[dict[str, Any]]:
        """Get storage pool information from GNS NAS device.

        API Endpoint: GET /api/gs/v1.0/pool

        Returns:
            list: List of storage pool information dictionaries

        """
        url = f"{self.base_url}{ENDPOINT_STORAGE_POOLS}"
        headers = self._get_auth_headers()

        pools, _ = self._handle_api_request(
            HTTP_METHOD_GET, url, "get storage pools", headers=headers
        )
        if not pools:
            return []

        # API returns array directly, not wrapped in {code, data}
        if isinstance(pools, list):
            self._is_online = True
            return pools

        # Some versions might wrap the array in {code, data}
        if isinstance(pools, dict):
            if pools.get("code") == API_SUCCESS_CODE:
                data = pools.get("data", [])
                if isinstance(data, list):
                    self._is_online = True
                    return data
                _LOGGER.error("Unexpected storage pools data format: %s", type(data))
                return []

            error_msg = pools.get("msg", "Unknown error")
            _LOGGER.error(
                "Failed to get storage pools: %s (code=%s)",
                error_msg,
                pools.get("code"),
            )

        return []

    @_require_auth
    @_handle_session_retry
    def get_disks(self) -> list[dict[str, Any]]:
        """Get disk information from GNS NAS device.

        API Endpoint: GET /api/gs/v1.0/disk

        Returns:
            list: List of disk information dictionaries

        """
        url = f"{self.base_url}{ENDPOINT_STORAGE_DISKS}"
        headers = self._get_auth_headers()

        result, _ = self._handle_api_request(
            HTTP_METHOD_GET, url, "get disks", headers=headers
        )
        if not result or not isinstance(result, dict):
            return []

        if result.get("code") == API_SUCCESS_CODE and "data" in result:
            self._is_online = True
            return result["data"]

        error_msg = result.get("msg", "Unknown error")
        _LOGGER.error(
            "Failed to get disks: %s (code=%s)", error_msg, result.get("code")
        )
        return []

    def get_storage_summary(self) -> dict[str, Any]:
        """Get storage summary including pools and disks.

        Returns:
            dict: Storage summary with processed information

        """
        pools = self.get_storage_pools()
        disks = self.get_disks()

        summary: dict[str, Any] = {
            "pools": [],
            "disks": [],
        }

        # Process pools
        for pool in pools:
            status = str(pool.get("status", "")).upper()
            used_bytes = pool.get("used", 0) or 0
            free_bytes = pool.get("free", 0) or 0
            total_bytes = used_bytes + free_bytes

            pool_summary = {
                "id": pool.get("id"),
                "name": pool.get("name"),
                "status": status.lower(),  # Convert to lowercase for consistent UI display
                "size_gb": (
                    round(total_bytes / (1024**3), 2) if total_bytes > 0 else 0
                ),
                "usage_percent": 0,
            }

            # Calculate usage percentage
            if total_bytes > 0:
                pool_summary["usage_percent"] = round(
                    (used_bytes / total_bytes) * 100, 2
                )

            summary["pools"].append(pool_summary)

        # Process disks
        for disk in disks:
            health_status = disk.get("health_status", "").upper()
            disk_capacity_bytes = disk.get("capacity", 0) or 0

            disk_summary = {
                "location": disk.get("location"),
                "display_name": disk.get("display_name"),
                "model": disk.get("model"),
                "status": health_status.lower(),  # Convert to lowercase for consistent UI display
                "temperature_c": disk.get("temperature"),
                "size_gb": (
                    round(disk_capacity_bytes / (1024**3), 2)
                    if disk_capacity_bytes > 0
                    else 0
                ),
            }

            summary["disks"].append(disk_summary)

        _LOGGER.debug(
            "Storage summary: %d pools, %d disks",
            len(summary["pools"]),
            len(summary["disks"]),
        )

        return summary

    def reboot_device(self) -> bool:
        """Reboot the NAS device.

        Returns:
            bool: True if reboot command successful

        """
        return self._send_power_command(ENDPOINT_DEVICE_REBOOT, "reboot")

    def sleep_device(self) -> bool:
        """Put the NAS device to sleep.

        API Endpoint: POST /api/gs/v1.0/hardware/power/run_sleep
        Content-Type: multipart/form-data

        Returns:
            bool: True if sleep command successful, False otherwise

        """
        return self._send_power_command(ENDPOINT_DEVICE_SLEEP, "sleep")

    def shutdown_device(self) -> bool:
        """Shutdown the NAS device.

        Returns:
            bool: True if shutdown command successful

        """
        return self._send_power_command(ENDPOINT_DEVICE_SHUTDOWN, "shutdown")

    def wake_device(
        self,
        mac_address: str | None = None,
        broadcast_ip: str = WOL_BROADCAST_IP,
        port: int = WOL_DEFAULT_PORT,
    ) -> bool:
        """Wake the NAS device using Wake-on-LAN (WOL).

        Sends a magic packet to wake up the device. The device must support WOL
        and have it enabled in BIOS/UEFI settings.

        Args:
            mac_address: Target device MAC address. If None, uses self.device_mac
            broadcast_ip: Broadcast IP address (default: "255.255.255.255")
            port: UDP port for WOL (default: 9, can also use 7)

        Returns:
            bool: True if magic packet sent successfully, False otherwise

        Note:
            - Device must have WOL enabled in BIOS (PME Event Wake Up)
            - Network card must support WOL
            - Device must be connected to power
            - This only sends the packet, actual wake-up depends on hardware support

        """
        target_mac = mac_address or self.device_mac

        if not target_mac:
            _LOGGER.error(
                "No MAC address available for WOL. "
                "Please provide mac_address parameter or set device_mac"
            )
            return False

        try:
            magic_packet = self._build_magic_packet(target_mac)

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                _LOGGER.info(
                    "Sending WOL magic packet to MAC: %s via %s:%d",
                    target_mac,
                    broadcast_ip,
                    port,
                )
                sock.sendto(magic_packet, (broadcast_ip, port))

        except ValueError as err:
            _LOGGER.error("Invalid MAC address for WOL: %s", err)
        except OSError as err:
            _LOGGER.error("Failed to send WOL packet: %s", err)
        else:
            _LOGGER.info("WOL magic packet sent successfully to %s", target_mac)
            return True
        return False

    @property
    def is_online(self) -> bool:
        """Check if device is online.

        Returns:
            bool: True if device is online and reachable

        """
        return self._is_online

    def _fetch_user_info(self) -> bool:
        """Fetch user information from base_info endpoint.

        This method is called after successful login to get user details
        including admin status, user permissions, and access control.

        Returns:
            bool: True if user info fetched successfully, False otherwise

        """
        if not self.session_id:
            _LOGGER.warning("Cannot fetch user info: not logged in")
            return False

        url = f"{self.base_url}{ENDPOINT_USER_INFO}"
        headers = {"Authorization": f"Bearer {self.session_id}"}

        _LOGGER.debug("Fetching user info from: %s", url)
        result, _ = self._handle_api_request(
            HTTP_METHOD_GET, url, "get user info", headers=headers
        )
        if not result or not isinstance(result, dict):
            return False

        _LOGGER.debug("User info response: %s", result)

        if result.get("code") == 0 and "data" in result:
            self._user_info = result["data"]
            self._is_admin = (
                self._user_info.get("is_admin", False) if self._user_info else False
            )

            _LOGGER.info(
                "User info retrieved: username=%s, is_admin=%s",
                self._user_info.get("username") if self._user_info else None,
                self._is_admin,
            )

            return True
        error_msg = result.get("msg", "Unknown error")
        _LOGGER.error(
            "Failed to get user info: %s (code=%s)", error_msg, result.get("code")
        )
        self._is_admin = None
        return False

    def _fetch_device_mac(self) -> bool:
        """Fetch device MAC address from network card list.

        This method retrieves the network interface list and finds the MAC address
        of the interface that matches the device's IP address. This MAC is used
        for Wake-on-LAN functionality.

        Returns:
            bool: True if MAC address fetched successfully, False otherwise

        """
        if not self.session_id:
            _LOGGER.warning("Cannot fetch device MAC: not logged in")
            return False

        url = f"{self.base_url}{ENDPOINT_NETWORK_CARDS}"
        headers = {"Authorization": f"Bearer {self.session_id}"}

        _LOGGER.debug("Fetching network card list from: %s", url)
        result, _ = self._handle_api_request(
            HTTP_METHOD_GET, url, "get network card list", headers=headers
        )
        if not result or not isinstance(result, dict):
            return False

        _LOGGER.debug("Network card list response: %s", result)

        if result.get("code") == 0 and "data" in result:
            network_cards = result["data"]

            # Find the network card that matches the device IP
            matched_card = None
            for card in network_cards:
                ipv4_address = card.get("ipv4_address", "")

                # Check if this card's IP matches the device host IP
                if ipv4_address and ipv4_address == self.host:
                    matched_card = card
                    break

            if matched_card:
                self.device_mac = matched_card.get("mac", "")

                _LOGGER.info(
                    "Device MAC address found: %s (interface: %s, IP: %s)",
                    self.device_mac,
                    matched_card.get("name"),
                    matched_card.get("ipv4_address"),
                )

                return True
            # If no exact match, try to find the first active interface
            _LOGGER.warning(
                "No network card found with IP %s, looking for active interface",
                self.host,
            )

            for card in network_cards:
                link_state = card.get("link_state", "")
                ipv4_address = card.get("ipv4_address", "")

                # Look for an active interface with an IP address
                if link_state == "LINK_STATE_UP" and ipv4_address:
                    self.device_mac = card.get("mac", "")

                    _LOGGER.info(
                        "Using active network card MAC: %s (interface: %s, IP: %s)",
                        self.device_mac,
                        card.get("name"),
                        ipv4_address,
                    )

                    return True

            _LOGGER.warning("No active network card found, MAC address not available")
            return False
        error_msg = result.get("msg", "Unknown error")
        _LOGGER.error(
            "Failed to get network card list: %s (code=%s)",
            error_msg,
            result.get("code"),
        )

        return False

    @property
    def is_admin(self) -> bool:
        """Check if current user is admin.

        Returns:
            bool: True if user is admin, False otherwise (including when status is unknown)

        Note:
            This property returns cached value only. It does NOT trigger network requests.
            User info is fetched during login. If you need to refresh, call _fetch_user_info()
            from an executor job.

        """
        # IMPORTANT: Do NOT call _fetch_user_info() here!
        # This property may be called from the main event loop (e.g., in entity.available)
        # Network requests MUST be done in executor jobs to avoid blocking the event loop
        return self._is_admin if self._is_admin is not None else False

    @property
    def user_info(self) -> dict[str, Any] | None:
        """Get cached user information.

        Returns:
            User information from base_info endpoint, or None if not available.

        """
        return self._user_info

    @_require_auth
    @_handle_session_retry
    def get_network_cards(self) -> list[dict[str, Any]]:
        """Get list of network cards/interfaces.

        Returns:
            List of network card information dictionaries, or empty list if failed

        """
        result = self._get_api_data(ENDPOINT_NETWORK_CARDS, "get network cards")
        if isinstance(result, list):
            _LOGGER.info("Retrieved %d network cards", len(result))
            return result
        return []

    @_require_auth
    @_handle_session_retry
    def get_network_data(self, duration: int = 10) -> dict[str, Any] | None:
        """Get network interface data from GNS NAS device.

        API Endpoint: POST /api/v2.0/reporting/netdata_get_data

        Args:
            duration: Data collection duration in seconds (default: 10)

        Returns:
            dict: Network data with interface statistics, or None if failed

        """
        url = self._build_url(ENDPOINT_NETWORK_DATA, use_v2=True)
        headers = self._get_auth_headers()
        headers[HEADER_CONTENT_TYPE] = CONTENT_TYPE_JSON

        payload_data = {
            "graphs": [{"name": "interface", "identifier": "all"}],
            "reporting_query_netdata": {"duration": duration},
        }

        result, _ = self._handle_api_request(
            HTTP_METHOD_POST,
            url,
            "get network data",
            timeout=GNS_DEFAULT_TIMEOUT,
            data=json.dumps(payload_data),
            headers=headers,
        )
        if not result or not isinstance(result, dict):
            return None

        if result.get("code") == API_SUCCESS_CODE and "data" in result:
            self._is_online = True
            return self._process_network_data(result["data"])

        error_msg = result.get("msg", "Unknown error")
        _LOGGER.error(
            "Failed to get network data: %s (code=%s)", error_msg, result.get("code")
        )
        return None

    @_require_auth
    @_handle_session_retry
    def get_system_info(self) -> dict[str, Any] | None:
        """Get system information from GNS NAS device.

        API Endpoint: GET /api/gs/v1.0/system/device/system_info

        Returns:
            dict: System information data, or None if failed

        """
        return self._get_api_data(ENDPOINT_SYSTEM_INFO, "get system info")

    def _process_network_data(self, network_data: list[Any]) -> dict[str, Any]:
        """Process raw network data and extract real-time metrics.

        Args:
            network_data: Raw network data from API response

        Returns:
            dict: Processed network metrics with real-time data (only used fields)

        """
        if not network_data or len(network_data) == 0:
            return {}

        # Extract the first interface data (usually "all" interfaces)
        interface_data = network_data[0]
        data_points = interface_data.get("data", [])

        # Get the latest data point (real-time data)
        latest_data = data_points[-1] if data_points else None

        # Calculate current rates in Bytes/s
        current_received_bps = 0.0
        current_sent_bps = 0.0

        if latest_data and len(latest_data) >= 3:
            # Convert from kbit/s to Bytes/s (1 kbit = 1000 bits, 1 Byte = 8 bits)
            # kbit/s × 1000 ÷ 8 = kbit/s × 125 = Bytes/s
            current_received_bps = round(latest_data[1] * 125, 1)
            current_sent_bps = round(latest_data[2] * 125, 1)

        _LOGGER.debug(
            "Network speed: received=%s B/s, sent=%s B/s",
            current_received_bps,
            current_sent_bps,
        )

        # Return only the fields that are actually used by sensors
        return {
            "real_time": {
                "received_bytes_per_sec": current_received_bps,
                "sent_bytes_per_sec": current_sent_bps,
            },
        }

    def _format_running_time(self, running_time_str: str) -> int:
        """Convert running time string to total seconds.

        Args:
            running_time_str: Running time string in format "days:hours:minutes" or "hours:minutes"

        Returns:
            int: Total running time in seconds, 0 if parsing fails

        """
        total_seconds = 0
        if not running_time_str or not isinstance(running_time_str, str):
            return total_seconds

        try:
            parts = running_time_str.split(":")

            # Limit parts to prevent index errors
            if len(parts) not in [2, 3]:
                _LOGGER.warning("Unexpected running time format: %s", running_time_str)
                return total_seconds

            # Maximum reasonable values (considering enterprise NAS may run for years)
            max_days = 365 * 50  # 50 years
            max_hours = 23
            max_minutes = 59

            if len(parts) == 3:
                # Format: days:hours:minutes
                days = min(int(parts[0]), max_days)
                hours = min(int(parts[1]), max_hours)
                minutes = min(int(parts[2]), max_minutes)

                # Calculate total seconds
                total_seconds = days * 86400 + hours * 3600 + minutes * 60

                # Cap at 10 years worth of seconds to prevent unreasonable values
                total_seconds = min(total_seconds, 10 * 365 * 86400)

            elif len(parts) == 2:
                # Format: hours:minutes
                hours = min(int(parts[0]), max_hours)
                minutes = min(int(parts[1]), max_minutes)

                # Calculate total seconds
                total_seconds = hours * 3600 + minutes * 60

        except (ValueError, TypeError) as e:
            _LOGGER.error("Failed to format running time '%s': %s", running_time_str, e)

        return total_seconds

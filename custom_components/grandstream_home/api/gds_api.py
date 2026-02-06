"""GDS Phone API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import logging
import socket
import time
from typing import Any, TypeVar

import requests
from requests import RequestException, Session
import urllib3

from ..const import (
    ACCEPT_JSON,
    CONTENT_TYPE_FORM,
    CONTENT_TYPE_JSON,
    DEFAULT_USERNAME,
    DEVICE_TYPE_GDS,
    GDS_TIMEOUT_CONNECT,
    GDS_TIMEOUT_READ,
    GDS_TIMEOUT_SOCKET_CHECK,
    HEADER_CONTENT_TYPE,
    HTTP_METHOD_GET,
    HTTP_METHOD_POST,
)
from ..error import GrandstreamRTSPError
from ..utils import format_host_url

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

# API Endpoints
ENDPOINT_ACCESS = "access"
ENDPOINT_LOGIN = "dologin"
ENDPOINT_PHONE_STATUS = "api-get_phone_status"
ENDPOINT_GDS_GNS_CONFIG = "cgi-bin/api-gds_gns_config"
ENDPOINT_SYS_OPERATION = "api-sys_operation"

# Default Values
DEFAULT_RTSP_PORT = 554
DEFAULT_RTSP_USERNAME = "admin"
DEFAULT_HTTP_PORT = 80
DEFAULT_HTTPS_PORT = 443
DEFAULT_VERSION = "18.0.1.26"


# Response Status
RESPONSE_SUCCESS = "success"
RESPONSE_ERROR = "error"

# Session States
SESSION_EXPIRED = "session-expired"
ACCOUNT_LOCKED = "locked"

# RTSP Stream Paths
RTSP_SUB_STREAM = "/grandstream/sub_stream"

# HTTP Headers
HEADER_ORIGIN = "Origin"
HEADER_HOST = "Host"
HEADER_ACCEPT = "Accept"
HEADER_ACCEPT_ENCODING = "Accept-Encoding"
HEADER_ACCEPT_LANGUAGE = "Accept-Language"
HEADER_CONNECTION = "Connection"
HEADER_COOKIE = "Cookie"
HEADER_X_REQUESTED_WITH = "X-Requested-With"

ACCEPT_ENCODING_GZIP = "gzip, deflate"
ACCEPT_LANGUAGE_EN = "en-US,en;q=0.5"

# Connection Values
CONNECTION_KEEP_ALIVE = "keep-alive"


@dataclass
class APIResponse:
    """Standardized API response structure."""

    success: bool
    data: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "response": RESPONSE_SUCCESS if self.success else RESPONSE_ERROR,
            "body": self.data if self.success else self.error,
        }


class GDSPhoneAPI:
    """GDS Phone API implementation with optimized architecture.

    Features:
    - Unified error handling
    - Request caching for performance
    - Session management with auto-retry
    - Type-safe methods with proper annotations
    - Constants-based configuration
    """

    def __init__(
        self,
        host: str | None = None,
        username: str | None = None,
        password: str | None = None,
        rtsp_username: str | None = None,
        rtsp_password: str | None = None,
        port: int | None = None,
        use_https: bool = False,
    ) -> None:
        """Initialize GDS Phone API.

        Args:
            host: Device IP address or hostname
            username: Device login username
            password: Device login password (plain text)
            rtsp_username: RTSP username
            rtsp_password: RTSP password
            port: Device port (optional, auto-determined)
            use_https: Use HTTPS protocol

        """
        # Core attributes
        self.host = host
        self.username = username
        self.password = password
        self.device_type = DEVICE_TYPE_GDS
        self.use_https = use_https
        self.port = port if port else DEFAULT_HTTP_PORT

        # Build base addresses
        protocol = "https" if use_https else "http"
        default_port = DEFAULT_HTTPS_PORT if use_https else DEFAULT_HTTP_PORT

        if not self.host:
            raise ValueError("Host is required")

        host_url = format_host_url(self.host)
        if self.port == default_port:
            self.base_address = f"{protocol}://{host_url}"
        else:
            self.base_address = f"{protocol}://{host_url}:{self.port}"

        # Session setup
        self.session: Session = requests.Session()
        if use_https:
            self.session.verify = False

        self.base_url: str = f"{self.base_address}/cgi-bin/"
        self.base_url_root: str = f"{self.base_address}/"

        # Runtime state
        self.session_id: str | None = None
        self.device_mac: str | None = None
        self.version: str | None = None
        self.ha_ip_address: str | None = None

        # RTSP configuration
        self.rtsp_username = rtsp_username
        self.rtsp_password = rtsp_password

        # Login failure tracking to prevent account lockout
        self._login_failed_count: int = 0
        self._last_login_attempt: float = 0
        self._account_locked: bool = False
        self._account_lock_expire_time: float = 0

        # Authentication and connection state tracking
        self._is_authenticated: bool = False
        self._is_online: bool = False

        _LOGGER.debug(
            "GDSPhoneAPI initialized: host=%s, protocol=%s, port=%s",
            host,
            protocol,
            self.port,
        )

    # ==================== Decorators ====================

    @staticmethod
    def _require_auth(func: Callable[..., T]) -> Callable[..., T]:
        """Ensure authentication before API calls."""

        @wraps(func)
        def wrapper(self: GDSPhoneAPI, *args: Any, **kwargs: Any) -> T:
            self._ensure_authenticated()
            return func(self, *args, **kwargs)

        return wrapper

    @staticmethod
    def _handle_session_retry(
        func: Callable[..., dict[str, Any]],
    ) -> Callable[..., dict[str, Any]]:
        """Handle session expiration with auto-retry."""

        @wraps(func)
        def wrapper(self: GDSPhoneAPI, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = func(self, *args, **kwargs)

            # Check for session expiration
            if self._is_session_expired(result):
                _LOGGER.info("Session expired, re-authenticating")
                # Mark as not authenticated when session expires
                self._is_authenticated = False
                if self.login():
                    _LOGGER.info("Re-authentication successful, retrying")
                    return func(self, *args, **kwargs)
                _LOGGER.error("Re-authentication failed")
                return APIResponse(
                    success=False, error="Re-authentication failed"
                ).to_dict()

            return result

        return wrapper

    # ==================== Helper Methods ====================

    def _build_headers(
        self, content_type: str = CONTENT_TYPE_FORM, include_auth: bool = True
    ) -> dict[str, str]:
        """Build HTTP headers with optional authentication.

        Args:
            content_type: Content-Type header value
            include_auth: Include authentication Cookie

        Returns:
            Headers dictionary

        """
        if self.host is None:
            raise ValueError("Host must be set before making requests")
        headers = {
            HEADER_ORIGIN: self.base_address,
            HEADER_HOST: (
                f"{format_host_url(self.host)}:{self.port}"
                if self.port not in [DEFAULT_HTTP_PORT, DEFAULT_HTTPS_PORT]
                else f"{format_host_url(self.host)}"
            ),
            HEADER_ACCEPT: ACCEPT_JSON,
            HEADER_ACCEPT_ENCODING: ACCEPT_ENCODING_GZIP,
            HEADER_ACCEPT_LANGUAGE: ACCEPT_LANGUAGE_EN,
            HEADER_CONNECTION: CONNECTION_KEEP_ALIVE,
            HEADER_CONTENT_TYPE: content_type,
            HEADER_X_REQUESTED_WITH: "XMLHttpRequest",
        }

        if include_auth and self.session_id and self.device_mac:
            headers[HEADER_COOKIE] = (
                f"oem=0; device={self.device_mac}; "
                f"version={self.version or DEFAULT_VERSION}; "
                f"locale=en; sid={self.session_id}; "
                f"session-role={DEFAULT_USERNAME}; session-identity={self.session_id}"
            )
            _LOGGER.debug("Authentication cookie added")

        return headers

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_root_url: bool = False,
    ) -> dict[str, Any]:
        """Unified request method with standardized error handling.

        Args:
            method: HTTP method (GET/POST/PUT)
            endpoint: API endpoint
            params: Query parameters
            data: Form data
            json_data: JSON body
            headers: Custom headers
            use_root_url: Use root URL instead of cgi-bin URL

        Returns:
            Parsed JSON response or error dict

        """
        base_url = self.base_url_root if use_root_url else self.base_url
        url = f"{base_url}{endpoint}"

        # Log request details
        _LOGGER.debug(
            "Making API request: %s %s, params=%s, data=%s, json_data=%s",
            method.upper(),
            url,
            params,
            (
                {k: "***" if "password" in k.lower() else v for k, v in data.items()}
                if data
                else None
            ),
            (
                {
                    k: "***" if "password" in k.lower() else v
                    for k, v in json_data.items()
                }
                if json_data
                else None
            ),
        )

        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                data=data,
                json=json_data,
                headers=headers,
                timeout=(GDS_TIMEOUT_CONNECT, GDS_TIMEOUT_READ),
            )

            _LOGGER.debug(
                "Request: %s %s, Status: %s",
                method.upper(),
                endpoint,
                response.status_code,
            )

            if response.status_code == 200:
                response_json = response.json()
                # Log response details (masking sensitive data)
                _LOGGER.debug("Response from %s: %s", endpoint, response_json)
                self._is_online = True  # Device responded successfully, mark as online
                return response_json

            _LOGGER.error("HTTP error %s for %s", response.status_code, endpoint)
            return APIResponse(
                success=False, error=f"HTTP {response.status_code}"
            ).to_dict()

        except requests.exceptions.SSLError as e:
            _LOGGER.error("SSL error for %s: %s", endpoint, e)
            self._is_online = False
            return APIResponse(success=False, error=f"SSL error: {e}").to_dict()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            _LOGGER.error("Connection/Timeout error for %s: %s", endpoint, e)
            self._is_online = False
            return APIResponse(success=False, error=f"Connection failed: {e}").to_dict()
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            _LOGGER.error("JSON parse error for %s: %s", endpoint, e)
            self._is_online = True  # Device is online, but response is invalid
            return APIResponse(success=False, error=f"Invalid response: {e}").to_dict()
        except RequestException as e:
            _LOGGER.error("Request error for %s: %s", endpoint, e)
            self._is_online = False  # General request exception, assume offline
            return APIResponse(success=False, error=str(e)).to_dict()

    def _ensure_authenticated(self) -> None:
        """Ensure user is authenticated, login if necessary."""
        if not self.session_id:
            # If device is reported offline, no need to attempt login
            if not self._is_online:
                _LOGGER.warning("Device is offline, skipping authentication attempt")
                raise RuntimeError("Device is offline")

            # Check if account is locked
            if self._account_locked and time.time() < self._account_lock_expire_time:
                remaining_time = int(self._account_lock_expire_time - time.time())
                raise RuntimeError(
                    f"Account is locked. Will wait {remaining_time} seconds before retrying"
                )

            # Check login failure threshold
            if self._login_failed_count >= 2:
                # Wait at least 60 seconds before allowing retry after multiple failures
                time_since_last_attempt = time.time() - self._last_login_attempt
                if time_since_last_attempt < 300:
                    raise RuntimeError(
                        f"Too many login failures. Will wait {int(300 - time_since_last_attempt)} seconds before retrying"
                    )
                # Reset counter after waiting period
                self._login_failed_count = 0

            _LOGGER.info("Not authenticated, attempting login")
            if not self.login():
                raise RuntimeError("Authentication failed")
            _LOGGER.info("Authentication successful")

    @staticmethod
    def _is_session_expired(response: dict[str, Any]) -> bool:
        """Check if response indicates session expiration.

        Args:
            response: API response dictionary

        Returns:
            True if session expired

        """
        if not isinstance(response, dict):
            return False

        # Check for 'unauthorized' in body (string format)
        if (
            response.get("response") == RESPONSE_SUCCESS
            and response.get("body") == "unauthorized"
        ):
            return True

        # Check for session-expired in body (dict format)
        return (
            response.get("response") == RESPONSE_ERROR
            and isinstance(response.get("body"), dict)
            and response["body"].get("status") == SESSION_EXPIRED
        )

    # ==================== Authentication ====================

    def login(self) -> bool:
        """Perform GDS device login.

        Returns:
            True if login successful

        """
        _LOGGER.info("Attempting GDS device login")
        return self._perform_login()

    def _get_challenge(self) -> str:
        """Get authentication challenge token.

        Returns:
            Challenge token

        Raises:
            RuntimeError: If challenge retrieval fails

        """
        # Generate a random challenge access hass
        challenge_access_hass = hashlib.sha256(str(time.time()).encode()).hexdigest()
        data = {"access": challenge_access_hass}
        headers = self._build_headers(include_auth=False)

        _LOGGER.debug("Requesting challenge token with hass: %s", challenge_access_hass)
        response = self._make_request(
            HTTP_METHOD_POST, ENDPOINT_ACCESS, data=data, headers=headers
        )

        if response.get("response") == RESPONSE_SUCCESS:
            challenge = response.get("body")
            _LOGGER.debug("Challenge token received")
            if challenge is None:
                raise RuntimeError("Challenge token is None")
            return str(challenge)

        error_msg = f"Failed to get challenge: {response}"
        _LOGGER.error("%s", error_msg)
        raise RuntimeError(error_msg)

    def _generate_login_secret(self, challenge: str) -> str:
        """Generate SHA256 hass for login.

        Args:
            challenge: Challenge token from device

        Returns:
            SHA256 hassed password

        """
        login_string = f"{self.password}{challenge}"
        return hashlib.sha256(login_string.encode("utf-8")).hexdigest()

    def _perform_login(self) -> bool:
        """Execute login process.

        Returns:
            True if successful

        """
        try:
            # Check if account is locked
            if self._account_locked and time.time() < self._account_lock_expire_time:
                remaining_time = int(self._account_lock_expire_time - time.time())
                _LOGGER.warning("Account is locked. Will wait %d seconds before retrying",
                    remaining_time,
                )
                return False

            # Record login attempt time
            self._last_login_attempt = time.time()

            # Get challenge
            challenge = self._get_challenge()

            # Generate secret
            secret = self._generate_login_secret(challenge)

            # Perform login
            data = {"username": self.username, "password": secret}
            headers = self._build_headers(include_auth=False)

            _LOGGER.debug("Sending login request")
            response = self._make_request(
                HTTP_METHOD_POST, ENDPOINT_LOGIN, data=data, headers=headers
            )

            if response.get("response") == RESPONSE_SUCCESS:
                body = response.get("body", {})
                self.session_id = body.get("sid")
                self.device_mac = body.get("mac")
                self.version = body.get("ver")

                _LOGGER.info(
                    "Login successful: session_id=%s, MAC=%s, version=%s",
                    self.session_id,
                    self.device_mac,
                    self.version,
                )

                if not all([self.session_id, self.device_mac, self.version]):
                    _LOGGER.warning("Login response missing some fields: %s", body)

                # Reset failure count and set authentication/online state on success
                self._login_failed_count = 0
                self._account_locked = False
                self._is_authenticated = True
                self._is_online = True
                return True

            # Check if account is locked
            if (
                response.get("response") == RESPONSE_ERROR
                and response.get("body") == "locked"
            ):
                lock_time = response.get("lockTime", 300)
                _LOGGER.error(
                    "Login failed: Account is locked. Lock time: %d seconds "
                    "Will wait for the lock to expire before trying again",
                    lock_time,
                )
                # Mark account as locked and set expiration time
                self._account_locked = True
                self._account_lock_expire_time = time.time() + lock_time
                self._login_failed_count = 0  # Reset counter when locked
                self._is_authenticated = False
                # Device is online but account is locked
                self._is_online = True
                return False

            # Login failed - this is an authentication failure, increment count
            self._login_failed_count += 1
            _LOGGER.error(
                "Authentication failed (attempt %d/3): %s",
                self._login_failed_count,
                response,
            )

            # Warn if approaching lockout threshold
            if self._login_failed_count >= 2:
                _LOGGER.warning(
                    "Multiple authentication failures detected (%d/3) "
                    "Further failures may lock the account for 5 minutes",
                    self._login_failed_count,
                )

            self._is_authenticated = False
            # Device is online but authentication failed (wrong credentials)
            self._is_online = True
        except requests.exceptions.ConnectionError as e:
            # Connection errors should not increment login failure count
            # They indicate device is offline, not authentication failure
            _LOGGER.warning(
                "Device connection failed during login (device may be offline): %s", e
            )
            self._is_authenticated = False
            self._is_online = False
        except RuntimeError as e:
            # Check if this is a "device offline" error (not authentication failure)
            if "offline" in str(e).lower():
                _LOGGER.warning("Login skipped: %s", e)
                self._is_authenticated = False
            else:
                # This is likely an authentication failure
                self._login_failed_count += 1
                _LOGGER.error(
                    "Login failed (attempt %d/3): %s", self._login_failed_count, e
                )
                self._is_authenticated = False
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            # These are likely authentication-related errors
            self._login_failed_count += 1
            _LOGGER.error("Login error (attempt %d/3): %s", self._login_failed_count, e)
            self._is_authenticated = False
        except RequestException as e:
            # Check if this is a connection-related error
            if "connection" in str(e).lower() or "timeout" in str(e).lower():
                _LOGGER.warning("Login request failed due to connection issues: %s", e)
                self._is_authenticated = False
                self._is_online = False
            else:
                # Other request errors may count as authentication failures
                self._login_failed_count += 1
                _LOGGER.error(
                    "Login error (attempt %d/3): %s", self._login_failed_count, e
                )
                self._is_authenticated = False
        return self._is_authenticated

    @property
    def is_authenticated(self) -> bool:
        """Check if device is authenticated.

        Returns:
            True if authenticated (has valid session) and not locked

        """
        return (
            self._is_authenticated
            and self.session_id is not None
            and not self._account_locked
        )

    @property
    def is_online(self) -> bool:
        """Check if device is online and reachable.

        Returns:
            True if device is online (regardless of authentication state)

        """
        return self._is_online

    @property
    def is_account_locked(self) -> bool:
        """Check if account is locked.

        Returns:
            True if account is currently locked

        """
        # Check if lock has expired
        if self._account_locked and time.time() >= self._account_lock_expire_time:
            self._account_locked = False
            _LOGGER.info("Account lock has expired, resetting lock status")
        return self._account_locked

    @_require_auth
    @_handle_session_retry
    def get_phone_status(self) -> dict[str, Any]:
        """Get device status using session authentication.

        Returns:
            Status response dictionary

        """
        headers = self._build_headers()
        response = self._make_request(
            HTTP_METHOD_GET, ENDPOINT_PHONE_STATUS, headers=headers
        )

        if response.get("response") == RESPONSE_SUCCESS:
            _LOGGER.debug("Device status retrieved: %s", response)

        return response

    @_require_auth
    def reboot_device(self) -> dict[str, Any]:
        """Reboot the device.

        Returns:
            Reboot response dictionary

        """
        params = {"request": "REBOOT"}
        response = self._make_request(
            HTTP_METHOD_GET, ENDPOINT_SYS_OPERATION, params=params
        )
        _LOGGER.info("Reboot response: %s", response)
        return response

    def get_rtsp_url(self) -> str:
        """Get RTSP streaming URL.

        Returns:
            RTSP URL string

        Raises:
            GrandstreamRTSPError: If credentials are missing or port unreachable

        """
        if not self.rtsp_username or not self.rtsp_password:
            raise GrandstreamRTSPError(
                "RTSP credentials missing. Please configure rtsp_username and rtsp_password."
            )

        rtsp_port = DEFAULT_RTSP_PORT

        # Check port reachability
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(GDS_TIMEOUT_SOCKET_CHECK)
                sock.connect((self.host, rtsp_port))
        except OSError:
            _LOGGER.warning("RTSP port %s on %s is unreachable. Ensure RTSP is enabled",
                rtsp_port,
                self.host,
            )

        rtsp_url = (
            f"rtsp://{self.rtsp_username}:{self.rtsp_password}@"
            f"{self.host}:{rtsp_port}{RTSP_SUB_STREAM}"
        )

        _LOGGER.info(
            "RTSP URL generated: rtsp://%s:***@%s:%s%s",
            self.rtsp_username,
            self.host,
            rtsp_port,
            RTSP_SUB_STREAM,
        )
        return rtsp_url

    @_require_auth
    @_handle_session_retry
    def register_ha_urls(
        self,
        status_url: str,
        command_url: str,
        ha_instance_id: str | None = None,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Register Home Assistant webhook URLs.

        Args:
            status_url: Status push URL
            command_url: Command result URL
            ha_instance_id: Optional HA instance ID
            timestamp: Optional timestamp (defaults to current time)

        Returns:
            Registration response dictionary

        """
        params = {"cmd": "set", "type": "gns_ha_register"}

        payload = {
            "status_url": status_url,
            "command_url": command_url,
            "timestamp": timestamp if timestamp else int(time.time()),
        }

        if ha_instance_id:
            payload["ha_instance_id"] = ha_instance_id

        headers = self._build_headers(content_type=CONTENT_TYPE_JSON)
        _LOGGER.debug("Registering HA URLs: %s", payload)

        response = self._make_request(
            HTTP_METHOD_POST,
            ENDPOINT_GDS_GNS_CONFIG,
            params=params,
            json_data=payload,
            headers=headers,
            use_root_url=True,
        )

        _LOGGER.info("HA URL registration response: %s", response)
        return response

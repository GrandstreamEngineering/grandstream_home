"""Config flow for Grandstream Home."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .const import (
    CONF_COMMAND_WEBHOOK_ID,
    CONF_DEVICE_TYPE,
    CONF_PASSWORD,
    CONF_RTSP_ENABLE,
    CONF_RTSP_PASSWORD,
    CONF_RTSP_USERNAME,
    CONF_STATUS_WEBHOOK_ID,
    CONF_USE_HTTPS,
    CONF_USERNAME,
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTPS_PORT,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DEFAULT_USERNAME_GNS,
    DEVICE_TYPE_GDS,
    DEVICE_TYPE_GNS_NAS,
    DOMAIN,
)
from .utils import encrypt_password, generate_unique_id

_LOGGER = logging.getLogger(__name__)


class GrandstreamConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Grandstream Home."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._name: str | None = None
        self._port: int = DEFAULT_PORT
        self._device_type: str | None = None
        self._auth_info: dict[str, Any] | None = None
        self._webhook_ids: dict[str, str] | None = None
        self._use_https: bool = True  # Track if using HTTPS protocol

    async def async_step_user(self, user_input=None):
        """Handle the initial step for manual addition.

        Args:
            user_input: User input data from the form

        Returns:
            FlowResult: Next step or form to show

        """
        errors = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._name = user_input[CONF_NAME]
            self._device_type = user_input[CONF_DEVICE_TYPE]

            # Set default port based on device type
            # GNS NAS devices default to DEFAULT_HTTPS_PORT (HTTPS), others default to 80
            if self._device_type == DEVICE_TYPE_GNS_NAS:
                self._port = DEFAULT_HTTPS_PORT
                self._use_https = True
            else:
                self._port = DEFAULT_PORT
                self._use_https = False

            # Use global function to generate unique ID
            unique_id = generate_unique_id(
                self._name, self._device_type, self._host, self._port
            )

            # Check if already configured
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            _LOGGER.info(
                "Manual device addition: %s (Type: %s), unique ID: %s",
                self._name,
                self._device_type,
                unique_id,
            )
            return await self.async_step_auth()

        # Show form with input fields
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_GDS): vol.In(
                        [DEVICE_TYPE_GDS, DEVICE_TYPE_GNS_NAS]
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: Any
    ) -> config_entries.ConfigFlowResult:
        """Handle zeroconf discovery callback."""
        self._host = discovery_info.host
        txt_properties = discovery_info.properties or {}

        _LOGGER.debug(
            "Zeroconf discovery - Type: %s, Host: %s, discovery_info: %s",
            discovery_info.type,
            self._host,
            discovery_info,
        )

        is_device_info_service = "_device-info" in discovery_info.type
        has_valid_txt_properties = txt_properties and txt_properties != {"": None}

        # Extract device information from TXT records or service name
        if is_device_info_service and has_valid_txt_properties:
            result = await self._process_device_info_service(
                discovery_info, txt_properties
            )
        else:
            result = await self._process_standard_service(discovery_info)

        if result is not None:
            return result

        # Set discovery card main title as device name
        if self._name:
            self.context["title_placeholders"] = {"name": self._name}

        _LOGGER.info(
            "Zeroconf device discovery: %s (Type: %s) at %s:%s",
            self._name,
            self._device_type,
            self._host,
            self._port,
        )

        # Use global function to generate unique ID
        unique_id = generate_unique_id(
            self._name or "", self._device_type or "", self._host or "", self._port
        )

        # Check if already configured
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return await self.async_step_auth()

    def _is_grandstream(self, product_name):
        """Check if the device is a Grandstream device.

        Args:
            product_name: Product name to check

        Returns:
            bool: True if it's a Grandstream device

        """
        return any(
            prefix in str(product_name).upper()
            for prefix in (DEVICE_TYPE_GNS_NAS, DEVICE_TYPE_GDS)
        )

    async def _process_device_info_service(
        self, discovery_info: Any, txt_properties: dict[str, Any]
    ) -> config_entries.ConfigFlowResult | None:
        """Process device info service discovery.

        Args:
            discovery_info: Zeroconf discovery information
            txt_properties: TXT record properties

        Returns:
            ConfigFlowResult if device should be ignored, None otherwise

        """
        _LOGGER.debug("txt_properties:%s", txt_properties)

        # Check if this is a Grandstream device by examining TXT records
        product_name = txt_properties.get("product_name", "")
        hostname = txt_properties.get("hostname", "")
        # Only process Grandstream devices (GDS, GNS)
        is_grandstream = self._is_grandstream(product_name)

        if not is_grandstream:
            _LOGGER.debug(
                "Ignoring non-Grandstream device: %s (product: %s)",
                hostname,
                product_name,
            )
            return self.async_abort(reason="not_grandstream_device")

        # Determine device type and name based on product_name
        self._device_type = self._determine_device_type_from_product(txt_properties)

        # Extract device name - prefer hostname for device-info service
        if hostname:
            self._name = str(hostname).strip().upper()
        elif product_name:
            self._name = str(product_name).strip().upper()
        else:
            self._name = (
                discovery_info.name.split(".")[0] if discovery_info.name else ""
            )

        # Extract port and protocol from TXT records
        self._extract_port_and_protocol(txt_properties, is_https_default=True)

        # Log additional device information
        self._log_device_info(txt_properties)
        return None

    async def _process_standard_service(
        self, discovery_info: Any
    ) -> config_entries.ConfigFlowResult | None:
        """Process standard service discovery.

        Args:
            discovery_info: Zeroconf discovery information

        Returns:
            ConfigFlowResult if device should be ignored, None otherwise

        """
        # For HTTP/HTTPS services or services without valid TXT records
        self._name = (
            discovery_info.name.split(".")[0].upper() if discovery_info.name else ""
        )

        # Check if this is a Grandstream device
        is_grandstream = self._is_grandstream(self._name)

        if not is_grandstream:
            _LOGGER.debug("Ignoring non-Grandstream device: %s", self._name)
            return self.async_abort(reason="not_grandstream_device")

        # Set device type based on name
        if DEVICE_TYPE_GNS_NAS in self._name.upper():
            self._device_type = DEVICE_TYPE_GNS_NAS
            self._port = discovery_info.port or DEFAULT_HTTPS_PORT
            self._use_https = True
        elif DEVICE_TYPE_GDS in self._name.upper():
            self._device_type = DEVICE_TYPE_GDS
            self._port = discovery_info.port or DEFAULT_PORT
            self._use_https = "_https" in discovery_info.type
        else:
            # Default fallback
            self._device_type = DEVICE_TYPE_GDS
            self._port = discovery_info.port or DEFAULT_PORT
            self._use_https = "_https" in discovery_info.type

        return None

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle authentication step.

        Args:
            user_input: User input data from the form

        Returns:
            FlowResult: Next step or form to show

        """
        errors = {}
        _LOGGER.info("Async_step_auth %s", user_input)

        # Determine if device is GNS type
        is_gns_device = self._device_type == DEVICE_TYPE_GNS_NAS

        # Determine default username based on device type
        default_username = DEFAULT_USERNAME_GNS if is_gns_device else DEFAULT_USERNAME

        # Get current form values (preserve on validation error)
        current_username = (
            user_input.get(CONF_USERNAME, default_username)
            if user_input
            else default_username
        )
        current_password = user_input.get(CONF_PASSWORD, "") if user_input else ""
        current_port = (
            user_input.get(CONF_PORT, self._port) if user_input else self._port
        )

        if user_input is not None:
            # Process user input
            rtsp_enabled = user_input.get(CONF_RTSP_ENABLE, False)

            self._auth_info = {
                CONF_USERNAME: user_input.get(CONF_USERNAME, default_username),
                CONF_PASSWORD: encrypt_password(user_input[CONF_PASSWORD], self.unique_id or "default"),
                CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
                CONF_RTSP_ENABLE: rtsp_enabled,
            }

            # Validate RTSP credentials if enabled
            if rtsp_enabled:
                rtsp_username = user_input.get(CONF_RTSP_USERNAME)
                rtsp_password = user_input.get(CONF_RTSP_PASSWORD)

                if rtsp_username and rtsp_password:
                    self._auth_info[CONF_RTSP_USERNAME] = rtsp_username
                    self._auth_info[CONF_RTSP_PASSWORD] = encrypt_password(rtsp_password, self.unique_id or "default")
                    return await self.async_step_webhook_setup()

                # RTSP enabled but missing credentials
                errors["rtsp_username"] = "missing_rtsp_credentials"
                errors["rtsp_password"] = "missing_rtsp_credentials"
            else:
                # RTSP not enabled, proceed directly
                return await self.async_step_webhook_setup()

        # Build form schema
        schema_dict = self._build_auth_schema(
            is_gns_device, current_username, current_password, current_port, user_input
        )

        # Build description placeholders
        description_placeholders = {
            "host": self._host or "",
            "device_type": self._device_type or "",
            "username": default_username,
        }

        return self.async_show_form(
            step_id="auth",
            description_placeholders=description_placeholders,
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    def _build_auth_schema(
        self,
        is_gns_device: bool,
        current_username: str,
        current_password: str,
        current_port: int,
        user_input: dict[str, Any] | None,
    ) -> dict:
        """Build authentication form schema.

        Args:
            is_gns_device: Whether the device is GNS type
            current_username: Current username value
            current_password: Current password value
            current_port: Current port value
            user_input: User input data (for preserving RTSP fields)

        Returns:
            dict: Form schema dictionary

        """
        schema_dict: dict[Any, type] = {}

        # GNS devices need username input, GDS uses fixed username
        if is_gns_device:
            schema_dict[vol.Required(CONF_USERNAME, default=current_username)] = str

        schema_dict.update(
            {
                vol.Required(CONF_PASSWORD, default=current_password): str,
                vol.Optional(CONF_PORT, default=current_port): int,
            }
        )

        # Only show RTSP configuration for non-GNS devices
        if not is_gns_device:
            schema_dict[vol.Required(CONF_RTSP_ENABLE, default=True)] = bool

            # Preserve RTSP credentials if user has already entered them
            rtsp_username_default = (
                user_input.get(CONF_RTSP_USERNAME, "") if user_input else ""
            )
            rtsp_password_default = (
                user_input.get(CONF_RTSP_PASSWORD, "") if user_input else ""
            )

            schema_dict.update(
                {
                    vol.Optional(
                        CONF_RTSP_USERNAME, default=rtsp_username_default
                    ): str,
                    vol.Optional(
                        CONF_RTSP_PASSWORD, default=rtsp_password_default
                    ): str,
                }
            )

        return schema_dict

    def _determine_device_type_from_product(
        self, txt_properties: dict[str, Any]
    ) -> str:
        """Determine device type based on product_name from TXT records.

        Args:
            txt_properties: TXT record properties from Zeroconf discovery

        Returns:
            str: Device type constant (DEVICE_TYPE_GNS_NAS or DEVICE_TYPE_GDS)

        """
        product_name = txt_properties.get("product_name", "").strip().upper()

        if not product_name:
            _LOGGER.debug("No product_name found in TXT records, defaulting to GDS")
            return DEVICE_TYPE_GDS

        _LOGGER.debug("Determining device type from product_name: %s", product_name)

        # Check if product name starts with GNS
        if product_name.startswith(DEVICE_TYPE_GNS_NAS):
            _LOGGER.debug("Matched GNS device from product_name")
            return DEVICE_TYPE_GNS_NAS

        # Default to GDS for all other cases
        _LOGGER.debug("Defaulting to GDS device type")
        return DEVICE_TYPE_GDS

    def _extract_port_and_protocol(
        self, txt_properties: dict[str, Any], is_https_default: bool = True
    ) -> None:
        """Extract port and protocol information from TXT records.

        Args:
            txt_properties: TXT record properties
            is_https_default: Whether to default to HTTPS if no port found

        """
        https_port = txt_properties.get("https_port")
        http_port = txt_properties.get("http_port")

        if https_port:
            try:
                self._port = int(https_port)
                self._use_https = True
            except (ValueError, TypeError):
                _LOGGER.warning("Invalid https_port value: %s", https_port)
            else:
                return

        if http_port:
            try:
                self._port = int(http_port)
                self._use_https = False
            except (ValueError, TypeError):
                _LOGGER.warning("Invalid http_port value: %s", http_port)
            else:
                return

        # Default values if no valid port found
        if is_https_default:
            self._port = DEFAULT_HTTPS_PORT
            self._use_https = True
        else:
            self._port = DEFAULT_HTTP_PORT
            self._use_https = False

    def _log_device_info(self, txt_properties: dict[str, Any]) -> None:
        """Log device information from TXT records.

        Args:
            txt_properties: TXT record properties

        """
        info_fields = {
            "hostname": "Device hostname",
            "product_name": "Device product",
            "version": "Firmware version",
            "mac": "MAC address",
        }

        for field, label in info_fields.items():
            value = txt_properties.get(field)
            if value:
                _LOGGER.debug("%s: %s", label, value)

    async def async_step_webhook_setup(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Generate Webhook ID and set up Webhook configuration.

        Args:
            user_input: User input data from the form

        Returns:
            FlowResult: Next step or form to show

        """
        # Generate unique Webhook IDs
        status_webhook_id = secrets.token_hex(32)
        command_webhook_id = secrets.token_hex(32)

        self._webhook_ids = {
            CONF_STATUS_WEBHOOK_ID: status_webhook_id,
            CONF_COMMAND_WEBHOOK_ID: command_webhook_id,
        }

        _LOGGER.info("Generated Webhook IDs - Status: %s, Command: %s",
            status_webhook_id[:8],
            command_webhook_id[:8],
        )

        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle confirmation of adding the device.

        Args:
            user_input: User input data from the form

        Returns:
            FlowResult: Configuration entry creation result

        """
        _LOGGER.info("Confirming device addition: %s", self._name)
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                description_placeholders={"name": self._name or ""},
                errors={},
                data_schema=vol.Schema({}),
            )

        # Ensure required data is available
        if (
            not self._name
            or not self._host
            or not self._auth_info
            or not self._webhook_ids
        ):
            _LOGGER.error("Missing required configuration data")
            return self.async_abort(reason="missing_data")

        # Use device type from user selection or default to GDS
        device_type = self._device_type or DEVICE_TYPE_GNS_NAS

        # Use global function to generate unique ID
        unique_id = generate_unique_id(self._name, device_type, self._host, self._port)

        # Update unique ID
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        # Get username from auth_info (user input) or use default based on device type
        username = self._auth_info.get(CONF_USERNAME)
        if not username:
            username = (
                DEFAULT_USERNAME_GNS
                if device_type == DEVICE_TYPE_GNS_NAS
                else DEFAULT_USERNAME
            )

        data = {
            CONF_HOST: self._host,
            CONF_PORT: self._auth_info.get(CONF_PORT, DEFAULT_PORT),
            CONF_NAME: self._name,
            CONF_USERNAME: username,
            CONF_PASSWORD: self._auth_info[CONF_PASSWORD],
            CONF_DEVICE_TYPE: device_type,
            CONF_STATUS_WEBHOOK_ID: self._webhook_ids[CONF_STATUS_WEBHOOK_ID],
            CONF_COMMAND_WEBHOOK_ID: self._webhook_ids[CONF_COMMAND_WEBHOOK_ID],
            CONF_USE_HTTPS: self._use_https,  # Save protocol information
        }

        # Add RTSP configuration
        rtsp_enable = self._auth_info.get(CONF_RTSP_ENABLE, False)
        data[CONF_RTSP_ENABLE] = rtsp_enable

        # Only add RTSP username and password when RTSP is enabled
        if rtsp_enable:
            rtsp_username = self._auth_info.get(CONF_RTSP_USERNAME)
            rtsp_password = self._auth_info.get(CONF_RTSP_PASSWORD)

            # If no RTSP username or password provided, use default values
            if not rtsp_username:
                rtsp_username = DEFAULT_USERNAME
            if not rtsp_password:
                rtsp_password = self._auth_info[
                    CONF_PASSWORD
                ]  # Use device password as default RTSP password

            data[CONF_RTSP_USERNAME] = rtsp_username
            data[CONF_RTSP_PASSWORD] = rtsp_password

        _LOGGER.info("Creating config entry: %s, unique ID: %s", self._name, unique_id)
        return self.async_create_entry(
            title=self._name,
            description="This is a Grandstream device!",
            data=data,
        )

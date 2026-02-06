"""Constants for the Grandstream Home integration."""

DOMAIN = "grandstream_home"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PORT = "port"

# Webhook configuration items
CONF_STATUS_WEBHOOK_ID = "status_webhook_id"
CONF_COMMAND_WEBHOOK_ID = "command_webhook_id"

# RTSP configuration items
CONF_RTSP_ENABLE = "rtsp_enable"
CONF_RTSP_USERNAME = "rtsp_username"
CONF_RTSP_PASSWORD = "rtsp_password"

# Protocol configuration
CONF_USE_HTTPS = "use_https"

DEFAULT_PORT = 80
DEFAULT_USERNAME = "gdsha"
DEFAULT_USERNAME_GNS = "admin"

# Device Types
CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_GDS = "GDS"
DEVICE_TYPE_GNS_NAS = "GNS"

# API Content Types
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_FORM = "application/x-www-form-urlencoded"
ACCEPT_JSON = "application/json, text/plain, */*"

# API Header Names
HEADER_AUTHORIZATION = "Authorization"
HEADER_CONTENT_TYPE = "Content-Type"

# HTTP Methods
HTTP_METHOD_GET = "GET"
HTTP_METHOD_POST = "POST"

# API Timeout Settings (seconds)
GDS_TIMEOUT_CONNECT = 5
GDS_TIMEOUT_READ = 10
GDS_TIMEOUT_SOCKET_CHECK = 2

GNS_DEFAULT_TIMEOUT = 20

# Default Port Settings
DEFAULT_HTTP_PORT = 5000
DEFAULT_HTTPS_PORT = 5001

# Version information
INTEGRATION_VERSION = "1.0.0"

# Coordinator settings
COORDINATOR_UPDATE_INTERVAL = 10  # seconds - How often to poll device status
COORDINATOR_ERROR_THRESHOLD = 3  # Max consecutive errors before marking unavailable

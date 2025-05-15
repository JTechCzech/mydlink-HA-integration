"""Constants for the MyDlink integration."""

# Domain
DOMAIN = "mydlink"

# Configuration
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Services
SERVICE_TOGGLE = "toggle"

# Default values
DEFAULT_NAME = "MyDlink Smart Plug"

# Platform attributes
ATTR_DEVICE_ID = "device_id"
ATTR_DEVICE_NAME = "device_name"
ATTR_DEVICE_MODEL = "device_model"
ATTR_MAC = "mac"
ATTR_MYDLINK_ID = "mydlink_id"

# API Constants
API_OAUTH_BASE_URL = "https://api.auto.mydlink.com"
API_DEVICE_LIST_URL = "https://mp-eu-openapi.auto.mydlink.com/me/device/list"
API_DEVICE_INFO_URL = "https://mp-eu-openapi.auto.mydlink.com/me/device/info"
WS_URL = "wss://mp-eu-dcdca.auto.mydlink.com/SwitchCamera"

CLIENT_ID = "mydlinkuapandroid"
ANDROID_ID = "bd36a6c011f1287e"
OAUTH_SECRET = "5259311fa8cab90f09f2dc1e09d2d8ee"

# WebSocket client name
CLIENT_NAME = "Dlink2HomeAssistant"

# Device Types
TYPE_SWITCH = 16

# Data storage keys
DATA_API_CLIENT = "api_client"
DATA_DEVICES = "devices"

# Log messages
LOG_API_ERROR = "Chyba API: {}"
LOG_WS_ERROR = "WebSocket chyba: {}"
LOG_LOGIN_SUCCESS = "Přihlášení úspěšné"
LOG_DEVICE_FOUND = "Nalezeno zařízení: {}"

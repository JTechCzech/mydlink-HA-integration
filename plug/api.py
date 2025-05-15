"""API client for MyDlink API."""
import hashlib
import time
import urllib.parse
import json
import logging
from datetime import datetime, timedelta

import aiohttp
import httpx
import asyncio
from aiohttp import ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.util import Throttle

from .const import (
    API_OAUTH_BASE_URL,
    API_DEVICE_LIST_URL,
    API_DEVICE_INFO_URL,
    WS_URL,
    CLIENT_ID,
    ANDROID_ID,
    OAUTH_SECRET,
    CLIENT_NAME,
    TYPE_SWITCH,
    LOG_API_ERROR,
    LOG_WS_ERROR,
    LOG_LOGIN_SUCCESS,
    LOG_DEVICE_FOUND,
)

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=30)
TOKEN_EXPIRATION = timedelta(days=2)  # Konzervativně předpokládáme 2 dny místo 3
STORAGE_VERSION = 1
STORAGE_KEY = "mydlink_credentials"

class DlinkAuthError(Exception):
    """Exception for authentication errors."""

class DlinkApiError(Exception):
    """Exception for API errors."""

class WebSocketClient:
    """Asynchronní WebSocket klient."""

    def __init__(self, url, on_message, on_connect=None, on_disconnect=None):
        """Initialize WebSocket client."""
        self.url = url
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.ws = None
        self.task = None
        self.reconnect_task = None
        self.is_connected = False
        self.reconnect_interval = 10  # Sekundy pro opětovné připojení

    async def connect(self):
        """Connect to WebSocket server."""
        if self.is_connected:
            return

        try:
            self.ws = await asyncio.wait_for(
                aiohttp.ClientSession().ws_connect(self.url), 
                timeout=30
            )
            self.is_connected = True
            _LOGGER.debug("WebSocket připojen")
            
            if self.on_connect:
                await self.on_connect()
                
            self.task = asyncio.create_task(self._listen())
            
        except Exception as err:
            self.is_connected = False
            _LOGGER.error(f"Chyba při připojování k WebSocket: {err}")
            # Naplánovat opětovné připojení
            if self.reconnect_task is None or self.reconnect_task.done():
                self.reconnect_task = asyncio.create_task(self._schedule_reconnect())

    async def _schedule_reconnect(self):
        """Schedule reconnection after interval."""
        await asyncio.sleep(self.reconnect_interval)
        await self.connect()

    async def _listen(self):
        """Listen for WebSocket messages."""
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if self.on_message:
                        await self.on_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        except Exception as err:
            _LOGGER.error(f"Chyba při zpracování WebSocket: {err}")
        finally:
            self.is_connected = False
            if self.on_disconnect:
                await self.on_disconnect()
            # Naplánovat opětovné připojení
            if self.reconnect_task is None or self.reconnect_task.done():
                self.reconnect_task = asyncio.create_task(self._schedule_reconnect())

    async def send(self, message):
        """Send message to WebSocket server."""
        if not self.is_connected or self.ws is None:
            _LOGGER.error("WebSocket není připojen")
            return False
            
        try:
            await self.ws.send_str(message)
            return True
        except Exception as err:
            _LOGGER.error(f"Chyba při odesílání zprávy: {err}")
            self.is_connected = False
            return False

    async def close(self):
        """Close WebSocket connection."""
        if self.ws is not None:
            await self.ws.close()
            self.is_connected = False
        
        if self.task and not self.task.done():
            self.task.cancel()
            
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()

class MyDlinkAPI:
    """API Client class for MyDlink API."""

    def __init__(self, hass: HomeAssistant, email: str, password: str):
        """Initialize the API client."""
        self.hass = hass
        self.email = email
        self._password = password
        self._access_token = None
        self._token_expiration = None
        self._device_data = []
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{email}")
        self._ws = None
        self._ws_client_id = None
        self._ws_callback = {}  # Pro ukládání callbacků podle zařízení
        self._ws_lock = asyncio.Lock()
        self._session = None
        self._httpx_client = None  # HTTPX klient s HTTP/2

    async def async_load_stored_credentials(self):
        """Load stored credentials from storage."""
        stored_data = await self._store.async_load()
        if stored_data:
            self._access_token = stored_data.get("access_token")
            expiration_timestamp = stored_data.get("token_expiration")
            
            if expiration_timestamp:
                self._token_expiration = datetime.fromtimestamp(expiration_timestamp)
                
            self._device_data = stored_data.get("devices", [])
            
            # Zkontrolujeme platnost tokenu
            if self._token_expiration and datetime.now() < self._token_expiration:
                return True
                
        # Token neexistuje nebo je neplatný
        return False

    async def async_save_credentials(self):
        """Save credentials to storage."""
        data_to_save = {
            "access_token": self._access_token,
            "token_expiration": self._token_expiration.timestamp() if self._token_expiration else None,
            "devices": self._device_data
        }
        await self._store.async_save(data_to_save)

    def _md5_hashing(self, data):
        """Create MD5 hash from data."""
        return hashlib.md5(data.encode('utf-8')).hexdigest()

    def _create_login_query(self):
        """Create login query with signature."""
        hashed_password = self._md5_hashing(self._password)
        timestamp = str(int(time.time()))

        params = {
            'client_id': CLIENT_ID,
            'redirect_uri': 'https://mydlink.com',
            'user_name': self.email,
            'password': hashed_password,
            'response_type': 'token',
            'timestamp': timestamp,
            'uc_id': ANDROID_ID,
            'uc_name': 'HomeAssistant'
        }
        
        login_query = f"/oauth/authorize2?{urllib.parse.urlencode(params)}"
        signature = self._md5_hashing(login_query + OAUTH_SECRET)
        return f"{login_query}&sig={signature}"

    async def async_login(self):
        """Perform login to MyDlink API using HTTP/2."""
        try:
            # Vytvoření finální cesty s parametry
            final_path = self._create_login_query()
            
            headers = {
                'Host': 'api.auto.mydlink.com',
                'x-md-os-type': 'android',
                'x-md-os-ver': '7.0',
                'x-md-app-ver': '02.12.02.496',
                'x-md-lang': 'en',
                'Accept-Encoding': 'gzip',
                'User-Agent': 'okhttp/4.8.0',
            }
            
            # Vytvoření HTTPX klienta s HTTP/2 podporou, pokud neexistuje
            if self._httpx_client is None:
                self._httpx_client = httpx.AsyncClient(http2=True)
                
            # Použití HTTPX klienta s HTTP/2
            response = await self._httpx_client.get(
                f"{API_OAUTH_BASE_URL}{final_path}", 
                headers=headers,
                follow_redirects=False  # Zakážeme automatické přesměrování
            )
            
            if 'location' not in response.headers:
                _LOGGER.error("Nepodařilo se přihlásit, chybí hlavička Location")
                raise DlinkAuthError("Přihlášení selhalo - chybí přesměrování")
                
            # Získáme token z přesměrování
            location_url = response.headers['location']
            parsed_url = urllib.parse.urlparse(location_url)
            query_params = urllib.parse.parse_qs(parsed_url.fragment or parsed_url.query)
            
            if 'access_token' not in query_params:
                _LOGGER.error("Přihlášení selhalo, token nenalezen v odpovědi")
                raise DlinkAuthError("Přihlášení selhalo - token nenalezen")
                
            self._access_token = query_params['access_token'][0]
            self._token_expiration = datetime.now() + TOKEN_EXPIRATION
            
            _LOGGER.info(LOG_LOGIN_SUCCESS)
            
            # Po získání tokenu načteme zařízení
            await self.async_get_device_list()
            
            # Uložíme přihlašovací údaje
            await self.async_save_credentials()
            
            return True
            
        except Exception as err:
            _LOGGER.error(LOG_API_ERROR.format(str(err)))
            raise DlinkAuthError(f"Chyba při přihlašování: {str(err)}")

    async def async_get_device_tokens(self, mydlink_ids):
        """Get device tokens for specific mydlink IDs using POST request."""
        if not self._access_token:
            _LOGGER.error("Chybí access token, je nutné se přihlásit")
            return {}
            
        try:
            # Příprava seznamu zařízení pro požadavek
            device_list = [{"mydlink_id": mydlink_id} for mydlink_id in mydlink_ids]
            payload = {"data": device_list}
            
            headers = {
                'Host': 'mp-eu-openapi.auto.mydlink.com',
                'x-md-os-type': 'android',
                'x-md-os-ver': '7.0',
                'x-md-app-ver': '02.12.02.496',
                'x-md-lang': 'cs',
                'Accept-Encoding': 'gzip',
                'User-Agent': 'okhttp/4.8.0',
                'Content-Type': 'application/json',
            }
            
            url = f"{API_DEVICE_INFO_URL}?access_token={self._access_token}"
            
            # Použijeme HTTPX klienta, pokud je k dispozici
            if self._httpx_client:
                response = await self._httpx_client.post(
                    url, 
                    headers=headers,
                    json=payload
                )
                
                if response.status_code != 200:
                    _LOGGER.error(f"Chyba při získávání device tokenů: {response.status_code}")
                    return {}
                    
                result = response.json()
            else:
                # Fallback na aiohttp
                if self._session is None:
                    self._session = aiohttp.ClientSession()
                    
                async with self._session.post(
                    url, 
                    headers=headers,
                    json=payload
                ) as response:
                    if response.status != 200:
                        _LOGGER.error(f"Chyba při získávání device tokenů: {response.status}")
                        return {}
                        
                    result = await response.json()
            
            if not result.get("data"):
                _LOGGER.error("Žádná zařízení nebyla nalezena v odpovědi")
                return {}
                
            # Zpracujeme device tokeny z odpovědi
            device_tokens = {}
            for device in result["data"]:
                mydlink_id = device.get('mydlink_id', '')
                device_token = device.get('device_token', '')
                
                # Pokud device_token není v odpovědi, použijeme MAC adresu a pevnou hodnotu jako fallback
                if not device_token:
                    mac = device.get('mac', '')
                    device_token = f"{mac}-14e97fa5-3ce5-22b0-100d-5a971f4c226b"
                    _LOGGER.warning(f"Device token nenalezen pro {device.get('device_name')}, používám fallback")
                
                if mydlink_id:
                    device_tokens[mydlink_id] = {
                        'device_token': device_token,
                        'device_name': device.get('device_name', ''),
                        'mac': device.get('mac', ''),
                        'device_model': device.get('device_model', '')
                    }
                    _LOGGER.debug(f"Zařízení {device.get('device_name')} ({mydlink_id}) má device_token: {device_token}")
            
            return device_tokens
                
        except Exception as err:
            _LOGGER.error(f"Chyba při získávání device tokenů: {str(err)}")
            return {}

    async def async_get_device_list(self):
        """Get device list from API."""
        if not self._access_token:
            _LOGGER.error("Chybí access token, je nutné se přihlásit")
            return False
            
        try:
            # Použijeme HTTPX klienta, pokud je k dispozici, jinak vytvoříme aiohttp session
            if self._httpx_client:
                headers = {
                    'Host': 'mp-eu-openapi.auto.mydlink.com',
                    'x-md-os-type': 'android',
                    'x-md-os-ver': '7.0',
                    'x-md-app-ver': '02.12.02.496',
                    'x-md-lang': 'cs',
                    'Accept-Encoding': 'gzip',
                    'User-Agent': 'okhttp/4.8.0',
                }
                
                url = f"{API_DEVICE_LIST_URL}?access_token={self._access_token}"
                
                response = await self._httpx_client.get(url, headers=headers)
                if response.status_code != 200:
                    _LOGGER.error(f"Chyba při získávání zařízení: {response.status_code}")
                    return False
                    
                result = response.json()
            else:
                # Fallback na aiohttp
                if self._session is None:
                    self._session = aiohttp.ClientSession()
                    
                headers = {
                    'Host': 'mp-eu-openapi.auto.mydlink.com',
                    'x-md-os-type': 'android',
                    'x-md-os-ver': '7.0',
                    'x-md-app-ver': '02.12.02.496',
                    'x-md-lang': 'cs',
                    'Accept-Encoding': 'gzip',
                    'User-Agent': 'okhttp/4.8.0',
                }
                
                url = f"{API_DEVICE_LIST_URL}?access_token={self._access_token}"
                
                async with self._session.get(url, headers=headers) as response:
                    if response.status != 200:
                        _LOGGER.error(f"Chyba při získávání zařízení: {response.status}")
                        return False
                        
                    result = await response.json()
                
            if not result.get("data"):
                _LOGGER.error("Žádná zařízení nebyla nalezena")
                return False
                
            # Získáme mydlink_id všech zařízení
            mydlink_ids = [device.get('mydlink_id') for device in result["data"] if device.get('mydlink_id')]
            
            # Získáme device tokeny pro všechna zařízení pomocí POST
            device_tokens = {}
            if mydlink_ids:
                device_tokens = await self.async_get_device_tokens(mydlink_ids)
                
            # Zpracování seznamu zařízení
            self._device_data = []
            for device in result["data"]:
                mydlink_id = device.get('mydlink_id', '')
                
                # Pokud máme device token z POST požadavku, použijeme ho
                device_token = ''
                if mydlink_id in device_tokens:
                    device_token = device_tokens[mydlink_id].get('device_token', '')
                
                # Pokud device_token není k dispozici, použijeme fallback
                if not device_token:
                    mac = device.get('mac', '')
                    device_token = f"{mac}-14e97fa5-3ce5-22b0-100d-5a971f4c226b"
                    _LOGGER.warning(f"Device token nenalezen pro {device.get('device_name')}, používám fallback")
                
                device_info = {
                    'device_name': device.get('device_name', ''),
                    'device_model': device.get('device_model', ''),
                    'mac': device.get('mac', ''),
                    'mydlink_id': mydlink_id,
                    'device_token': device_token
                }
                self._device_data.append(device_info)
                _LOGGER.info(LOG_DEVICE_FOUND.format(device_info['device_name']))
                _LOGGER.debug(f"Zařízení {device_info['device_name']} má device_token: {device_token}")
            
            return True
                
        except Exception as err:
            _LOGGER.error(LOG_API_ERROR.format(str(err)))
            return False

    @property
    def devices(self):
        """Return list of available devices."""
        return self._device_data

    def get_device_by_mac(self, mac):
        """Get device info by MAC address."""
        for device in self._device_data:
            if device["mac"] == mac:
                return device
        return None
        
    def get_device_by_mydlink_id(self, mydlink_id):
        """Get device info by mydlink_id."""
        for device in self._device_data:
            if device["mydlink_id"] == mydlink_id:
                return device
        return None

    # WebSocket metody pro ovládání zařízení
    async def _on_ws_message(self, message):
        """Handle incoming websocket messages."""
        try:
            msg = json.loads(message)
            _LOGGER.debug("WS zpráva: %s", msg)
            
            # Po přihlášení získáme client_id
            if msg.get('command') == 'sign_in' and msg.get('code') == 0 and msg.get('client_id'):
                self._ws_client_id = msg['client_id']
                _LOGGER.info("WS přihlášení OK, client_id: %s", self._ws_client_id)
            
            # Odpověď na get_setting - obsahuje stav zařízení
            if msg.get('command') == 'get_setting' and isinstance(msg.get('setting'), list):
                device_id = msg.get('device_id')
                if device_id and device_id in self._ws_callback:
                    type16 = next((s for s in msg['setting'] if s.get('type') == TYPE_SWITCH), None)
                    if type16 and isinstance(type16.get('metadata', {}).get('value'), int):
                        state = type16['metadata']['value'] == 1
                        callback = self._ws_callback[device_id]
                        # Zavolat callback asynchronně
                        self.hass.async_create_task(self._execute_callback(callback, state))
            
            # Změna stavu zásuvky (event)
            if msg.get('command') == 'event' and msg.get('event', {}).get('metadata', {}).get('type') == TYPE_SWITCH:
                device_id = msg.get('device_id')
                if device_id and device_id in self._ws_callback:
                    value = msg['event']['metadata']['value']
                    state = value == 1
                    callback = self._ws_callback[device_id]
                    # Zavolat callback asynchronně
                    self.hass.async_create_task(self._execute_callback(callback, state))
            
            # Neplatný owner_id - potřebujeme obnovit token
            if msg.get('command') == 'sign_in' and msg.get('code') == 82 and msg.get('message') == 'incorrect owner-id':
                _LOGGER.warning("Neplatný owner-id, je potřeba obnovit token")
                self.hass.async_create_task(self.async_login())

        except Exception as err:
            _LOGGER.error("Chyba při zpracování WS zprávy: %s", str(err))
    
    async def _execute_callback(self, callback, state):
        """Execute callback safely."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(state)
            else:
                callback(state)
        except Exception as err:
            _LOGGER.error("Chyba při provádění callbacku: %s", str(err))

    async def _on_ws_connect(self):
        """Handle websocket connection."""
        _LOGGER.info("WS připojen, odesílám sign_in...")
        await self._send_sign_in()
        
    async def _on_ws_disconnect(self):
        """Handle websocket disconnection."""
        _LOGGER.info("WS odpojen")
        self._ws_client_id = None

    async def _send_sign_in(self):
        """Send sign in message to websocket."""
        if not self._ws or not self._ws.is_connected:
            _LOGGER.error("WS není připojen")
            return

        sign_in_seq = int(time.time() * 1000) % 10000
        
        message = {
            "scope": [
                "user", "device:status", "device:control", 
                "viewing", "photo", "policy", "client", "event"
            ],
            "client_name": CLIENT_NAME,
            "command": "sign_in",
            "role": "client_agent",
            "owner_token": self._access_token,
            "sequence_id": sign_in_seq,
            "timestamp": int(time.time()),
            "owner_id": self.email
        }
        
        await self._ws.send(json.dumps(message))

    async def async_ensure_ws_connected(self):
        """Ensure websocket is connected."""
        async with self._ws_lock:
            if self._ws and self._ws.is_connected and self._ws_client_id:
                return True

            # Ukončit existující spojení
            if self._ws:
                await self._ws.close()
                self._ws = None

            # Resetovat _ws_client_id
            self._ws_client_id = None
            
            # Vytvořit nové WebSocket spojení
            self._ws = WebSocketClient(
                WS_URL,
                self._on_ws_message,
                self._on_ws_connect,
                self._on_ws_disconnect
            )
            
            await self._ws.connect()
            
            # Počkat na přihlášení a získání client_id
            for _ in range(20):  # 10 sekund (20 x 0.5s)
                if self._ws_client_id:
                    return True
                await asyncio.sleep(0.5)
                
            _LOGGER.error("Nepodařilo se připojit k WS serveru nebo získat client_id")
            return False

    async def async_get_device_state(self, device_id, callback=None):
        """Get device state using websocket."""
        if callback:
            self._ws_callback[device_id] = callback
            
        if not await self.async_ensure_ws_connected():
            return False
            
        if not self._ws_client_id:
            _LOGGER.error("Chybí WS client_id")
            return False
            
        # Najít zařízení podle ID
        device = self.get_device_by_mac(device_id)
        if not device:
            _LOGGER.error("Zařízení s ID %s nebylo nalezeno", device_id)
            return False
            
        get_setting_seq = int(time.time() * 1000) % 10000
        
        message = {
            "device_id": device_id,
            "client_id": self._ws_client_id,
            "command": "get_setting",
            "setting": [
                {"name": "device", "type": TYPE_SWITCH, "uid": 0, "idx": 0}
            ],
            "sequence_id": get_setting_seq,
            "timestamp": int(time.time()),
            "device_token": device.get("device_token")
        }
        
        if self._ws and self._ws.is_connected:
            success = await self._ws.send(json.dumps(message))
            if success:
                _LOGGER.debug("Odesílám get_setting pro %s", device_id)
                return True
            else:
                _LOGGER.error("Chyba při odesílání get_setting")
                return False
        else:
            _LOGGER.error("WS není připojen")
            return False

    async def async_set_device_state(self, device_id, state):
        """Set device state using websocket."""
        if not await self.async_ensure_ws_connected():
            return False
            
        if not self._ws_client_id:
            _LOGGER.error("Chybí WS client_id")
            return False
            
        # Najít zařízení podle ID
        device = self.get_device_by_mac(device_id)
        if not device:
            _LOGGER.error("Zařízení s ID %s nebylo nalezeno", device_id)
            return False
            
        set_setting_seq = int(time.time() * 1000) % 10000
        value = 1 if state else 0
        
        message = {
            "device_id": device_id,
            "client_id": self._ws_client_id,
            "command": "set_setting",
            "setting": [
                {
                    "metadata": {"value": value},
                    "type": TYPE_SWITCH,
                    "uid": 0,
                    "idx": 0
                }
            ],
            "sequence_id": set_setting_seq,
            "timestamp": int(time.time()),
            "device_token": device.get("device_token")
        }
        
        if self._ws and self._ws.is_connected:
            success = await self._ws.send(json.dumps(message))
            if success:
                _LOGGER.debug("Odesílám set_setting (%s) pro %s", value, device_id)
                return True
            else:
                _LOGGER.error("Chyba při odesílání set_setting")
                return False
        else:
            _LOGGER.error("WS není připojen")
            return False

    async def async_shutdown(self):
        """Shut down the API client asynchronously."""
        if self._ws:
            await self._ws.close()
            self._ws = None
            
        if self._session:
            await self._session.close()
            self._session = None
            
        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None

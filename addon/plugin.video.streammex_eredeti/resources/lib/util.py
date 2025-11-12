import base64
import gzip
import io
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import closing

try:  # Kodi runtime
    import xbmc  # type: ignore
except ImportError:  # pragma: no cover - allow local execution
    xbmc = None  # type: ignore

try:  # Kodi runtime
    import xbmcaddon  # type: ignore
except ImportError:  # pragma: no cover - allow local execution
    xbmcaddon = None  # type: ignore

try:  # Kodi runtime
    import xbmcvfs  # type: ignore
except ImportError:  # pragma: no cover - allow local execution
    xbmcvfs = None  # type: ignore

LOG_DEBUG = 0
LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3
LOG_LEVEL_MAP = {
    LOG_DEBUG: getattr(xbmc, "LOGDEBUG", LOG_DEBUG),
    LOG_INFO: getattr(xbmc, "LOGINFO", LOG_INFO),
    LOG_WARNING: getattr(xbmc, "LOGWARNING", LOG_WARNING),
    LOG_ERROR: getattr(xbmc, "LOGERROR", LOG_ERROR),
}
LOG_PREFIX = "[StreamMex] "


def _log(message, level=LOG_INFO):
    payload = f"{LOG_PREFIX}{message}"
    if xbmc:
        xbmc.log(payload, level=LOG_LEVEL_MAP.get(level, LOG_INFO))
    else:  # pragma: no cover - local execution helper
        print(payload)


def log_info(message):
    _log(message, LOG_INFO)


def log_warning(message):
    _log(message, LOG_WARNING)


def log_error(message):
    _log(message, LOG_ERROR)


def log_debug(message, enabled=False):
    if enabled:
        _log(message, LOG_DEBUG)


def now_ts():
    return int(time.time())


def translate_path(path):
    if xbmcvfs and hasattr(xbmcvfs, "translatePath"):
        return xbmcvfs.translatePath(path)
    return os.path.expanduser(path)


def ensure_directory(path):
    if xbmcvfs:
        if not xbmcvfs.exists(path):
            xbmcvfs.mkdirs(path)
    else:  # pragma: no cover
        os.makedirs(path, exist_ok=True)


def file_exists(path):
    if xbmcvfs:
        return xbmcvfs.exists(path)
    return os.path.exists(path)


def read_text_file(path, encoding="utf-8"):
    if not file_exists(path):
        return None
    if xbmcvfs:
        fh = xbmcvfs.File(path, "r")
        try:
            data = fh.read()
        finally:
            fh.close()
        return data
    with open(path, "r", encoding=encoding) as fh:  # pragma: no cover
        return fh.read()


def write_text_file(path, data, encoding="utf-8"):
    directory = os.path.dirname(path)
    if directory and not file_exists(directory):
        ensure_directory(directory)
    if xbmcvfs:
        fh = xbmcvfs.File(path, "w")
        try:
            fh.write(data)
        finally:
            fh.close()
    else:  # pragma: no cover
        with open(path, "w", encoding=encoding) as fh:
            fh.write(data)


def write_binary_file(path, data):
    directory = os.path.dirname(path)
    if directory and not file_exists(directory):
        ensure_directory(directory)
    if xbmcvfs:
        fh = xbmcvfs.File(path, "wb")
        try:
            fh.write(data)
        finally:
            fh.close()
    else:  # pragma: no cover
        with open(path, "wb") as fh:
            fh.write(data)


def read_json_file(path, default=None):
    if default is None:
        default = {}
    content = read_text_file(path)
    if content is None:
        return default
    try:
        return json.loads(content)
    except ValueError:
        return default


def write_json_file(path, payload):
    write_text_file(path, json.dumps(payload, indent=2, sort_keys=True))


def get_setting(addon, key, default=None):
    if not addon:
        return default
    try:
        value = addon.getSetting(key)
    except Exception:  # pragma: no cover - safety for testing
        return default
    if value == "":
        return default
    return value


def get_bool_setting(addon, key, default=False):
    value = get_setting(addon, key, None)
    if value is None:
        return default
    return value.lower() == "true"


def get_float_setting(addon, key, default=0.0):
    value = get_setting(addon, key, None)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def http_request(url, headers=None, timeout=30):
    request_headers = headers or {}
    req = urllib.request.Request(url, headers=request_headers)
    try:
        with closing(urllib.request.urlopen(req, timeout=timeout)) as response:
            status = getattr(response, "status", response.getcode())
            data = response.read()
            response_headers = dict(response.getheaders())
            return status, data, response_headers
    except urllib.error.HTTPError as err:
        return err.code, err.read() if err.fp else b"", dict(err.headers or {})
    except urllib.error.URLError:
        return None, b"", {}


def maybe_decompress(data, headers, source_url=""):
    encoding = (headers.get("Content-Encoding") or "").lower()
    if encoding in {"gzip", "x-gzip"}:
        return gzip.GzipFile(fileobj=io.BytesIO(data)).read()
    if source_url.lower().endswith(".gz"):
        return gzip.GzipFile(fileobj=io.BytesIO(data)).read()
    return data


def decode_base64(data):
    return base64.b64decode(data)


def get_addon(addon_id):
    if xbmcaddon:
        return xbmcaddon.Addon(addon_id)
    raise RuntimeError("Kodi addon API unavailable")


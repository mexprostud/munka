import os, re, json, base64, zlib
import urllib.request, ssl
from datetime import datetime

from . import util
from . import m3u
from . import epg  # csak a jelenlét miatt importáljuk, a fájlírást itt végezzük

PASTEBIN_DIRECT = "https://pastebin.com/raw/k1gwxFYJ"

HTTPS_PATTERN = re.compile(r"https?://[^\s'\"\\)]+", re.I)

def _http_get(url, timeout=30):
    """Visszaad: (status, data_bytes, headers_dict) – util.http_request ha van, különben urllib."""
    try:
        if hasattr(util, "http_request"):
            status, data, headers = util.http_request(url, timeout=timeout)
            return int(status or 0), data or b"", headers or {}
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            data = r.read()
            headers = dict(r.headers.items()) if getattr(r, "headers", None) else {}
            return getattr(r, "status", 200) or 200, data, headers
    except Exception as e:
        try:
            util.log_warning(f"[StreamMex] HTTP error {url}: {e}")
        except Exception:
            pass
        return 0, b"", {}

def _write_text(path, text):
    try:
        if hasattr(util, "write_text_file"):
            util.write_text_file(path, text)
            return
    except Exception:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def _read_text(path):
    try:
        if hasattr(util, "read_text_file"):
            return util.read_text_file(path) or ""
    except Exception:
        pass
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def _safe_b64_blocks(raw_bytes):
    """Kinyer b'...'/b"..." blokkokat és próbálja base64→(zlib?) dekódolni; visszaadja a dekódolt szövegek listáját."""
    out = []
    # minták: b'....' vagy b"...."
    for m in re.finditer(rb"b'([^']+)'|b\"([^\"]+)\"", raw_bytes):
        b = m.group(1) or m.group(2)
        if not b:
            continue
        s = b.replace(b"\n", b"").replace(b"\r", b"").strip()
        # base64 padding pótlás
        pad = (-len(s)) % 4
        if pad:
            s = s + b"=" * pad
        for attempt in (s,):
            try:
                dec = base64.b64decode(attempt, validate=False)
            except Exception:
                continue
            # lehet zlib-bel csomagolt
            try:
                dec2 = zlib.decompress(dec)
                dec = dec2
            except Exception:
                pass
            try:
                text = dec.decode("utf-8", "ignore")
            except Exception:
                text = ""
            if text:
                out.append(text)
    return out

def _find_pastebin_urls(text):
    urls = []
    for u in HTTPS_PATTERN.findall(text or ""):
        if "pastebin.com" in u:
            if "/raw/" not in u:
                # átfordítás raw formára
                m = re.search(r"pastebin\.com/([A-Za-z0-9]+)", u)
                if m:
                    u = f"https://pastebin.com/raw/{m.group(1)}"
            urls.append(u)
    # egyedisítés, sorrend megtartásával
    seen = set(); uniq = []
    for u in urls:
        if u not in seen:
            uniq.append(u); seen.add(u)
    return uniq

def _parse_m3u_channels(text):
    """Nagyon egyszerű M3U parser: #EXTINF sorból név, következő URL sorból stream."""
    chans = []
    lines = (text or "").splitlines()
    last_name = None
    for ln in lines:
        if ln.startswith("#EXTINF"):
            # név a vessző utáni rész
            name = ln.split(",", 1)[-1].strip() if "," in ln else None
            last_name = name or None
        elif ln and not ln.startswith("#"):
            url = ln.strip()
            if last_name and url.startswith(("http://", "https://")):
                chans.append({"name": last_name, "url": url})
                last_name = None
    return chans

class PlaylistCoordinator:
    def __init__(self, addon):
        self.addon = addon
        self.debug_logging = util.get_bool_setting(addon, 'debug_logging', False)

        profile = util.translate_path(addon.getAddonInfo('profile')) if addon else ""
        if profile:
            util.ensure_directory(profile)
        self.profile_path = profile or ""

        # Alap kimenetek (felhasználói beállítás felülírhatja)
        m3u_out = util.get_setting(addon, 'output_m3u_path', None)
        epg_out = util.get_setting(addon, 'output_epg_path', None)
        self.playlist_path = util.translate_path(m3u_out) if m3u_out else os.path.join(self.profile_path, "Hungary.m3u")
        self.epg_path = util.translate_path(epg_out) if epg_out else os.path.join(self.profile_path, "Hungary.xml")

    # Default.py kompat: a plugin ezt hívja
    def get_channels(self, force=False, channel_type=None, **kwargs):
        info = self.ensure_assets(force=force)
        return info.get("channels") or []

    def ensure_assets(self, force=False):
        """Index letöltés (Pastebin → Bee), Hungary szűrés, M3U letöltés/összefűzés, EPG váz, csatornalista vissza."""
        # 1) próbáljuk közvetlen Pastebin RAW-t
        entries = self._fetch_index_from_pastebin_direct()
        # 2) ha nem megy, Bee default.py-ból nyerjünk új RAW linket
        if not entries:
            entries = self._fetch_index_from_bee()
        if not entries:
            util.log_error("[StreamMex] Playlist index could not be retrieved")
            return {"channels": []}

        hun_entries = self._filter_hungary(entries)
        if not hun_entries:
            util.log_warning("[StreamMex] No Hungary entries in index")
            return {"channels": []}

        # M3U-k letöltése és összefűzése
        combined_lines = ["#EXTM3U"]
        channels = []
        for e in hun_entries:
            url = e.get("url") or ""
            if not url.lower().endswith((".m3u", ".m3u8")):
                continue
            status, data, _ = _http_get(url, timeout=45)
            if status != 200 or not data:
                util.log_warning(f"[StreamMex] M3U fetch failed: {url} (status {status})")
                continue
            txt = data.decode("utf-8", "ignore")
            # összeépítés a kimeneti fájlhoz
            part = "\n".join([ln for ln in txt.splitlines() if not ln.strip().upper().startswith("#EXTM3U")])
            combined_lines.append(part)
            # csatornák a plugin saját listájához
            channels.extend(_parse_m3u_channels(txt))

        # M3U mentés
        m3u_text = "\n".join(combined_lines) + "\n"
        _write_text(self.playlist_path, m3u_text)
        util.log_info(f"[StreamMex] M3U written -> {self.playlist_path} ({len(channels)} channels)")

        # minimál XMLTV váz (ha nincs valós EPG aggregátor)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<tv generator-info-name="StreamMex">\n'
            f'  <!-- generated: {datetime.utcnow().isoformat()}Z -->\n'
            '</tv>\n'
        )
        _write_text(self.epg_path, xml)
        util.log_info(f"[StreamMex] XMLTV written -> {self.epg_path}")

        # duplikátum szűrés név+url alapján
        seen = set(); uniq = []
        for c in channels:
            key = (c.get("name"), c.get("url"))
            if key in seen: 
                continue
            seen.add(key); uniq.append(c)
        return {"channels": uniq}

    def _fetch_index_from_pastebin_direct(self):
        status, data, _ = _http_get(PASTEBIN_DIRECT, timeout=30)
        if status != 200 or not data:
            return []
        try:
            payload = json.loads(data.decode("utf-8", "ignore"))
        except Exception as e:
            util.log_warning(f"[StreamMex] Pastebin JSON parse failed: {e}")
            return []
        if isinstance(payload, list):
            out = []
            for e in payload:
                if not isinstance(e, dict):
                    continue
                name = e.get("name") or e.get("title") or e.get("label") or ""
                link = e.get("url") or e.get("link") or e.get("stream") or ""
                if link:
                    out.append({"name": name, "url": link})
            if out:
                util.log_info(f"[StreamMex] Pastebin direct OK: {len(out)} entries")
            return out
        return []

    def _fetch_index_from_bee(self):
        """Bee default.py deobfuszkálása: Pastebin RAW link(ek) kinyerése és onnan index JSON letöltése."""
        bee_path = "/storage/.kodi/addons/plugin.video.playlistbee/default.py"
        raw = _read_text(bee_path)
        if not raw:
            util.log_warning("[StreamMex] Bee default.py not readable")
            return []

        # 1) közvetlen linkek a plain textben
        candidates = _find_pastebin_urls(raw)

        # 2) base64 blokkokban levő exec-kód dekódolása és újabb linkek kinyerése
        decoded_texts = _safe_b64_blocks(raw.encode("utf-8", "ignore"))
        for t in decoded_texts:
            candidates.extend(_find_pastebin_urls(t))

        # egyedisítés
        seen = set(); uniq = []
        for u in candidates:
            if u not in seen:
                uniq.append(u); seen.add(u)

        for url in uniq:
            status, data, _ = _http_get(url, timeout=30)
            if status != 200 or not data:
                continue
            try:
                payload = json.loads(data.decode("utf-8", "ignore"))
            except Exception:
                continue
            if isinstance(payload, list):
                out = []
                for e in payload:
                    if not isinstance(e, dict):
                        continue
                    name = e.get("name") or e.get("title") or e.get("label") or ""
                    link = e.get("url") or e.get("link") or e.get("stream") or ""
                    if link:
                        out.append({"name": name, "url": link})
                if out:
                    util.log_info(f"[StreamMex] Bee→Pastebin OK: {len(out)} entries")
                    return out
        util.log_warning("[StreamMex] Bee→Pastebin: no valid index found")
        return []

    def _filter_hungary(self, entries):
        """‘hun’/‘hungary’ szűrő névben vagy URL-ben (kis/nagybetű-független)."""
        out = []
        for e in entries or []:
            if not isinstance(e, dict): 
                continue
            blob = (e.get("name") or "") + " " + (e.get("url") or "")
            if re.search(r"(?:\bhun\b|hungary)", blob, flags=re.I):
                out.append(e)
        return out

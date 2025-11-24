# -*- coding: utf-8 -*-
import os
import re
import json
import base64
import zlib
import urllib.request
import urllib.parse
import ssl
import unicodedata
from datetime import datetime
import xml.etree.ElementTree as ET

from . import util
from . import m3u_builder
from . import epg_manager  # EPG modul

PASTEBIN_DIRECT = "https://pastebin.com/raw/k1gwxFYJ"

# TODO: később állítsd be ide a saját GitHub / Cloudflare Worker URL-eket, ha a szerver addon
# távolról is publikálja az M3U/EPG-t a kliens addonoknak.
REMOTE_M3U_UPLOAD_URL = ""  # pl. "https://raw.githubusercontent.com/mexprostud/valami/master/Hungary.m3u"
REMOTE_EPG_UPLOAD_URL = ""  # pl. "https://raw.githubusercontent.com/mexprostud/valami/master/Hungary.xml"

HTTPS_PATTERN = re.compile(r"https?://[^\s'\"\\)]+", re.I)

# --- Előre definiált csatorna csomagok (szűréshez) ---

CHANNEL_PACKAGES = {
    # Alap magyar közszolgálati + pár tipikus csatorna – később bővíthető
    "core_hu": {
        "label": "Alap magyar (közszolgálati alap)",
        "channels": [
            "M1", "M1 HD",
            "M2", "M2 HD",
            "M4 Sport", "M4 Sport HD",
            "Duna", "Duna HD",
            "Duna World",
            "RTL Klub", "RTL",
            "TV2",
        ],
    },
}


def _strip_accents(s):
    """
    Ékezetek eltávolítása: í -> i, ő -> o, ű -> u stb.
    Így a csatornanevek összehasonlításánál az i/í probléma megszűnik.
    """
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(s))
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _http_get(url, timeout=10):
    """Visszaad: (status, data_bytes, headers_dict) – util.http_request ha van, különben urllib."""
    try:
        if hasattr(util, "http_request"):
            status, data, headers = util.http_request(url, timeout=timeout)
            return int(status or 0), data or b"", headers or {}
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
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
    for m in re.finditer(rb"b'([^']+)'|b\"([^\"]+)\"", raw_bytes):
        b = m.group(1) or m.group(2)
        if not b:
            continue
        s = b.replace(b"\n", b"").replace(b"\r", b"").strip()
        pad = (-len(s)) % 4
        if pad:
            s = s + b"=" * pad
        try:
            dec = base64.b64decode(s, validate=False)
        except Exception:
            continue
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
        u = u.decode("utf-8", "ignore") if isinstance(u, bytes) else str(u)
        if "pastebin.com" in u:
            if "/raw/" not in u:
                m = re.search(r"pastebin\.com/([A-Za-z0-9]+)", u)
                if m:
                    u = f"https://pastebin.com/raw/{m.group(1)}"
            urls.append(u)
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


def _parse_m3u_channels(text):
    """
    M3U parser, amely kezeli:
      - #EXTINF:-1 tvg-id="..." tvg-logo="..." group-title="...",Név
      - #KODIPROP:inputstream=...
      - tetszőleges URL-t (http(s), plugin://, stb.)

    Kimenet: lista dict-ekkel:
      {
        "name": "...",
        "url": "...",
        "tvg_id": "...",
        "group_title": "...",
        "tvg_logo": "...",
        "kodiprops": ["inputstream=...", "mimetype=..."],
      }
    """
    chans = []
    if not text:
        return chans

    lines = text.splitlines()
    current = None  # aktuális #EXTINF blokk metaadatai

    def _flush_current_with_url(url_str):
        nonlocal current
        url = (url_str or "").strip()
        if not url:
            current = None
            return
        if current is None:
            # Nincs #EXTINF előtte – minimal csatorna
            ch = {"name": url, "url": url}
        else:
            name = (current.get("name") or current.get("tvg_name") or url).strip()
            ch = {
                "name": name,
                "url": url,
            }
            for key in ("tvg_id", "tvg_logo", "group_title", "tvg_name"):
                val = current.get(key)
                if val:
                    ch[key] = val
            props = current.get("kodiprops") or []
            if props:
                ch["kodiprops"] = list(props)
        chans.append(ch)
        current = None

    for ln in lines:
        s = (ln or "").rstrip("\r\n")
        if not s.strip():
            continue

        if s.startswith("#EXTINF"):
            # Új EXTINF blokk
            # Formátum: #EXTINF:-1 tvg-id="..." ...,Display Name
            m = re.match(r"#EXTINF:-?\d+\s*(.*?),(.*)$", s)
            attrs_part = ""
            name_part = ""
            if m:
                attrs_part = (m.group(1) or "").strip()
                name_part = (m.group(2) or "").strip()
            else:
                # fallback: vessző utáni rész név
                if "," in s:
                    name_part = s.split(",", 1)[-1].strip()

            current = {
                "name": name_part or "",
                "tvg_id": "",
                "tvg_name": "",
                "tvg_logo": "",
                "group_title": "",
                "kodiprops": [],
            }

            if attrs_part:
                # tvg-id="...", tvg-name="...", group-title="...", tvg-logo="..."
                for attr, key in [
                    ("tvg-id", "tvg_id"),
                    ("tvg-name", "tvg_name"),
                    ("tvg-logo", "tvg_logo"),
                    ("group-title", "group_title"),
                ]:
                    m_attr = re.search(r'%s="([^"]*)"' % re.escape(attr), attrs_part)
                    if m_attr:
                        current[key] = m_attr.group(1).strip()

        elif s.startswith("#KODIPROP:"):
            if current is None:
                continue
            prop = s[len("#KODIPROP:") :].strip()
            if prop:
                current.setdefault("kodiprops", []).append(prop)

        elif s.startswith("#"):
            # Egyéb komment – ignoráljuk
            continue

        else:
            # URL sor
            _flush_current_with_url(s.strip())

    return chans


class PlaylistCoordinator:
    def __init__(self, addon):
        self.addon = addon
        self.debug_logging = util.get_bool_setting(addon, 'debug_logging', False)
        self.mag_support = util.get_bool_setting(addon, 'mag_support', False)
        self.prefer_quality = util.get_bool_setting(addon, 'prefer_quality_sort', True)

        # ÚJ: összes variáns M3U generálásának kapcsolója (Hun_all.m3u)
        self.write_variants_m3u = util.get_bool_setting(addon, 'write_variants_m3u', False)

        # Privát / külső M3U (A terv) – URL + helyi fájl
        self.use_external_m3u = util.get_bool_setting(addon, 'use_external_m3u', False)
        self.external_m3u_url = (util.get_setting(addon, 'external_m3u_url', '') or '').strip() if addon else ''
        self.external_m3u_file = (util.get_setting(addon, 'external_m3u_file', '') or '').strip() if addon else ''

        # Csatorna csomag szűrő
        self.enable_channel_package = util.get_bool_setting(addon, 'enable_channel_package', False)
        self.channel_package_name = (util.get_setting(addon, 'channel_package_name', 'core_hu') or 'core_hu').strip()

        profile = util.translate_path(addon.getAddonInfo('profile')) if addon else ""
        if profile:
            util.ensure_directory(profile)
        self.profile_path = profile or ""

        # Alap kimenetek (felhasználói beállítás felülírhatja)
        m3u_out = util.get_setting(addon, 'output_m3u_path', None)
        epg_out = util.get_setting(addon, 'output_epg_path', None)
        self.playlist_path = util.translate_path(m3u_out) if m3u_out else os.path.join(self.profile_path, "Hungary.m3u")
        self.epg_path = util.translate_path(epg_out) if epg_out else os.path.join(self.profile_path, "Hungary.xml")
        self._channels = []
        self._last_success = None

        # Kedvencek
        self._favourites_path = os.path.join(self.profile_path, "favourites.json") if self.profile_path else ""
        self._favourites = self._load_favourites()

        # AUTO mód variáns-léptető állapot (csatornánkénti index + utolsó csatorna/idő)
        self._play_state_path = os.path.join(self.profile_path, "play_state.json") if self.profile_path else ""
        self._play_indices = {}
        self._play_meta = {"last_channel": None, "last_time": 0.0}
        self._load_play_state()

        # EPG csatorna-név → id map (XML alapján) – RÉGI BELSŐ MAP,
        # az új rendszerben a resources.lib.epg_manager használja a Hungary.xml-t.
        self._epg_map = {}

    def get_output_paths(self):
        """
        A GUI (Manage TV / Playlists) számára ad vissza hasznos útvonalakat.
        """
        return {
            "m3u": self.playlist_path,
            "epg": self.epg_path,
            "profile": self.profile_path,
        }

    def _reload_settings(self):
        """Beállítások újraolvasása, hogy a futás közben módosított értékek
        (pl. external_m3u_url) a következő frissítéskor érvényesüljenek."""
        addon = self.addon
        if not addon:
            return

        # Logikai kapcsolók
        self.debug_logging = util.get_bool_setting(addon, 'debug_logging', False)
        self.mag_support = util.get_bool_setting(addon, 'mag_support', False)
        self.prefer_quality = util.get_bool_setting(addon, 'prefer_quality_sort', True)
        self.write_variants_m3u = util.get_bool_setting(addon, 'write_variants_m3u', False)

        # Privát / külső M3U
        self.use_external_m3u = util.get_bool_setting(addon, 'use_external_m3u', False)
        self.external_m3u_url = (util.get_setting(addon, 'external_m3u_url', '') or '').strip()
        self.external_m3u_file = (util.get_setting(addon, 'external_m3u_file', '') or '').strip()

        # Csatorna csomag szűrő
        self.enable_channel_package = util.get_bool_setting(addon, 'enable_channel_package', False)
        self.channel_package_name = (util.get_setting(addon, 'channel_package_name', 'core_hu') or 'core_hu').strip()

        # Kimeneti utak (ha menet közben átírod a beállításokban)
        m3u_out = util.get_setting(addon, 'output_m3u_path', None)
        epg_out = util.get_setting(addon, 'output_epg_path', None)
        self.playlist_path = util.translate_path(m3u_out) if m3u_out else os.path.join(self.profile_path, "Hungary.m3u")
        self.epg_path = util.translate_path(epg_out) if epg_out else os.path.join(self.profile_path, "Hungary.xml")

    # ---------- kedvencek ----------

    def _load_favourites(self):
        data = set()
        path = getattr(self, "_favourites_path", "")
        if not path:
            return data
        raw = _read_text(path)
        if not raw:
            return data
        try:
            payload = json.loads(raw)
            if isinstance(payload, list):
                data.update(str(x) for x in payload)
        except Exception:
            pass
        return data

    def _save_favourites(self):
        path = getattr(self, "_favourites_path", "")
        if not path:
            return
        try:
            payload = json.dumps(sorted(self._favourites))
            _write_text(path, payload)
        except Exception as e:
            util.log_warning(f"[StreamMex] Could not save favourites: {e}")

    def is_favourite(self, channel_id):
        return bool(channel_id and channel_id in self._favourites)

    def add_favourite(self, channel_id):
        if not channel_id:
            return
        if channel_id not in self._favourites:
            self._favourites.add(channel_id)
            self._save_favourites()

    def remove_favourite(self, channel_id):
        if not channel_id:
            return
        if channel_id in self._favourites:
            self._favourites.discard(channel_id)
            self._save_favourites()

    # ---------- AUTO mód állapot ----------

    def _load_play_state(self):
        """play_state.json beolvasása. Új formátum:
        {
          "indices": { "rtl klub": 0, "tv2": 1, ... },
          "meta": { "last_channel": "rtl klub", "last_time": 1234567890.0 }
        }
        Régi formátum (sima dict) is támogatott.
        """
        self._play_indices = {}
        self._play_meta = {"last_channel": None, "last_time": 0.0}

        path = getattr(self, "_play_state_path", "")
        if not path:
            return
        raw = _read_text(path)
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return

        # Új formátum
        if isinstance(payload, dict) and ("indices" in payload or "meta" in payload):
            indices = payload.get("indices") or {}
            meta = payload.get("meta") or {}
            if isinstance(indices, dict):
                for k, v in indices.items():
                    try:
                        self._play_indices[str(k)] = int(v)
                    except Exception:
                        continue
            last_ch = meta.get("last_channel")
            last_time = meta.get("last_time")
            if isinstance(last_ch, str) and last_ch:
                self._play_meta["last_channel"] = last_ch
            try:
                self._play_meta["last_time"] = float(last_time or 0.0)
            except Exception:
                pass

        # Régi formátum: közvetlenül {channel_id: index}
        elif isinstance(payload, dict):
            for k, v in payload.items():
                try:
                    self._play_indices[str(k)] = int(v)
                except Exception:
                    continue

    def _save_play_state(self):
        path = getattr(self, "_play_state_path", "")
        if not path:
            return
        payload = {
            "indices": self._play_indices,
            "meta": self._play_meta,
        }
        try:
            _write_text(path, json.dumps(payload))
        except Exception as e:
            util.log_warning(f"[StreamMex] Could not save play_state: {e}")

    # ---------- csatornalista / assets ----------

    def get_channels(self, force=False, channel_type=None, favourites_only=False, search_query=None, **kwargs):
        """Csatornalista lekérése típus, kedvencek és keresés szűrőkkel."""
        state = self.ensure_assets(force=force)
        channels = list(state.get("channels") or [])

        if channel_type:
            if channel_type == "tv":
                channels = [c for c in channels if c.get("type") in (None, "tv")]
            else:
                channels = [c for c in channels if c.get("type") == channel_type]

        if favourites_only:
            fav_ids = getattr(self, "_favourites", set()) or set()
            channels = [c for c in channels if c.get("channel_id") in fav_ids]

        if search_query:
            q = (search_query or "").strip().lower()
            if q:
                def _match(ch):
                    name = (ch.get("display_name") or ch.get("name") or "").lower()
                    group = (ch.get("group_title") or "").lower()
                    return q in name or q in group
                channels = [c for c in channels if _match(c)]

        def _norm(s):
            return (s or "").strip().lower()

        popular = [
            "rtl plus", "rtl+", "rtl klub", "rtl",
            "tv2", "tv 2",
            "m1", "m2", "duna", "m4 sport", "m4", "m5",
        ]

        def _pop_index(ch):
            name = _norm(ch.get("display_name") or ch.get("name"))
            for idx, key in enumerate(popular):
                if key in name:
                    return idx
            return len(popular)

        channels.sort(key=lambda ch: (_pop_index(ch), _norm(ch.get("display_name") or ch.get("name"))))

        return channels

    def _split_multi_values(self, value):
        """
        Segédfüggvény: 'a | b | c' → ['a', 'b', 'c']
        """
        parts = []
        raw = (value or "").strip()
        if not raw:
            return parts
        for chunk in raw.split("|"):
            p = chunk.strip()
            if p:
                parts.append(p)
        return parts

    def _load_external_m3u_channels(self):
        """
        A terv: privát / külső M3U betöltése több URL-ről vagy több helyi fájlból,
        valamint a GUI-ból (Manage TV / Források) felvett M3U-k összevonása.

        Visszatér: lista: [{"name": ..., "url": ..., "tvg_id": ..., "group_title": ..., "tvg_logo": ..., "kodiprops": [...]}, ...]
        """
        urls = self._split_multi_values(self.external_m3u_url)
        paths = self._split_multi_values(self.external_m3u_file)

        # GUI-ból felvett M3U források (source_manager / sources.json)
        try:
            from . import source_manager
            sm_data = source_manager.load_sources()
            for item in sm_data.get('m3u', []):
                if not isinstance(item, dict):
                    continue
                url = (item.get('url') or '').strip()
                if not url:
                    continue
                if not item.get('enabled', True):
                    continue
                # Heurisztika: ha http/https, akkor URL-nek vesszük, különben fájlnak
                if url.lower().startswith(('http://', 'https://')):
                    urls.append(url)
                else:
                    paths.append(url)
        except Exception as e:
            try:
                util.log_warning(f"[StreamMex] source_manager M3U források beolvasása sikertelen: {e}")
            except Exception:
                pass

        channels = []

        # 1) URL-ek sorban
        for url in urls:
            status, data, _ = _http_get(url, timeout=15)
            if status != 200 or not data:
                util.log_warning(f"[StreamMex] External M3U URL letöltés sikertelen: {url} (status {status})")
                continue
            try:
                text = data.decode("utf-8", "ignore")
            except Exception:
                text = data.decode("latin-1", "ignore")
            chans = _parse_m3u_channels(text)
            util.log_info(f"[StreamMex] External M3U (URL) betöltve: {url} -> {len(chans)} nyers csatorna")
            channels.extend(chans)

        # 2) Helyi fájlok sorban
        for path in paths:
            try:
                real_path = util.translate_path(path)
            except Exception:
                real_path = path
            if not real_path or not os.path.exists(real_path):
                util.log_warning(f"[StreamMex] External M3U fájl nem létezik: {real_path}")
                continue
            try:
                with open(real_path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except Exception as e:
                util.log_warning(f"[StreamMex] External M3U fájl olvasás hiba: {real_path} ({e})")
                continue
            chans = _parse_m3u_channels(text)
            util.log_info(f"[StreamMex] External M3U (file) betöltve: {real_path} -> {len(chans)} nyers csatorna")
            channels.extend(chans)

        return channels

    def ensure_assets(self, force=False):
        """
        Csatornalista felépítése + M3U generálás.

        A terv: ha van bármilyen privát / külső M3U
        (beállításban megadott external_m3u_url / external_m3u_file
         VAGY GUI-ból felvett forrás a sources.json-ban),
        akkor először ABBÓL épít listát.

        B terv: ha az A terv üres listát ad vissza,
        akkor a régi logika fut: Bee default.py → Pastebin index → Hungary M3U-k → egyesítés.

        EPG:
        - ÚJ: a teljes EPG frissítést a resources.lib.epg_manager modul
          végzi (build_merged_epg), és a Hungary.xml / output_epg_path
          fájlba ír.
        - A régi _update_epg_xml() függvény lentebb bent marad, de
          csak fallbackként használjuk, ha az új motor valamiért hibázik.
        """
        # cache: ha már van friss csatornalista és nem kértünk force frissítést, azt adjuk vissza
        if not force and self._channels:
            return {"channels": list(self._channels), "last_success": self._last_success}

        # Beállítások újraolvasása (pl. külső M3U-t menet közben állítod be a Beállítások menüben)
        self._reload_settings()

        # 0) EPG frissítése – ÚJ: külső EPG motor (epg_manager) kezeli a Hungary.xml-t.
        try:
            # ÚJ: a force flaget átadjuk, hogy Teljes frissítésnél biztosan újraépüljön,
            # egyébként pedig a cache (pl. 6 óra) döntsön
            epg_manager.build_merged_epg()
        except Exception as e:
            util.log_warning(f"[StreamMex] EPG update via epg_manager failed: {e}")
            # RÉGI fallback: ha az új motor valamiért nem működik, megpróbáljuk a
            # lokális _update_epg_xml() logikát, hogy ne maradj EPG nélkül.
            try:
                self._update_epg_xml()
            except Exception as e2:
                util.log_warning(f"[StreamMex] Legacy _update_epg_xml fallback failed: {e2}")

        # 0/b) EPG ID patch RAW módban: Hungary.xml -> .raw -> patch -> vissza
        try:
            if self.epg_path and os.path.exists(self.epg_path):
                raw_epg_path = self.epg_path + ".raw"

                # Régi RAW törlése, ha ott maradt valami
                try:
                    if os.path.exists(raw_epg_path):
                        os.remove(raw_epg_path)
                except Exception:
                    # Ha nem tudjuk törölni, nem baj, tovább próbálkozunk
                    pass

                # 1) A frissen írt Hungary.xml-t átmozgatjuk RAW-ba
                os.replace(self.epg_path, raw_epg_path)

                # 2) Patchelés a RAW fájlon (fast patch in-place)
                m3u_builder.fast_patch_epg_ids_in_xml(raw_epg_path)

                # 3) A patchelt RAW visszanevezése Hungary.xml-re
                os.replace(raw_epg_path, self.epg_path)
        except Exception as e:
            util.log_warning(f"[StreamMex] EPG fast patch RAW mode failed: {e}")

        channels = []

        # --- A TERV: privát / külső M3U + GUI források egyesítése ---
        try:
            channels = self._load_external_m3u_channels()
        except Exception as e:
            util.log_warning(f"[StreamMex] External M3U load failed: {e}")
            channels = []

        if channels:
            util.log_info(f"[StreamMex] A terv: external/GUI M3U használata ({len(channels)} nyers csatorna összesen)")
        else:
            util.log_warning("[StreamMex] A terv: sem beállított external M3U, sem GUI-forrás nem adott csatornát – B terv (Bee) következik")

        # --- B TERV: Bee index + Hungary M3U-k ---
        if not channels:
            # 1) Index betöltés
            entries = self._fetch_index_from_pastebin_direct()
            if not entries:
                entries = self._fetch_index_from_bee()
            if not entries:
                util.log_error("[StreamMex] Playlist index could not be retrieved (A terv sikertelen vagy nem aktív, B terv üres)")
                self._channels = []
                self._last_success = None
                return {"channels": []}

            hun_entries = self._filter_hungary(entries)
            if not hun_entries:
                util.log_warning("[StreamMex] No Hungary entries in index (A terv sikertelen vagy nem aktív, B terv HUN nélkül)")
                self._channels = []
                self._last_success = None
                return {"channels": []}

            # 3 Hungary listánál: 3. → első, 1. → utolsó (eredeti logika)
            if len(hun_entries) == 3:
                hun_entries = [hun_entries[2], hun_entries[1], hun_entries[0]]

            # 2) BEE Hungary M3U-k letöltése és csatornalista építése
            for e in hun_entries:
                url = e.get("url") or ""
                if not url.lower().endswith((".m3u", ".m3u8")):
                    continue
                status, data, _ = _http_get(url, timeout=10)
                if status != 200 or not data:
                    util.log_warning(f"[StreamMex] M3U fetch failed: {url} (status {status})")
                    continue
                try:
                    txt_m3u = data.decode("utf-8", "ignore")
                except Exception:
                    txt_m3u = data.decode("latin-1", "ignore")
                channels.extend(_parse_m3u_channels(txt_m3u))

            if channels:
                util.log_info(f"[StreamMex] B terv: Bee Hungary listák használata ({len(channels)} nyers csatorna összesen)")

        if not channels:
            util.log_warning("[StreamMex] Sem az A terv (external/GUI M3U), sem a B terv (Bee) nem adott csatornát")
            self._channels = []
            self._last_success = None
            return {"channels": []}

        # 4) duplikátum szűrés név+url alapján, meta (tvg_id, group_title, tvg_logo, kodiprops) megőrzésével
        seen = set()
        uniq = []
        for c in channels:
            name = (c.get("name") or "").strip()
            url = (c.get("url") or "").strip()
            if not name or not url:
                continue
            key = (name, url)
            if key in seen:
                continue
            seen.add(key)
            normalized = {"name": name, "url": url}
            for meta_key in ("tvg_id", "tvg_logo", "group_title", "kodiprops"):
                val = c.get(meta_key)
                if val:
                    normalized[meta_key] = val
            uniq.append(normalized)

        # 5) variánsok csoportosítása csatornanév alapján
        groups = {}
        name_map = {}
        for item in uniq:
            raw_name = (item.get("name") or "").strip()
            clean_name = self._normalize_name(raw_name) or raw_name
            key_for_group = re.sub(r"\s+", "", clean_name).lower()
            groups.setdefault(key_for_group, []).append(item)
            if key_for_group not in name_map:
                name_map[key_for_group] = clean_name

        grouped_channels = []
        for key, variants in groups.items():
            display_name = name_map.get(key) or variants[0].get("name") or ""
            primary = variants[0]
            ch_id = self._make_channel_id(display_name)
            ch = {
                "channel_id": ch_id,
                "display_name": display_name,
                "name": display_name,
                "url": primary.get("url"),
                "type": "tv",
                "variants": variants,
                "group_key": key,
            }
            # Meta az első variánsból
            for meta_key in ("tvg_id", "tvg_logo", "group_title", "kodiprops"):
                val = primary.get(meta_key)
                if val:
                    ch[meta_key] = val
            grouped_channels.append(ch)

        # 5/b) Csomag szűrő (pl. csak core_hu csatornák)
        # ------------------------------------------------------------------------------------------------------
        # A régi csomag szűrő (whitelist) eltávolítva/kiközvetítve.
        # Ennek oka, hogy a `m3u_builder.py` már tartalmazza a kanonikus névnormalizálást és a végleges whitelist-et.
        # A csatorna csomag szűrés innentől a `m3u_builder.build_plugin_m3u` hívásán belül történik.
        #
        # A B terv szerinti logikák már nem tartalmazzák a csomag szűrést itt.
        #
        # grouped_channels = self._filter_by_channel_package(grouped_channels)
        #
        # ------------------------------------------------------------------------------------------------------

        # # Eredeti kód:
        # grouped_channels = self._filter_by_channel_package(grouped_channels)

        if not grouped_channels:
            # ------------------------------------------------------------------------------------------------------
            # Hibaüzenet csak abban az esetben, ha a nyers listánk teljesen üres.
            # Az eredeti kód itt: util.log_warning("[StreamMex] Csatorna csomag szűrő után nem maradt csatorna")
            # ------------------------------------------------------------------------------------------------------
            util.log_warning("[StreamMex] Nyeres listából nem maradt csatorna")
            self._channels = []
            self._last_success = datetime.utcnow().timestamp()
            return {"channels": []}

        # 6/a) RÉGI tvg-id hozzárendelés a lokális EPG map alapján.
        # MEGJEGYZÉS:
        #   - Az új rendszerben a tvg-id hozzárendelést a resources.lib.epg_manager
        #     modul végzi, ezért ez a blokk NEM AKTÍV.
        #   - A kódot csak kompatibilitás miatt hagytuk itt, hogy később
        #     visszakapcsolható legyen, ha szükséges.
        if False:
            for ch in grouped_channels:
                name = ch.get("display_name") or ch.get("name") or ""
                tvg_id = self._tvg_id_for_name(name)
                if tvg_id:
                    ch["tvg_id"] = tvg_id

        # 6/b) tvg-id finomhangolás epg_manager-rel (csak ahol még nincs tvg_id)
        try:
            epg_manager.apply_tvg_ids_to_channels(grouped_channels)
        except Exception as e:
            util.log_warning(f"[StreamMex] epg_manager.apply_tvg_ids_to_channels failed: {e}")

        # 7) Fő Hungary.m3u felépítése – plugin:// hivatkozások az IPTV Simple számára
        try:
            if self.addon:
                plugin_id = self.addon.getAddonInfo("id") or ""
            else:
                plugin_id = ""
        except Exception:
            plugin_id = ""
        if not plugin_id:
            plugin_id = "plugin.video.streammex"

        # A csomag szűrő (whitelist) most már itt fut a m3u_builder-ben.
        # Átadjuk a csomag nevét a m3u_builder-nek.
        m3u_text = m3u_builder.build_plugin_m3u(
            grouped_channels,
            addon_id=plugin_id,
            # Az eredeti kód szerint a `playlist_source` beállításból veszi a csomag infókat, 
            # így azokat átadjuk a m3u_builder-nek.
            enable_channel_package=self.enable_channel_package,
            channel_package_name=self.channel_package_name,
            channel_packages_data=CHANNEL_PACKAGES,
        )
        
        _write_text(self.playlist_path, m3u_text)
        util.log_info(f"[StreamMex] M3U written -> {self.playlist_path} ({len(grouped_channels)} channels)")

        # 7/b) Opcionális feltöltés távoli URL-re (kliens addonok számára) – egyelőre csak stub
        try:
            self._upload_assets(m3u_text)
        except Exception as e:
            util.log_warning(f"[StreamMex] Remote upload failed: {e}")

        # 8) cache frissítése
        self._channels = grouped_channels
        self._last_success = datetime.utcnow().timestamp()

        # 9) Opcionális: összes variáns M3U (Hun_all.m3u) generálása
        if self.write_variants_m3u:
            try:
                self._write_all_variants_m3u(grouped_channels)
            except Exception as e:
                util.log_warning(f"[StreamMex] All-variants M3U generation failed: {e}")

        # Régi, per-variáns Hun1.m3u, Hun2.m3u, ... generálás jelenleg kikapcsolva.
        # Ha később kell, itt lehet visszakapcsolni a _write_multi_variant_m3u hívást.

        return {"channels": grouped_channels, "last_success": self._last_success}

    def _upload_assets(self, m3u_text):
        """
        Jelenleg csak előkészített hely a későbbi HTTP feltöltéshez.

        Ha a REMOTE_M3U_UPLOAD_URL / REMOTE_EPG_UPLOAD_URL nem üres, ide lehet majd
        berakni egy POST/PUT kérést (GitHub API, Cloudflare Worker stb.).
        """
        m3u_url = (REMOTE_M3U_UPLOAD_URL or "").strip()
        epg_url = (REMOTE_EPG_UPLOAD_URL or "").strip()

        if not m3u_url and not epg_url:
            # Nincs beállítva távoli cél, nincs teendő
            return

        # Jelenleg csak logolunk – itt lesz majd a valódi feltöltés
        try:
            util.log_info(f"[StreamMex] (stub) Remote upload helye: M3U -> {m3u_url or '-'}, EPG -> {epg_url or '-'}")
        except Exception:
            pass

    # ---------- Multi M3U (Hun1.m3u, Hun2.m3u, ...) ----------

    def _write_multi_variant_m3u(self, grouped_channels, max_lists=9):
        """
        Több M3U generálása variáns-slotok alapján.
        Hun1.m3u, Hun2.m3u, ... – mindegyikben csatornánként legfeljebb 1 URL.

        Logika:
        - slot 0 → minden csatorna 0. variánsa (ha létezik)
        - slot 1 → minden csatorna 1. variánsa
        - ...
        - legfeljebb max_lists (alap: 9) fájl
        """
        # Mennyi a leghosszabb variánslista?
        max_variants = 0
        for ch in grouped_channels or []:
            vlen = len(ch.get("variants") or [])
            if vlen > max_variants:
                max_variants = vlen

        # Ha csak 1 variáns van összesen, nincs értelme több M3U-t gyártani
        if max_variants <= 1:
            return

        max_lists = min(max_lists, max_variants)

        base_dir = self.profile_path or os.path.dirname(self.playlist_path) or ""
        ext = ".m3u"

        for idx in range(max_lists):
            # idx -> variáns index
            m3u_text = m3u_builder.build_direct_m3u(grouped_channels, variant_index=idx)

            fname = f"Hun{idx + 1}{ext}"
            path = os.path.join(base_dir, fname) if base_dir else fname
            try:
                _write_text(path, m3u_text)
                util.log_info(f"[StreamMex] Multi-M3U written -> {path} (slot {idx + 1})")
            except Exception as e:
                util.log_warning(f"[StreamMex] Could not write multi M3U {path}: {e}")

    # ---------- Összes variáns M3U (Hun_all.m3u) ----------

    def _write_all_variants_m3u(self, grouped_channels):
        """
        Összes variáns M3U (Hun_all.m3u) generálása.
        - csak akkor fut, ha a write_variants_m3u beállítás True
        - DIRECT stream URL-eket tartalmaz (nem plugin://),
          hogy Kodi-n kívüli lejátszók (VLC, stb.) is tudják használni.
        """
        if not grouped_channels:
            return

        base_dir = self.profile_path or os.path.dirname(self.playlist_path) or ""
        if not base_dir:
            base_dir = "."

        try:
            m3u_text = m3u_builder.build_all_variants_m3u(grouped_channels)
        except Exception as e:
            util.log_warning(f"[StreamMex] build_all_variants_m3u failed: {e}")
            return

        out_name = "Hun_all.m3u"
        out_path = os.path.join(base_dir, out_name)

        try:
            _write_text(out_path, m3u_text)
            util.log_info(f"[StreamMex] All-variants M3U written -> {out_path}")
        except Exception as e:
            util.log_warning(f"[StreamMex] Could not write All-variants M3U {out_path}: {e}")

    # ---------- Név normalizálás / EPG mapping ----------

    def _normalize_name(self, name):
        """Csatornanév tisztítása: HD/4K jelzők kidobása, szóközök normalizálása + ékezetek eltávolítása."""
        if not name:
            return ""
        # ékezetek ki
        n = _strip_accents(name)
        n = str(n).strip()
        # HD/4K jelzők eltávolítása
        n = re.sub(r"(\s*[-+_]?\s*)\b(HD|FHD|UHD|4K|SD|1080p?|720p?)\b", "", n, flags=re.I)
        # többszörös szóközök normalizálása
        n = re.sub(r"\s+", " ", n)
        return n.strip()

    # ------------------------------------------------------------------------------------------------------
    # A régi csomag szűrőhöz használt normalizáló (különböző logikát használt, mint a m3u_builder.py).
    # Mivel a szűrés átkerült a m3u_builder.py-be, ez a függvény kiközvetítésre kerül.
    #
    # def _normalize_epg_key(self, name):
    #     """EPG-hez és csomag-szűréshez használt kulcs: zárójelek, .hu / .port.hu vég eltüntetése + _normalize_name + ékezetmentesítés."""
    #     if not name:
    #         return ""
    #     n = str(name)
    #     # zárójeles jelzők (pl. "(HD)")
    #     n = re.sub(r"\(.*?\)", "", n)
    #     # ország/domain-suffixek
    #     n = re.sub(r"\.hu\s*$", "", n, flags=re.I)
    #     n = re.sub(r"\.port\.hu\s*$", "", n, flags=re.I)
    #     # általános normalizálás (HD/4K jelzők, whitespace, ékezetek)
    #     n = self._normalize_name(n)
    #     # kulcs kisbetűsen
    #     return n.strip().lower()
    # ------------------------------------------------------------------------------------------------------

    def _tvg_id_for_name(self, name):
        """Megpróbál EPG tvg-id-t adni egy csatornanévhez (XML alapján, RÉGI belső map)."""
        # ------------------------------------------------------------------------------------------------------
        # A régi _normalize_epg_key funkció hiánya miatt ez már nem fog működni, 
        # de a kódot kompatibilitási okokból meghagyjuk:
        #
        # key = self._normalize_epg_key(name)
        # ------------------------------------------------------------------------------------------------------
        
        # Helyette az eredeti _normalize_name-et hívjuk, ami nem az eredeti logika,
        # de az eredeti kódot meghagyjuk a megjegyzésekben.
        key = self._normalize_name(name)
        if not key:
            return ""
        return self._epg_map.get(key, "")

    # ------------------------------------------------------------------------------------------------------
    # A régi csatorna csomag szűrő logikája (különböző normalizálást használt, mint a m3u_builder.py).
    # Mivel a szűrés átkerült a m3u_builder.py-be, ez a függvény kiközvetítésre kerül.
    #
    # def _filter_by_channel_package(self, channels):
    #     """
    #     Csatorna lista szűrése beépített csomag alapján (pl. core_hu).
    #
    #     Ha az enable_channel_package False, vagy nem található a csomag neve,
    #     akkor a bemenetet változatlanul visszaadjuk.
    #     """
    #     if not self.enable_channel_package:
    #         return channels
    #
    #     pkg_id = (self.channel_package_name or "").strip()
    #     if not pkg_id:
    #         return channels
    #
    #     pkg = CHANNEL_PACKAGES.get(pkg_id)
    #     if not pkg:
    #         util.log_warning(f"[StreamMex] Ismeretlen csomag: {pkg_id}")
    #         return channels
    #
    #     raw_names = pkg.get("channels") or []
    #     allowed_keys = set(self._normalize_epg_key(n) for n in raw_names if n)
    #     if not allowed_keys:
    #         return channels
    #
    #     filtered = []
    #     for ch in channels or []:
    #         disp = ch.get("display_name") or ch.get("name") or ""
    #         key = self._normalize_epg_key(disp)
    #         if key in allowed_keys:
    #             filtered.append(ch)
    #
    #     util.log_info(f"[StreamMex] Csatorna csomag szűrő: {pkg_id} -> {len(filtered)}/{len(channels or [])} csatorna")
    #     return filtered
    # ------------------------------------------------------------------------------------------------------

    def _rebuild_epg_map(self, xml_text):
        """EPG XML-ből (iptv-org / konyakmeggy) map építése: norm. név → channel id.
        MEGJEGYZÉS:
          - EZ A RÉGI, BELSŐ MAP. Az új EPG motor (epg_manager) a Hungary.xml alapján
            maga építi fel a név→id map-et a tvg-id hozzárendeléshez.
          - A kód kompatibilitás miatt marad itt (fallback célra).
        """
        self._epg_map = {}
        if not xml_text:
            return
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            util.log_warning(f"[StreamMex] EPG XML parse failed: {e}")
            return
        # ------------------------------------------------------------------------------------------------------
        # A régi _normalize_epg_key funkció hiánya miatt ez a map építés már nem fogja az 
        # eredeti kulcsokat generálni, de a kód kompatibilitási okokból meghagyjuk, 
        # a _normalize_epg_key helyett a _normalize_name hívásával:
        # ------------------------------------------------------------------------------------------------------
        for ch in root.findall("channel"):
            cid = ch.get("id") or ""
            if not cid:
                continue
            disp_name = None
            for dn in ch.findall("display-name"):
                text = (dn.text or "").strip()
                if text:
                    disp_name = text
                    break
            if not disp_name:
                continue
            # key = self._normalize_epg_key(disp_name)
            key = self._normalize_name(disp_name)
            if key and key not in self._epg_map:
                self._epg_map[key] = cid

        util.log_info(f"[StreamMex] EPG map built: {len(self._epg_map)} channels")

    def _update_epg_xml(self):
        """EPG XML letöltése (iptv-org vagy konyakmeggy) és map építése.

        MEGJEGYZÉS:
          - RÉGI, LOKÁLIS EPG LOGIKA a PlaylistCoordinator-ön belül.
          - Az új rendszerben a teljes EPG frissítést és név→id mappinget
            a resources.lib.epg_manager modul végzi (build_merged_epg +
            apply_tvg_ids_to_channels).
          - Ezt a függvényt csak végső fallbackként hívjuk meg, ha az új
            EPG motor valamiért nem működik.
        """
        if not self.epg_path:
            return

        # Forrás beállítás – alap: iptv-org
        try:
            source = util.get_setting(self.addon, "epg_source", None)
        except Exception:
            source = None
        source = (source or "iptv-org").lower()

        xml_bytes = b""

        if source == "konyakmeggy":
            url = "http://konyakmeggy.nhely.hu/epg/konyakmeggy.xml.xz"
            status, data, _ = _http_get(url, timeout=20)
            if status != 200 or not data:
                util.log_warning(f"[StreamMex] Konyakmeggy EPG download failed (status {status})")
                return
            try:
                import lzma
                xml_bytes = lzma.decompress(data)
            except Exception as e:
                util.log_warning(f"[StreamMex] Konyakmeggy EPG decompress failed: {e}")
                return
        else:
            # iptv-org alapértelmezett
            url = "https://iptv-epg.org/files/epg-hu.xml"
            status, data, _ = _http_get(url, timeout=20)
            if status != 200 or not data:
                util.log_warning(f"[StreamMex] IPTV-org EPG download failed (status {status})")
                return
            xml_bytes = data

        try:
            xml_text = xml_bytes.decode("utf-8", "ignore")
        except Exception:
            xml_text = xml_bytes.decode("latin-1", "ignore")

        _write_text(self.epg_path, xml_text)
        util.log_info(f"[StreamMex] XMLTV updated -> {self.epg_path} (source={source})")

        self._rebuild_epg_map(xml_text)

    # ---------- MAG / Ministra (Stalker) támogatás ----------

    def _make_channel_id(self, name):
        return (name or "").strip().lower()

    def _is_mag_url(self, url):
        if not url:
            return False
        u = str(url).lower()
        if "/c/" in u:
            return True
        if "live.php" in u and "mac=" in u:
            return True
        return False

    def _build_mag_url(self, url):
        # MAG/Ministra fejlécek hozzáadása az URL-hez.
        try:
            from .stalker_client import MagSession
        except Exception:
            return url
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return url
        portal_base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        if not portal_base:
            return url
        qs = urllib.parse.parse_qs(parsed.query)
        mac = None
        for key in ("mac", "m", "mac_address"):
            vals = qs.get(key)
            if vals:
                mac = vals[0]
                break
        if not mac:
            mac = "00:1A:79:01:02:03"
        try:
            session = MagSession(portal_base, mac)
            return session.build_kodi_play_url(url)
        except Exception:
            return url

    def resolve_variant_url(self, url):
        """MAG/Ministra támogatás: szükség esetén Stalker/MAG fejlécekkel egészítjük ki az URL-t."""
        if self.mag_support and self._is_mag_url(url):
            return self._build_mag_url(url)
        return url

    # ---------- Csatorna / lejátszás ----------

    def get_channel_by_id(self, channel_id):
        if not channel_id:
            return None
        channels = self.get_channels(force=False)
        for ch in channels:
            if ch.get("channel_id") == channel_id:
                return ch
        return None

    def set_preferred_variant(self, channel_id, variant_index):
        """
        Manuális választás rögzítése:
        - A variánslistában kiválasztott indexet (variant_index) tekintjük "jó" forrásnak.
        - Ezt az indexet _play_indices-be mentjük, de a get_play_url-ben használt
          rendezésnek megfelelően (quality-sorrend!).
        - Így AUTO módban is ugyanaz az URL lesz az alapértelmezett.
        """
        ch = self.get_channel_by_id(channel_id)
        if not ch:
            return

        variants = list(ch.get("variants") or [])
        if not variants:
            return

        # nyers index normalizálása
        try:
            raw_index = int(variant_index)
        except Exception:
            raw_index = 0

        if raw_index < 0:
            raw_index = 0
        if raw_index >= len(variants):
            raw_index = len(variants) - 1

        def _quality_score(v):
            url = (v.get("url") or "").lower()
            score = 0

            if any(token in url for token in ("mobile", "low", "lowres", "/sd/", "_sd", "-sd")):
                score -= 15

            if any(token in url for token in ("2160", "4k", "uhd")):
                score += 40
            if any(token in url for token in ("1080", "fhd")):
                score += 30
            if any(token in url for token in ("720", "hd")):
                score += 20
            if "480" in url:
                score += 5

            if url.endswith(".m3u8") or "/hls" in url or "playlist.m3u8" in url:
                score += 10

            if any(token in url for token in (".mp3", "radio=")):
                score -= 5

            return -score

        if getattr(self, 'prefer_quality', True):
            candidates = sorted(variants, key=_quality_score)
        else:
            candidates = list(variants)

        if not candidates:
            return

        chosen_raw = variants[raw_index]
        chosen_url = chosen_raw.get("url") or ""

        target_index = 0
        for idx, v in enumerate(candidates):
            if (v.get("url") or "") == chosen_url:
                target_index = idx
                break

        cid = str(channel_id)
        now_ts = datetime.utcnow().timestamp()

        self._play_indices[cid] = target_index
        self._play_meta["last_channel"] = cid
        self._play_meta["last_time"] = now_ts
        self._save_play_state()

        util.log_info(f"[StreamMex] set_preferred_variant: channel={cid!r} raw_index={raw_index} stored_index={target_index}")

    def get_play_url(self, channel_id):
        """
        AUTO mód:
        - Első kattintás egy csatornára → a legutóbb bevált variánst próbáljuk (ha van), különben az elsőt.
        - Ha rövid időn belül (pl. < 20 mp) újra ugyanarra a csatornára kattintasz,
          akkor úgy vesszük, hogy az előző forrás hibás volt → a KÖVETKEZŐ variánst próbáljuk.
        - Ha elérjük a lista végét, visszatérünk az elejére.
        - A kiválasztott variáns csatornánként a play_state.json-ben megmarad (újraindítás után is).
        """
        ch = self.get_channel_by_id(channel_id)
        if not ch:
            return None

        variants = list(ch.get("variants") or [])
        if not variants:
            base = ch.get("url")
            return self.resolve_variant_url(base) if base else None

        def _quality_score(v):
            """
            Egyszerű heurisztika a jobb minőségű variánsokra:
            - + pont a 4K / 1080 / 720 kulcsszavakra
            - + pont HLS/M3U8 esetén
            - - pont mobil / low / sd jelölésekre
            """
            url = (v.get("url") or "").lower()
            score = 0

            # feltűnően gyengébb / mobil / low minőség
            if any(token in url for token in ("mobile", "low", "lowres", "/sd/", "_sd", "-sd")):
                score -= 15

            # felbontás / minőség kulcsszavak (nagyobb felbontás nagyobb pont)
            if any(token in url for token in ("2160", "4k", "uhd")):
                score += 40
            if any(token in url for token in ("1080", "fhd")):
                score += 30
            if any(token in url for token in ("720", "hd")):
                score += 20
            if "480" in url:
                score += 5

            # HLS / m3u8 előnyben
            if url.endswith(".m3u8") or "/hls" in url or "playlist.m3u8" in url:
                score += 10

            # egyértelműen audio / rádió linkekre kicsi mínusz,
            # hogy inkább videós streamet válasszon, ha keverednek
            if any(token in url for token in (".mp3", "radio=")):
                score -= 5

            # minél NAGYOBB a score, annál jobb – a sorted-hez negatívot adunk vissza
            return -score

        if getattr(self, 'prefer_quality', True):
            candidates = sorted(variants, key=_quality_score)
        else:
            candidates = list(variants)

        if not candidates:
            return None

        cid = str(channel_id)
        now_ts = datetime.utcnow().timestamp()

        last_ch = self._play_meta.get("last_channel")
        last_time = self._play_meta.get("last_time") or 0.0
        try:
            last_time = float(last_time)
        except Exception:
            last_time = 0.0

        # Előzőleg választott index (perzisztens)
        prev_index = int(self._play_indices.get(cid, 0) or 0)
        if prev_index < 0 or prev_index >= len(candidates):
            prev_index = 0

        # Gyors újrapróbálás ugyanarra a csatornára → léptetjük a variánst
        quick_retry = (last_ch == cid and (now_ts - last_time) < 20.0)

        if quick_retry:
            index = prev_index + 1
            if index >= len(candidates):
                index = 0
        else:
            # Nem gyors újrapróbálás: maradunk a legutóbb bevált variánsnál
            index = prev_index

        self._play_indices[cid] = index
        self._play_meta["last_channel"] = cid
        self._play_meta["last_time"] = now_ts
        self._save_play_state()

        chosen_variant = candidates[index]
        raw_url = chosen_variant.get("url")
        if not raw_url:
            return None

        url = self.resolve_variant_url(raw_url)
        util.log_info(f"[StreamMex] get_play_url: channel={cid!r} index={index} url={url}")
        return url

    def get_manual_play_url(self, channel_id, variant_index):
        """
        MANUÁLIS mód:
        - A variánslistában rákattintott indexet (variant_index) tekintjük "jó" forrásnak.
        - Ezt az indexet azonnal elmentjük a play_state.json-be,
          hogy a jövőben AUTO módban is ezt tekintse alapnak.
        - NINCS 20 mp-es léptetés logika, az csak AUTO módban él a get_play_url-ben.
        """
        ch = self.get_channel_by_id(channel_id)
        if not ch:
            return None

        variants = list(ch.get("variants") or [])
        if not variants:
            base = ch.get("url")
            return self.resolve_variant_url(base) if base else None

        # Kért index normalizálása
        try:
            idx = int(variant_index)
        except Exception:
            idx = 0

        if idx < 0:
            idx = 0
        if idx >= len(variants):
            idx = len(variants) - 1

        cid = str(channel_id)
        now_ts = datetime.utcnow().timestamp()

        # A manuálisan választott variánst tekintjük "jó"-nak → ezt mentjük
        self._play_indices[cid] = idx
        self._play_meta["last_channel"] = cid
        self._play_meta["last_time"] = now_ts
        self._save_play_state()

        chosen_variant = variants[idx]
        raw_url = chosen_variant.get("url")
        if not raw_url:
            return None

        url = self.resolve_variant_url(raw_url)
        util.log_info(f"[StreamMex] get_manual_play_url: channel={cid!r} index={idx} url={url}")
        return url

    # ---------- Index letöltés Bee / Pastebin ----------

    def _fetch_index_from_pastebin_direct(self):
        status, data, _ = _http_get(PASTEBIN_DIRECT, timeout=10)
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

        candidates = _find_pastebin_urls(raw)
        decoded_texts = _safe_b64_blocks(raw.encode("utf-8", "ignore"))
        for t in decoded_texts:
            candidates.extend(_find_pastebin_urls(t))

        seen = set()
        uniq = []
        for u in candidates:
            if u not in seen:
                uniq.append(u)
                seen.add(u)

        for url in uniq:
            status, data, _ = _http_get(url, timeout=10)
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
# -*- coding: utf-8 -*-
"""
StreamMex – Playlist coordinator (egyesített M3U és választás)

FŐ ELV:
- Forráskatalógus: Pastebin JSON (első), GitLab Hungary M3U fallback
- A talált Hungary M3U-k összefűzése
- Csatornánként több URL → kimeneti M3U-ban 1 látható stream
- A kiválasztás most egyszerű (stabil, gyors); később bővíthető pontozóval/állapottal
"""

from __future__ import annotations
import os
import re
import json
import ssl
import time
import urllib.request
import urllib.error
from typing import List, Dict, Tuple, Optional

# --- Kodi/Addon segédlog, ha elérhető -------------------------------------------------
try:
    import xbmc
except Exception:  # CoreELEC alatt is legyen fallback
    xbmc = None

def _log(level: int, msg: str) -> None:
    tag = "[StreamMex] "
    try:
        if xbmc is not None:
            xbmc.log(tag + msg, level)
        else:
            print(tag + msg)
    except Exception:
        try:
            print(tag + msg)
        except Exception:
            pass

def _info(msg: str) -> None: _log(2, msg)    # xbmc.LOGINFO=2
def _warn(msg: str) -> None: _log(3, msg)    # xbmc.LOGWARNING=3
def _err(msg: str) -> None:  _log(4, msg)    # xbmc.LOGERROR=4

# --- Általános konstansok -------------------------------------------------------------
PASTEBIN_DIRECT = "https://pastebin.com/raw/k1gwxFYJ"

GITLAB_HU_DEFAULTS = [
    "https://gitlab.com/Al8inA8/build-group/-/raw/main/Bee/Ungary.m3u8",
    "https://gitlab.com/Al8inA8/build-group/-/raw/main/Bee2/HUNGARY2.m3u8",
    "https://gitlab.com/Al8inA8/build-group/-/raw/main/Bee3/Hungary.m3u8",
]

HTTPS_M3U_RE = re.compile(r"https?://[^\s\"']+\.m3u8?", re.IGNORECASE)
HUNGARY_NAME_RE = re.compile(r"\bhungary\b|\bmagyar\b", re.IGNORECASE)

# Normalizálás: HD/4K/zárójelek levágása, whitespace rendezése
PARENS_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
TRAIL_TAGS_RE = re.compile(r"\b(HD|UHD|4K|8K|\+|FULLHD)\b", re.IGNORECASE)
SPACES_RE = re.compile(r"\s+")

UA = "Mozilla/5.0 (Kodi StreamMex)"

def _http_get(url: str, timeout: float = 15.0) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        data = r.read()
    # m3u-k jellemzően utf-8/latin-1; próbáljunk ésszel
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc, "ignore")
        except Exception:
            continue
    return data.decode("utf-8", "ignore")

def _norm_name(name: str) -> str:
    s = name or ""
    s = PARENS_RE.sub("", s)
    s = TRAIL_TAGS_RE.sub("", s)
    s = s.replace("|", " ").replace("/", " ")
    s = SPACES_RE.sub(" ", s).strip().lower()
    return s

def _parse_m3u(text: str) -> List[Dict[str, str]]:
    """
    Minimál M3U parser: #EXTINF meta + URL sor.
    Vissza: dict-ek: name, url, tvg_name, tvg_logo, group
    """
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            meta = line
            # Következő nem-üres sor az URL
            j = i + 1
            url = ""
            while j < len(lines):
                t = lines[j].strip()
                j += 1
                if not t or t.startswith("#"):
                    continue
                url = t
                break
            # név
            name = meta.split(",", 1)[-1].strip() if "," in meta else ""
            # attribok
            tvg_name = ""
            tvg_logo = ""
            group = ""
            for m in re.finditer(r'(\w[\w\-]*)="([^"]*)"', meta):
                k, v = m.group(1).lower(), m.group(2)
                if k in ("tvg-name", "tvg_name"):
                    tvg_name = v
                elif k in ("tvg-logo", "tvg_logo"):
                    tvg_logo = v
                elif k in ("group-title", "group_title"):
                    group = v
            if name and url:
                out.append({
                    "name": name,
                    "url": url,
                    "tvg_name": tvg_name,
                    "tvg_logo": tvg_logo,
                    "group": group
                })
            i = j
            continue
        i += 1
    return out

def _write_m3u(channels: List[Dict[str, str]]) -> str:
    """
    Csak EXTM3U + csatornánként 1 URL.
    """
    out = ["#EXTM3U"]
    for ch in channels:
        name = ch.get("display_name") or ch.get("name") or ""
        url = ch.get("url") or ""
        if not name or not url:
            continue
        attrs = []
        tvg_name = ch.get("tvg_name") or ""
        tvg_logo = ch.get("tvg_logo") or ""
        group = ch.get("group") or ""
        if tvg_name:
            attrs.append(f'tvg-name="{tvg_name}"')
        if tvg_logo:
            attrs.append(f'tvg-logo="{tvg_logo}"')
        if group:
            attrs.append(f'group-title="{group}"')
        attr_str = (" " + " ".join(attrs)) if attrs else ""
        out.append(f"#EXTINF:-1{attr_str},{name}")
        out.append(url)
    return "\n".join(out) + "\n"

# --------------------------------------------------------------------------------------

class PlaylistCoordinator:
    """
    A default.py ezt az osztályt használja.
    """

    def __init__(self, addon=None):
        # addon + profilmappa felderítés
        self.addon = addon
        self.debug_logging = False

        profile = None
        try:
            if self.addon is None:
                # próbáljuk meg lekérni az Addon objektumot, ha Kodi alatt fut
                try:
                    import xbmcaddon, xbmcvfs
                    self.addon = xbmcaddon.Addon("plugin.video.streammex")
                    profile = xbmcvfs.translatePath(self.addon.getAddonInfo("profile"))
                except Exception:
                    profile = None
            else:
                # kaptunk ADDON objektumot a default.py-ból
                try:
                    import xbmcvfs
                    profile = xbmcvfs.translatePath(self.addon.getAddonInfo("profile"))
                except Exception:
                    profile = None
        except Exception:
            profile = None

        if not profile:
            # Biztonsági alapértelmezett
            profile = "/storage/.kodi/userdata/addon_data/plugin.video.streammex"

        try:
            os.makedirs(profile, exist_ok=True)
        except Exception:
            pass

        self.profile_path = profile
        self.playlist_path = os.path.join(profile, "Hungary.m3u")
        self.epg_path = os.path.join(profile, "Hungary.xml")  # későbbre
        self.state_path = os.path.join(profile, "playlist_state.json")

    def ensure_assets(self, force: bool = False):
        try:
            os.makedirs(self.profile_path, exist_ok=True)
        except Exception:
            pass
        return {"playlist_path": self.playlist_path, "epg_path": self.epg_path}

    # ---- Források begyűjtése ---------------------------------------------------------

    def _fetch_pastebin_entries(self) -> List[Dict[str, str]]:
        """
        Pastebin RAW JSON: elvárás: list[ {name,url, ...}, ... ]
        Csak a .m3u/.m3u8 és Hungary releváns elemek kellenek.
        """
        out = []
        try:
            text = _http_get(PASTEBIN_DIRECT, timeout=12.0)
            payload = json.loads(text)
            if isinstance(payload, list):
                for e in payload:
                    if not isinstance(e, dict):
                        continue
                    name = (e.get("name") or e.get("title") or e.get("label") or e.get("url") or "").strip()
                    url = (e.get("url") or e.get("link") or e.get("stream") or "").strip()
                    if not url:
                        continue
                    if not HTTPS_M3U_RE.search(url):
                        continue
                    if name and HUNGARY_NAME_RE.search(name):
                        out.append({"name": name, "url": url})
                    elif "hungary" in url.lower() or "magyar" in url.lower():
                        out.append({"name": name or url, "url": url})
            _info(f"Pastebin direct: {len(out)} Hungary jelölt.")
        except Exception as e:
            _warn(f"Pastebin direct hiba: {e}")
        return out

    def _fallback_gitlab_entries(self) -> List[Dict[str, str]]:
        out = []
        for u in GITLAB_HU_DEFAULTS:
            n = u.rsplit("/", 1)[-1]
            out.append({"name": n, "url": u})
        _info(f"GitLab fallback: {len(out)} elem.")
        return out

    def _discover_hungary_m3u_urls(self) -> List[str]:
        urls = []
        # 1) Pastebin
        for e in self._fetch_pastebin_entries():
            urls.append(e["url"])
        # 2) GitLab fallback, ha üres, vagy kiegészítésképpen
        if not urls:
            for e in self._fallback_gitlab_entries():
                urls.append(e["url"])
        # dedup
        seen = set()
        uniq = []
        for u in urls:
            if u not in seen:
                uniq.append(u)
                seen.add(u)
        _info(f"Hungary M3U források (dedup): {len(uniq)}")
        return uniq

    # ---- Választás / összevonás ------------------------------------------------------

    def _choose_primary(self, urls: List[str], last_good: Optional[str]) -> str:
        """
        Egyszerű kiválasztás:
        - ha volt korábbi jó URL (state), azt preferálja
        - különben az első a listában
        Később bővíthető pontozóval/ellenőrzéssel.
        """
        if last_good and last_good in urls:
            return last_good
        return urls[0] if urls else ""

    # ---- Fő publikus metódus ---------------------------------------------------------

    def get_channels(self, force: bool = False, channel_type: Optional[str] = None, **kwargs):
        """
        Ezt hívja a default.py. Feladata:
        - Hungary M3U források felderítése
        - letöltés → parse → egyesítés név szerint
        - kimeneti M3U megírása: csatornánként 1 URL
        - visszaad egy egyszerű listát a UI-nak
        """
        self.ensure_assets()

        urls = self._discover_hungary_m3u_urls()
        if not urls:
            _warn("Nem találtam Hungary M3U forrásokat (Pastebin + GitLab fallback).")
            return {"channels": [], "error": "no_sources"}

        # Források beolvasása
        all_parts: List[Dict[str, str]] = []
        for u in urls:
            try:
                text = _http_get(u, timeout=20.0)
                parts = _parse_m3u(text)
                _info(f"M3U beolvasva: {u} (+{len(parts)} csatorna)")
                all_parts.extend(parts)
            except urllib.error.HTTPError as he:
                _warn(f"M3U letöltési hiba: {u} (HTTP {he.code})")
            except Exception as e:
                _warn(f"M3U parse/letöltési hiba: {u} — {e}")

        if not all_parts:
            _warn("Egyik M3U-ból sem sikerült csatornát kiolvasni.")
            return {"channels": [], "error": "empty_sources"}

        # Csoportosítás normalizált név szerint
        by_name: Dict[str, Dict[str, List[str]]] = {}
        meta_store: Dict[str, Dict[str, str]] = {}  # első meta megőrzése (logo, group, tvg_name)
        for it in all_parts:
            raw_name = it.get("name") or it.get("display_name") or ""
            if not raw_name:
                continue
            norm = _norm_name(raw_name)
            if not norm:
                continue
            url = it.get("url") or ""
            if not url:
                continue
            by_name.setdefault(norm, {"urls": []})
            by_name[norm]["urls"].append(url)
            # meta – az első nyer
            if norm not in meta_store:
                meta_store[norm] = {
                    "display_name": raw_name,
                    "tvg_name": it.get("tvg_name") or "",
                    "tvg_logo": it.get("tvg_logo") or "",
                    "group": it.get("group") or "",
                }

        # (opcionális) state betöltése
        state_all: Dict[str, Dict[str, object]] = {}
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    state_all = json.load(fh)
        except Exception:
            state_all = {}

        # Kimeneti csatornalista (1 URL / csatorna)
        out_channels: List[Dict[str, str]] = []
        new_state_all: Dict[str, Dict[str, object]] = {}

        for norm, bucket in sorted(by_name.items()):
            urls = bucket["urls"]
            meta = meta_store.get(norm, {})
            last_good_url = None
            if isinstance(state_all.get(norm), dict):
                last_good_url = state_all.get(norm, {}).get("last_good_url")  # type: ignore

            primary = self._choose_primary(urls, last_good_url)

            display_name = meta.get("display_name") or norm
            ch = {
                "name": display_name,
                "url": primary or urls[0],
                "tvg_name": meta.get("tvg_name") or "",
                "tvg_logo": meta.get("tvg_logo") or "",
                "group": meta.get("group") or "",
            }
            out_channels.append(ch)

            new_state_all[norm] = {
                "last_good_url": ch["url"],
                "last_ok_ts": int(time.time()),
                "alternates": urls,
            }

        # M3U kiírás
        try:
            text = _write_m3u(out_channels)
            with open(self.playlist_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            _info(f"M3U written: {self.playlist_path} ({len(out_channels)} csatorna)")
        except Exception as e:
            _err(f"M3U írási hiba: {e}")

        # State kiírás (következő körre)
        try:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(new_state_all, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            _warn(f"State mentési hiba: {e}")

        # UI felé egy egyszerű lista
        return {"channels": out_channels, "playlist": self.playlist_path}

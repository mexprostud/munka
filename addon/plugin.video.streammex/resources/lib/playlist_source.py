# -*- coding: utf-8 -*-
"""
StreamMex – Playlist coordinator (egyesített M3U és választás)

- Források: Pastebin JSON (első), GitLab Hungary M3U fallback
- GitLab 403 kerülése: böngésző UA + Referer fejlécek
- Összefűzés: csatornánként 1 URL kerül a kimeneti M3U-ba
- VISSZATÉRÉS: LISTA (amit a default.py vár) – minden elem dict:
  { "display_name", "url", "tvg_name", "tvg_logo", "group" }
"""

from __future__ import annotations
import os, re, json, ssl, time, urllib.request, urllib.error
from typing import List, Dict, Optional

try:
    import xbmc
except Exception:
    xbmc = None

def _log(level: int, msg: str) -> None:
    tag = "[StreamMex] "
    try:
        if xbmc is not None:
            xbmc.log(tag + msg, level)
        else:
            print(tag + msg)
    except Exception:
        try: print(tag + msg)
        except Exception: pass

def _info(m: str): _log(2, m)   # LOGINFO
def _warn(m: str): _log(3, m)   # LOGWARNING
def _err(m: str):  _log(4, m)   # LOGERROR

PASTEBIN_DIRECT = "https://pastebin.com/raw/k1gwxFYJ"
GITLAB_HU_DEFAULTS = [
    "https://gitlab.com/Al8inA8/build-group/-/raw/main/Bee/Ungary.m3u8",
    "https://gitlab.com/Al8inA8/build-group/-/raw/main/Bee2/HUNGARY2.m3u8",
    "https://gitlab.com/Al8inA8/build-group/-/raw/main/Bee3/Hungary.m3u8",
]

HTTPS_M3U_RE = re.compile(r"https?://[^\s\"']+\.m3u8?", re.IGNORECASE)
HUNGARY_NAME_RE = re.compile(r"\bhungary\b|\bmagyar\b", re.IGNORECASE)
PARENS_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
TRAIL_TAGS_RE = re.compile(r"\b(HD|UHD|4K|8K|\+|FULLHD)\b", re.IGNORECASE)
SPACES_RE = re.compile(r"\s+")

# Erősebb böngésző UA + GitLab Referer a 403 ellen
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
COMMON_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/plain, */*;q=0.1",
    "Accept-Language": "en-US,en;q=0.9,hu;q=0.8",
    "Cache-Control": "no-cache",
}

def _http_get(url: str, timeout: float = 20.0) -> str:
    ctx = ssl.create_default_context()
    headers = dict(COMMON_HEADERS)
    if "gitlab.com" in url:
        headers["Referer"] = "https://gitlab.com/"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        data = r.read()
    for enc in ("utf-8", "latin-1"):
        try: return data.decode(enc, "ignore")
        except Exception: continue
    return data.decode("utf-8", "ignore")

def _norm_name(name: str) -> str:
    s = name or ""
    s = PARENS_RE.sub("", s)
    s = TRAIL_TAGS_RE.sub("", s)
    s = s.replace("|", " ").replace("/", " ")
    s = SPACES_RE.sub(" ", s).strip().lower()
    return s

def _parse_m3u(text: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            meta = line
            j = i + 1
            url = ""
            while j < len(lines):
                t = lines[j].strip()
                j += 1
                if not t or t.startswith("#"): continue
                url = t; break
            name = meta.split(",", 1)[-1].strip() if "," in meta else ""
            tvg_name = tvg_logo = group = ""
            for m in re.finditer(r'(\w[\w\-]*)="([^"]*)"', meta):
                k, v = m.group(1).lower(), m.group(2)
                if k in ("tvg-name","tvg_name"): tvg_name = v
                elif k in ("tvg-logo","tvg_logo"): tvg_logo = v
                elif k in ("group-title","group_title"): group = v
            if name and url:
                out.append({
                    "display_name": name,
                    "url": url,
                    "tvg_name": tvg_name,
                    "tvg_logo": tvg_logo,
                    "group": group
                })
            i = j; continue
        i += 1
    return out

def _write_m3u(channels: List[Dict[str, str]]) -> str:
    out = ["#EXTM3U"]
    for ch in channels:
        name = ch.get("display_name") or ""
        url  = ch.get("url") or ""
        if not name or not url: continue
        attrs = []
        if ch.get("tvg_name"): attrs.append(f'tvg-name="{ch["tvg_name"]}"')
        if ch.get("tvg_logo"): attrs.append(f'tvg-logo="{ch["tvg_logo"]}"')
        if ch.get("group"):    attrs.append(f'group-title="{ch["group"]}"')
        attr_str = (" " + " ".join(attrs)) if attrs else ""
        out.append(f"#EXTINF:-1{attr_str},{name}")
        out.append(url)
    return "\n".join(out) + "\n"

class PlaylistCoordinator:
    def __init__(self, addon=None):
        self.addon = addon
        self.profile_path = self._detect_profile(addon)
        try: os.makedirs(self.profile_path, exist_ok=True)
        except Exception: pass
        self.playlist_path = os.path.join(self.profile_path, "Hungary.m3u")
        self.epg_path      = os.path.join(self.profile_path, "Hungary.xml")
        self.state_path    = os.path.join(self.profile_path, "playlist_state.json")

    def _detect_profile(self, addon) -> str:
        try:
            if addon is not None:
                import xbmcvfs
                return xbmcvfs.translatePath(addon.getAddonInfo("profile"))
            else:
                import xbmcaddon, xbmcvfs
                a = xbmcaddon.Addon("plugin.video.streammex")
                return xbmcvfs.translatePath(a.getAddonInfo("profile"))
        except Exception:
            return "/storage/.kodi/userdata/addon_data/plugin.video.streammex"

    def ensure_assets(self, force: bool = False):
        try: os.makedirs(self.profile_path, exist_ok=True)
        except Exception: pass
        return {"playlist_path": self.playlist_path, "epg_path": self.epg_path}

    def _fetch_pastebin_entries(self) -> List[str]:
        urls: List[str] = []
        try:
            text = _http_get(PASTEBIN_DIRECT, timeout=12.0)
            payload = json.loads(text)
            if isinstance(payload, list):
                for e in payload:
                    if not isinstance(e, dict): continue
                    name = (e.get("name") or e.get("title") or e.get("label") or e.get("url") or "").strip()
                    url  = (e.get("url")  or e.get("link")  or e.get("stream") or "").strip()
                    if not url: continue
                    if not HTTPS_M3U_RE.search(url): continue
                    if name and HUNGARY_NAME_RE.search(name):
                        urls.append(url)
                    elif "hungary" in url.lower() or "magyar" in url.lower():
                        urls.append(url)
            _info(f"Pastebin direct: {len(urls)} Hungary jelölt.")
        except Exception as e:
            _warn(f"Pastebin direct hiba: {e}")
        return urls

    def _discover_hungary_m3u_urls(self) -> List[str]:
        urls = self._fetch_pastebin_entries()
        if not urls:
            urls = list(GITLAB_HU_DEFAULTS)
        # dedup, sorrend tartása
        seen, uniq = set(), []
        for u in urls:
            if u not in seen:
                uniq.append(u); seen.add(u)
        _warn(f"Hungary M3U források (dedup): {len(uniq)}")
        return uniq

    def _choose_primary(self, urls: List[str], last_good: Optional[str]) -> str:
        if last_good and last_good in urls: return last_good
        return urls[0] if urls else ""

    # FONTOS: a default.py LISTÁT vár vissza – itt mindig listát adunk vissza
    def get_channels(self, force: bool = False, channel_type: Optional[str] = None, **kwargs) -> List[Dict[str, str]]:
        self.ensure_assets()

        sources = self._discover_hungary_m3u_urls()
        all_items: List[Dict[str, str]] = []

        for u in sources:
            try:
                text = _http_get(u, timeout=20.0)
                parts = _parse_m3u(text)
                _info(f"M3U beolvasva: {u} (+{len(parts)} csatorna)")
                all_items.extend(parts)
            except urllib.error.HTTPError as he:
                _err(f"M3U letöltési hiba: {u} (HTTP {he.code})")
            except Exception as e:
                _warn(f"M3U parse/letöltési hiba: {u} — {e}")

        if not all_items:
            _err("Egyik M3U-ból sem sikerült csatornát kiolvasni.")
            # ÜRES LISTA visszaadása – így a default.py nem száll el
            return []

        # Normalizált név → gyűjtés és meta
        grouped: Dict[str, Dict[str, object]] = {}
        for it in all_items:
            raw = it.get("display_name") or ""
            url = it.get("url") or ""
            if not raw or not url: continue
            norm = _norm_name(raw)
            if not norm: continue
            g = grouped.setdefault(norm, {"urls": [], "meta": {
                "display_name": raw,
                "tvg_name": it.get("tvg_name",""),
                "tvg_logo": it.get("tvg_logo",""),
                "group":    it.get("group",""),
            }})
            g["urls"].append(url)  # type: ignore

        # Egyszerű választás + kimeneti M3U
        out_entries: List[Dict[str, str]] = []
        for norm in sorted(grouped.keys()):
            bucket = grouped[norm]
            urls   = bucket["urls"]  # type: ignore
            meta   = bucket["meta"]  # type: ignore
            primary = self._choose_primary(urls, last_good=None)
            out_entries.append({
                "display_name": meta["display_name"],   # type: ignore
                "url": primary,
                "tvg_name": meta.get("tvg_name",""),    # type: ignore
                "tvg_logo": meta.get("tvg_logo",""),    # type: ignore
                "group":    meta.get("group",""),       # type: ignore
            })

        # M3U fájl írása (Kodi IPTV Simple Client-hez)
        try:
            m3u_text = _write_m3u(out_entries)
            with open(self.playlist_path, "w", encoding="utf-8") as fh:
                fh.write(m3u_text)
            _info(f"M3U written: {self.playlist_path} ({len(out_entries)} csatorna)")
        except Exception as e:
            _err(f"M3U írási hiba: {e}")

        # VISSZA: lista (a default.py ezt fogyasztja)
        return out_entries

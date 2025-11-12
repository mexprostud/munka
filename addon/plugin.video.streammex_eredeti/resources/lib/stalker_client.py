# -*- coding: utf-8 -*-
"""
stalker_client.py — könnyű MAG/Stalker „fejléces” kliens

Cél:
- Tipikus MAG/Ministra portál-fejlécek összeállítása (User-Agent, X-User-Agent, Referer, Cookie)
- Opcionális „warmup” (GET /c/) a session cookie miatt
- Kodi-kompatibilis lejátszó URL építése:  stream_url + "|" + URL-enkódolt fejlécek
- Kíméletes elérhetőség-próba (HEAD vagy kicsi Range)

Semmit nem módosít a Kodi-n, nem ír fájlokat. Önmagában, importálva használható.
"""

from __future__ import annotations
import time
import urllib.request
import urllib.parse
import http.cookiejar
from typing import Dict, Tuple, Optional

# Tipikus MAG UA-k (egyik gyakran elég)
MAG_UA = (
    "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
    "(KHTML, like Gecko) MAG250 stbapp ver: 2 rev: 250 Safari/533.3"
)
X_USER_AGENT = "Model: MAG254; Link: Ethernet"
DEFAULT_LANG = "en"
DEFAULT_TZ = "Europe/Budapest"

class MagSession:
    """
    Egyszerű wrapper a MAG-szerű kéréshez.
    - portal_base: pl. "http://iptv.darktv.nl" VAGY a portál host, amihez Referer-t szeretnél.
    - mac: "00:1A:79:xx:xx:xx" (ha nincs, adhatunk egy dummy-t is, de sok szerver ellenőrzi)
    """

    def __init__(self, portal_base: str, mac: str, lang: str = DEFAULT_LANG, tz: str = DEFAULT_TZ):
        self.portal_base = portal_base.rstrip("/")
        self.mac = mac.upper()
        self.lang = lang
        self.tz = tz

        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPHandler()
        )

        # Alap fejlécek
        self.headers: Dict[str, str] = {
            "User-Agent": MAG_UA,
            "X-User-Agent": X_USER_AGENT,
            "Accept": "*/*",
            "Connection": "Keep-Alive",
            "Referer": f"{self.portal_base}/c/",
            # Egyes portálok szeretik, ha ezek is mennek
            "X-Requested-With": "XMLHttpRequest",
        }

        # Alap „MAG-ízű” sütik (sok szerveren mindegy, de nem árt)
        self.static_cookies = {
            "mac": self.mac,
            "stb_lang": self.lang,
            "timezone": self.tz,
        }

    # ---- belső segédek -----------------------------------------------------

    def _cookie_header(self) -> str:
        """Aktuális Cookie header felépítése (jar + statikus)."""
        jar = []
        # jelen sütik
        for c in self.cookies:
            jar.append(f"{c.name}={c.value}")
        # statikusak (ha nincs már jar-ban)
        for k, v in self.static_cookies.items():
            if not any(s.startswith(k + "=") for s in jar):
                jar.append(f"{k}={v}")
        return "; ".join(jar) if jar else ""

    def _build_request(self, url: str, extra_headers: Optional[Dict[str, str]] = None) -> urllib.request.Request:
        h = dict(self.headers)
        ck = self._cookie_header()
        if ck:
            h["Cookie"] = ck
        if extra_headers:
            h.update(extra_headers)
        return urllib.request.Request(url, headers=h)

    # ---- publikus API -------------------------------------------------------

    def warmup(self, timeout: float = 5.0) -> None:
        """
        Opcionális: GET /c/ – sok portál itt ad vissza session sütit.
        Hibát nem dobunk tovább; csendben elnyeljük, mert változatos szerverek vannak.
        """
        try:
            url = f"{self.portal_base}/c/"
            req = self._build_request(url)
            with self.opener.open(req, timeout=timeout) as r:
                _ = r.read(128)
        except Exception:
            pass  # nem kritikus

    def probe_head_or_range(self, url: str, timeout: float = 4.0) -> Tuple[int, Dict[str, str]]:
        """
        Kíméletes elérhetőség-próba.
        1) HEAD kísérlet
        2) ha nem megy, GET Range: bytes=0-256

        Vissza: (status_code, response_headers_dict)
        """
        # 1) HEAD
        try:
            req = self._build_request(url, {"Range": None})
            req.get_method = lambda: "HEAD"
            with self.opener.open(req, timeout=timeout) as r:
                return (getattr(r, "status", 200), dict(r.headers or {}))
        except Exception:
            pass

        # 2) kis Range GET
        try:
            req = self._build_request(url, {"Range": "bytes=0-256"})
            with self.opener.open(req, timeout=timeout) as r:
                _ = r.read(32)
                return (getattr(r, "status", 200), dict(r.headers or {}))
        except Exception:
            return (0, {})

    def kodi_header_string(self, extra: Optional[Dict[str, str]] = None) -> str:
        """
        Kodi ‚|’-es header sztringet ad vissza.
        FIGYELEM: az értékeket URL-enkódoljuk.
        Példa:  "User-Agent=...&Referer=...&Cookie=...&X-User-Agent=..."
        """
        h = dict(self.headers)
        ck = self._cookie_header()
        if ck:
            h["Cookie"] = ck
        if extra:
            h.update(extra)

        # Kodi-nál NEM szabad a kulcsokat enkódolni, csak az értékeket.
        pairs = []
        for k, v in h.items():
            if v is None:
                continue
            pairs.append(f"{k}={urllib.parse.quote(str(v), safe='')}")
        return "&".join(pairs)

    def build_kodi_play_url(self, stream_url: str, extra_headers: Optional[Dict[str, str]] = None) -> str:
        """
        Lejátszható URL Kodi/ffmpeg számára.
        Példa kimenet:
          http://host/play/live.php?mac=...&stream=51118&extension=ts|User-Agent=...&Referer=...&Cookie=...
        """
        header_str = self.kodi_header_string(extra_headers)
        if header_str:
            return f"{stream_url}|{header_str}"
        return stream_url

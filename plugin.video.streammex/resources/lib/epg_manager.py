# -*- coding: utf-8 -*-
"""
EPG azonosító kezelő a streammex addonhoz.

Feladat:
- különböző EPG források (iptv-org, konyakmeggy) támogatása,
- csatornanevek normalizálása,
- tvg-id hozzárendelése a csatornalistához.

FONTOS:
- Eredetileg az XML letöltést a playlist_source végezte.
- Az új verzióban ez a modul KÜLSŐ "EPG motor"-ként is használható:
  az EPG forrásokból felépíti a végső Hungary.xml fájlt (vagy output_epg_path-ot),
  és ebből név -> tvg-id leképezést készít.

KIEGÉSZÍTÉS:
- Az új GUI-s EPG kezeléshez (source_manager / sources.json) az epg_manager
  képes EPG XML fájlt készíteni több EPG forrásból (iptv-org / konyak +
  GUI-ból felvett URL-ek), és ebből név -> tvg-id leképezést építeni.
- A tvg-id hozzárendelés tehát KÉTLÉPCSŐS:
    1) táblás/heurisztikás mapping (EPG_ID_MAP, normalize_name),
    2) ha még nincs, akkor az EPG XML alapján név -> id
       (konyak / iptv-org / GUI-s források).

- ÚJ LOGIKA:
    - Automatikus mód: csak az "alap" EPG letöltés/frissítés történik
      (iptv-org / konyak), merge nélkül (ensure_base_epg_fresh).
    - Merge (több EPG forrás összefésülése) CSAK kézi hívásra történik:
      build_merged_epg(), pl. "EPG egyesítése most" menüpontból.
"""

from __future__ import annotations

import os
import re
import unicodedata
import gzip
import lzma
import time  # ÚJ: fájl életkor ellenőrzéséhez
import xml.etree.ElementTree as ET
from enum import Enum
from typing import Any, Dict, Iterable, Optional

try:
    import xbmcaddon  # type: ignore
except ImportError:  # unit teszt / külső futtatás esetén
    xbmcaddon = None  # type: ignore

# A util és source_manager modul az EPG összevonáshoz (GUI-s forráslista).
try:
    from . import util
    from . import source_manager
except Exception:  # egységteszt / külső környezet
    util = None  # type: ignore
    source_manager = None  # type: ignore

if xbmcaddon is not None:
    ADDON = xbmcaddon.Addon()
else:
    ADDON = None

# Mennyi ideig tekintjük "frissnek" az alap EPG-t (Hungary.xml)
BASE_EPG_MAX_AGE_HOURS = 24

# ---------------------------------------------------------------------------
# EPG forrás típusok
# ---------------------------------------------------------------------------


class EPGSource(str, Enum):
    NONE = "none"
    IPTVORG = "iptvorg"
    KONYAK = "konyak"


def epg_source_from_setting(value: Optional[str]) -> EPGSource:
    """
    String -> EPGSource.

    Kezeli ezeket is: "iptv-org", "IPTVORG", "1", "2" stb.
    """
    if not value:
        return EPGSource.NONE

    v = value.strip().lower()

    # "iptv-org" -> "iptvorg"
    v = v.replace("-", "")

    # numerikus mód (régi epg_mode beállítás)
    if v in ("0", "none"):
        return EPGSource.NONE
    if v in ("1", "iptvorg"):
        return EPGSource.IPTVORG
    if v in ("2", "konyak", "konyakmeggy"):
        return EPGSource.KONYAK

    try:
        return EPGSource(v)
    except ValueError:
        return EPGSource.NONE


# Régi, numerikus módok (settings.xml / epg_mode)
EPG_MODE_NONE = "0"
EPG_MODE_IPTVORG = "1"
EPG_MODE_KONYAK = "2"


def get_epg_mode() -> str:
    """
    Visszaadja az aktuális EPG módot (settings.xml / epg_mode).
    Ha nincs addon vagy beállítás, "0" (nincs) az alap.
    """
    if ADDON is None:
        return EPG_MODE_NONE

    try:
        return ADDON.getSetting("epg_mode")
    except Exception:
        return EPG_MODE_NONE


def get_epg_url() -> str:
    """
    Ha a beállításokban tárolod az EPG XML linkeket,
    itt adjuk vissza az IPTV Simple-nek szánt URL-t.

    Várt settings:
      - epg_mode: "0" / "1" / "2"
      - epg_url_iptvorg
      - epg_url_konyak
    """
    if ADDON is None:
        return ""

    mode = get_epg_mode()

    if mode == EPG_MODE_IPTVORG:
        # pl. <setting id="epg_url_iptvorg" type="text" ... />
        return ADDON.getSetting("epg_url_iptvorg")

    if mode == EPG_MODE_KONYAK:
        # pl. <setting id="epg_url_konyak" type="text" ... />
        return ADDON.getSetting("epg_url_konyak")

    return ""


def _get_epg_source_from_addon() -> EPGSource:
    """
    EPGSource eldöntése addon beállítások alapján.

    Próbáljuk:
      1) epg_source (pl. "iptvorg", "konyak", "none")
      2) epg_mode   ("0", "1", "2")

    MEGJEGYZÉS:
      - Ha semmilyen érvényes beállítás nincs, az alapértelmezés IPTVORG,
        hogy a tvg-id logika "magától" is működjön.
    """
    # 1) epg_source (újabb logika)
    if ADDON is not None:
        try:
            src = ADDON.getSetting("epg_source")
        except Exception:
            src = None
        src_enum = epg_source_from_setting(src)
        if src_enum is not EPGSource.NONE:
            return src_enum

    # 2) epg_mode (régi numerikus)
    mode = get_epg_mode()
    if mode == EPG_MODE_IPTVORG:
        return EPGSource.IPTVORG
    if mode == EPG_MODE_KONYAK:
        return EPGSource.KONYAK

    # Alapértelmezés: iptv-org (régi _update_epg_xml logika szerint is ez volt a default)
    return EPGSource.IPTVORG


# ---------------------------------------------------------------------------
# Név normalizálás (táblás mappinghez)
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """
    Csatornanév agresszív normalizálása:
      - ékezetek eltávolítása,
      - nagybetűsítés,
      - zárójelek közötti rész kidobása,
      - '+' -> 'PLUS',
      - .HU / .PORT.HU vég eltávolítása,
      - végéről " TV" / " CSATORNA" levágása,
      - betű + szóköz + szám -> betű+szám (ARENA 4 -> ARENA4),
      - nem alfanumerikus karakterek kidobása,
      - végéről tipikus HD/SD/FHD/UHD suffixek levágása.

    Példák:
      "ARENA 4 HD"        -> "ARENA4"
      "Arena4 (HD).hu"    -> "ARENA4"
      "RTL Klub (HD)"     -> "RTLKLUB"
      "RTL+"              -> "RTLPLUS"
      "RTL Plus HD"       -> "RTLPLUS"
      "Hír TV"            -> "HIRTV"
      "Spektrum Home TV"  -> "SPEKTRUMHOME"
      "Animal Planet HD"  -> "ANIMALPLANET"
    """
    if not name:
        return ""

    # ékezetek lekapása, nagybetűsítés
    txt = unicodedata.normalize("NFKD", name)
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = txt.upper()

    # zárójelben lévő jelölések kidobása
    cleaned = []
    depth = 0
    for ch in txt:
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            continue
        if depth > 0:
            continue
        cleaned.append(ch)
    txt = "".join(cleaned)

    # '+' -> 'PLUS'
    txt = txt.replace("+", "PLUS")

    # .HU / .PORT.HU a végéről
    txt = re.sub(r"\.HU\s*$", "", txt)
    txt = re.sub(r"\.PORT\.HU\s*$", "", txt)

    # " TV" / " CSATORNA" a végéről
    txt = re.sub(r"\s+TV$", "", txt)
    txt = re.sub(r"\s+CSATORNA$", "", txt)

    # betű + szóköz + szám -> betű+szám (ARENA 4 -> ARENA4)
    txt = re.sub(r"(\D)\s+(\d)", r"\1\2", txt)

    # csak betűk és számok maradnak
    buf = []
    for ch in txt:
        if ch.isalnum():
            buf.append(ch)
    key = "".join(buf)

    # tipikus végződések eltávolítása
    for suffix in ("HD", "FHD", "UHD", "SD"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]

    return key


# ---------------------------------------------------------------------------
# EPG azonosító táblák
#
# A kulcs: normalize_name() eredménye.
# Az érték: forrásspecifikus tvg-id-k dict-je.
#
# Ezt nyugodtan bővítheted saját igény szerint.
# ---------------------------------------------------------------------------

EPG_ID_MAP: Dict[str, Dict[str, str]] = {
    # példa: "AMC"
    "AMC": {
        "iptvorg": "AMC.hu",
        "konyak": "AMC.hu",
    },
    "ANIMALPLANET": {
        "iptvorg": "ANIMAL_PLANET.hu",
    },
    "ARENA4": {
        "iptvorg": "ARENA4.hu",
    },
    "ATV": {
        "iptvorg": "ATV.hu",
    },
    "AXN": {
        "iptvorg": "AXN.hu",
    },
    "BBCEARTH": {
        "iptvorg": "BBC_EARTH.hu",
    },
    # IDE tudsz majd még rengeteg csatornát felvenni...
}


def _lookup_epg_id(norm_name: str, source: EPGSource) -> Optional[str]:
    """
    Először a táblában keres, majd néhány egyszerű heuristikával próbálkozik.

    A norm_name a normalize_name() eredménye legyen.
    """
    if not norm_name or source is EPGSource.NONE:
        return None

    mapping = EPG_ID_MAP.get(norm_name)
    if mapping:
        by_src = mapping.get(source.value)
        if by_src:
            return by_src

    # Heurisztikák – itt lehet még okosítani
    # Arena 4 mindenféle változata
    if "ARENA4" in norm_name:
        if source is EPGSource.IPTVORG:
            return "ARENA4.hu"

    # RTL csatornák például:
    if norm_name in ("RTLKLB", "RTLK", "RTLKLUB"):
        if source is EPGSource.IPTVORG:
            return "RTLKLUB.hu"

    # ha semmi sem jött össze
    return None


def _source_from_any(source_value: Optional[str]) -> EPGSource:
    """
    Ha van explicit source_value (pl. 'iptvorg' vagy '1'),
    azt próbáljuk értelmezni, különben addon beállításból dolgozunk.
    """
    if source_value:
        return epg_source_from_setting(source_value)
    return _get_epg_source_from_addon()


# ---------------------------------------------------------------------------
# ÚJ RÉSZ: GUI-s EPG egyesítés (Hungary.xml / output_epg_path) és név->id map
# ---------------------------------------------------------------------------

# A PROFIL könyvtár feloldása (special:// útvonal Kodi alatt).
if ADDON is not None:
    try:
        _profile = ADDON.getAddonInfo("profile") or "."
    except Exception:
        _profile = "."
else:
    _profile = "."

# Ha van util.translate_path, használjuk, hogy a special:// elérési útból
# valódi fájlrendszer elérés legyen.
if util is not None:
    try:
        _profile = util.translate_path(_profile)
    except Exception:
        pass

# Alapértelmezett EPG kimeneti útvonal:
# - ha van output_epg_path beállítás -> azt használjuk,
# - különben <profil>/Hungary.xml
if util is not None and ADDON is not None:
    try:
        _epg_out = util.get_setting(ADDON, "output_epg_path", None)
    except Exception:
        _epg_out = None
else:
    _epg_out = None

if _epg_out:
    try:
        _epg_path = util.translate_path(_epg_out) if util is not None else _epg_out
    except Exception:
        _epg_path = _epg_out
else:
    _epg_path = os.path.join(_profile, "Hungary.xml")

PROFILE = _profile

# MEGJEGYZÉS:
# A régi név (MERGED_EPG_FILE) kompatibilitás kedvéért marad,
# de valójában mindig a fő EPG fájlra mutat (Hungary.xml vagy output_epg_path).
MERGED_EPG_FILE = _epg_path

# Névmap cache egy plugin-futásra (hogy ne olvassa/parszolja újra az XML-t
# minden egyes csatornánál).
_NAME_MAP_CACHE: Dict[str, str] = {}
_NAME_MAP_MTIME: float = 0.0


def _download_and_parse(url: str) -> Optional[ET.Element]:
    """
    EPG XML letöltése és parse-olása.

    Támogatja:
      - sima .xml
      - .gz (gzip)
      - .xz (lzma)
    """
    if util is None:
        return None

    util.log_info(f"[EPG] Letöltés: {url}")
    try:
        status, data, _ = util.http_request(url, timeout=30)
    except Exception as e:
        util.log_warning(f"[EPG] Letöltés hiba ({url}): {e}")
        return None

    if status != 200 or not data:
        return None

    xml_str = ""
    try:
        if url.endswith(".gz"):
            xml_str = gzip.decompress(data).decode("utf-8", "ignore")
        elif url.endswith(".xz"):
            xml_str = lzma.decompress(data).decode("utf-8", "ignore")
        else:
            xml_str = data.decode("utf-8", "ignore")

        if xml_str:
            return ET.fromstring(xml_str)
    except Exception as e:
        util.log_warning(f"[EPG] Parse hiba ({url}): {e}")
    return None


# ---------------------------------------------------------------------------
# ÚJ: vékony, automatikus alap-EPG frissítés (merge nélkül)
# ---------------------------------------------------------------------------

def ensure_base_epg_fresh(max_age_hours: int = BASE_EPG_MAX_AGE_HOURS) -> str:
    """
    Gondoskodik róla, hogy legyen egy ALAP EPG fájlunk (MERGED_EPG_FILE),
    amit tvg-id kereséshez használunk.

    FONTOS:
      - EZ NEM végez merge-et, csak egy forrásból (iptv-org / konyak)
        letölti az EPG-t és Hungary.xml-ként elmenti.
      - Ha GUI-s EPG források vannak beállítva (source_manager / sources.json),
        akkor NEM írja felül az EPG-t: ilyenkor feltételezzük, hogy
        kézzel futtatod a build_merged_epg()-t.

    max_age_hours:
      - ha a fájl ennél fiatalabb, NEM töltjük le újra (cache).
    """
    if util is None:
        return MERGED_EPG_FILE

    # Ha GUI-s EPG források aktívak, nem piszkáljuk automatikusan.
    sm_data = {}
    if source_manager is not None:
        try:
            sm_data = source_manager.load_sources()
        except Exception:
            sm_data = {}
    gui_epg_sources = []
    if isinstance(sm_data, dict):
        gui_epg_sources = sm_data.get("epg", []) or []

    if gui_epg_sources:
        util.log_info("[EPG] GUI-s EPG források vannak beállítva; automatikus alap EPG letöltés kihagyva.")
        return MERGED_EPG_FILE

    # Ha van már EPG fájl és elég friss, nem töltünk le újat.
    try:
        st = os.stat(MERGED_EPG_FILE)
        age_hours = (time.time() - st.st_mtime) / 3600.0
        if st.st_size > 0 and age_hours < float(max_age_hours):
            util.log_info("[EPG] Hungary.xml friss (cache), letöltés kihagyva.")
            return MERGED_EPG_FILE
    except OSError:
        # nincs fájl vagy nem elérhető -> letöltjük
        pass

    src_enum = _get_epg_source_from_addon()
    if src_enum is EPGSource.KONYAK:
        url = "http://konyakmeggy.nhely.hu/epg/konyakmeggy.xml.xz"
        label = "Konyakmeggy"
    else:
        # Alapértelmezés: iptv-org / epg-hu.xml
        url = "https://iptv-epg.org/files/epg-hu.xml"
        label = "IPTV-Org"

    util.log_info(f"[EPG] Alap EPG frissítése ({label}): {url}")

    try:
        status, data, _ = util.http_request(url, timeout=30)
    except Exception as e:
        util.log_warning(f"[EPG] Alap EPG letöltés hiba ({url}): {e}")
        return MERGED_EPG_FILE

    if status != 200 or not data:
        util.log_warning(f"[EPG] Alap EPG letöltés sikertelen (status={status})")
        return MERGED_EPG_FILE

    try:
        if url.endswith(".gz"):
            xml_bytes = gzip.decompress(data)
        elif url.endswith(".xz"):
            xml_bytes = lzma.decompress(data)
        else:
            xml_bytes = data

        # biztonság kedvéért legyen érvényes XML (ha gond van, nem írjuk felül)
        ET.fromstring(xml_bytes.decode("utf-8", "ignore"))
    except Exception as e:
        util.log_warning(f"[EPG] Alap EPG tartalom hiba: {e}")
        return MERGED_EPG_FILE

    # Könyvtár biztosítása
    try:
        directory = os.path.dirname(MERGED_EPG_FILE)
        if util is not None:
            util.ensure_directory(directory)
        else:
            os.makedirs(directory, exist_ok=True)
    except Exception:
        pass

    try:
        with open(MERGED_EPG_FILE, "wb") as f:
            f.write(xml_bytes)
        util.log_info(f"[EPG] Alap EPG mentve: {MERGED_EPG_FILE}")
    except Exception as e:
        util.log_error(f"[EPG] Alap EPG mentési hiba: {e}")

    # Cache-t töröljük, hogy legközelebb újra épüljön
    global _NAME_MAP_CACHE, _NAME_MAP_MTIME
    _NAME_MAP_CACHE = {}
    _NAME_MAP_MTIME = 0.0

    return MERGED_EPG_FILE


def build_merged_epg() -> str:
    """
    Összes EPG (GUI + opcionális beépített) egyesítése
    egyetlen XML fájlba.

    A GUI-ból felvett EPG forrásokat a source_manager.load_sources()
    'epg' listájából vesszük. Ha ott nincs semmi, akkor a régi
    iptv-org / konyakmeggy alapértelmezett URL-eket használjuk.

    Visszatérési érték:
        MERGED_EPG_FILE útvonal (ami valójában a fő EPG fájl:
        output_epg_path vagy <profil>/Hungary.xml).

    MEGJEGYZÉS:
      - Ez a művelet KÉZI hívásra készült (EPG merge menü), nem
        automatikus induláskori frissítésre.
    """
    if util is None:
        return MERGED_EPG_FILE

    # GUI-s forráslista (sources.json / "epg")
    data = source_manager.load_sources() if source_manager is not None else {}
    sources = data.get("epg", []) if isinstance(data, dict) else []

    # Alapértelmezett források, ha a GUI üres
    if not sources:
        src_enum = _get_epg_source_from_addon()
        # Ha nincs explicit beállítás, iptv-org az alap
        if src_enum is EPGSource.KONYAK:
            sources.append({
                "url": "http://konyakmeggy.nhely.hu/epg/konyakmeggy.xml.xz",
                "enabled": True,
                "label": "Konyakmeggy (alapértelmezett)",
            })
        else:
            # IPTV-org (default)
            sources.append({
                "url": "https://iptv-epg.org/files/epg-hu.xml",
                "enabled": True,
                "label": "IPTV-Org (alapértelmezett)",
            })

    channels: Dict[str, ET.Element] = {}
    programmes = []

    util.log_info("[EPG] Egyesítés indítása...")

    for src in sources:
        if not isinstance(src, dict):
            continue
        if not src.get("enabled", True):
            continue
        url = src.get("url") or ""
        if not url:
            continue

        root = _download_and_parse(url)
        if root is None:
            continue

        for ch in root.findall("channel"):
            cid = ch.get("id")
            if cid and cid not in channels:
                channels[cid] = ch

        for prog in root.findall("programme"):
            programmes.append(prog)

    new_root = ET.Element("tv")
    for cid in sorted(channels.keys()):
        new_root.append(channels[cid])
    for prog in programmes:
        new_root.append(prog)

    tree = ET.ElementTree(new_root)

    # hibajavítás: gondoskodunk róla, hogy a célkönyvtár létezzen
    try:
        directory = os.path.dirname(MERGED_EPG_FILE)
        if util is not None:
            util.ensure_directory(directory)
        else:
            os.makedirs(directory, exist_ok=True)
    except Exception:
        pass

    try:
        with open(MERGED_EPG_FILE, "wb") as f:
            tree.write(f, encoding="utf-8", xml_declaration=True)
        util.log_info(f"[EPG] Sikeres mentés: {MERGED_EPG_FILE}")
    except Exception as e:
        if util is not None:
            util.log_error(f"[EPG] Mentési hiba: {e}")

    # Cache-t töröljük
    global _NAME_MAP_CACHE, _NAME_MAP_MTIME
    _NAME_MAP_CACHE = {}
    _NAME_MAP_MTIME = 0.0

    return MERGED_EPG_FILE


def _build_name_map_from_merged_epg() -> Dict[str, str]:
    """
    EPG XML -> { norm_név : channel_id } térkép.

    A normalizálás itt EGYSZERŰBB, hogy kompatibilis legyen a Konyak/iptv-org
    display-name-jeivel. A normalize_name() (táblás mappinghez) agresszívebb,
    ezért itt egy kicsit finomabb logikát használunk, de a kód egyszerű kedvéért
    a normalize_name()-t is elfogadjuk fallbackként.

    MEGJEGYZÉS:
      - Itt már NEM külön merged_epg.xml-t használunk, hanem ugyanazt az
        EPG kimeneti fájlt (MERGED_EPG_FILE), amit a build_merged_epg()
        vagy az ensure_base_epg_fresh() is ír (Hungary.xml / output_epg_path).
      - A függvény eredménye CACHE-ELT: egy plugin futás alatt csak egyszer
        parszoljuk az XML-t.
    """
    global _NAME_MAP_CACHE, _NAME_MAP_MTIME

    # Ha már van cache és a fájl nem változott, azt használjuk.
    try:
        st = os.stat(MERGED_EPG_FILE)
    except OSError:
        _NAME_MAP_CACHE = {}
        _NAME_MAP_MTIME = 0.0
        return {}

    if _NAME_MAP_CACHE and _NAME_MAP_MTIME == st.st_mtime:
        return _NAME_MAP_CACHE

    name_map: Dict[str, str] = {}

    def _simple_norm(txt: str) -> str:
        if not txt:
            return ""
        s = unicodedata.normalize("NFKD", txt)
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = s.upper()
        s = re.sub(r"\.HU\s*$", "", s)
        s = re.sub(r"\s+TV$", "", s)
        buf = [ch for ch in s if ch.isalnum()]
        return "".join(buf)

    try:
        tree = ET.parse(MERGED_EPG_FILE)
        root = tree.getroot()
    except Exception:
        _NAME_MAP_CACHE = {}
        _NAME_MAP_MTIME = st.st_mtime
        return name_map

    for ch in root.findall("channel"):
        cid = ch.get("id")
        if not cid:
            continue
        for dn in ch.findall("display-name"):
            if not dn.text:
                continue
            n1 = _simple_norm(dn.text)
            if n1 and n1 not in name_map:
                name_map[n1] = cid
            # fallback kulcs a meglévő normalize_name alapján
            n2 = normalize_name(dn.text)
            if n2 and n2 not in name_map:
                name_map[n2] = cid

    _NAME_MAP_CACHE = name_map
    _NAME_MAP_MTIME = st.st_mtime

    return name_map


# ---------------------------------------------------------------------------
# Publikus API
# ---------------------------------------------------------------------------


def get_tvg_id_for_channel(
    channel_id: Optional[str],
    name: Optional[str] = None,
    source_value: Optional[str] = None,
) -> Optional[str]:
    """
    Egyetlen csatorna tvg-id-je.

    - channel_id: belső azonosító (pl. "rtl klub")
    - name: megjelenített név (pl. "RTL Klub HD")
    - source_value: opcionális, pl. "iptvorg", "konyak", "1", "2"
                    ha None, akkor addon beállítás alapján döntünk.

    LÉPÉSEK:
      1) táblás + heur. mapping (EPG_ID_MAP)
      2) ha ez nem ad találatot, akkor az EPG XML-ből próbálunk
         név alapján tvg-id-t keresni (_build_name_map_from_merged_epg).
    """
    source = _source_from_any(source_value)
    if source is EPGSource.NONE:
        return None

    # 1) próba channel_id alapján (táblás mapping)
    if channel_id:
        norm = normalize_name(channel_id)
        epg_id = _lookup_epg_id(norm, source)
        if epg_id:
            return epg_id

    # 2) próba name alapján (táblás mapping)
    if name:
        norm = normalize_name(name)
        epg_id = _lookup_epg_id(norm, source)
        if epg_id:
            return epg_id

    # 3) fallback: EPG XML alapján
    if not name:
        return None

    name_map = _build_name_map_from_merged_epg()
    if not name_map:
        return None

    # a merged_epg.xml-nél egyszerűsített normalizálást használunk
    key1 = normalize_name(name)
    if key1 in name_map:
        return name_map[key1]

    # nagyon egyszerű fallback: csak ékezetek + nagybetű
    txt = unicodedata.normalize("NFKD", name)
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = txt.upper()
    txt = re.sub(r"\.HU\s*$", "", txt)
    txt = re.sub(r"\s+TV$", "", txt)
    buf = [ch for ch in txt if ch.isalnum()]
    key2 = "".join(buf)

    return name_map.get(key2)


def apply_tvg_ids(
    channels: Iterable[Dict[str, Any]],
    source_value: Optional[str] = None,
) -> None:
    """
    Végigmegy a csatornalistán, és ha nincs tvg-id, akkor
    a megadott (vagy beállításból vett) epg_source szerint próbál hozzárendelni.

    channels: a PlaylistCoordinator állapottárolójában lévő lista/dict-ek.
    Módosításokat HELYBEN végzi (in-place).
    """
    source = _source_from_any(source_value)
    if source is EPGSource.NONE:
        return

    for ch in channels:
        # ha már van tvg-id, nem piszkáljuk
        if ch.get("tvg_id"):
            continue

        # a legjobb tippelt név
        name = (
            ch.get("tvg_name")
            or ch.get("display_name")
            or ch.get("name")
            or ch.get("channel_id")
        )

        epg_id = get_tvg_id_for_channel(
            ch.get("channel_id"),
            name,
            source_value=source.value,
        )
        if epg_id:
            ch["tvg_id"] = epg_id


def apply_tvg_ids_to_channels(channels):
    """
    VISSZAFELÉ kompatibilis wrapper a régi névre.

    Régi kód:
        channels = epg.apply_tvg_ids_to_channels(channels)

    Új működés:
        1) addon beállításból kiderítjük az EPG forrást (alap: iptv-org),
        2) apply_tvg_ids(...) hívás történik (táblás + EPG XML fallback),
        3) ugyanazt a listát adjuk vissza (in-place módosítva)
    """
    if not isinstance(channels, list):
        # mást is elviselünk, de a legtöbb helyen list az elvárás
        channels = list(channels or [])

    apply_tvg_ids(channels, source_value=None)
    return channels

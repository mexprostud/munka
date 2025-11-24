# -*- coding: utf-8 -*-
"""
Magyar IPTV M3U 'motor' – SERVER V2 (whitelist-szépnevek etalon).

FŐ ELVEK:
- Szigorú szűrés: csak a WHITELIST_DISPLAY_ORDER-ben lévő csatornák maradnak.
- Külföldi listák szűrése: idegen csatornák automatikusan kiesnek.
- Okos névfelismerés: "RTL Kettő" / "RTL II" -> "RTL 2" lesz a kimenetben.
- tvg-id: mindig a beégetett M3U_EPG_ID_MAP-ből jön (iptv-org standard).
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Tuple, Optional

# --- ÚJ IMPORT: A whitelist modul, amiben az okos névfelismerő van ---
try:
    import whitelist
except ImportError:
    # Ha véletlenül hiányozna a fájl, ne omoljon össze azonnal,
    # bár a működéshez kritikus.
    whitelist = None 

try:
    import xbmcvfs  # type: ignore
except Exception:
    xbmcvfs = None  # type: ignore

Channel = Dict[str, Any]

# ---------------------------------------------
# 1. Normalizáló/tisztító segédfüggvények
# ---------------------------------------------

_COLOR_TAG_RE = re.compile(r'\[/?COLOR[^\]]*\]', re.IGNORECASE)

# ÚJ: Szám-szöveg és római szám konverzió a "háttér" azonosításhoz.
# Ez biztosítja, hogy a "RTL Kettő", "RTL II" és "RTL 2" mind ugyanazt a kulcsot ("RTL2") adja.
_NUM_REPLACEMENTS = {
    # Római számok
    r'\bII\b': '2',
    r'\bIII\b': '3',
    r'\bIV\b': '4',
    r'\bVII\b': '7', 
    # Magyar számnevek
    r'\bEGY\b': '1',
    r'\bKETTO\b': '2',
    r'\bHAROM\b': '3',
    r'\bNEGY\b': '4',
    r'\bOT\b': '5',
    r'\bHAT\b': '6',
    r'\bHET\b': '7',
}

def _safe_str(value: Any, default: str = '') -> str:
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default

def _strip_color_tags(name: str) -> str:
    if not name:
        return ''
    return _COLOR_TAG_RE.sub('', name)

def _normalize_epg_name(name: str) -> str:
    """
    Agresszív normalizálás kulcsképzéshez.
    A cél: minden variációból (RTL Kettő, RTL II) egységes technikai kulcs (RTL2) legyen.
    """
    txt = _safe_str(name).strip()
    if not txt:
        return ''
    
    # 1. Színkódok ki
    txt = _strip_color_tags(txt)
    
    # 2. Ékezetmentesítés és nagybetűsítés
    txt_norm = unicodedata.normalize('NFD', txt)
    txt = ''.join(ch for ch in txt_norm if unicodedata.category(ch) != 'Mn')
    txt = txt.upper()
    
    # 3. Szám-szöveg egységesítés (A kulcs logika!)
    # A szöveges számokat (KETTO, II) számjegyre (2) cseréljük.
    for pattern, replacement in _NUM_REPLACEMENTS.items():
        txt = re.sub(pattern, replacement, txt)

    # 4. Speciális karakterek tisztítása
    cleaned: List[str] = []
    depth = 0
    for ch in txt:
        if ch in '([':
            depth += 1
            continue
        if ch in ')]':
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            cleaned.append(ch)
    txt = ''.join(cleaned)

    # 5. Egyéb cserék
    txt = txt.replace('+', 'PLUS')
    txt = re.sub(r'\.HU\s*$', '', txt)
    txt = re.sub(r'\.PORT\.HU\s*$', '', txt)
    txt = re.sub(r'\s+TV$', '', txt)
    txt = re.sub(r'\s+CSATORNA$', '', txt)
    
    # 6. Szóközök eltüntetése a betű és szám közül (pl. TV 2 -> TV2)
    txt = re.sub(r'(\D)\s+(\d)', r'\1\2', txt)
    
    # 7. Csak alfanumerikus karakterek maradnak
    txt = re.sub(r'[^0-9A-Z]+', '', txt)
    
    # 8. Felbontás jelzők levágása a végéről (így az M1 és M1 HD ugyanaz lesz)
    txt = re.sub(r'(HD|FHD|UHD|SD|4K|8K)$', '', txt)
    
    return txt

def _make_mapping_key(name: str) -> str:
    return _normalize_epg_name(name)

def _clean_display_name_for_user(raw_name: str) -> str:
    """Csak akkor fut, ha valamiért nem találjuk a whitelistben (fallback)."""
    s = _strip_color_tags(_safe_str(raw_name))
    out_chars: List[str] = []
    depth = 0
    for ch in s:
        if ch == '(':
            depth += 1
            continue
        if ch == ')':
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            out_chars.append(ch)
    s = ''.join(out_chars)
    s = re.sub(r'\b(HD|FHD|UHD|SD|4K|8K)\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\.\s*hu\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\.\s*port\.hu\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# ---------------------------------------------
# 2. Mapping + whitelist (szép lista az etalon)
# ---------------------------------------------

# Itt a kulcsoknak (pl. 'RTL2') már a normalizált, számjegyes formát kell követniük!
M3U_EPG_ID_MAP: Dict[str, Dict[str, str]] = {
    'AMC': {'display': 'AMC', 'tvg_id': 'AMC.hu'},
    'ANIMALPLANET': {'display': 'Animal Planet', 'tvg_id': 'ANIMAL.hu'},
    'APOSTOLTV': {'display': 'Apostol TV', 'tvg_id': 'APOSTOL TV.hu'},
    'ARENA4': {'display': 'Arena4', 'tvg_id': 'ARENA4.hu'},
    'ARENA4PLUSZ': {'display': 'Arena4+', 'tvg_id': ''}, # Nincs az XML-ben
    'AUTOMOTORSESPORT': {'display': 'Auto Motor és Sport', 'tvg_id': 'AUTO MOTOR SPORT.hu'},
    'AXN': {'display': 'AXN', 'tvg_id': 'AXN.hu'},
    'BABYTV': {'display': 'Baby TV', 'tvg_id': 'BABYTV.hu'},
    'BBCEARTH': {'display': 'BBC Earth', 'tvg_id': 'BBC EARTH.hu'},
    'BBCENTERTAINMENT': {'display': 'BBC Entertainment', 'tvg_id': ''}, # Nincs az XML-ben (csak BBCWORLD.hu van)
    'BRAZZERSTVEUROPE': {'display': 'Brazzers TV Europe', 'tvg_id': ''}, # Nincs az XML-ben
    'CARTOONITO': {'display': 'Cartoonito', 'tvg_id': 'CARTOONITO.hu'},
    'CARTOONNETWORK': {'display': 'Cartoon Network', 'tvg_id': 'CARTOON.hu'},
    'CENTOXCENTOTV': {'display': 'Cento XCento', 'tvg_id': ''}, # Nincs az XML-ben
    'CINEMAX': {'display': 'Cinemax', 'tvg_id': 'CINEMAX.hu'},
    'CINEMAX2': {'display': 'Cinemax 2', 'tvg_id': 'CINEMAX2.hu'}, # Kulcs: CINEMAX2
    'CLUBMTV': {'display': 'Club MTV', 'tvg_id': 'CLUB MTV.hu'},
    'COMEDYCENTRAL': {'display': 'Comedy Central', 'tvg_id': 'COMEDY.hu'},
    'COMEDYCENTRALFAMILY': {'display': 'Comedy Central Family', 'tvg_id': ''}, # Nincs az XML-ben
    'COOL': {'display': 'Cool', 'tvg_id': 'COOL.hu'},
    'CRIMEANDINVESTIGATION': {'display': 'Crime & Investigation', 'tvg_id': ''}, # Nincs az XML-ben
    'D1TV': {'display': 'D1 TV', 'tvg_id': 'D1TV.hu'},
    'DAVINCILEARNING': {'display': 'Da Vinci', 'tvg_id': 'DAVINCI.hu'},
    'DIKHTV': {'display': 'Dikh TV', 'tvg_id': 'DIKH TV.hu'},
    'DIRECTONE': {'display': 'Direct One', 'tvg_id': 'DIRECT ONE TV.hu'},
    'DIRECTONEHD': {'display': 'Direct One HD', 'tvg_id': 'DIRECT ONE TV.hu'},
    'DISCOVERYCHANNEL': {'display': 'Discovery Channel', 'tvg_id': 'DISCOVERY.hu'},
    'DISCOVERYSCIENCE': {'display': 'Discovery Science', 'tvg_id': ''}, # Nincs az XML-ben
    'DISNEYCHANNEL': {'display': 'Disney Channel', 'tvg_id': 'DISNEY.hu'},
    'DORCELTV': {'display': 'Dorcel TV', 'tvg_id': ''}, # Nincs az XML-ben
    'DUCKTV': {'display': 'Duck TV', 'tvg_id': 'DUCKTV.hu'},
    'DUNA': {'display': 'Duna', 'tvg_id': 'DUNA.hu'},
    'DUNAWORLD': {'display': 'Duna World', 'tvg_id': 'DUNAWORLD.hu'},
    'EPICDRAMA': {'display': 'Epic Drama', 'tvg_id': 'EPIC DRAMA.hu'},
    'EURONEWS': {'display': 'Euronews', 'tvg_id': 'EURONEWS.hu'},
    'EUROSPORT1': {'display': 'Eurosport 1', 'tvg_id': 'EUROSPORT.hu'},
    'EUROSPORT2': {'display': 'Eurosport 2', 'tvg_id': 'EUROSPORT2.hu'},
    'EWTN': {'display': 'EWTN', 'tvg_id': 'BONUM.hu'}, # Az XML-ben BONUM.hu néven fut (display: EWTN / Bonum TV)
    'EXTREMESPORTSCHANNEL': {'display': 'Extreme Sports Channel', 'tvg_id': 'EXTREMESP.hu'},
    'FASHIONTV': {'display': 'Fashion TV', 'tvg_id': 'FASHIONTV.hu'}, # Vagy FTVHD.hu is van az XML-ben
    'FILM4': {'display': 'Film4', 'tvg_id': 'FILM4.hu'},
    'FILMBOX': {'display': 'FilmBox', 'tvg_id': 'FILMBOX.hu'},
    'FILMBOXEXTRAHD': {'display': 'FilmBox Extra HD', 'tvg_id': 'FILMBOX EXTRA HD.hu'},
    'FILMBOXFAMILY': {'display': 'FilmBox Family', 'tvg_id': 'FILMBOXFAMILY.hu'},
    'FILMBOXPREMIUM': {'display': 'FilmBox Premium', 'tvg_id': 'FILMBOX PREMIUM.hu'},
    'FILMBOXSTARS': {'display': 'FilmBox Stars', 'tvg_id': 'FILMBOX STARS.hu'},
    'FILMCAFE': {'display': 'Film Café', 'tvg_id': 'FILMCAFE.hu'},
    'FILMMANIA': {'display': 'Film Mánia', 'tvg_id': 'FILMMANIA.hu'},
    'FILMNOW': {'display': 'FilmNow', 'tvg_id': ''}, # Nincs az XML-ben
    'FILMPLUS': {'display': 'Film+', 'tvg_id': 'FILMPLUS.hu'},
    'FISHINGANDHUNTING': {'display': 'Fishing & Hunting', 'tvg_id': 'FISHING HUNTING.hu'},
    'FITHD': {'display': 'Fit HD', 'tvg_id': 'FIT HD.hu'},
    'FOODNETWORK': {'display': 'Food Network', 'tvg_id': 'FOODNETWORK.hu'},
    'GALAXY4': {'display': 'Galaxy4', 'tvg_id': 'GALAXY.hu'},
    'HATOSCSATORNA': {'display': 'Hatoscsatorna', 'tvg_id': ''}, # Nincs az XML-ben
    'HBO': {'display': 'HBO', 'tvg_id': 'HBOHD.hu'},
    'HBO2': {'display': 'HBO 2', 'tvg_id': 'HBO2.hu'},
    'HBO3': {'display': 'HBO 3', 'tvg_id': 'HBO3.hu'},
    'HETITV': {'display': 'Heti TV', 'tvg_id': 'HETI TV.hu'},
    'HGTV': {'display': 'HGTV', 'tvg_id': 'HGTV.hu'},
    'HIRTV': {'display': 'HírTV', 'tvg_id': 'HIRTV.hu'},
    'HISTORY': {'display': 'History', 'tvg_id': 'HISTORYHD.hu'},
    'HISTORY2': {'display': 'History 2', 'tvg_id': ''}, # Nincs az XML-ben
    'HUSTLERTV': {'display': 'Hustler TV', 'tvg_id': ''}, # Nincs az XML-ben
    'ID': {'display': 'ID', 'tvg_id': 'ID.hu'},
    'IZAURATV': {'display': 'Izaura TV', 'tvg_id': 'IZAURA TV.hu'},
    'JIMJAM': {'display': 'JimJam', 'tvg_id': 'JIMJAM.hu'},
    'JOCKYTV': {'display': 'Jocky TV', 'tvg_id': 'JOCKYTV.hu'},
    'KOLYOKKLUB': {'display': 'Kölyökklub', 'tvg_id': 'Kolyokklub.hu'},	
    'LIFETV': {'display': 'LifeTV', 'tvg_id': 'LIFE TV.hu'},
    'LOVENATURE': {'display': 'Love Nature', 'tvg_id': 'LOVE NATURE.hu'},
    'M1': {'display': 'M1', 'tvg_id': 'M1.hu'},
    'M2PETOFITV': {'display': 'M2 / Petőfi TV', 'tvg_id': 'M2.hu'},
    'M4SPORT': {'display': 'M4 Sport', 'tvg_id': 'M4 SPORT.hu'},
    'M4SPORTPLUSZ': {'display': 'M4 Sport+', 'tvg_id': 'M4 SPORT PLUSZ.hu'},
    'M5': {'display': 'M5', 'tvg_id': 'M5.hu'},
    'MAGYARMOZITV': {'display': 'Magyar Mozi TV', 'tvg_id': 'MAGYAR MOZI TV.hu'},
    'MATCH4': {'display': 'Match4', 'tvg_id': 'MATCH4.hu'},
    'MAX4': {'display': 'MAX4', 'tvg_id': 'MAX4.hu'},
    'MEZZO': {'display': 'Mezzo', 'tvg_id': 'MEZZO.hu'},
    'MEZZOLIVEHD': {'display': 'Mezzo Live', 'tvg_id': 'MEZZOHD.hu'},
    'MINIMAX': {'display': 'Minimax', 'tvg_id': 'MINIMAX.hu'},
    'MOZIPLUSZ': {'display': 'Mozi+', 'tvg_id': 'MOZI PLUSZ.hu'},
    'MOZIVERZUM': {'display': 'Moziverzum', 'tvg_id': 'MOZIVERZUM.hu'},
    'MTV00S': {'display': 'MTV 00s', 'tvg_id': 'MTV00S.hu'},
    'MTV80S': {'display': 'MTV 80s', 'tvg_id': 'MTV80S.hu'},
    'MTV90S': {'display': 'MTV 90s', 'tvg_id': 'MTV90S.hu'},
    'MTVEUROPE': {'display': 'MTV Europe', 'tvg_id': 'MTV EURO.hu'},
    'MTVHITS': {'display': 'MTV Hits', 'tvg_id': 'MTVHITS.hu'},
    'MTVLIVEHD': {'display': 'MTV Live', 'tvg_id': 'MTVLIVEHD.hu'},
    'MUZSIKATV': {'display': 'Muzsika TV', 'tvg_id': 'MUZSIKATV.hu'},
    'NATGEOWILD': {'display': 'Nat Geo Wild', 'tvg_id': 'NATGEOWILD.hu'},
    'NATIONALGEOGRAPHIC': {'display': 'National Geographic', 'tvg_id': 'NATGEO.hu'},
    'NICKELODEON': {'display': 'Nickelodeon', 'tvg_id': 'NICKELODEON.hu'}, # Vagy NICKELODEONHD.hu
    'NICKJR': {'display': 'Nick Jr.', 'tvg_id': 'NICKJR.hu'},
    'NICKTOONS': {'display': 'Nicktoons', 'tvg_id': 'NICKTOONS.hu'},
    'OZONETV': {'display': 'OzoneTV', 'tvg_id': 'OZONE TV.hu'},
    'PARAMOUNTNETWORK': {'display': 'Paramount Network', 'tvg_id': 'PARAMOUNT.hu'},
    'PASSIONXXX': {'display': 'Passion XXX', 'tvg_id': ''}, # Nincs az XML-ben
    'PAX': {'display': 'Pax', 'tvg_id': 'PAX.hu'},
    'PLAYBOYTV': {'display': 'Playboy TV', 'tvg_id': ''}, # Nincs az XML-ben
    'PRIME': {'display': 'Prime', 'tvg_id': 'PRIME.hu'},
    'PRIVATE': {'display': 'Private TV', 'tvg_id': ''}, # Nincs az XML-ben
    'REDLIGHTHD': {'display': 'Redlight HD', 'tvg_id': ''}, # Nincs az XML-ben
    
    # FRISSÍTVE: Kulcsok most már a számjegyesek!
    'RTL': {'display': 'RTL', 'tvg_id': 'RTL.hu'},
    'RTLGOLD': {'display': 'RTL Gold', 'tvg_id': 'RTL GOLD.hu'},
    'RTL3': {'display': 'RTL 3', 'tvg_id': 'RTL3.hu'}, # RTL3 kulcs, RTL 3 név
    'RTL2': {'display': 'RTL 2', 'tvg_id': 'RTL2.hu'}, # RTL2 kulcs, RTL 2 név
    'RTLOTHON': {'display': 'RTL Otthon', 'tvg_id': 'RTL OTTHON.hu'},
    
    'SLAGERTV': {'display': 'Sláger TV', 'tvg_id': 'SLAGERTV.hu'},
    'SOROZATPLUSZ': {'display': 'Sorozat+', 'tvg_id': 'SOROZAT.hu'},
    'SPEKTRUM': {'display': 'Spektrum', 'tvg_id': 'SPEKTRUM.hu'},
    'SPEKTRUMHOME': {'display': 'Spektrum Home', 'tvg_id': 'SPEKTRUMHOME.hu'},
    'SPILER1TV': {'display': 'Spíler 1', 'tvg_id': 'SPILER TV.hu'}, # Kulcs: SPILER1TV (mert Spíler 1 -> SPILER1 -> SPILER1TV alias)
    'SPILER2TV': {'display': 'Spíler 2', 'tvg_id': 'SPILER2 TV.hu'},
    'SPORT1': {'display': 'Sport1', 'tvg_id': 'SPORT1.hu'},
    'SPORT2': {'display': 'Sport2', 'tvg_id': 'SPORT2.hu'},
    'STARCHANNEL': {'display': 'STAR Channel', 'tvg_id': ''}, # Nincs az XML-ben
    'STINGRAYCLASSICA': {'display': 'Stingray Classica', 'tvg_id': 'CLASSICA.hu'},
    'STINGRAYICONCERTS': {'display': 'Stingray iConcerts', 'tvg_id': 'https://musor.tv/images/stingray_iconcerts.svgCERTS.hu'}, # Hibásnak tűnő ID az XML-ben, de ez van ott
    'STORY4': {'display': 'Story4', 'tvg_id': 'STORY4.hu'},
    'SUPERONE': {'display': 'SuperOne', 'tvg_id': ''}, # Nincs az XML-ben
    'SUPERTV2': {'display': 'SuperTV2', 'tvg_id': 'SUPERTV2.hu'},
    'TEENNICK': {'display': 'TeenNick', 'tvg_id': 'TEENNICK.hu'},
    'THEHISTORYCHANNEL': {'display': 'The History Channel', 'tvg_id': 'HISTORYHD.hu'}, # Duplikált History
    'TLC': {'display': 'TLC', 'tvg_id': 'TLC.hu'},
    'TRAVELCHANNEL': {'display': 'Travel Channel', 'tvg_id': 'TRAVEL.hu'},
    'TRAVELXP': {'display': 'Travel XP', 'tvg_id': 'TRAVELXP.hu'}, # Van TRAVELXP 4K.hu is
    'TV2': {'display': 'TV2', 'tvg_id': 'TV2.hu'},
    'TV2COMEDY': {'display': 'TV2 Comedy', 'tvg_id': 'TV2 COMEDY.hu'},
    'TV2KIDS': {'display': 'TV2 Kids', 'tvg_id': 'TV2 KIDS.hu'},
    'TV2KLUB': {'display': 'TV2 Klub', 'tvg_id': 'TV2 KLUB.hu'},
    'TV2PAPRIKA': {'display': 'TV Paprika', 'tvg_id': 'PAPRIKA.hu'},
    'TV2SEF': {'display': 'TV2 Séf', 'tvg_id': 'TV2 SEF.hu'},
    'TV4': {'display': 'TV4', 'tvg_id': 'TV4.hu'},
    'VIASAT2': {'display': 'VIASAT2', 'tvg_id': 'VIASAT2.hu'},
    'VIASAT3': {'display': 'VIASAT3', 'tvg_id': 'VIASAT3.hu'},
    'VIASAT6': {'display': 'VIASAT6', 'tvg_id': 'VIASAT6.hu'},
    'VIASATEXPLORE': {'display': 'Viasat Explore', 'tvg_id': 'VIASATEXP.hu'},
    'VIASATHISTORY': {'display': 'Viasat History', 'tvg_id': 'VIASATHIST.hu'},
    'VIASATNATURE': {'display': 'Viasat Nature', 'tvg_id': 'VIASATNAT.hu'},
    'VIASATTRUECRIME': {'display': 'Viasat True Crime', 'tvg_id': ''}, # Nincs az XML-ben
    'VIASTATFILM': {'display': 'VIASAT FILM', 'tvg_id': 'VIASAT FILM.hu'},
    'VIVIDTV': {'display': 'Vivid TV', 'tvg_id': ''}, # Nincs az XML-ben
    'ZENEBUTIK': {'display': 'Zenebutik', 'tvg_id': 'ZENEBUTIK.hu'},
}

# FRISSÍTVE: A Whitelist most már a kívánt "RTL 2", "RTL 3" formátumot tartalmazza.
# A külföldi listákból érkező "RTL Kettő" vagy "RTL II" a normalizálás után
# illeszkedni fog erre.

# ===========================================================================
# 1. OKOS NÉVFELISMERŐ MODUL (ÚJ FEJLESZTÉS)
# ===========================================================================

# ALIAS ADATBÁZIS
# Kulcs = A "szép név" csupa nagybetűvel (így könnyű lesz visszaalakítani).
# Érték = Lista a lehetséges variációkról, amivel kezdődhet a név.
# A sorrend a listákban fontos: a leghosszabb variációk vannak elöl.

ALIAS_DB: Dict[str, List[str]] = {
    # --- ORSZÁGOS KERESKEDELMI / KÖZSZOLGÁLATI ---
    'RTL':              ['RTL KLUB', 'RTLKLUB', 'RTL'], 
    'TV2':              ['TV2', 'TV 2'],
    'RTL GOLD':         ['RTL GOLD', 'RTLGOLD'],
    'RTL 2':            ['RTL 2', 'RTL2', 'RTL KETTO', 'RTL II', 'RTLKETTO'],
    'RTL 3':            ['RTL 3', 'RTL3', 'RTL HAROM', 'RTL III', 'RTLHAROM', 'RTL+'],
    'RTL OTTHON':       ['RTL OTTHON', 'RTLOTTHON'],
    'TV PAPRIKA':       ['TV PAPRIKA', 'TVPAPRIKA', 'PAPRIKA TV', 'PAPRIKA'],
    'TV2 COMEDY':       ['TV2 COMEDY', 'TV2COMEDY'],
    'TV2 KLUB':         ['TV2 KLUB', 'TV2KLUB', 'FEM3'],
    'TV2 SÉF':          ['TV2 SEF', 'TV2SEF'], 
    'TV4':              ['TV4', 'TV 4'],
    'SUPERTV2':         ['SUPER TV2', 'SUPERTV2', 'SUPER TV 2'],
    'ATV SPIRIT':       ['ATV SPIRIT', 'ATVSPIRIT'], 
    'ATV':              ['ATV'],
    'M1':               ['M1'],
    'M2 / PETŐFI TV':   ['M2 / PETOFI', 'M2 PETOFI', 'M2'], 
    'M5':               ['M5'],
    'M4 SPORT+':        ['M4 SPORT+', 'M4 SPORT PLUS', 'M4SPORT+', 'M4SPORTPLUS', 'M4 +', 'M4+'],
    'M4 SPORT':         ['M4 SPORT', 'M4SPORT', 'M4'], 
    'DUNA WORLD':       ['DUNA WORLD', 'DUNAWORLD'],
    'DUNA':             ['DUNA TV', 'DUNA'],
    'HÍRTV':            ['HIR TV', 'HIRTV', 'HIR'], 

    # --- SPORT ---
    'SPÍLER 1':         ['SPILER 1', 'SPILER1', 'TV2 SPILER 1', 'TV2 SPILER1'], 
    'SPÍLER 2':         ['SPILER 2', 'SPILER2', 'TV2 SPILER 2', 'TV2 SPILER2'], 
    'SPORT1':           ['SPORT 1', 'SPORT1'],
    'SPORT2':           ['SPORT 2', 'SPORT2'],
    'ARENA4':           ['ARENA 4', 'ARENA4'],
    'MATCH4':           ['MATCH 4', 'MATCH4'],
    'EUROSPORT 1':      ['EUROSPORT 1', 'EUROSPORT1'],
    'EUROSPORT 2':      ['EUROSPORT 2', 'EUROSPORT2'],
    'AUTO MOTOR ÉS SPORT': ['AUTO MOTOR ES SPORT', 'AUTO MOTOR', 'AUTOMOTOR'], 
    'EXTREME SPORTS CHANNEL': ['EXTREME SPORTS CHANNEL', 'EXTREME SPORTS', 'EXTREMESPORTS'],
    'FISHING & HUNTING': ['FISHING & HUNTING', 'FISHING AND HUNTING', 'FISHING HUNTING', 'F&H'],
    'FIT HD':           ['FIT HD', 'FIT TV', 'FIT'],
    'DIGI SPORT 1':     ['DIGI SPORT 1', 'DIGISPORT 1', 'DIGISPORT1'],
    'DIGI SPORT 2':     ['DIGI SPORT 2', 'DIGISPORT 2', 'DIGISPORT2'],

    # --- FILM / SOROZAT ---
    'AMC':              ['AMC'],
    'AXN':              ['AXN'], 
    'COOL':             ['COOL'],
    'FILM+':            ['FILM+', 'FILM +', 'FILM PLUS', 'FILMPLUS'],
    'FILM4':            ['FILM 4', 'FILM4'],
    'FILM MÁNIA':       ['FILM MANIA', 'FILMMANIA'], 
    'FILM CAFÉ':        ['FILM CAFE', 'FILMCAFE'],   
    'FILMBOX PREMIUM':  ['FILMBOX PREMIUM', 'FILMBOXPREMIUM'],
    'FILMBOX EXTRA HD': ['FILMBOX EXTRA HD', 'FILMBOX EXTRA', 'FILMBOXEXTRA'],
    'FILMBOX FAMILY':   ['FILMBOX FAMILY', 'FILMBOXFAMILY'],
    'FILMBOX STARS':    ['FILMBOX STARS', 'FILMBOXSTARS', 'FILMBOX PLUS'],
    'FILMBOX':          ['FILMBOX'], 
    'FILMNOW':          ['FILM NOW', 'FILMNOW', 'DIGI FILM'],
    'HBO 2':            ['HBO 2', 'HBO2'],
    'HBO 3':            ['HBO 3', 'HBO3'],
    'HBO':              ['HBO'], 
    'CINEMAX 2':        ['CINEMAX 2', 'CINEMAX2'],
    'CINEMAX':          ['CINEMAX'],
    'PARAMOUNT NETWORK':['PARAMOUNT NETWORK', 'PARAMOUNT CHANNEL', 'PARAMOUNT'],
    'PRIME':            ['PRIME'],
    'MOZI+':            ['MOZI+', 'MOZI +', 'MOZI PLUS', 'MOZIPLUS', 'MOZI HD'],
    'MOZIVERZUM':       ['MOZIVERZUM'],
    'MAGYAR MOZI TV':   ['MAGYAR MOZI TV', 'MAGYAR MOZI', 'MAGYARMOZI'],
    'SOROZAT+':         ['SOROZAT+', 'SOROZAT +', 'SOROZAT PLUS', 'SOROZATPLUS'],
    'STORY4':           ['STORY 4', 'STORY4'],
    'JOCKY TV':         ['JOCKY TV', 'JOCKYTV', 'JOCKY'],
    'IZAURA TV':        ['IZAURA TV', 'IZAURATV', 'IZAURA'],
    'EPIC DRAMA':       ['EPIC DRAMA', 'EPICDRAMA'],
    'VIASAT2':          ['VIASAT 2', 'VIASAT2', 'SONY MAX'],
    'VIASAT3':          ['VIASAT 3', 'VIASAT3'],
    'VIASAT6':          ['VIASAT 6', 'VIASAT6'],
    'VIASAT FILM':      ['VIASAT FILM', 'VIASATFILM', 'SONY MOVIE'],
    'STAR CHANNEL':     ['STAR CHANNEL', 'STARCHANNEL'], 
    'MAX4':             ['MAX4', 'MAX 4'],

    # --- ISMERETTERJESZTŐ ---
    'DISCOVERY SCIENCE':['DISCOVERY SCIENCE', 'DISC SCIENCE'],
    'DISCOVERY CHANNEL':['DISCOVERY CHANNEL', 'DISCOVERY'],
    'ANIMAL PLANET':    ['ANIMAL PLANET'],
    'BBC EARTH':        ['BBC EARTH'],
    'NAT GEO WILD':     ['NAT GEO WILD', 'NATGEO WILD', 'NATGEOWILD'],
    'NATIONAL GEOGRAPHIC': ['NATIONAL GEOGRAPHIC', 'NAT GEO', 'NATGEO'], 
    'SPEKTRUM HOME':    ['SPEKTRUM HOME', 'SPEKTRUMHOME'],
    'SPEKTRUM':         ['SPEKTRUM'],
    'HISTORY 2':        ['HISTORY 2', 'HISTORY2'],
    'HISTORY':          ['THE HISTORY CHANNEL', 'HISTORY CHANNEL', 'HISTORY'],
    'VIASAT EXPLORE':   ['VIASAT EXPLORE', 'VIASATEXPLORE'],
    'VIASAT HISTORY':   ['VIASAT HISTORY', 'VIASATHISTORY'],
    'VIASAT NATURE':    ['VIASAT NATURE', 'VIASATNATURE'],
    'VIASAT TRUE CRIME':['VIASAT TRUE CRIME', 'VIASAT CRIME'],
    'LOVE NATURE':      ['LOVE NATURE', 'LOVENATURE'],
    'OZONETV':          ['OZONE TV', 'OZONETV', 'OZONE'],
    'TRAVEL XP':        ['TRAVEL XP', 'TRAVELXP'],
    'TRAVEL CHANNEL':   ['TRAVEL CHANNEL', 'TRAVEL'],
    'HGTV':             ['HGTV'],
    'FOOD NETWORK':     ['FOOD NETWORK', 'FOOD'],
    'TLC':              ['TLC'],
    'GALAXY4':          ['GALAXY 4', 'GALAXY4', 'GALAXY'],
    'DA VINCI':         ['DA VINCI', 'DAVINCI'],
    'DIGI ANIMAL WORLD':['DIGI ANIMAL WORLD', 'DIGI WORLD', 'DIGI ANIMAL'],
    'DIGI LIFE':        ['DIGI LIFE', 'DIGILIFE'],
    'ID':               ['ID', 'INVESTIGATION DISCOVERY', 'DISCOVERY ID'],
    'CRIME & INVESTIGATION': ['CRIME & INVESTIGATION', 'CRIME AND INVESTIGATION', 'CRIME INV'],

    # --- GYEREK ---
    'NICKELODEON':      ['NICKELODEON'],
    'NICK JR.':         ['NICK JR', 'NICKJR'], 
    'NICKTOONS':        ['NICKTOONS'],
    'TEENNICK':         ['TEENNICK', 'TEEN NICK'],
    'MINIMAX':          ['MINIMAX'],
    'JIMJAM':           ['JIMJAM', 'JIM JAM'],
    'DISNEY CHANNEL':   ['DISNEY CHANNEL', 'DISNEY'],
    'DISNEY JUNIOR':    ['DISNEY JUNIOR', 'DISNEY JR'], 
    'CARTOON NETWORK':  ['CARTOON NETWORK', 'CARTOON'],
    'CARTOONITO':       ['CARTOONITO', 'BOOMERANG'],
    'DUCK TV':          ['DUCK TV', 'DUCKTV', 'DUCK'],
    'BABY TV':          ['BABY TV', 'BABYTV'],
    'KÖLYÖKKLUB':       ['KOLYOKKLUB', 'KOLYOK KLUB'], 
    'TV2 KIDS':         ['TV2 KIDS', 'TV2KIDS', 'KIWI'],

    # --- ZENE ---
    'MTV 00S':          ['MTV 00S', 'MTV 00', 'MTV00S'],
    'MTV 80S':          ['MTV 80S', 'MTV 80', 'MTV80S'],
    'MTV 90S':          ['MTV 90S', 'MTV 90', 'MTV90S'],
    'MTV HITS':         ['MTV HITS', 'MTVHITS'],
    'MTV LIVE':         ['MTV LIVE', 'MTVLIVE'],
    'MTV EUROPE':       ['MTV EUROPE', 'MTV'], 
    'CLUB MTV':         ['CLUB MTV'],
    'ZENEBUTIK':        ['ZENEBUTIK'],
    'SLÁGER TV':        ['SLAGER TV', 'SLAGERTV'], 
    'MUZSIKA TV':       ['MUZSIKA TV', 'MUZSIKATV'],
    'MEZZO LIVE':       ['MEZZO LIVE'],
    'MEZZO':            ['MEZZO'],
    'STINGRAY CLASSICA':['STINGRAY CLASSICA', 'CLASSICA'],
    'STINGRAY ICONCERTS':['STINGRAY ICONCERTS', 'ICONCERTS'],

    # --- EGYÉB / FELNŐTT ---
    'COMEDY CENTRAL FAMILY': ['COMEDY CENTRAL FAMILY', 'COMEDY FAMILY'],
    'COMEDY CENTRAL':   ['COMEDY CENTRAL', 'COMEDY'],
    'LIFETV':           ['LIFETV', 'LIFE TV'],
    'DIKH TV':          ['DIKH TV', 'DIKHTV', 'DIKH'],
    'HETI TV':          ['HETI TV', 'HETITV'],
    'HATOSCSATORNA':    ['HATOSCSATORNA', 'HATOS'],
    'FIX TV':           ['FIX TV', 'FIXTV'],
    'D1 TV':            ['D1 TV', 'D1TV', 'D1'],
    'APOSTOL TV':       ['APOSTOL TV', 'APOSTOL'],
    'PAX':              ['PAX'],
    'EWTN':             ['EWTN', 'BONUM'],
    'SUPERONE':         ['SUPER ONE', 'SUPERONE'],
    'HUSTLER TV':       ['HUSTLER TV', 'HUSTLER'],
    'BRAZZERS TV EUROPE': ['BRAZZERS TV EUROPE', 'BRAZZERS'],
    'PRIVATE TV':       ['PRIVATE TV', 'PRIVATE'],
    'PLAYBOY TV':       ['PLAYBOY TV', 'PLAYBOY'],
    'VIVID TV':         ['VIVID TV', 'VIVID'],
    'PASSION XXX':      ['PASSION XXX', 'PASSION'],
    'REDLIGHT HD':      ['REDLIGHT HD', 'REDLIGHT'],
    'DORCEL TV':        ['DORCEL TV', 'DORCEL'],
    'CENTO XCENTO':     ['CENTO X CENTO', 'CENTOXCENTO'],
    'DIRECT ONE HD':    ['DIRECT ONE', 'DIRECTONE'], 
}


# MOTOR MODUL
def _build_lookup_list(alias_db):
    """
    Létrehoz egy keresési listát, és AUTOMATIZÁLJA a variációkat.
    """
    lookup = []
    for internal_id, aliases in alias_db.items():
        for alias in aliases:
            # 1. Hozzáadjuk az eredeti aliast (szóközös verzió)
            lookup.append((alias, internal_id))
            
            # 2. AUTOMATIZÁLÁS: Hozzáadjuk a szóköz nélküli verziót is
            no_space_alias = alias.replace(' ', '')
            if no_space_alias != alias:
                lookup.append((no_space_alias, internal_id))
    
    # Rendezés: Hossz csökkenő sorrendben (hosszabb alias elöl)
    lookup.sort(key=lambda x: len(x[0]), reverse=True)
    return lookup

# Ezt hívjuk meg egyszer a program elején
_SORTED_ALIASES = _build_lookup_list(ALIAS_DB)


def identify_channel_id(raw_name: str) -> Optional[str]:
    """
    Bemenet: Bármilyen nyers csatornanév
    Kimenet: A "Szép Név" (nagybetűs belső kulcs) VAGY None.
    Automatikusan levágja a szemetet a név végéről.
    """
    if not raw_name:
        return None

    # 1. Tisztítás (Színkódok, Ékezetek, Kis-nagybetű)
    # Minden nem alfanumerikus jel cseréje szóközre (beleértve az aláhúzást is!)
    nfkd_form = unicodedata.normalize('NFKD', str(raw_name))
    only_ascii = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    cleaned = re.sub(r'[^A-Z0-9]', ' ', only_ascii.upper())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    if not cleaned:
        return None

    # 2. Keresés a rendezett alias listában
    for alias, internal_id in _SORTED_ALIASES:
        # A tisztított név KEZDŐDIK valamelyik aliasszal?
        if cleaned.startswith(alias):
            # Ellenőrzés: Az alias után közvetlenül vége van a stringnek, vagy szóköz jön?
            remaining = cleaned[len(alias):]
            if not remaining or remaining.startswith(' '):
                return internal_id

    return None

WHITELIST_DISPLAY_ORDER: List[str] = [
    'RTL',
    'TV2',
    'RTL Gold',
    'RTL 3',        # Kérésre: RTL 3
    'RTL 2',        # Kérésre: RTL 2
    'RTL Otthon',
    'TV Paprika',
    'TV2 Comedy',
    'TV2 Klub',
    'TV2 Séf',
    'TV4',
    'SuperTV2',
    'ATV',
    'ATV Spirit',
    'M1',
    'M2 / Petőfi TV',
    'M5',
    'M4 Sport',
    'M4 Sport+',
    'Spíler 1',
    'Spíler 2',
    'Sport1',
    'Sport2',
    'Arena4',
    'Auto Motor és Sport',
    'AMC',
    'Animal Planet',
    'AXN',
    'BBC Earth',
    'Cartoon Network',
    'Cartoonito',
    'Cento XCento',
    'Cinemax',
    'Cinemax 2',
    'Club MTV',
    'Comedy Central',
    'Comedy Central Family',
    'Cool',
    'Crime & Investigation',
    'D1 TV',
    'Da Vinci',
    'Dikh TV',
    'Direct One HD',
    'Discovery Channel',
    'Discovery Science',
    'Disney Channel',
    'Dorcel TV',
    'Duck TV',
    'Duna',
    'Duna World',
    'Epic Drama',
    'Euronews',
    'Eurosport 1',
    'Eurosport 2',
    'EWTN',
    'Extreme Sports Channel',
    'Fashion TV',
    'Film Café',
    'Film Mánia',
    'Film+',
    'Film4',
    'FilmBox',
    'FilmBox Extra HD',
    'FilmBox Family',
    'FilmBox Premium',
    'FilmBox Stars',
    'FilmNow',
    'Fishing & Hunting',
    'Fit HD',
    'Food Network',
    'Galaxy4',
    'Hatoscsatorna',
    'HBO',
    'HBO 2',
    'HBO 3',
    'Heti TV',
    'HGTV',
    'HírTV',
    'History',
    'History 2',
    'Hustler TV',
    'ID',
    'Izaura TV',
    'JimJam',
    'Jocky TV',
    'LifeTV',
    'Love Nature',
    'Magyar Mozi TV',
    'Match4',
    'MAX4',
    'Mezzo',
    'Mezzo Live',
    'Minimax',
    'Mozi+',
    'Moziverzum',
    'Nat Geo Wild',
    'National Geographic',
    'Nick Jr.',
    'Nickelodeon',
    'Nicktoons',
    'OzoneTV',
    'Paramount Network',
    'Prime',
    'Private TV',
    'Redlight HD',
    'VIASAT2',
    'VIASAT3',
    'VIASAT6',
    'VIASAT FILM',
    'Sorozat+',
    'Spektrum',
    'Spektrum Home',
    'Spíler 1',
    'Spíler 2',
    'Sport1',
    'Sport2',
    'STAR Channel',
    'Story4',
    'SuperOne',
    'TeenNick',
    'The History Channel',
    'TLC',
    'Travel Channel',
    'Travel XP',
    'Baby TV',
    'TV2 Kids',
    'Kölyökklub',	
    'Viasat Explore',
    'Viasat History',
    'Viasat Nature',
    'Viasat True Crime',
    'Vivid TV',
    'MTV 00s',
    'MTV 80s',
    'MTV 90s',
    'MTV Europe',
    'MTV Hits',
    'MTV Live',
    'Muzsika TV',
    'Sláger TV',
    'Zenebutik',
    'Stingray Classica',
    'Stingray iConcerts',
    'Apostol TV',
    'Pax',
    'Passion XXX',
    'Brazzers TV Europe',
    'Playboy TV',
]

def _build_whitelist_index() -> Dict[str, int]:
    idx: Dict[str, int] = {}
    order = 0
    for name in WHITELIST_DISPLAY_ORDER:
        key = _make_mapping_key(name)
        if key and key not in idx:
            idx[key] = order
            order += 1
    return idx

_WHITELIST_ORDER_INDEX: Dict[str, int] = _build_whitelist_index()

def _build_whitelist_display_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for name in WHITELIST_DISPLAY_ORDER:
        key = _make_mapping_key(name)
        if key and key not in mapping:
            mapping[key] = name
    return mapping

_WHITELIST_DISPLAY_BY_KEY: Dict[str, str] = _build_whitelist_display_map()

# Csökkentett alias lista - a legtöbb dolgot már a normalizáló megoldja
# MÓDOSÍTÁS: A régi alias rendszer teljesen kikötve, mert a whitelist.py
# modul végzi a csatornák helyes azonosítását.
# M3U_EPG_NAME_ALIASES: Dict[str, str] = {
#     'RTLKLUB': 'RTL', # RTL Klub -> RTL
#     'KOLYOKKLUB': 'KOLYOKKLUB',
#     'DIKH': 'DIKHTV',
#     'IZAURA': 'IZAURATV',
#     'M2': 'M2PETOFITV',
#     'SPILER1': 'SPILER1TV', 
#     'SPILER2': 'SPILER2TV',
# }

# ---------------------------------------------
# 3. Mediaklikk FIXED_PUBLIC_CHANNELS
# ---------------------------------------------

FIXED_PUBLIC_CHANNELS: List[Channel] = [
    {
        'display_name': 'M1 HD',
        'tvg_id': 'M1 HD',
        'tvg_name': 'M1 HD',
        'group_title': 'Közszolgálati',
        'tvg_logo': 'm1.png',
        'kodiprops': ['inputstream=inputstream.adaptive', 'inputstream.adaptive.manifest_type=hls', 'mimetype=application/x-mpegURL'],
        'url': 'plugin://plugin.video.mediaklikk/?action=resolve&url=mtv1live&mediatype=tv&title=M1',
    },
    {
        'display_name': 'M2 / Petőfi TV HD',
        'tvg_id': 'M2 / Petőfi TV HD',
        'tvg_name': 'M2 / Petőfi TV HD',
        'group_title': 'Közszolgálati',
        'kodiprops': ['inputstream=inputstream.adaptive', 'inputstream.adaptive.manifest_type=hls', 'mimetype=application/x-mpegURL'],
        'url': 'plugin://plugin.video.mediaklikk/?action=resolve&url=mtv2live&mediatype=tv&title=M2',
    },
    {
        'display_name': 'Duna HD',
        'tvg_id': 'DUNA HD',
        'tvg_name': 'DUNA HD',
        'group_title': 'Közszolgálati',
        'tvg_logo': 'duna.png',
        'kodiprops': ['inputstream=inputstream.adaptive', 'inputstream.adaptive.manifest_type=hls', 'mimetype=application/x-mpegURL'],
        'url': 'plugin://plugin.video.mediaklikk/?action=resolve&url=dunalive&mediatype=tv&title=Duna',
    },
    {
        'display_name': 'Duna World HD',
        'tvg_id': 'DUNA W/M4 Sport+ HD',
        'tvg_name': 'DUNA W/M4 Sport+ HD',
        'group_title': 'Közszolgálati',
        'tvg_logo': 'duna world.png',
        'kodiprops': ['inputstream=inputstream.adaptive', 'inputstream.adaptive.manifest_type=hls', 'mimetype=application/x-mpegURL'],
        'url': 'plugin://plugin.video.mediaklikk/?action=resolve&url=mtv4plus&mediatype=tv&title=Duna%20World',
    },
    {
        'display_name': 'M5 HD',
        'tvg_id': 'M5 HD',
        'tvg_name': 'M5 HD',
        'group_title': 'Közszolgálati',
        'tvg_logo': 'm5.png',
        'kodiprops': ['inputstream=inputstream.adaptive', 'inputstream.adaptive.manifest_type=hls', 'mimetype=application/x-mpegURL'],
        'url': 'plugin://plugin.video.mediaklikk/?action=resolve&url=mtv5live&mediatype=tv&title=M5',
    },
    {
        'display_name': 'M4 Sport HD',
        'tvg_id': 'M4 Sport HD',
        'tvg_name': 'M4 Sport HD',
        'group_title': 'Sport',
        'tvg_logo': 'm4 sport.png',
        'kodiprops': ['inputstream=inputstream.adaptive', 'inputstream.adaptive.manifest_type=hls', 'mimetype=application/x-mpegURL'],
        'url': 'plugin://plugin.video.mediaklikk/?action=resolve&url=mtv4live&mediatype=tv&title=M4%20Sport',
    },
]

# ---------------------------------------------
# 4. Opcionális saját logó (alapból kikapcsolva)
# ---------------------------------------------

USE_LOCAL_LOGO_FILES: bool = True
LOCAL_LOGO_FOLDER: str = 'special://home/addons/plugin.video.streammex/logos'
LOCAL_LOGO_EXTENSION: str = '.png'

def _build_local_logo_value(display_name: str) -> str:
    if not USE_LOCAL_LOGO_FILES:
        return ''
    name = _safe_str(display_name).strip()
    if not name:
        return ''
    
    # Biztonságos logó fájlnév: ékezetmentes, kisbetűs
    # Pl. "RTL 2" -> "rtl 2.png"
    txt_norm = unicodedata.normalize('NFD', name)
    name_no_accents = ''.join(ch for ch in txt_norm if unicodedata.category(ch) != 'Mn')
    
    return f'{LOCAL_LOGO_FOLDER}/{name_no_accents.lower()}{LOCAL_LOGO_EXTENSION}'

# ---------------------------------------------
# 5. EPG-mapping + whitelist alkalmazás
# ---------------------------------------------

def _apply_epg_mapping_and_whitelist(channels: Iterable[Channel],
                                     whitelist_only: bool = True,
                                     enable_channel_package: bool = False, 
                                     channel_package_name: Optional[str] = None,
                                     channel_packages_data: Optional[Dict[str, Any]] = None, 
                                     ) -> List[Channel]:
    
    mapped: List[Channel] = []
    
    # -------------------------------------------------------------------------
    # ÚJ: Csomag szűrő inicializálása
    # -------------------------------------------------------------------------
    allowed_package_keys: Optional[set[str]] = None
    if enable_channel_package:
        pkg_id = (channel_package_name or '').strip()
        pkg = (channel_packages_data or {}).get(pkg_id)
        if pkg and pkg_id:
            raw_names = pkg.get('channels') or []
            # A csomagban lévő nevek kanonizálása (ugyanaz a logika, mint a M3U_EPG_ID_MAP-hez)
            allowed_package_keys = set(_make_mapping_key(n) for n in raw_names if n)

    for ch in channels:
        new_ch: Channel = dict(ch)

        raw_name = _safe_str(
            new_ch.get('display_name')
            or new_ch.get('name')
            or new_ch.get('channel_id')
            or ''
        ).strip()
        if not raw_name:
            continue

        # 1) Kulcs képzés
        # ITT CSERÉLTÜK LE A LOGIKÁT AZ ÚJ "OKOS" WHITELIST MODULRA.
        
        # --- RÉGI MÓDSZER (KIKOMMENTELVE A KÉRÉSNEK MEGFELELŐEN) ---
        # A régi logika közvetlenül a nyers nevet küldte a normalizálóba.
        # key_raw = _make_mapping_key(raw_name)
        # -----------------------------------------------------------
        
        # --- ÚJ MÓDSZER (whitelist.py modul használata) ---
        detected_id = None
        if whitelist:
            # Meghívjuk az okos felismerőt. Ez visszaadja a Belső ID-t (pl. "RTL 2"),
            # ha felismeri a csatornát.
            detected_id = whitelist.identify_channel_id(raw_name)

        if detected_id:
            # Ha felismertük, akkor ebből a tiszta névből (pl "RTL 2") generáljuk 
            # a technikai kulcsot ("RTL2") a belső map-eléshez.
            key_raw = _make_mapping_key(detected_id)
        else:
            # Ha nem ismerte fel (pl. külföldi csatorna), akkor megpróbáljuk a nyers névből,
            # de ez valószínűleg úgyis fennakad majd a későbbi whitelist szűrőn.
            key_raw = _make_mapping_key(raw_name)
            
        if not key_raw:
            continue

        # 2) EPG ID és Szép név keresése
        # MODOSÍTÁS: A régi alias rendszer (.get()) kikötve.
        # A key_raw már a whitelist által ellenőrzött kulcs, nem kell alias lookup.
        # epg_key = M3U_EPG_NAME_ALIASES.get(key_raw, key_raw)
        epg_key = key_raw
        entry = M3U_EPG_ID_MAP.get(epg_key)

        pretty_name = ''
        if entry:
            tvg_id = _safe_str(entry.get('tvg_id', '')).strip()

            # Szép név a whitelistből
            # Ha key_raw="RTL2", akkor a mapben "RTL 2" van.
            pretty_name = _WHITELIST_DISPLAY_BY_KEY.get(epg_key, '').strip()
            
            if not pretty_name:
                pretty_name = _safe_str(entry.get('display', '')).strip() or raw_name

            new_ch['tvg_id'] = tvg_id
            new_ch['tvg_name'] = pretty_name
            new_ch['display_name'] = pretty_name
            new_ch['name'] = pretty_name
        else:
            # Ha nincs a map-ben, de whitelist-es logika miatt maradhatna (bár EPG ID nélkül)
            pretty_name = _clean_display_name_for_user(raw_name)
            if pretty_name:
                new_ch['display_name'] = pretty_name
                new_ch['name'] = pretty_name

        # 3) Szűrés (Külföldi listák és csomagok kezelése)
        # Az EPG kulcs alapján szűrünk (ami már "okosított").
        wl_key = epg_key
        
        # Csomag szűrés
        if allowed_package_keys is not None:
            if wl_key not in allowed_package_keys and key_raw not in allowed_package_keys:
                # Nincs benne a csomagban
                continue

        if whitelist_only:
            order_index = _WHITELIST_ORDER_INDEX.get(wl_key)

            # Ha a raw név alapján nem találja, próbáljuk a pretty nevet
            if order_index is None and pretty_name:
                key2_raw = _make_mapping_key(pretty_name)
                # MODOSÍTÁS: Alias lookup kikötve itt is.
                # key2_alias = M3U_EPG_NAME_ALIASES.get(key2_raw, key2_raw)
                key2_alias = key2_raw
                order_index = _WHITELIST_ORDER_INDEX.get(key2_alias)
                if order_index is not None:
                    wl_key = key2_alias

            # Ha a csatorna (akár külföldi, akár magyar) nincs a whitelist indexben, itt KIESIK.
            if order_index is None:
                # Whitelisten kívül esik -> kidobjuk
                continue

        order_index = _WHITELIST_ORDER_INDEX.get(wl_key, 10_000)
        sort_name = _safe_str(new_ch.get('display_name') or new_ch.get('name'))
        new_ch['_sort_key'] = (order_index, sort_name)

        # Dedup-hoz az EPG-kulcsot tároljuk
        new_ch['_epg_key'] = epg_key
        mapped.append(new_ch)

    # Dedup + rendezés
    dedup: Dict[tuple, Channel] = {}
    for ch in mapped:
        tvg_id = _safe_str(ch.get('tvg_id', '')).strip()
        epg_key = _safe_str(ch.get('_epg_key', '')).strip()
        name = _safe_str(ch.get('display_name') or ch.get('name'))
        dedup_key = (tvg_id or name, epg_key or name)
        existing = dedup.get(dedup_key)
        if existing is None:
            dedup[dedup_key] = ch
        else:
            if ch.get('_fixed_public') and not existing.get('_fixed_public'):
                dedup[dedup_key] = ch

    result = list(dedup.values())
    result.sort(
        key=lambda ch: ch.get(
            '_sort_key',
            (10_000, _safe_str(ch.get('display_name') or ch.get('name')))
        )
    )
    for ch in result:
        ch.pop('_sort_key', None)
        ch.pop('_epg_key', None)
    return result

# ---------------------------------------------
# 6. EXTINF sorok + KODIPROP + plugin URL
# ---------------------------------------------

def _build_extinf_line(channel: Channel) -> str:
    """#EXTINF:-1 sor építése. Ha van display_name, azt változatlanul használjuk."""
    display = _safe_str(channel.get('display_name', '')).strip()
    if display:
        name = display
    else:
        name_raw = _safe_str(
            channel.get('name')
            or channel.get('channel_id')
            or 'Unknown'
        )
        name = _clean_display_name_for_user(name_raw) or 'UNKNOWN'
    tvg_id = _safe_str(channel.get('tvg_id', '')).strip()
    tvg_name = _safe_str(channel.get('tvg_name', '')).strip() or name
    group = _safe_str(channel.get('group_title', '')).strip()
    attrs: List[str] = []
    if tvg_id:
        attrs.append(f'tvg-id="{tvg_id}"')
    if tvg_name:
        attrs.append(f'tvg-name="{tvg_name}"')
    if group:
        attrs.append(f'group-title="{group}"')
    logo_value = _build_local_logo_value(name)
    if logo_value:
        attrs.append(f'tvg-logo="{logo_value}"')
    if attrs:
        return '#EXTINF:-1 ' + ' '.join(attrs) + f',{name}'
    return f'#EXTINF:-1,{name}'

def _build_kodiprops_lines(channel: Channel) -> List[str]:
    lines: List[str] = []
    props = channel.get('kodiprops') or []
    if not isinstance(props, (list, tuple)):
        return lines
    for item in props:
        if isinstance(item, str):
            kv = item.strip()
            if kv:
                lines.append(f'#KODIPROP:{kv}')
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            k = _safe_str(item[0]).strip()
            v = _safe_str(item[1]).strip()
            if k and v:
                lines.append(f'#KODIPROP:{k}={v}')
    return lines

def _build_plugin_url(channel: Channel, addon_id: str = 'plugin.video.streammex', action: str = 'play') -> str:
    channel_id = _safe_str(channel.get('channel_id') or channel.get('id') or '').strip()
    if not channel_id:
        return ''
    from urllib.parse import urlencode as _enc
    query = {'action': action, 'id': channel_id}
    return f'plugin://{addon_id}/?{_enc(query)}'

def _build_url_for_plugin_output(channel: Channel, addon_id: str = 'plugin.video.streammex', action: str = 'play') -> str:
    url = _safe_str(channel.get('url', '')).strip()
    if url.startswith('plugin://'):
        return url
    return _build_plugin_url(channel, addon_id=addon_id, action=action)

def build_plugin_m3u(channels: Iterable[Channel], 
                     addon_id: str = 'plugin.video.streammex', 
                     action: str = 'play', 
                     whitelist_only: bool = True, 
                     include_fixed_public: bool = True,
                     enable_channel_package: bool = False, # ÚJ: Csatorna csomag szűrő engedélyezése
                     channel_package_name: Optional[str] = None, # ÚJ: Csatorna csomag neve
                     channel_packages_data: Optional[Dict[str, Any]] = None, # ÚJ: Csatorna csomag lista
                     ) -> str:
    
    all_channels: List[Channel] = []
    if include_fixed_public:
        for ch in FIXED_PUBLIC_CHANNELS:
            ch_copy = dict(ch)
            ch_copy['_fixed_public'] = True
            all_channels.append(ch_copy)
    for ch in channels:
        all_channels.append(dict(ch))
        
    # A csomag szűrő és a whitelist szűrő egy helyen fut, kanonikus kulcsokkal.
    mapped = _apply_epg_mapping_and_whitelist(
        all_channels, 
        whitelist_only=whitelist_only,
        enable_channel_package=enable_channel_package,
        channel_package_name=channel_package_name,
        channel_packages_data=channel_packages_data,
    )
    
    out_lines: List[str] = ['#EXTM3U tvg-shift=1']
    for ch in mapped:
        url = _build_url_for_plugin_output(ch, addon_id=addon_id, action=action)
        if not url:
            continue
        out_lines.append(_build_extinf_line(ch))
        out_lines.extend(_build_kodiprops_lines(ch))
        out_lines.append(url)
    return '\n'.join(out_lines) + '\n'

# ---------------------------------------------
# 7. Fájl kiírás / write_plugin_m3u_file
# ---------------------------------------------

def _translate_path(path: str) -> str:
    if path.startswith('special://') and xbmcvfs is not None:
        try:
            return xbmcvfs.translatePath(path)  # type: ignore[attr-defined]
        except Exception:
            return path
    return path

def write_m3u_file(path: str, content: str) -> str:
    real_path = _translate_path(path)
    directory = os.path.dirname(real_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(real_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return real_path

def write_plugin_m3u_file(addon, channels: Iterable[Channel], filename: str = 'streammex.m3u', whitelist_only: bool = True, include_fixed_public: bool = True) -> str:
    if addon is None:
        raise RuntimeError('write_plugin_m3u_file: addon nem lehet None')
    try:
        addon_id = addon.getAddonInfo('id') or 'plugin.video.streammex'
    except Exception:
        addon_id = 'plugin.video.streammex'
    try:
        profile = addon.getAddonInfo('profile') or ''
    except Exception:
        profile = ''
    if not profile:
        profile = os.path.join('special://profile', 'addon_data', addon_id)
    full_path = os.path.join(profile, filename)
    m3u_text = build_plugin_m3u(channels, addon_id=addon_id, action='play', whitelist_only=whitelist_only, include_fixed_public=include_fixed_public)
    return write_m3u_file(full_path, m3u_text)

# ---------------------------------------------
# 8. EPG "Fast Patch" – Hungary.xml id/channel normalizálás
# ---------------------------------------------

def fast_patch_epg_ids_in_xml(
    xml_path: str,
    mapping: Dict[str, Dict[str, str]] = M3U_EPG_ID_MAP,
) -> str:
    """
    Gyors szöveg-alapú patch a Hungary.xml fájlra.

    - NEM épít XML fát, csak sima szöveget cserél (nagyon gyors).
    - CSAK az id="..." és channel="..." attribútumokat érinti.
    - A már meglévő M3U_EPG_ID_MAP 'tvg_id' értékei alapján dolgozik.

    Logika:
      * a mapping-ben lévő tvg_id a KÁNONIKUS forma (pl. "RTL GOLD.hu").
      * ebből származtatunk alternatív formákat:
          - space -> "_"   (pl. "RTL_GOLD.hu")
          - "_"   -> space (pl. "RTL GOLD.hu")
      * az XML-ben minden id="ALT" / channel="ALT" előfordulást
        id="KANONIKUS" / channel="KANONIKUS"-ra cserélünk.

    Példa:
      tvg_id = "RTL GOLD.hu"  (ez van az M3U-ban is)

      XML-ben:
        <channel id="RTL_GOLD.hu"> ... </channel>
        <programme ... channel="RTL_GOLD.hu">

      Patch után:
        <channel id="RTL GOLD.hu"> ... </channel>
        <programme ... channel="RTL GOLD.hu">
    """

    # special:// útvonal opcionális kezelése
    real_path = _translate_path(xml_path)

    try:
        with open(real_path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        # Ha nincs ilyen fájl, egyszerűen visszatérünk az elérési úttal
        return real_path
    except Exception:
        # Bármi más olvasási hiba esetén sem dobunk tovább, ne álljon meg az addon
        return real_path

    original_text = text

    for key, info in mapping.items():
        base_id = _safe_str(info.get("tvg_id", "")).strip()
        if not base_id:
            # csatorna, amihez nincs EPG ID – kihagyjuk
            continue

        # A base_id a kánonikus forma (amit az M3U-ba is írunk).
        # Képezünk lehetséges alternatívákat:
        #   - space -> "_"
        #   - "_"   -> space
        candidates = set()

        # 1) space -> "_"
        candidates.add(base_id.replace(" ", "_"))
        # 2) "_" -> space
        candidates.add(base_id.replace("_", " "))

        # Ne próbáljuk magát a kánonikus formát lecserélni önmagára
        if base_id in candidates:
            candidates.remove(base_id)

        # Minden alternatív formáról kánonikusra cserélünk attribútum szinten
        for alt in candidates:
            if not alt or alt == base_id:
                continue

            old_id_attr = f'id="{alt}"'
            new_id_attr = f'id="{base_id}"'
            if old_id_attr in text:
                text = text.replace(old_id_attr, new_id_attr)

            old_ch_attr = f'channel="{alt}"'
            new_ch_attr = f'channel="{base_id}"'
            if old_ch_attr in text:
                text = text.replace(old_ch_attr, new_ch_attr)

    # Ha nem történt változás, felesleges visszaírni
    if text != original_text:
        try:
            with open(real_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            # Ha nem tudjuk visszaírni, inkább csendben elengedjük, mint hogy megálljon az addon
            pass

    return real_path

# =============================================================================
# ###########################################################################
# ### MODUL: DIREKT M3U ÉS EXTRA LISTÁK (Kliensben törölhető)             ###
# ###########################################################################
# =============================================================================

from typing import Callable, Optional
UrlBuilder = Callable[[Channel], Optional[str]]

def build_m3u(
    channels: Iterable[Channel],
    url_builder: UrlBuilder,
    header: str = "#EXTM3U",
) -> str:
    """
    Általános M3U generátor.
    - channels: csatornák listája (dict-ek).
    - url_builder: függvény, ami egy channel-ből URL-t készít (vagy None-t ad, ha nincs).
    - header: első sor (alapértelmezett: #EXTM3U).

    Visszaad: teljes M3U szöveg (utf-8, \n sorvégekkel).

    Ha a channel dict tartalmaz 'kodiprops' listát, akkor minden elemhez
    beszúr egy-egy #KODIPROP: sort az EXTINF és az URL közé.
    """
    lines: List[str] = []

    if header:
        lines.append(header)

    for ch in channels:
        url = url_builder(ch)
        if not url:
            # Csatorna, aminél nincs elérhető URL – kihagyjuk a listából
            continue

        extinf = _build_extinf_line(ch)
        lines.append(extinf)

        # KODIPROP sorok (ha vannak)
        kodiprops = ch.get("kodiprops") or []
        if isinstance(kodiprops, (list, tuple)):
            for kp in kodiprops:
                s_kp = _safe_str(kp).strip()
                if s_kp:
                    lines.append(f"#KODIPROP:{s_kp}")

        lines.append(_safe_str(url))

    # Végén egy lezáró újsor
    return "\n".join(lines) + "\n"

def _get_variant_url(channel: Channel, variant_index: int = 0) -> Optional[str]:
    """
    Direkt URL kiválasztása:
      - ha van 'variants' lista és az adott index létezik:
            variants[variant_index]['url']
      - különben channel['url'] (ha van).
      - ha egyik sincs, None.

    NINCS fallback a legutolsó variánsra, így a több HunX.m3u
    viselkedése egyezik a régi kézzel írt implementációval.
    """
    variants = channel.get("variants")
    if isinstance(variants, list) and variants:
        if 0 <= variant_index < len(variants):
            v = variants[variant_index]
            if isinstance(v, dict):
                url = v.get("url")
                if url:
                    return _safe_str(url)
        # nincs ilyen index -> nincs URL ebben a slotban
        return None

    # fallback: alap 'url'
    base_url = channel.get("url")
    if base_url:
        return _safe_str(base_url)

    return None


def build_direct_m3u(
    channels: Iterable[Channel],
    variant_index: int = 0,
) -> str:
    """
    Olyan M3U-t épít, ahol közvetlen stream URL-ek vannak (nem plugin://).
    Főleg fejlesztéshez / teszteléshez hasznos.

    variant_index:
      - 0 -> első variáns
      - 1 -> második, stb.
      - ha az adott csatornának nincs ilyen variánsa, az a csatorna
        kimarad ebből az M3U-ból.

    A csatornanevekből a Kodi-s [COLOR] tagek el vannak távolítva, hogy
    külső lejátszókban is szép, tiszta név látszódjon.
    """
    def _clean_channel(ch: Channel) -> Channel:
        name = _safe_str(
            ch.get("display_name") or ch.get("name") or ch.get("channel_id") or ""
        )
        clean_name = _strip_color_tags(name).strip()
        new_ch = dict(ch)
        if clean_name:
            new_ch["display_name"] = clean_name
            new_ch["name"] = clean_name
        return new_ch

    clean_channels = (_clean_channel(ch) for ch in channels)

    return build_m3u(
        clean_channels,
        url_builder=lambda ch: _get_variant_url(ch, variant_index=variant_index),
        header="#EXTM3U",
    )


def build_all_variants_m3u(
    channels: Iterable[Channel],
) -> str:
    """
    Egyetlen M3U-t épít, amiben MINDEN variáns benne van,
    külön sorban. A csatornák neve:
      "AMC (1)", "AMC (2)", ...
    Debug / VLC / külső lejátszók számára hasznos.

    Itt DIREKT stream URL-ek mennek (nem plugin://).
    A nevekből itt is el lesznek távolítva a [COLOR] tagek.
    """
    flat: List[Channel] = []

    for ch in channels:
        name = _safe_str(
            ch.get("display_name") or ch.get("name") or ch.get("channel_id") or ""
        )
        clean_name = _strip_color_tags(name).strip()

        base_meta = {
            "tvg_id": ch.get("tvg_id"),
            "tvg_name": ch.get("tvg_name"),
            # tvg_logo-t nem írjuk ki az EXTINF-be, de a dict-ben maradhat, ha később kéne
            "tvg_logo": ch.get("tvg_logo"),
            "group_title": ch.get("group_title"),
            "kodiprops": ch.get("kodiprops") or [],
        }

        variants = ch.get("variants") or []
        if isinstance(variants, list) and variants:
            for idx, v in enumerate(variants, start=1):
                if isinstance(v, dict):
                    url = _safe_str(v.get("url") or "")
                    if not url:
                        continue
                    tvg_id = v.get("tvg_id") or base_meta["tvg_id"]
                    tvg_name = v.get("tvg_name") or base_meta["tvg_name"]
                    tvg_logo = v.get("tvg_logo") or base_meta["tvg_logo"]
                    group_title = v.get("group_title") or base_meta["group_title"]
                    v_kps = v.get("kodiprops") or []
                else:
                    url = _safe_str(v)
                    if not url:
                        continue
                    tvg_id = base_meta["tvg_id"]
                    tvg_name = base_meta["tvg_name"]
                    tvg_logo = base_meta["tvg_logo"]
                    group_title = base_meta["group_title"]
                    v_kps = []

                # csatorna szintű + variáns szintű KODIPROP-ok egyesítése
                kodiprops: List[str] = []
                for kp in base_meta["kodiprops"]:
                    s_kp = _safe_str(kp).strip()
                    if s_kp and s_kp not in kodiprops:
                        kodiprops.append(s_kp)
                for kp in v_kps:
                    s_kp = _safe_str(kp).strip()
                    if s_kp and s_kp not in kodiprops:
                        kodiprops.append(s_kp)

                flat.append({
                    "display_name": f"{clean_name} ({idx})" if clean_name else f"{name} ({idx})",
                    "url": url,
                    "tvg_id": tvg_id,
                    "tvg_name": tvg_name,
                    "tvg_logo": tvg_logo,
                    "group_title": group_title,
                    "kodiprops": kodiprops,
                })
        else:
            url = _safe_str(ch.get("url") or "")
            if url:
                flat.append({
                    "display_name": clean_name or name,
                    "url": url,
                    "tvg_id": base_meta["tvg_id"],
                    "tvg_name": base_meta["tvg_name"],
                    "tvg_logo": base_meta["tvg_logo"],
                    "group_title": base_meta["group_title"],
                    "kodiprops": base_meta["kodiprops"],
                })

    return build_m3u(
        flat,
        url_builder=lambda ch: _safe_str(ch.get("url") or ""),
        header="#EXTM3U",
    )
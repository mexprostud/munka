# -*- coding: utf-8 -*-
import json
import os
from . import util

try:
    import xbmcaddon
    PROFILE = xbmcaddon.Addon().getAddonInfo('profile')
    # HIBAJAVÍTÁS: Kodi alatt a 'profile' általában special:// útvonal,
    # ezért ezt lefordítjuk valódi fájlrendszer elérési útra, hogy az
    # os.path és a saját fájlkezelő segédfüggvények is jól működjenek.
    try:
        PROFILE = util.translate_path(PROFILE)
    except Exception:
        pass
except:
    PROFILE = "."

SOURCES_FILE = os.path.join(PROFILE, "sources.json")

def load_sources():
    """Betölti a felhasználó által hozzáadott forrásokat."""
    # HIBAJAVÍTÁS: special:// elérési útvonalak miatt a sima os.path.exists
    # helyett a util.file_exists-et használjuk, ami Kodi alatt xbmcvfs.exists-t hív.
    if not util.file_exists(SOURCES_FILE):
        return {"m3u": [], "epg": []}
    try:
        data = util.read_json_file(SOURCES_FILE)
        if "m3u" not in data: data["m3u"] = []
        if "epg" not in data: data["epg"] = []
        return data
    except Exception:
        return {"m3u": [], "epg": []}

def save_sources(data):
    """Forráslista mentése JSON fájlba (sources.json).
    HIBAJAVÍTÁS: mentés előtt gondoskodunk róla, hogy a célkönyvtár létezzen.
    """
    directory = os.path.dirname(SOURCES_FILE)
    try:
        util.ensure_directory(directory)
    except Exception:
        # Ha valamiért nem érhető el az ensure_directory, fallback az os.makedirs-re.
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            pass
    util.write_json_file(SOURCES_FILE, data)

def add_source(source_type, url, label=None):
    """Hozzáad egy forrást (m3u vagy epg)."""
    data = load_sources()
    if not label:
        label = url.split('/')[-1]
    
    # Duplikáció szűrés
    for item in data.get(source_type, []):
        if item['url'] == url:
            return False 

    entry = {"url": url, "label": label, "enabled": True}
    data[source_type].append(entry)
    save_sources(data)
    return True

def remove_source(source_type, index):
    data = load_sources()
    if 0 <= index < len(data[source_type]):
        del data[source_type][index]
        save_sources(data)
        return True
    return False

def toggle_source(source_type, index):
    data = load_sources()
    if 0 <= index < len(data[source_type]):
        data[source_type][index]['enabled'] = not data[source_type][index]['enabled']
        save_sources(data)
        return True
    return False
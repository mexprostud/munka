# -*- coding: utf-8 -*-
import sys
from datetime import datetime
from urllib.parse import parse_qsl, urlencode

try:
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcplugin
except ImportError:
    # Ha Kodi környezeten kívül fut, ezek None-ok
    xbmc = xbmcaddon = xbmcgui = xbmcplugin = None

# A saját modulok
from resources.lib.playlist_source import PlaylistCoordinator
from resources.lib import epg_manager
from resources.lib import source_manager  # Új: A forráskezelő GUI logikájához


def get_addon():
    """Visszaadja az addont objektumot."""
    if xbmcaddon:
        return xbmcaddon.Addon()
    raise RuntimeError('Kodi addon API unavailable')


def get_handle():
    """Visszaadja a Kodi plugin handle-t (biztonságosan)."""
    try:
        # A Kodi hívás során a sys.argv[1] a plugin handle
        return int(sys.argv[1])
    except (IndexError, ValueError):
        return -1  # Hibás indítás esetén -1


def get_params():
    """Visszaadja a Kodi plugin paramétereket (biztonságosan)."""
    if len(sys.argv) < 3:
        return {}
    # A Kodi hívás során a sys.argv[2] tartalmazza a paramétereket
    return dict(parse_qsl(sys.argv[2][1:]))


# Kodi globális változók biztonságos inicializálása
ADDON = get_addon()
HANDLE = get_handle()
PARAMS = get_params()
# Fő logika koordinátora
COORDINATOR = PlaylistCoordinator(ADDON)

# --- Akció konstansok ---
ACTION_LIST = 'list'
ACTION_FAVOURITES = 'favourites'
ACTION_REFRESH = 'refresh'
ACTION_PLAY = 'play'
ACTION_SETTINGS = 'settings'
ACTION_SEARCH = 'search'
ACTION_SEARCH_RESULTS = 'search_results'
ACTION_ADD_FAVOURITE = 'add_favourite'
ACTION_REMOVE_FAVOURITE = 'remove_favourite'

# --- ÚJ: TV / M3U / EPG kezelés menü ---
ACTION_MANAGE_TV = 'manage_tv'

# --- ÚJ Forráskezelő Akciók (Plan A) ---
ACTION_MANAGE_M3U = 'manage_m3u'
ACTION_MANAGE_EPG = 'manage_epg'
ACTION_ADD_SOURCE = 'add_source'
ACTION_DEL_SOURCE = 'del_source'
ACTION_TOG_SOURCE = 'tog_source'  # Toggle (ki/be kapcsolás)
ACTION_MERGE_EPG = 'merge_epg'    # Kézi EPG egyesítés
ACTION_CHANNEL_LIST = 'channel_list'  # (később használható)


def build_url(query):
    """Létrehozza a Kodi plugin URL-t a query dict-ből."""
    return sys.argv[0] + '?' + urlencode(query)


def format_timestamp(ts):
    """Timestamp formázása olvasható dátum/idő stringgé."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')


# --- AUTO / MANUAL variáns mód kapcsoló ---
def is_manual_variant_mode():
    """
    True = kézi variáns választás (csatornák mappaként → variáns lista)
    False = AUTO mód (kattintásra azonnali lejátszás, 20 mp-es léptetéssel)
    """
    try:
        return ADDON is not None and ADDON.getSetting('variant_manual_select') == 'true'
    except Exception:
        return False


# ----------------------------------------------------------------------
# --- ÚJ MENÜ FUNKCIÓK (Forráskezelő – Plan A) ---
# ----------------------------------------------------------------------

def manage_sources(source_type):
    """M3U vagy EPG források listázása és kezelése."""
    sources_data = source_manager.load_sources()
    sources = sources_data.get(source_type, [])

    # + Új forrás hozzáadása
    li = xbmcgui.ListItem(label=f"[COLOR green]+ Új {source_type.upper()} forrás[/COLOR]")
    url = build_url({'action': ACTION_ADD_SOURCE, 'type': source_type})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # Források listázása
    for idx, src in enumerate(sources):
        enabled = bool(src.get('enabled', True))
        label_txt = src.get('label') or src.get('url') or f"#{idx}"
        url_txt = src.get('url') or ""
        state = "BE" if enabled else "KI"
        color = "white" if enabled else "gray"
        label = f"[{state}] {label_txt} [COLOR {color}]({url_txt})[/COLOR]"

        li = xbmcgui.ListItem(label=label)

        # Context menü a forrásokhoz
        cmds = []
        del_url = build_url({'action': ACTION_DEL_SOURCE, 'type': source_type, 'index': idx})
        tog_url = build_url({'action': ACTION_TOG_SOURCE, 'type': source_type, 'index': idx})
        cmds.append(('Törlés', f"RunPlugin({del_url})"))
        cmds.append(('Ki/Be Kapcsolás', f"RunPlugin({tog_url})"))
        li.addContextMenuItems(cmds)

        # A lista elem (nem navigál sehova)
        xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': 'noop'}), li, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)


def add_source_gui(source_type):
    """GUI a forrás URL/fájl útvonal megadásához és elmentéséhez."""
    opts = ["URL megadása (http/https)", "Helyi fájl tallózása"]
    ret = xbmcgui.Dialog().select(f"Új {source_type.upper()} Forrás Típusa", opts)
    if ret < 0:
        return  # Mégse

    path = ""
    if ret == 0:
        # URL
        path = xbmcgui.Dialog().input("Add meg az URL-t (pl. http://valami.hu/lista.m3u)")
    else:
        # Helyi fájl tallózása (1 = files/directory)
        path = xbmcgui.Dialog().browseSingle(1, "Válassz fájlt", "files")

    if path:
        # Fájlnévből javasolt név
        def_val = path.split('/')[-1] or path
        # POZÍCIÓS paraméter: kompatibilis a régi xbmcgui.Dialog().input API-val is
        name = xbmcgui.Dialog().input("Add meg a forrás nevét (opcionális)", def_val)

        # Forrás mentése
        source_manager.add_source(source_type, path, name)
        # Frissítjük a konténert, hogy látszódjon az új forrás
        xbmc.executebuiltin('Container.Refresh')


def run_epg_merge():
    """Kézi EPG egyesítés indítása."""
    if xbmcgui:
        xbmcgui.Dialog().notification("EPG", "Egyesítés indítása...", time=2000)
    # A logikát az EPG manager végzi – force=True, hogy biztosan újraépítse
    try:
        epg_manager.build_merged_epg(force=True)
    except Exception:
        pass
    if xbmcgui:
        xbmcgui.Dialog().notification("EPG", "Egyesítés kész!", time=3000)


# ----------------------------------------------------------------------
# --- ÚJ: TV / M3U / EPG KEZELÉS (IPTV Merge-szerű menü) ---
# ----------------------------------------------------------------------

def manage_tv_menu():
    """
    TV csatornalista kezelés menü (Plan A + kimeneti utak).
    - Megjeleníti a generált Hungary.m3u / Hungary.xml elérési útját
    - Gyors linkek TV listához, Kedvencekhez, teljes frissítéshez és forráskezeléshez
    """
    paths = COORDINATOR.get_output_paths() or {}
    m3u_path = paths.get('m3u') or "Ismeretlen"
    epg_path = paths.get('epg') or "Ismeretlen"
    profile_path = paths.get('profile') or "Ismeretlen"

    xbmcplugin.setContent(HANDLE, 'files')

    # Csatornalista (M3U) információ
    li = xbmcgui.ListItem(label="M3U kimenet (csatornalista)")
    li.setLabel2(m3u_path)
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': 'noop'}), li, isFolder=False)

    # EPG információ
    li = xbmcgui.ListItem(label="EPG kimenet (XMLTV)")
    li.setLabel2(epg_path)
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': 'noop'}), li, isFolder=False)

    # Profil könyvtár
    li = xbmcgui.ListItem(label="Profil könyvtár")
    li.setLabel2(profile_path)
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': 'noop'}), li, isFolder=False)

    # Elválasztó jellegű üres sor
    sep = xbmcgui.ListItem(label="---------------------------")
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': 'noop'}), sep, isFolder=False)

    # Gyors link: TV csatornák
    li = xbmcgui.ListItem(label="TV csatornák megnyitása")
    url = build_url({'action': ACTION_LIST, 'type': 'tv'})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # Gyors link: Kedvencek
    li = xbmcgui.ListItem(label="Kedvencek megnyitása")
    url = build_url({'action': ACTION_FAVOURITES})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    # Teljes frissítés (M3U + EPG Merge)
    li = xbmcgui.ListItem(label="Teljes frissítés (M3U + EPG Merge)")
    url = build_url({'action': ACTION_REFRESH})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)

    # Kézi EPG egyesítés
    li = xbmcgui.ListItem(label="EPG egyesítése most")
    url = build_url({'action': ACTION_MERGE_EPG})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)

    # Forráskezelés – Plan A
    li = xbmcgui.ListItem(label="M3U források kezelése (Plan A)")
    url = build_url({'action': ACTION_MANAGE_M3U})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    li = xbmcgui.ListItem(label="EPG források kezelése")
    url = build_url({'action': ACTION_MANAGE_EPG})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)

    xbmcplugin.endOfDirectory(HANDLE)


# ----------------------------------------------------------------------
# --- MEGLÉVŐ FŐ MENÜ FUNKCIÓK ---
# ----------------------------------------------------------------------

def show_root():
    """A főmenü listázása."""
    # Állapot lekérése (csak info a frissítés idejéről)
    state = COORDINATOR.ensure_assets(force=False)
    last_success = state.get('last_success')

    # 1. Élő TV (Fő funkció)
    li = xbmcgui.ListItem(label="TV Csatornák")
    li.setLabel2(f"Utoljára frissítve: {format_timestamp(last_success)}")
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': ACTION_LIST, 'type': 'tv'}), li, isFolder=True)

    # 1/b. TV csatornalista kezelés (IPTV Merge-szerű menü)
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_MANAGE_TV}),
        xbmcgui.ListItem(label="TV csatornalista kezelése"),
        isFolder=True
    )

    # 2. Kedvencek
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_FAVOURITES}),
        xbmcgui.ListItem(label="Kedvencek"),
        isFolder=True
    )

    # 3. Forráskezelés (Plan A beállítók)
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_MANAGE_M3U}),
        xbmcgui.ListItem(label="M3U Források kezelése (Plan A)"),
        isFolder=True
    )
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_MANAGE_EPG}),
        xbmcgui.ListItem(label="EPG Források kezelése"),
        isFolder=True
    )

    # 4. Eszközök / Beállítások
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_SEARCH}),
        xbmcgui.ListItem(label="Keresés"),
        isFolder=True
    )
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_REFRESH}),
        xbmcgui.ListItem(label="Teljes Frissítés (M3U + EPG Merge)"),
        isFolder=False
    )
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_MERGE_EPG}),
        xbmcgui.ListItem(label="EPG Egyesítése most"),
        isFolder=False
    )
    xbmcplugin.addDirectoryItem(
        HANDLE,
        build_url({'action': ACTION_SETTINGS}),
        xbmcgui.ListItem(label="Beállítások"),
        isFolder=False
    )

    xbmcplugin.endOfDirectory(HANDLE)


def list_channels(channel_type=None, favourites_only=False, search_query=None):
    """Listázza a csatornákat szűrés és rendezés után."""
    channels = COORDINATOR.get_channels(
        channel_type=channel_type,
        favourites_only=favourites_only,
        search_query=search_query,
        force=False
    )

    xbmcplugin.setContent(HANDLE, 'videos')  # Tartalom típusa: videók

    # Mappa (MANUAL) vagy AUTO mód?
    manual_mode = is_manual_variant_mode()

    for entry in channels:
        label = entry.get('display_name') or entry.get('name') or "Ismeretlen"
        li = xbmcgui.ListItem(label=label)
        logo = entry.get('tvg_logo')
        if logo:
            # Logó/ikon beállítása
            li.setArt({'thumb': logo, 'icon': logo})

        # Meta adatok beállítása
        li.setInfo('video', {'title': label, 'genre': entry.get('group_title')})

        ch_id = entry.get('channel_id')

        # Context menü a kedvencekhez
        cmds = []
        if favourites_only or COORDINATOR.is_favourite(ch_id):
            cmds.append(("Kedvenc törlése", f"RunPlugin({build_url({'action': ACTION_REMOVE_FAVOURITE, 'id': ch_id})})"))
        else:
            cmds.append(("Kedvenc hozzáadása", f"RunPlugin({build_url({'action': ACTION_ADD_FAVOURITE, 'id': ch_id})})"))
        li.addContextMenuItems(cmds)

        # MANUAL mód: mappa → variáns lista
        if manual_mode:
            url = build_url({'action': ACTION_LIST, 'type': 'variants', 'id': ch_id})
            is_folder = True
        else:
            # AUTO mód: azonnali lejátszás (20 mp-es léptetés a get_play_url-ben)
            url = build_url({'action': ACTION_PLAY, 'id': ch_id})
            li.setProperty('IsPlayable', 'true')
            is_folder = False

        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=is_folder)

    xbmcplugin.endOfDirectory(HANDLE)


# --- Variánsok listázása MANUAL módban ---
def list_variants(channel_id):
    """Megjeleníti az adott csatorna összes variánsát kézi választáshoz."""
    ch = COORDINATOR.get_channel_by_id(channel_id)
    if not ch:
        if xbmcgui:
            xbmcgui.Dialog().notification(
                ADDON.getAddonInfo('name'),
                "Csatorna nem található",
                time=3000
            )
        return

    variants = ch.get('variants') or []
    if not variants:
        # Ha nincs variáns, fallback az alap URL-re
        base_url = ch.get('url')
        if base_url:
            base_url = COORDINATOR.resolve_variant_url(base_url)
            li = xbmcgui.ListItem(label=ch.get('display_name') or ch.get('name') or base_url)
            li.setProperty('IsPlayable', 'true')
            xbmcplugin.addDirectoryItem(HANDLE, base_url, li, isFolder=False)
            xbmcplugin.endOfDirectory(HANDLE)
        return

    xbmcplugin.setContent(HANDLE, 'videos')
    for idx, v in enumerate(variants, start=1):
        # Itt NEM oldjuk fel az URL-t, csak variáns indexet adunk át
        label = f"{ch.get('display_name') or ch.get('name')} [{idx}]"
        li = xbmcgui.ListItem(label=label)
        li.setProperty('IsPlayable', 'true')
        li.setArt({
            'icon': ADDON.getAddonInfo('icon'),
            'fanart': ADDON.getAddonInfo('fanart'),
        })
        li.setInfo('video', {'title': label})

        # Csak ID + v_index → a get_manual_play_url fogja menteni és feloldani
        play_url = build_url({
            'action': ACTION_PLAY,
            'id': channel_id,    # eredeti csatorna ID
            'v_index': idx - 1   # 0-alapú index a variants listában
        })
        xbmcplugin.addDirectoryItem(HANDLE, play_url, li, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)


def play_channel(url_or_id):
    """
    Csatorna lejátszásának feloldása és indítása.

    - Ha 'url_or_id' egy URL (tartalmaz '://'), akkor közvetlenül azt játssza.
    - Ha nem URL, akkor csatorna ID-nek tekinti és AUTO mód szerint a
      COORDINATOR.get_play_url() segítségével választ variánst.
    """
    if isinstance(url_or_id, str) and '://' in url_or_id:
        # Ez egy közvetlen URL (pl. MANUAL mód feloldott URL-je)
        url = url_or_id
    else:
        # Ez egy ID (AUTO módból)
        url = COORDINATOR.get_play_url(url_or_id)

    if url:
        li = xbmcgui.ListItem(path=url)
        xbmcplugin.setResolvedUrl(HANDLE, True, li)
    else:
        if xbmcgui:
            xbmcgui.Dialog().notification(
                ADDON.getAddonInfo('name'),
                "Nincs elérhető stream",
                time=3000
            )


def handle_search():
    """Keresési felület megjelenítése."""
    if not xbmcgui:
        return
    query = xbmcgui.Dialog().input("Írd be a keresőkifejezést")
    if query:
        # A keresési eredmények listázása
        list_channels(search_query=query)


def refresh_assets():
    """Teljes M3U és EPG frissítés indítása (M3U + EPG Merge)."""
    # M3U + EPG frissítés (PlaylistCoordinator – a force=True-t átadjuk az EPG motornak is)
    COORDINATOR.ensure_assets(force=True)
    if xbmcgui:
        xbmcgui.Dialog().notification(ADDON.getAddonInfo('name'), "Frissítve!", time=3000)


# ----------------------------------------------------------------------
# --- ROUTING (Fő belépési pont) ---
# ----------------------------------------------------------------------

action = PARAMS.get('action')

if action is None:
    # Főmenü megjelenítése
    show_root()

# Listázás és Lejátszás
elif action == ACTION_LIST:
    list_type = PARAMS.get('type')
    if list_type == 'variants':
        # MANUAL mód: variánsok listázása
        list_variants(PARAMS.get('id'))
    else:
        list_channels(channel_type=list_type)

elif action == ACTION_PLAY:
    # MANUÁLIS mód esetén: ID + v_index → get_manual_play_url
    ch_id = PARAMS.get('id')
    v_index = PARAMS.get('v_index')
    direct_url = PARAMS.get('direct_url')  # más forrásból is jöhet

    if ch_id is not None and v_index is not None:
        # Manuális variáns választás: itt mentjük a választott variánst
        url = COORDINATOR.get_manual_play_url(ch_id, v_index)
        if url:
            play_channel(url)
        elif direct_url:
            # Fallback: ha valamiért nem sikerült, de van direct_url
            play_channel(direct_url)
        else:
            # Utolsó fallback: AUTO mód ugyanarra a csatornára
            play_channel(ch_id)
    elif direct_url:
        # Ha csak direct_url van (pl. más kódból), játsszuk le közvetlenül
        play_channel(direct_url)
    else:
        # Klasszikus AUTO mód: csak ID alapján
        play_channel(ch_id)

elif action == ACTION_FAVOURITES:
    list_channels(favourites_only=True)

elif action == ACTION_SEARCH:
    handle_search()

# TV / M3U / EPG kezelés menü
elif action == ACTION_MANAGE_TV:
    manage_tv_menu()

# Forráskezelő (ÚJ logikák)
elif action == ACTION_MANAGE_M3U:
    manage_sources('m3u')

elif action == ACTION_MANAGE_EPG:
    manage_sources('epg')

elif action == ACTION_ADD_SOURCE:
    add_source_gui(PARAMS.get('type'))

elif action == ACTION_DEL_SOURCE:
    source_manager.remove_source(PARAMS.get('type'), int(PARAMS.get('index')))
    xbmc.executebuiltin('Container.Refresh')

elif action == ACTION_TOG_SOURCE:
    source_manager.toggle_source(PARAMS.get('type'), int(PARAMS.get('index')))
    xbmc.executebuiltin('Container.Refresh')

# Eszközök
elif action == ACTION_REFRESH:
    refresh_assets()
    xbmc.executebuiltin('Container.Refresh')

elif action == ACTION_MERGE_EPG:
    run_epg_merge()

elif action == ACTION_SETTINGS:
    ADDON.openSettings()

# Kedvencek logika (ContextMenu-ből hívva)
elif action == ACTION_ADD_FAVOURITE:
    COORDINATOR.add_favourite(PARAMS.get('id'))
    if xbmcgui:
        xbmcgui.Dialog().notification(
            ADDON.getAddonInfo('name'),
            "Hozzáadva a kedvencekhez",
            time=1000
        )

elif action == ACTION_REMOVE_FAVOURITE:
    COORDINATOR.remove_favourite(PARAMS.get('id'))
    xbmc.executebuiltin('Container.Refresh')

# Ha ismeretlen action, a főmenü jön be
else:
    show_root()

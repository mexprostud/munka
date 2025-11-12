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
    xbmc = xbmcaddon = xbmcgui = xbmcplugin = None  # type: ignore

from resources.lib.playlist_source import PlaylistCoordinator

def get_addon():
    if xbmcaddon:
        return xbmcaddon.Addon()
    raise RuntimeError('Kodi addon API unavailable')

def get_handle():
    try:
        return int(sys.argv[1])
    except (IndexError, ValueError):
        return -1

def get_params():
    if len(sys.argv) < 3:
        return {}
    return dict(parse_qsl(sys.argv[2][1:]))

ADDON = get_addon()
HANDLE = get_handle()
PARAMS = get_params()
COORDINATOR = PlaylistCoordinator(ADDON)

ACTION_LIST = 'list'
ACTION_FAVOURITES = 'favourites'
ACTION_REFRESH = 'refresh'
ACTION_PLAY = 'play'
ACTION_SETTINGS = 'open_settings'
ACTION_SEARCH = 'search'
ACTION_SEARCH_RESULTS = 'search_results'
ACTION_ADD_FAVOURITE = 'add_favourite'
ACTION_REMOVE_FAVOURITE = 'remove_favourite'

CHANNEL_TYPE_TV = 'tv'
CHANNEL_TYPE_RADIO = 'radio'

def build_url(query):
    return sys.argv[0] + '?' + urlencode(query)

def format_timestamp(ts):
    if not ts:
        return ADDON.getLocalizedString(32006)  # n/a
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')

def show_root():
    state = COORDINATOR.ensure_assets(force=False)
    channels = state.get('channels', [])
    last_success = state.get('last_success')
    items = []
    items.append({
        'label': ADDON.getLocalizedString(32001),  # Live TV
        'label2': ADDON.getLocalizedString(32008) % format_timestamp(last_success),
        'action': ACTION_LIST,
        'params': {'type': CHANNEL_TYPE_TV}
    })
    if any(channel.get('type') == CHANNEL_TYPE_RADIO for channel in channels):
        items.append({
            'label': ADDON.getLocalizedString(32005),  # Radio
            'action': ACTION_LIST,
            'params': {'type': CHANNEL_TYPE_RADIO}
        })
    items.append({'label': ADDON.getLocalizedString(32007), 'action': ACTION_FAVOURITES})  # Favourites
    items.append({'label': ADDON.getLocalizedString(32009), 'action': ACTION_SEARCH})      # Search
    items.append({'label': ADDON.getLocalizedString(32002), 'action': ACTION_REFRESH, 'is_folder': False})
    items.append({'label': ADDON.getLocalizedString(32003), 'action': ACTION_SETTINGS, 'is_folder': False})

    for item in items:
        list_item = xbmcgui.ListItem(label=item['label'])
        if item.get('label2'):
            list_item.setLabel2(item['label2'])
        list_item.setArt({'icon': ADDON.getAddonInfo('icon'), 'fanart': ADDON.getAddonInfo('fanart')})
        params = item.get('params', {})
        url = build_url({'action': item['action'], **params})
        xbmcplugin.addDirectoryItem(HANDLE, url, list_item, isFolder=item.get('is_folder', True))
    xbmcplugin.endOfDirectory(HANDLE)

def list_channels(channel_type=None, favourites_only=False, search_query=None, preloaded=None):
    channels = preloaded if preloaded is not None else COORDINATOR.get_channels(
        channel_type=channel_type, favourites_only=favourites_only, search_query=search_query
    )

    if not channels and xbmcgui:
        if favourites_only:
            message_id = 32013  # No favourites yet
        elif search_query:
            message_id = 32016  # No results
        else:
            message_id = 32017  # No channels
        xbmcgui.Dialog().notification(ADDON.getAddonInfo('name'), ADDON.getLocalizedString(message_id), time=3000)

    xbmcplugin.setContent(HANDLE, 'videos')
    for entry in channels:
        label = entry.get('display_name')
        list_item = xbmcgui.ListItem(label=label)
        list_item.setProperty('IsPlayable', 'true')

        logo = entry.get('tvg_logo')
        art = {'icon': ADDON.getAddonInfo('icon'), 'fanart': ADDON.getAddonInfo('fanart')}
        if logo:
            art.update({'thumb': logo, 'icon': logo})
        list_item.setArt(art)

        info = {'title': label}
        if entry.get('group_title'):
            info['genre'] = entry.get('group_title')
        list_item.setInfo('video', info)

        context_items = []
        ch_id = entry.get('channel_id')
        if favourites_only:
            context_items.append((ADDON.getLocalizedString(32012), f"RunPlugin({build_url({'action': ACTION_REMOVE_FAVOURITE, 'id': ch_id})})"))
        else:
            if COORDINATOR.is_favourite(ch_id):
                context_items.append((ADDON.getLocalizedString(32012), f"RunPlugin({build_url({'action': ACTION_REMOVE_FAVOURITE, 'id': ch_id})})"))
            else:
                context_items.append((ADDON.getLocalizedString(32011), f"RunPlugin({build_url({'action': ACTION_ADD_FAVOURITE, 'id': ch_id})})"))
        context_items.append((ADDON.getLocalizedString(32002), f"RunPlugin({build_url({'action': ACTION_REFRESH})})"))
        list_item.addContextMenuItems(context_items)

        play_url = build_url({'action': ACTION_PLAY, 'id': ch_id})
        xbmcplugin.addDirectoryItem(HANDLE, play_url, list_item, isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)

def play_channel(url):
    li = xbmcgui.ListItem(path=url)
    xbmcplugin.setResolvedUrl(HANDLE, True, li)

def refresh_assets():
    COORDINATOR.ensure_assets(force=True)
    if xbmcgui:
        xbmcgui.Dialog().notification(ADDON.getAddonInfo('name'), ADDON.getLocalizedString(32004), time=3000)
    if xbmc:
        xbmc.executebuiltin('Container.Refresh')

def open_settings():
    if xbmcaddon:
        ADDON.openSettings()

def show_favourites():
    list_channels(favourites_only=True)

def handle_search():
    if not xbmcgui:
        return
    query = xbmcgui.Dialog().input(ADDON.getLocalizedString(32010))
    if not query:
        return
    url = build_url({'action': ACTION_SEARCH_RESULTS, 'query': query})
    if xbmc:
        xbmc.executebuiltin(f'Container.Update({url})')

def list_search_results(query):
    channels = COORDINATOR.get_channels(search_query=query)
    list_channels(search_query=query, preloaded=channels)

def add_favourite(channel_id):
    if not channel_id:
        return
    COORDINATOR.add_favourite(channel_id)
    if xbmcgui:
        xbmcgui.Dialog().notification(ADDON.getAddonInfo('name'), ADDON.getLocalizedString(32014), time=3000)
    if xbmc:
        xbmc.executebuiltin('Container.Refresh')

def remove_favourite(channel_id):
    if not channel_id:
        return
    COORDINATOR.remove_favourite(channel_id)
    if xbmcgui:
        xbmcgui.Dialog().notification(ADDON.getAddonInfo('name'), ADDON.getLocalizedString(32015), time=3000)
    if xbmc:
        xbmc.executebuiltin('Container.Refresh')

action = PARAMS.get('action')

if action is None:
    show_root()
elif action == ACTION_LIST:
    list_channels(channel_type=PARAMS.get('type'))
elif action == ACTION_PLAY:
    channel = COORDINATOR.get_channel_by_id(PARAMS.get('id'))
    url = channel.get('url') if channel else None
    if url:
        play_channel(url)
elif action == ACTION_REFRESH:
    refresh_assets()
elif action == ACTION_SETTINGS:
    open_settings()
elif action == ACTION_FAVOURITES:
    show_favourites()
elif action == ACTION_SEARCH:
    handle_search()
elif action == ACTION_SEARCH_RESULTS:
    list_search_results(PARAMS.get('query', ''))
elif action == ACTION_ADD_FAVOURITE:
    add_favourite(PARAMS.get('id'))
elif action == ACTION_REMOVE_FAVOURITE:
    remove_favourite(PARAMS.get('id'))
else:
    show_root()
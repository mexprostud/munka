import sys
from urllib.parse import quote

import requests
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

from resources.lib.brotlipython import brotlidec


def resp_text(resp):
    """Return decoded response text."""
    if resp and resp.headers.get('content-encoding') == 'br':
        out = []
        # terrible implementation but it's pure Python
        return brotlidec(resp.content, out).decode('utf-8')
    response_content = resp.text

    return response_content.replace("\'", '"')


class Helper:
    def __init__(self, base_url=None, handle=None):
        self.base_url = base_url
        self.handle = handle
        self.addon = xbmcaddon.Addon()
        self.addon_name = xbmcaddon.Addon().getAddonInfo('id')
        self.addon_version = xbmcaddon.Addon().getAddonInfo('version')

        self.art = {'icon': self.addon.getAddonInfo('icon'),
                    'fanart': self.addon.getAddonInfo('fanart'),
                    }

        self._sess = None

        # API
        self.base_api_url = 'https://api.sweet.tv/{}'

        self.auth_url = self.base_api_url.format('SigninService/Start.json')
        self.check_auth_url = self.base_api_url.format('SigninService/GetStatus.json')
        self.token_url = self.base_api_url.format('AuthenticationService/Token.json')
        self.UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        self.version = '6.4.96'
        self.params = {}

        self.uuid = self.get_setting('uuid')
        self.bearer = self.get_setting('bearer')
        self.refresh_token = self.get_setting('refresh_token')
        self.logged = self.get_setting('logged')
        self.countryCode = self.get_setting('countryCode')

        self.headers = {
            'Host': 'api.sweet.tv',
            'user-agent': self.UA,
            'accept': 'application/json, text/plain, */*',
            'accept-language': self.countryCode,
            'x-device': '1;22;0;2;' + self.version,
            'origin': 'https://sweet.tv',
            'dnt': '1',
            'referer': 'https://sweet.tv/',
        }
        if self.bearer:
            self.headers.update({'authorization': self.bearer})

        self.json_data = {
            "device": {
                "type": "DT_SmartTV",
                "sub_type": "DST_WINDOWS",
                "application": {
                    "type": "AT_SWEET_TV_Player"
                },
                "model": self.UA,
                "firmware": {
                    "versionCode": 1,
                    "versionString": self.version
                },
                "uuid": self.uuid,
                "supported_drm": {
                    "widevine_modular": True
                },
                "screen_info": {
                    "aspectRatio": 6,
                    "width": 1920,
                    "height": 1080
                }
            }
        }

    @property
    def sess(self):
        if self._sess is None:
            self._sess = requests.Session()

        return self._sess

    def get_setting(self, setting_id):
        setting = xbmcaddon.Addon(self.addon_name).getSetting(setting_id)
        if setting == 'true':
            return True
        elif setting == 'false':
            return False
        else:
            return setting

    def set_setting(self, key, value):
        return xbmcaddon.Addon(self.addon_name).setSetting(key, value)

    def open_settings(self):
        xbmcaddon.Addon(self.addon_name).openSettings()

    def dialog_choice(self, heading, message, agree, disagree):
        return xbmcgui.Dialog().yesno(heading, message, yeslabel=agree, nolabel=disagree)

    def add_item(self, title, url, playable=False, info=None, art=None, content=None, folder=True, contextmenu=None):

        list_item = xbmcgui.ListItem(label=title)
        if playable:
            list_item.setProperty('IsPlayable', 'true')
            folder = False
        if art:
            list_item.setArt(art)
        else:
            art = {
                'icon': self.addon.getAddonInfo('icon'),
                'fanart': self.addon.getAddonInfo('fanart')
            }
            list_item.setArt(art)
        if info:
            list_item.setInfo('Video', info)
        if content:
            xbmcplugin.setContent(self.handle, content)
        if contextmenu:
            list_item.addContextMenuItems(contextmenu, replaceItems=True)
        xbmcplugin.addDirectoryItem(self.handle, url, list_item, isFolder=folder)

    def eod(self, cache=True):
        xbmcplugin.endOfDirectory(self.handle, cacheToDisc=cache)

    def refresh(self):
        return xbmc.executebuiltin('Container.Refresh()')

    def notification(self, heading, message):
        xbmcgui.Dialog().notification(heading, message, time=3000)

    def request_sess(self, url, method='get', data={}, headers={}, cookies={}, params={}, result=True, json=False,
                     allow=True, json_data=False):
        params = params if params else self.params
        if method == 'get':
            resp = self.sess.get(url, headers=headers, cookies=cookies, timeout=30, params=params, verify=False,
                                 allow_redirects=allow)
        elif method == 'post':
            if json_data:
                resp = self.sess.post(url, headers=headers, json=data, cookies=cookies, timeout=30, params=params,
                                      verify=False, allow_redirects=allow)
            else:
                resp = self.sess.post(url, headers=headers, data=data, cookies=cookies, timeout=30, params=params,
                                      verify=False, allow_redirects=allow)
        elif method == 'delete':
            resp = self.sess.delete(url, headers=headers, cookies=cookies, timeout=30, params=params, verify=False,
                                    allow_redirects=allow)
        if result:
            return resp.json() if json else resp_text(resp)
        else:
            return resp

    def PlayVid(self, mpdurl, lic_url='', PROTOCOL='', DRM='', certificate='', flags=True, subs=None, vod=False):
        from inputstreamhelper import Helper
        play_item = xbmcgui.ListItem(path=mpdurl)
        if subs:
            play_item.setSubtitles(subs)
        if PROTOCOL:

            is_helper = Helper(PROTOCOL, drm=DRM)
            if is_helper.check_inputstream():
                if sys.version_info >= (3, 0, 0):
                    play_item.setProperty('inputstream', is_helper.inputstream_addon)
                else:
                    play_item.setProperty('inputstreamaddon', is_helper.inputstream_addon)
                if 'mpd' in PROTOCOL:
                    play_item.setMimeType('application/xml+dash')
                else:
                    play_item.setMimeType('application/vnd.apple.mpegurl')
                play_item.setProperty('inputstream.adaptive.manifest_type', PROTOCOL)
                play_item.setProperty('inputstream.adaptive.manifest_headers',
                                      'User-Agent=' + quote(self.UA) + '&Referer=' + quote('https://sweet.tv/'))
                play_item.setProperty('inputstream.adaptive.stream_headers',
                                      'User-Agent=' + quote(self.UA) + '&Referer=' + quote('https://sweet.tv/'))

                if vod == True:
                    play_item.setProperty('ResumeTime', '1')
                    play_item.setProperty('TotalTime', '1')

                if DRM and lic_url:
                    play_item.setProperty('inputstream.adaptive.license_type', DRM)
                    play_item.setProperty('inputstream.adaptive.manifest_update_parameter', 'full')
                    play_item.setProperty('inputstream.adaptive.license_key', lic_url)
                    if certificate:
                        play_item.setProperty('inputstream.adaptive.server_certificate', certificate)
                if flags:
                    play_item.setProperty('inputstream.adaptive.license_flags', "persistent_storage")
                play_item.setContentLookup(False)

        xbmcplugin.setResolvedUrl(self.handle, True, listitem=play_item)

    def ffmpeg_player(self, stream_url):

        sURL = stream_url + '|User-Agent=' + quote(self.UA) + '&Referer=' + quote('https://sweet.tv/')
        play_item = xbmcgui.ListItem(path=sURL)
        xbmcplugin.setResolvedUrl(self.handle, True, listitem=play_item)

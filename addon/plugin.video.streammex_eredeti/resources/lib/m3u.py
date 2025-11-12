import re
from collections import OrderedDict

EXTINF_ATTR_PATTERN = re.compile(r'(\w[\w-]*)="([^"]*)"')


def parse(content):
    entries = []
    current = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('#EXTM3U'):
            continue
        if line.startswith('#EXTINF'):
            current = _parse_extinf(line)
        elif current:
            current['url'] = line
            entries.append(current)
            current = None
    return entries


def _parse_extinf(line):
    header, _, title = line.partition(',')
    metadata = OrderedDict()
    metadata['name'] = title.strip()
    for key, value in EXTINF_ATTR_PATTERN.findall(header):
        metadata[key.lower()] = value
    if 'tvg-name' in metadata and 'tvg_name' not in metadata:
        metadata['tvg_name'] = metadata['tvg-name']
    if 'tvg-id' in metadata and 'tvg_id' not in metadata:
        metadata['tvg_id'] = metadata['tvg-id']
    if 'tvg-logo' in metadata and 'tvg_logo' not in metadata:
        metadata['tvg_logo'] = metadata['tvg-logo']
    if 'group-title' in metadata and 'group_title' not in metadata:
        metadata['group_title'] = metadata['group-title']
    metadata.setdefault('tvg_id', metadata.get('tvg_id', '') or metadata.get('tvg-id', ''))
    metadata.setdefault('tvg_name', metadata.get('tvg_name', '') or metadata.get('tvg-name', ''))
    metadata.setdefault('group_title', metadata.get('group_title', '') or metadata.get('group-title', ''))
    metadata.setdefault('tvg_logo', metadata.get('tvg_logo', '') or metadata.get('tvg-logo', ''))
    return metadata


def merge_playlists(playlists):
    merged = []
    seen = set()
    for entries in playlists:
        for item in entries:
            key = _build_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _build_key(item):
    tvg_id = (item.get('tvg_id') or '').strip().lower()
    if tvg_id:
        return ('id', tvg_id)
    tvg_name = (item.get('tvg_name') or item.get('name') or '').strip().lower()
    url = (item.get('url') or '').strip()
    return ('fallback', tvg_name, url)


def render(entries):
    lines = ['#EXTM3U']
    for item in entries:
        attrs = []
        if item.get('tvg_id'):
            attrs.append(f'tvg-id="{item.get("tvg_id")}"')
        if item.get('tvg_name'):
            attrs.append(f'tvg-name="{item.get("tvg_name")}"')
        if item.get('tvg_logo'):
            attrs.append(f'tvg-logo="{item.get("tvg_logo")}"')
        if item.get('group_title'):
            attrs.append(f'group-title="{item.get("group_title")}"')
        line_attrs = ' '.join(attrs)
        lines.append(f'#EXTINF:-1 {line_attrs},{item.get("name") or item.get("tvg_name") or ""}'.strip())
        lines.append(item.get('url', ''))
    return '\n'.join(lines) + '\n'


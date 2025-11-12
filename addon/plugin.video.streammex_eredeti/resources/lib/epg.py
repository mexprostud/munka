import copy
import xml.etree.ElementTree as ET

from . import util


def _clone(element):
    return copy.deepcopy(element)


def _ensure_parent(element):
    element.tail = None
    return element


def merge_sources(xml_sources, timeout, logger, debug_enabled=False):
    tv_root = ET.Element('tv')
    channel_ids = set()
    programme_count = 0

    for source in xml_sources:
        status, data, headers = util.http_request(source, timeout=timeout)
        if status != 200:
            util.log_warning(f"EPG source fetch failed ({status}) for {source}")
            continue
        try:
            xml_bytes = util.maybe_decompress(data, headers, source)
        except Exception:  # pragma: no cover - corrupted data
            util.log_warning(f"EPG source decompression failed for {source}")
            continue
        try:
            parsed_root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            util.log_warning(f"EPG source parse error for {source}")
            continue

        for channel in parsed_root.findall('channel'):
            channel_id = channel.get('id')
            if channel_id and channel_id not in channel_ids:
                tv_root.append(_ensure_parent(_clone(channel)))
                channel_ids.add(channel_id)
        for programme in parsed_root.findall('programme'):
            tv_root.append(_ensure_parent(_clone(programme)))
            programme_count += 1

        util.log_debug(f"Merged EPG source {source} (channels={len(channel_ids)} programmes={programme_count})", enabled=debug_enabled)

    return tv_root


def build_epg(channels, xml_sources, output_path, timeout=45, debug_enabled=False):
    logger = util
    xml_sources = [src for src in xml_sources if src]
    tv_root = None

    if xml_sources:
        tv_root = merge_sources(xml_sources, timeout, logger, debug_enabled=debug_enabled)
    if tv_root is None or not list(tv_root):
        tv_root = _build_placeholder_epg(channels)

    tree = ET.ElementTree(tv_root)
    xml_bytes = ET.tostring(tv_root, encoding='utf-8', xml_declaration=True)
    util.write_binary_file(output_path, xml_bytes)
    util.log_info(f"EPG written to {output_path}")
    return output_path


def _build_placeholder_epg(channels):
    tv_root = ET.Element('tv')
    for item in channels:
        channel_id = item.get('tvg_id') or item.get('tvg_name') or item.get('name') or item.get('url')
        if not channel_id:
            continue
        channel = ET.SubElement(tv_root, 'channel', attrib={'id': channel_id})
        display = ET.SubElement(channel, 'display-name')
        display.text = item.get('tvg_name') or item.get('name') or channel_id
    return tv_root


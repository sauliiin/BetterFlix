# -*- coding: utf-8 -*-
import json
import re
import time
import urllib.parse

import requests
import xbmc
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from database import db


class FineTuning:
    """Parâmetros de busca, resolução e cache de streams do YouTube."""
    CACHE_TTL = 6 * 60 * 60
    NETWORK_TIMEOUT = 5
    REQUEST_RETRIES = 1
    REQUEST_BACKOFF = 0.2
    REQUEST_STATUS_FORCELIST = (429, 500, 502, 503, 504)
    INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEKK4044nXAjbLtcxoo"
    INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/player"
    CLIENT_ANDROID = {
        "clientName": "ANDROID",
        "clientVersion": "20.10.38",
        "androidSdkVersion": 34,
        "osName": "Android",
        "osVersion": "14",
        "platform": "MOBILE",
    }
    ITAG_PRIORITY = (22, 59, 18)


session = requests.Session()
retries = Retry(
    total=FineTuning.REQUEST_RETRIES,
    backoff_factor=FineTuning.REQUEST_BACKOFF,
    status_forcelist=list(FineTuning.REQUEST_STATUS_FORCELIST),
)
session.mount("https://", HTTPAdapter(max_retries=retries))


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _get_cached_stream(video_id):
    try:
        row = db.fetch_one("SELECT url, info, timestamp FROM youtube_streams WHERE video_id = ?", (video_id,))
        if not row:
            return None, None
        cached_url, info_json, timestamp = row
        if (time.time() - (timestamp or 0)) < FineTuning.CACHE_TTL:
            info = json.loads(info_json or "{}")
            if cached_url and info.get("playback_type") == "progressive":
                return cached_url, info
        db.execute_query("DELETE FROM youtube_streams WHERE video_id = ?", (video_id,))
    except Exception:
        pass
    return None, None


def _cache_stream(video_id, url, info):
    try:
        db.execute_query(
            "INSERT OR REPLACE INTO youtube_streams (video_id, url, info, timestamp) VALUES (?, ?, ?, ?)",
            (video_id, url, json.dumps(info or {}), time.time()),
        )
    except Exception:
        pass


def _get_cached_search(query):
    try:
        row = db.fetch_one("SELECT plugin_url, timestamp FROM youtube_search_cache WHERE query = ?", (query,))
        if not row:
            return False, ""
        plugin_url, timestamp = row
        if (time.time() - (timestamp or 0)) < FineTuning.CACHE_TTL:
            return True, plugin_url or ""
        db.execute_query("DELETE FROM youtube_search_cache WHERE query = ?", (query,))
    except Exception:
        pass
    return False, ""


def _cache_search(query, plugin_url):
    try:
        db.execute_query(
            "INSERT OR REPLACE INTO youtube_search_cache (query, plugin_url, timestamp) VALUES (?, ?, ?)",
            (query, plugin_url or "", time.time()),
        )
    except Exception:
        pass


def cleanup_expired_cache():
    try:
        cutoff = time.time() - FineTuning.CACHE_TTL
        db.execute_query("DELETE FROM youtube_streams WHERE timestamp < ?", (cutoff,))
        db.execute_query("DELETE FROM youtube_search_cache WHERE timestamp < ?", (cutoff,))
    except Exception:
        pass


cleanup_expired_cache()


def extract_video_id(plugin_url):
    if not plugin_url or "video_id=" not in plugin_url:
        return None
    try:
        parsed = urllib.parse.urlparse(plugin_url)
        params = urllib.parse.parse_qs(parsed.query)
        return params.get("video_id", [None])[0]
    except Exception:
        return None


def search_first_plugin_url(query, timeout=None):
    if not query:
        return ""
    cache_hit, cached_plugin_url = _get_cached_search(query)
    if cache_hit:
        return cached_plugin_url
    try:
        encoded = urllib.parse.quote_plus(query)
        search_url = "https://www.youtube.com/results?search_query=%s" % encoded
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        response = session.get(search_url, headers=headers, timeout=float(timeout or FineTuning.NETWORK_TIMEOUT))
        if response.status_code != 200:
            return ""
        html = response.text or ""
        matches = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        if not matches:
            _cache_search(query, "")
            return ""
        plugin_url = "plugin://plugin.video.youtube/play/?video_id=%s" % matches[0]
        _cache_search(query, plugin_url)
        return plugin_url
    except Exception as e:
        xbmc.log("[StreamResolver] search error: %s" % e, xbmc.LOGWARNING)
        return ""


def _make_player_request(video_id, timeout=None):
    payload = {
        "videoId": video_id,
        "context": {"client": FineTuning.CLIENT_ANDROID},
        "contentCheckOk": True,
        "racyCheckOk": True,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip",
        "X-Goog-Api-Format-Version": "2",
    }
    try:
        response = session.post(
            "%s?key=%s" % (FineTuning.INNERTUBE_URL, FineTuning.INNERTUBE_API_KEY),
            json=payload,
            headers=headers,
            timeout=float(timeout or FineTuning.NETWORK_TIMEOUT),
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def _pick_best_progressive(formats):
    if not formats:
        return None

    by_itag = {_safe_int(fmt.get("itag")): fmt for fmt in formats if fmt.get("url")}
    for itag in FineTuning.ITAG_PRIORITY:
        chosen = by_itag.get(itag)
        if chosen:
            return chosen

    candidates = []
    for fmt in formats:
        url = fmt.get("url")
        if not url:
            continue
        mime = (fmt.get("mimeType") or "").lower()
        has_audio = bool(fmt.get("audioQuality") or fmt.get("audioSampleRate") or "mp4a" in mime)
        if mime.startswith("video/mp4") and has_audio:
            candidates.append(fmt)

    if not candidates:
        return None
    candidates.sort(key=lambda item: _safe_int(item.get("height"), 0), reverse=True)
    return candidates[0]


def resolve_stream_url(video_id, use_cache=True, timeout=None):
    if not video_id:
        return None, None

    if use_cache:
        cached_url, cached_info = _get_cached_stream(video_id)
        if cached_url:
            return cached_url, cached_info

    data = _make_player_request(video_id, timeout=timeout)
    if not data:
        return None, None

    playability = data.get("playabilityStatus") or {}
    if (playability.get("status") or "").upper() != "OK":
        return None, None

    formats = (data.get("streamingData") or {}).get("formats") or []
    best = _pick_best_progressive(formats)
    if not best:
        return None, None

    stream_url = best.get("url")
    if not stream_url:
        return None, None

    height = _safe_int(best.get("height"))
    info = {
        "itag": str(_safe_int(best.get("itag"))),
        "height": height,
        "quality": best.get("qualityLabel") or ("%sp" % height if height else "unknown"),
        "playback_type": "progressive",
    }
    _cache_stream(video_id, stream_url, info)
    return stream_url, info


def pre_resolve_trailer(plugin_url, timeout=None):
    video_id = extract_video_id(plugin_url)
    if not video_id:
        return None, None
    return resolve_stream_url(video_id, use_cache=True, timeout=timeout)


def resolve_from_search_query(query, timeout=None):
    """
    Resolve stream direto a partir do primeiro resultado da busca do YouTube.
    Exemplo de query: "<titulo> official trailer (ano)".
    """
    plugin_url = search_first_plugin_url(query, timeout=timeout)
    if not plugin_url:
        return "", {}
    direct_url, info = pre_resolve_trailer(plugin_url, timeout=timeout)
    if not direct_url:
        return "", {}
    return direct_url, info or {}

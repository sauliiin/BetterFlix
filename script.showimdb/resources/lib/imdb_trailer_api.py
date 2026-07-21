# -*- coding: utf-8 -*-
import re
import time
from urllib.parse import parse_qs, urlparse

import requests
import xbmc
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from database import db
import tmdb_api


class FineTuning:
    """Parâmetros de rede, cache e prioridade de qualidade do IMDb."""
    IMDB_GQL_URL = "https://graphql.prod.api.imdb.a2z.com/"
    NETWORK_TIMEOUT = 5
    CACHE_MAX_AGE_VIDEO_ID = 30 * 24 * 60 * 60
    CACHE_MAX_AGE_PLAYBACK = 2 * 24 * 60 * 60
    CACHE_MAX_AGE_EMPTY_PLAYBACK = 12 * 60 * 60
    QUALITY_PRIORITY = (1080, 720)
    REQUEST_RETRIES = 1
    REQUEST_BACKOFF = 0.2
    REQUEST_STATUS_FORCELIST = (429, 500, 502, 503, 504)
    HEADERS = {
        "Referer": "https://www.imdb.com/",
        "Origin": "https://www.imdb.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
        "Accept-Language": "en-US,en",
    }


session = requests.Session()
retries = Retry(
    total=FineTuning.REQUEST_RETRIES,
    backoff_factor=FineTuning.REQUEST_BACKOFF,
    status_forcelist=list(FineTuning.REQUEST_STATUS_FORCELIST),
)
session.mount("https://", HTTPAdapter(max_retries=retries))


def _cleanup_old_cache():
    try:
        limit_video = time.time() - FineTuning.CACHE_MAX_AGE_VIDEO_ID
        now = time.time()
        db.execute_query("DELETE FROM imdb_trailer_ids WHERE timestamp < ?", (limit_video,))
        db.execute_query("DELETE FROM imdb_trailer_urls WHERE timestamp < ?", (now,))
    except Exception as e:
        xbmc.log("[IMDb_API] Cleanup error: %s" % e, xbmc.LOGERROR)


_cleanup_old_cache()


def _minify_query(query):
    return "\n".join(line.strip() for line in query.splitlines() if line.strip())


def _post_graphql(query, variables, operation_name=None):
    payload = {"query": _minify_query(query), "variables": variables or {}}
    if operation_name:
        payload["operationName"] = operation_name

    try:
        response = session.post(
            FineTuning.IMDB_GQL_URL,
            json=payload,
            headers=FineTuning.HEADERS,
            timeout=FineTuning.NETWORK_TIMEOUT,
        )
        if response.status_code != 200:
            xbmc.log("[IMDb_API] HTTP %s on GraphQL request" % response.status_code, xbmc.LOGERROR)
            return {}
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception as e:
        xbmc.log("[IMDb_API] GraphQL request failed: %s" % e, xbmc.LOGERROR)
        return {}


def _resolve_imdb_id(item_id, media_type):
    if not item_id:
        return ""
    if item_id.startswith("tt"):
        return item_id
    try:
        return tmdb_api.fetch_imdb_id(item_id, media_type) or ""
    except Exception:
        return ""


def _extract_quality(display_name):
    if not display_name:
        return 0
    match = re.search(r"(\d{3,4})", str(display_name))
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def _pick_best_playback_url(playback_urls, quality_priority):
    candidates = {}
    for item in playback_urls or []:
        if (item.get("videoMimeType") or "").upper() != "MP4":
            continue
        url = item.get("url") or ""
        if not url:
            continue
        label = (item.get("displayName") or {}).get("value", "")
        quality = _extract_quality(label)
        if quality and quality not in candidates:
            candidates[quality] = url

    if not candidates:
        return ""

    for quality in quality_priority or FineTuning.QUALITY_PRIORITY:
        try:
            q = int(quality)
        except Exception:
            continue
        if q in candidates:
            return candidates[q]
    return ""


def _extract_url_expiry(url):
    try:
        query = parse_qs(urlparse(url).query)
        raw = (query.get("Expires") or query.get("expires") or [""])[0]
        return int(raw) if raw else 0
    except Exception:
        return 0


def _compute_playback_valid_until(playback_url):
    now = time.time()
    max_until = now + FineTuning.CACHE_MAX_AGE_PLAYBACK
    expires_at = _extract_url_expiry(playback_url)
    if expires_at > (now + 180):
        return min(max_until, float(expires_at - 120))
    return max_until


def fetch_latest_trailer_video_id(imdb_id):
    if not imdb_id:
        return ""

    cache_key = "latest_%s" % imdb_id
    result = db.fetch_one("SELECT video_id, timestamp FROM imdb_trailer_ids WHERE cache_key = ?", (cache_key,))
    if result and (time.time() - result[1] < FineTuning.CACHE_MAX_AGE_VIDEO_ID):
        return result[0] or ""

    query = """
    query ($id: ID!) {
      title(id: $id) {
        latestTrailer {
          id
        }
      }
    }
    """
    data = _post_graphql(query, {"id": imdb_id})
    video_id = ""
    try:
        title = (data.get("data") or {}).get("title") or {}
        latest = title.get("latestTrailer") or {}
        video_id = latest.get("id") or ""
    except Exception:
        video_id = ""

    db.execute_query("INSERT OR REPLACE INTO imdb_trailer_ids VALUES (?, ?, ?)", (cache_key, video_id, time.time()))
    return video_id


def fetch_video_playback_url(video_id, quality_priority=FineTuning.QUALITY_PRIORITY):
    if not video_id:
        return ""

    quality_key = "-".join(str(int(q)) for q in (quality_priority or FineTuning.QUALITY_PRIORITY))
    cache_key = "play_%s_%s" % (video_id, quality_key)
    result = db.fetch_one("SELECT playback_url, timestamp FROM imdb_trailer_urls WHERE cache_key = ?", (cache_key,))
    if result and result[0] and (result[1] or 0) > time.time():
        return result[0] or ""

    query = """
    query VideoPlayback($viconst: ID!) {
      video(id: $viconst) {
        playbackURLs {
          displayName { value }
          videoMimeType
          url
        }
      }
    }
    """
    data = _post_graphql(query, {"viconst": video_id}, operation_name="VideoPlayback")
    playback_url = ""
    try:
        video_obj = (data.get("data") or {}).get("video") or {}
        playback_urls = video_obj.get("playbackURLs") or []
        playback_url = _pick_best_playback_url(playback_urls, quality_priority)
    except Exception as e:
        xbmc.log(f"[IMDb_API] Failed to parse playback URLs for {video_id}: {e}", xbmc.LOGERROR)
        playback_url = ""

    valid_until = (
        _compute_playback_valid_until(playback_url)
        if playback_url
        else (time.time() + FineTuning.CACHE_MAX_AGE_EMPTY_PLAYBACK)
    )
    db.execute_query("INSERT OR REPLACE INTO imdb_trailer_urls VALUES (?, ?, ?)", (cache_key, playback_url, valid_until))
    return playback_url


def fetch_trailer_url(item_id, media_type, quality_priority=FineTuning.QUALITY_PRIORITY):
    imdb_id = _resolve_imdb_id(item_id, media_type)
    if not imdb_id:
        return ""

    video_id = fetch_latest_trailer_video_id(imdb_id)
    if not video_id:
        return ""

    return fetch_video_playback_url(video_id, quality_priority=quality_priority)

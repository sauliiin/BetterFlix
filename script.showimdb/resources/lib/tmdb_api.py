# -*- coding: utf-8 -*-
import json
import time

import requests
import xbmc
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from database import db

# Aparelhos fracos: com _DEBUG=False os logs [ShowIMDB][DEBUG] nem são montados
# (a f-string deixa de ser construída). Ligue p/ True para diagnosticar.
_DEBUG = False


def _install_showimdb_debug_log_filter():
    try:
        if getattr(xbmc, '_showimdb_debug_filter_installed', False):
            return
    except Exception:
        pass

    original_log = xbmc.log

    def _filtered_log(message, level=xbmc.LOGDEBUG):
        try:
            if not _DEBUG and isinstance(message, str) and '[ShowIMDB][DEBUG]' in message:
                return
        except Exception:
            pass
        return original_log(message, level)

    try:
        xbmc.log = _filtered_log
        xbmc._showimdb_debug_filter_installed = True
    except Exception:
        pass


_install_showimdb_debug_log_filter()


class FineTuning:
    """Parâmetros de integração com TMDb e cache local."""
    API_KEY = "703cf5598b9fd74adac824baf7923126"
    NETWORK_TIMEOUT = 5
    CACHE_MAX_AGE_TRAILER = 60 * 24 * 60 * 60
    CACHE_MAX_AGE_KEYWORDS = 30 * 24 * 60 * 60
    KEYWORD_BADGES_SCHEMA_VERSION = 2
    REQUEST_RETRIES = 1
    REQUEST_BACKOFF = 0.2
    REQUEST_STATUS_FORCELIST = (429, 500, 502, 503, 504)


TMDB_KEYWORD_BADGES = {
    "based on true story": "Baseado em História Real",
    "prequel": "Prequel",
    "sequel": "Sequência",
    "spin off": "Spin-off",
    "spin-off": "Spin-off",
}


session = requests.Session()
retries = Retry(
    total=FineTuning.REQUEST_RETRIES,
    backoff_factor=FineTuning.REQUEST_BACKOFF,
    status_forcelist=list(FineTuning.REQUEST_STATUS_FORCELIST),
)
session.mount("https://", HTTPAdapter(max_retries=retries))


def _cleanup_old_trailers():
    """Limpa entradas antigas do cache de trailers."""
    try:
        limit_time = time.time() - FineTuning.CACHE_MAX_AGE_TRAILER
        db.execute_query("DELETE FROM tmdb_trailers WHERE timestamp < ?", (limit_time,))
    except Exception as e:
        xbmc.log(f"TMDb_API: Erro Cleanup: {e}", xbmc.LOGERROR)


_cleanup_old_trailers()


def fetch_tmdb_id_from_imdb(imdb_id):
    """Converte IMDb ID para TMDb ID e tipo de mídia."""
    if not imdb_id:
        if _DEBUG: xbmc.log("[ShowIMDB][DEBUG][TMDb] fetch_tmdb_id_from_imdb chamado sem imdb_id", xbmc.LOGWARNING)
        return None, None

    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] >>> fetch_tmdb_id_from_imdb | imdb_id={imdb_id}", xbmc.LOGINFO)
    cache_key = f"find_{imdb_id}"
    result = db.fetch_one("SELECT imdb_id, timestamp FROM tmdb_ids WHERE cache_key = ?", (cache_key,))
    if result:
        try:
            raw = result[0] or ""
            if raw.startswith("{"):
                data = json.loads(raw)
                tmdb_id_c = data.get("tmdb_id")
                mt_c = data.get("media_type")
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] CACHE HIT imdb->tmdb | imdb_id={imdb_id} | tmdb_id={tmdb_id_c} | media_type={mt_c}", xbmc.LOGINFO)
                return tmdb_id_c, mt_c
        except Exception:
            pass

    url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={FineTuning.API_KEY}&external_source=imdb_id"
    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] CACHE MISS imdb->tmdb — GET {url}", xbmc.LOGINFO)
    tmdb_id_found = None
    media_type_found = None
    try:
        t0 = time.time()
        response = session.get(url, timeout=FineTuning.NETWORK_TIMEOUT)
        elapsed = time.time() - t0
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] Resposta HTTP {response.status_code} em {elapsed:.2f}s | imdb_id={imdb_id}", xbmc.LOGINFO)
        if response.status_code == 200:
            data = response.json()
            if data.get("movie_results"):
                tmdb_id_found = data["movie_results"][0].get("id")
                media_type_found = "movie"
            elif data.get("tv_results"):
                tmdb_id_found = data["tv_results"][0].get("id")
                media_type_found = "tv"
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] Resultado | imdb_id={imdb_id} | tmdb_id={tmdb_id_found} | media_type={media_type_found}", xbmc.LOGINFO)
    except Exception as e:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] ERRO fetch_tmdb_id_from_imdb | imdb_id={imdb_id} | erro={e}", xbmc.LOGWARNING)

    if tmdb_id_found:
        to_cache = {"tmdb_id": tmdb_id_found, "media_type": media_type_found}
        db.execute_query("INSERT OR REPLACE INTO tmdb_ids VALUES (?, ?, ?)", (cache_key, json.dumps(to_cache), time.time()))
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] Salvo no cache tmdb_ids | imdb_id={imdb_id}", xbmc.LOGINFO)

    return tmdb_id_found, media_type_found


def fetch_imdb_id(tmdb_id, content_type):
    """Converte TMDb ID para IMDb ID."""
    if not tmdb_id or not content_type:
        return ""

    media_type = "tv" if content_type.lower() in ("tv", "tvshow", "season", "episode") else "movie"
    cache_key = f"{media_type}_{tmdb_id}"
    result = db.fetch_one("SELECT imdb_id FROM tmdb_ids WHERE cache_key = ?", (cache_key,))
    if result:
        raw = result[0] or ""
        if raw.startswith("{"):
            try:
                return json.loads(raw).get("imdb_id", "")
            except Exception:
                pass
        else:
            return raw

    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids?api_key={FineTuning.API_KEY}"
    imdb_id_found = ""
    try:
        response = session.get(url, timeout=FineTuning.NETWORK_TIMEOUT)
        if response.status_code == 200:
            imdb_id_found = response.json().get("imdb_id", "")
    except Exception:
        pass

    if imdb_id_found:
        to_cache = {"imdb_id": imdb_id_found}
        db.execute_query("INSERT OR REPLACE INTO tmdb_ids VALUES (?, ?, ?)", (cache_key, json.dumps(to_cache), time.time()))
    return imdb_id_found


def _video_score(video):
    site = (video.get("site") or "").lower()
    if site != "youtube":
        return (-1, 0, 0)
    is_trailer = 1 if (video.get("type") or "").lower() == "trailer" else 0
    is_official = 1 if bool(video.get("official", False)) else 0
    lang = (video.get("iso_639_1") or "").lower()
    lang_score = 2 if lang == "pt" else (1 if lang == "en" else 0)
    return (is_trailer, is_official, lang_score)


def fetch_trailer_url(tmdb_id, media_type):
    """Busca URL de trailer do YouTube via TMDb."""
    if not tmdb_id or not media_type:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] fetch_trailer_url chamado sem tmdb_id ou media_type | tmdb_id={tmdb_id} | media_type={media_type}", xbmc.LOGWARNING)
        return ""

    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] >>> fetch_trailer_url | tmdb_id={tmdb_id} | media_type={media_type}", xbmc.LOGINFO)
    media_type_clean = "tv" if media_type.lower() in ("tv", "tvshow", "season", "episode") else "movie"
    cache_key = f"trailer_{media_type_clean}_{tmdb_id}"
    result = db.fetch_one("SELECT trailer_url, timestamp FROM tmdb_trailers WHERE cache_key = ?", (cache_key,))
    if result and (time.time() - result[1] < FineTuning.CACHE_MAX_AGE_TRAILER):
        age_h = (time.time() - result[1]) / 3600
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] CACHE HIT trailer | tmdb_id={tmdb_id} | idade={age_h:.1f}h | url={result[0]}", xbmc.LOGINFO)
        return result[0]

    url = (
        f"https://api.themoviedb.org/3/{media_type_clean}/{tmdb_id}/videos"
        f"?api_key={FineTuning.API_KEY}&language=en-US,pt-BR"
    )
    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] CACHE MISS trailer — GET {url}", xbmc.LOGINFO)
    trailer_url = ""
    try:
        t0 = time.time()
        response = session.get(url, timeout=FineTuning.NETWORK_TIMEOUT)
        elapsed = time.time() - t0
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] Resposta HTTP {response.status_code} em {elapsed:.2f}s | tmdb_id={tmdb_id}", xbmc.LOGINFO)
        if response.status_code == 200:
            videos = response.json().get("results", [])
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] Videos encontrados: {len(videos)} | tmdb_id={tmdb_id}", xbmc.LOGINFO)
            if videos:
                scored = sorted(videos, key=_video_score, reverse=True)
                best = scored[0]
                if _DEBUG: xbmc.log(
                    f"[ShowIMDB][DEBUG][TMDb] Melhor vídeo | type={best.get('type')} site={best.get('site')} "
                    f"official={best.get('official')} lang={best.get('iso_639_1')} key={best.get('key')}",
                    xbmc.LOGINFO
                )
                if (best.get("site") or "").lower() == "youtube":
                    key = best.get("key") or ""
                    if key:
                        trailer_url = f"plugin://plugin.video.youtube/play/?video_id={key}"
    except Exception as e:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] ERRO fetch_trailer_url | tmdb_id={tmdb_id} | erro={e}", xbmc.LOGWARNING)

    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] Resultado trailer | tmdb_id={tmdb_id} | url={trailer_url or '(vazio)'}", xbmc.LOGINFO)
    db.execute_query("INSERT OR REPLACE INTO tmdb_trailers VALUES (?, ?, ?)", (cache_key, trailer_url, time.time()))
    return trailer_url


def fetch_keyword_badges(tmdb_id, media_type):
    """Busca keywords descritivas do TMDb e retorna labels em PT-BR."""
    if not tmdb_id or not media_type:
        return ""

    media_type_clean = "tv" if media_type.lower() in ("tv", "tvshow", "season", "episode") else "movie"
    cache_key = f"keywords_v{FineTuning.KEYWORD_BADGES_SCHEMA_VERSION}_{media_type_clean}_{tmdb_id}"
    result = db.fetch_one("SELECT badges, timestamp FROM tmdb_keyword_badges WHERE cache_key = ?", (cache_key,))
    if result and (time.time() - result[1] < FineTuning.CACHE_MAX_AGE_KEYWORDS):
        return result[0] or ""

    url = f"https://api.themoviedb.org/3/{media_type_clean}/{tmdb_id}/keywords?api_key={FineTuning.API_KEY}"
    labels = []
    seen = set()
    try:
        response = session.get(url, timeout=FineTuning.NETWORK_TIMEOUT)
        if response.status_code == 200:
            payload = response.json()
            raw_keywords = payload.get("keywords", payload.get("results", [])) or []
            names = set()
            for keyword in raw_keywords:
                name = (keyword.get("name") or "").strip().lower()
                if name:
                    names.add(name)

            for keyword_name, label in TMDB_KEYWORD_BADGES.items():
                if keyword_name in names and label not in seen:
                    labels.append(label)
                    seen.add(label)
    except Exception as e:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] ERRO fetch_keyword_badges | tmdb_id={tmdb_id} | erro={e}", xbmc.LOGWARNING)

    detail_url = f"https://api.themoviedb.org/3/{media_type_clean}/{tmdb_id}?api_key={FineTuning.API_KEY}&language=en-US"
    try:
        response = session.get(detail_url, timeout=FineTuning.NETWORK_TIMEOUT)
        if response.status_code == 200:
            details = response.json()
            detail_labels = []
            if media_type_clean == "movie" and details.get("belongs_to_collection"):
                detail_labels.append("Pertence a Coleção")
            if media_type_clean == "tv" and (details.get("status") or "").lower() == "ended":
                detail_labels.append("Finalizada")
            for label in detail_labels:
                if label not in seen:
                    labels.append(label)
                    seen.add(label)
    except Exception as e:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][TMDb] ERRO fetch_detail_badges | tmdb_id={tmdb_id} | erro={e}", xbmc.LOGWARNING)

    badges = ", ".join(labels)
    db.execute_query("INSERT OR REPLACE INTO tmdb_keyword_badges VALUES (?, ?, ?)", (cache_key, badges, time.time()))
    return badges

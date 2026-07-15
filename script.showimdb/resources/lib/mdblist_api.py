# -*- coding: utf-8 -*-
import json
import time
from datetime import datetime

import requests
import xbmc
import xbmcaddon

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
    """Parâmetros de API, cache e rede do MDBList."""
    API_KEY = "omqfcrbt1dm8hj98mwuvgpg9n"
    API_URL = "https://mdblist.com/api/?apikey=%s&i=%s"
    API_BASE_URL = "https://api.mdblist.com/%s"
    CACHE_MAX_AGE = 15 * 24 * 60 * 60
    BADGES_SCHEMA_VERSION = 2
    NETWORK_TIMEOUT = 7


session = requests.Session()

_api_key_cache = None  # (key, timestamp) — TTL 300s; evita Addon()+getSetting por chamada

def get_api_key():
    global _api_key_cache
    now = time.time()
    if _api_key_cache is not None and (now - _api_key_cache[1]) < 300:
        return _api_key_cache[0]
    key = FineTuning.API_KEY
    try:
        configured_key = (xbmcaddon.Addon("script.showimdb").getSetting("api_key") or "").strip()
        if configured_key:
            key = configured_key
    except Exception:
        pass
    _api_key_cache = (key, now)
    return key


def _safe_int(value):
    try:
        if value not in (None, ""):
            return int(value)
    except Exception:
        pass
    return None


def _build_ids(imdb_id="", tmdb_id="", tvdb_id=""):
    ids = {}
    if imdb_id:
        ids["imdb"] = str(imdb_id)
    tmdb_int = _safe_int(tmdb_id)
    if tmdb_int is not None:
        ids["tmdb"] = tmdb_int
    tvdb_int = _safe_int(tvdb_id)
    if tvdb_int is not None:
        ids["tvdb"] = tvdb_int
    return ids


def _collection_bucket(media_type):
    media_type = (media_type or "").lower()
    if media_type in ("movie", "movies"):
        return "movies"
    if media_type in ("season", "seasons"):
        return "seasons"
    if media_type in ("episode", "episodes"):
        return "episodes"
    return "shows"


def _result_count(result, keys, bucket):
    if not isinstance(result, dict):
        return 0
    total = 0
    for key in keys:
        value = result.get(key)
        if isinstance(value, dict):
            bucket_value = value.get(bucket)
            if isinstance(bucket_value, int):
                total += bucket_value
            elif isinstance(bucket_value, list):
                total += len(bucket_value)
        elif isinstance(value, int):
            total += value
    return total


def update_collection(action, media_type, imdb_id="", tmdb_id="", tvdb_id=""):
    """Adiciona ou remove um título da coleção do usuário no MDBList."""
    action = (action or "").lower()
    if action not in ("add", "remove"):
        raise ValueError("Ação MDBList inválida: %s" % action)

    ids = _build_ids(imdb_id=imdb_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id)
    if not ids:
        raise ValueError("Nenhum ID válido para enviar ao MDBList.")

    bucket = _collection_bucket(media_type)
    item = {"ids": ids}
    if action == "add":
        item["collected_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    payload = {bucket: [item]}
    endpoint = "sync/collection" if action == "add" else "sync/collection/remove"
    url = FineTuning.API_BASE_URL % endpoint

    xbmc.log(
        "[ShowIMDB][MDBList] update_collection | action=%s | bucket=%s | ids=%s"
        % (action, bucket, ids),
        xbmc.LOGINFO,
    )

    response = session.post(
        url,
        params={"apikey": get_api_key()},
        json=payload,
        timeout=FineTuning.NETWORK_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json() if response.content else {}
    changed_keys = ("added", "updated") if action == "add" else ("removed", "deleted")
    return {
        "result": result,
        "changed": _result_count(result, changed_keys, bucket),
        "not_found": _result_count(result, ("not_found",), bucket),
    }


def get_ratings(imdb_id):
    if not imdb_id:
        if _DEBUG: xbmc.log("[ShowIMDB][DEBUG][MDBList] get_ratings chamado sem imdb_id", xbmc.LOGWARNING)
        return {}

    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] >>> get_ratings | imdb_id={imdb_id}", xbmc.LOGINFO)

    result = db.fetch_one("SELECT data, timestamp FROM mdlist_data WHERE imdb_id = ?", (imdb_id,))
    if result and (time.time() - result[1] < FineTuning.CACHE_MAX_AGE):
        try:
            cached = json.loads(result[0])
            age_h = (time.time() - result[1]) / 3600
            if cached.get("badges_schema_version") == FineTuning.BADGES_SCHEMA_VERSION:
                if _DEBUG: xbmc.log(
                    f"[ShowIMDB][DEBUG][MDBList] CACHE HIT | imdb_id={imdb_id} | idade={age_h:.1f}h | "
                    f"imdb={cached.get('imdb_rating','')} lb={cached.get('letterboxd_rating','')} "
                    f"trakt={cached.get('trakt_rating','')} badges={cached.get('highlight_badges','')}",
                    xbmc.LOGINFO
                )
                return cached
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] CACHE HIT com schema antigo | imdb_id={imdb_id}", xbmc.LOGINFO)
        except Exception:
            pass

    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] CACHE MISS — buscando da API | imdb_id={imdb_id}", xbmc.LOGINFO)
    data_to_cache = {}
    try:
        url = FineTuning.API_URL % (get_api_key(), imdb_id)
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] GET {url}", xbmc.LOGINFO)
        t0 = time.time()
        response = session.get(url, timeout=FineTuning.NETWORK_TIMEOUT)
        elapsed = time.time() - t0
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] Resposta HTTP {response.status_code} em {elapsed:.2f}s | imdb_id={imdb_id}", xbmc.LOGINFO)
        response.raise_for_status()
        json_data = response.json()

        all_ratings_raw = json_data.get("ratings", [])
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] Ratings recebidos ({len(all_ratings_raw)} fontes): {[{'source': r.get('source'), 'value': r.get('value')} for r in all_ratings_raw]}", xbmc.LOGINFO)

        for rating in all_ratings_raw:
            source = rating.get("source")
            value = rating.get("value")
            if source == "imdb" and value is not None:
                data_to_cache["imdb_rating"] = str(value)
            elif source == "letterboxd" and value is not None:
                data_to_cache["letterboxd_rating"] = str(value)
            elif source == "trakt" and value is not None:
                data_to_cache["trakt_rating"] = str(value)

        badges = []
        highlight_dict = {
            "imdb-top-250": "Top 250 IMDb",
            "imdb-top-100": "Top 100 IMDb",
            "letterboxd-top-250": "Top 250 Letterboxd",
            "metacritic-must-see": "Must See Metacritic",
            "certified-fresh": "Certified Fresh",
            "certified-hot": "Certified Hot",
            "national-film-registry": "Tao Bom que Virou Patrimonio",
            "best-picture-winner": "Vencedor de Melhor Filme",
            "best-picture-nominated": "Indicado a Melhor Filme",
            "oscar-winner": "Vencedor do Oscar",
            "oscar-nominated": "Indicado ao Oscar",
            "cult-film": "Classico Cult",
            "belongs-to-collection": "Pertence a Coleção",
            "first-in-collection": "Pertence a Coleção",
            "collection-follow-up": "Pertence a Coleção",
        }

        keywords_raw = json_data.get("keywords", [])
        matched_keywords = [kw.get("name", "") for kw in keywords_raw if kw.get("name", "") in highlight_dict]
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] Keywords recebidas: {[kw.get('name') for kw in keywords_raw]} | Badges encontrados: {matched_keywords}", xbmc.LOGINFO)

        for kw in keywords_raw:
            name = kw.get("name", "")
            if name in highlight_dict:
                badges.append(highlight_dict[name])

        if badges:
            data_to_cache["highlight_badges"] = ", ".join(badges)
        data_to_cache["badges_schema_version"] = FineTuning.BADGES_SCHEMA_VERSION

        if _DEBUG: xbmc.log(
            f"[ShowIMDB][DEBUG][MDBList] RESULTADO FINAL | imdb_id={imdb_id} | "
            f"imdb_rating={data_to_cache.get('imdb_rating','')} | "
            f"letterboxd_rating={data_to_cache.get('letterboxd_rating','')} | "
            f"trakt_rating={data_to_cache.get('trakt_rating','')} | "
            f"highlight_badges={data_to_cache.get('highlight_badges','')}",
            xbmc.LOGINFO
        )

        db.execute_query("INSERT OR REPLACE INTO mdlist_data VALUES (?, ?, ?)", (imdb_id, json.dumps(data_to_cache), time.time()))
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] Salvo no cache SQLite | imdb_id={imdb_id}", xbmc.LOGINFO)
        return data_to_cache
    except Exception as e:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][MDBList] ERRO ao buscar ratings | imdb_id={imdb_id} | erro={e}", xbmc.LOGERROR)
        raise

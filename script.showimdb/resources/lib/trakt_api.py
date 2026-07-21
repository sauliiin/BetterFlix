# -*- coding: utf-8 -*-
import time

import requests
import xbmc

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
    """Parâmetros de integração com Trakt e filtragem de reviews."""
    API_KEY = "fbc2791a2609e77d4e9d1689b7332a7124428eb7d8ea46085876d8867755a357"
    CACHE_MAX_AGE = 15 * 24 * 60 * 60
    NETWORK_TIMEOUT = 10
    REQUEST_LIMIT = 200
    MAX_REVIEWS = 50
    MAX_COMMENT_LENGTH = 600
    ACCEPTED_LANGS = ("pt", "en", "es")
    HEADERS = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": API_KEY,
    }


session = requests.Session()


def get_reviews_by_imdb_id(imdb_id, media_type):
    """Retorna reviews formatadas do Trakt para um IMDb ID."""
    if not imdb_id:
        if _DEBUG: xbmc.log("[ShowIMDB][DEBUG][Trakt] get_reviews chamado sem imdb_id", xbmc.LOGWARNING)
        return ""

    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] >>> get_reviews | imdb_id={imdb_id} | media_type={media_type}", xbmc.LOGINFO)

    result = db.fetch_one("SELECT data, timestamp FROM trakt_reviews WHERE imdb_id = ?", (imdb_id,))
    if result and result[0] and (time.time() - result[1] < FineTuning.CACHE_MAX_AGE):
        age_h = (time.time() - result[1]) / 3600
        char_count = len(result[0])
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] CACHE HIT | imdb_id={imdb_id} | idade={age_h:.1f}h | tamanho={char_count} chars", xbmc.LOGINFO)
        return result[0]
    if result and not result[0]:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] Cache vazio encontrado, limpando | imdb_id={imdb_id}", xbmc.LOGINFO)
        db.execute_query("DELETE FROM trakt_reviews WHERE imdb_id = ?", (imdb_id,))

    trakt_type = "shows" if media_type == "tv" else "movies"
    url = f"https://api.trakt.tv/{trakt_type}/{imdb_id}/comments"
    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] CACHE MISS — buscando da API | url={url}", xbmc.LOGINFO)
    result_text = ""

    try:
        params = {"limit": FineTuning.REQUEST_LIMIT, "sort": "likes"}
        t0 = time.time()
        response = session.get(url, headers=FineTuning.HEADERS, params=params, timeout=FineTuning.NETWORK_TIMEOUT)
        elapsed = time.time() - t0
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] Resposta HTTP {response.status_code} em {elapsed:.2f}s | imdb_id={imdb_id}", xbmc.LOGINFO)
        response.raise_for_status()
        raw_comments = response.json()
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] Total de comentários recebidos: {len(raw_comments)} | imdb_id={imdb_id}", xbmc.LOGINFO)

        formatted_comments = []
        skipped_spoiler = 0
        skipped_lang = 0
        skipped_empty = 0
        skipped_long = 0

        for item in raw_comments:
            if len(formatted_comments) >= FineTuning.MAX_REVIEWS:
                break

            comment_text = (item.get("comment") or "").strip()
            is_spoiler = bool(item.get("spoiler", False))
            user_lang = (item.get("user") or {}).get("language", "en")
            if is_spoiler:
                skipped_spoiler += 1
                continue
            if not comment_text:
                skipped_empty += 1
                continue
            if len(comment_text) > FineTuning.MAX_COMMENT_LENGTH:
                skipped_long += 1
                continue
            if user_lang not in FineTuning.ACCEPTED_LANGS:
                skipped_lang += 1
                continue

            review_block = f"[B][COLOR FFE50914]ANÁLISE:[/COLOR][/B] {comment_text}"
            rating = item.get("user_rating")
            if rating is not None:
                review_block += f"  [B]NOTA: {rating}/10[/B]"
            formatted_comments.append(review_block)

        if _DEBUG: xbmc.log(
            f"[ShowIMDB][DEBUG][Trakt] Filtro reviews | imdb_id={imdb_id} | "
            f"aceitas={len(formatted_comments)} | spoilers={skipped_spoiler} | "
            f"idioma={skipped_lang} | vazias={skipped_empty} | longas={skipped_long}",
            xbmc.LOGINFO
        )

        if formatted_comments:
            result_text = "\n\n".join(formatted_comments)
    except Exception as e:
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] ERRO | imdb_id={imdb_id} | erro={e}", xbmc.LOGWARNING)
        return ""

    if result_text:
        db.execute_query("INSERT OR REPLACE INTO trakt_reviews VALUES (?, ?, ?)", (imdb_id, result_text, time.time()))
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] Reviews salvas no cache | imdb_id={imdb_id} | tamanho={len(result_text)} chars", xbmc.LOGINFO)
    else:
        db.execute_query("DELETE FROM trakt_reviews WHERE imdb_id = ?", (imdb_id,))
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trakt] Nenhuma review válida encontrada | imdb_id={imdb_id}", xbmc.LOGINFO)
    return result_text

# -*- coding: utf-8 -*-
import json
import time
import threading

from database import db


class FineTuning:
    CACHE_MAX_AGE = 15 * 24 * 60 * 60
    SCHEMA_VERSION = 2
    MEM_TTL = 60          # janela curta: cobre os merges consecutivos do mesmo item
    MEM_MAX_ENTRIES = 8   # itens recentes; evita crescer em sessões longas


# Cache em memória na frente do SQLite: os 4+ merges por item deixam de pagar
# SELECT+json.loads sob o conn_lock global. Escritas continuam indo ao DB na hora.
# Coerência: todos os escritores rodam no processo do service; o único externo é
# clear_cache.py (ação manual) — o TTL curto limita a janela de staleness.
_mem_lock = threading.Lock()
_mem_cache = {}  # imdb_id -> (payload_normalizado, timestamp)


def _copy_payload(payload):
    copied = dict(payload)
    copied["props"] = dict(payload.get("props", {}))
    return copied


def _mem_get(imdb_id):
    with _mem_lock:
        entry = _mem_cache.get(imdb_id)
        if entry is None:
            return None
        if (time.time() - entry[1]) >= FineTuning.MEM_TTL:
            _mem_cache.pop(imdb_id, None)
            return None
        return _copy_payload(entry[0])


def _mem_put(imdb_id, payload):
    with _mem_lock:
        _mem_cache[imdb_id] = (_copy_payload(payload), time.time())
        while len(_mem_cache) > FineTuning.MEM_MAX_ENTRIES:
            oldest = min(_mem_cache, key=lambda k: _mem_cache[k][1])
            _mem_cache.pop(oldest, None)


def _mem_drop(imdb_id):
    with _mem_lock:
        _mem_cache.pop(imdb_id, None)


_DEFAULT_PAYLOAD = {
    "schema_version": FineTuning.SCHEMA_VERSION,
    "props": {},
    "ratings_ready": False,
    "highlights_ready": False,
    "reviews_ready": False,
}


def _normalize_props(props):
    normalized = {}
    for key, value in (props or {}).items():
        normalized[str(key)] = "" if value is None else str(value)
    return normalized


def _normalize_payload(payload):
    normalized = dict(_DEFAULT_PAYLOAD)
    if isinstance(payload, dict):
        normalized["schema_version"] = int(payload.get("schema_version") or 0)
        normalized["props"] = _normalize_props(payload.get("props", {}))
        normalized["ratings_ready"] = bool(payload.get("ratings_ready"))
        normalized["highlights_ready"] = bool(payload.get("highlights_ready"))
        normalized["reviews_ready"] = bool(payload.get("reviews_ready"))
    return normalized


def load(imdb_id):
    if not imdb_id:
        return None
    cached = _mem_get(imdb_id)
    if cached is not None:
        return cached
    row = db.fetch_one("SELECT data, timestamp FROM ui_bundle_cache WHERE imdb_id = ?", (imdb_id,))
    if not row:
        return None
    if (time.time() - (row[1] or 0)) >= FineTuning.CACHE_MAX_AGE:
        clear(imdb_id)
        return None
    try:
        payload = json.loads(row[0] or "{}")
        if isinstance(payload, dict):
            normalized = _normalize_payload(payload)
            if normalized.get("schema_version") == FineTuning.SCHEMA_VERSION:
                _mem_put(imdb_id, normalized)
                return normalized
    except Exception:
        pass
    clear(imdb_id)
    return None


def save(imdb_id, payload):
    if not imdb_id:
        return
    normalized = _normalize_payload(payload)
    db.execute_query(
        "INSERT OR REPLACE INTO ui_bundle_cache VALUES (?, ?, ?)",
        (imdb_id, json.dumps(normalized), time.time()),
    )
    _mem_put(imdb_id, normalized)


def merge(imdb_id, props=None, remove_keys=None, ratings_ready=None, highlights_ready=None, reviews_ready=None):
    if not imdb_id:
        return None

    payload = load(imdb_id) or dict(_DEFAULT_PAYLOAD)
    payload["props"] = dict(payload.get("props", {}))

    if props:
        payload["props"].update(_normalize_props(props))

    for key in (remove_keys or []):
        payload["props"].pop(str(key), None)

    if ratings_ready is not None:
        payload["ratings_ready"] = bool(ratings_ready)
    if highlights_ready is not None:
        payload["highlights_ready"] = bool(highlights_ready)
    if reviews_ready is not None:
        payload["reviews_ready"] = bool(reviews_ready)

    save(imdb_id, payload)
    return payload


def clear(imdb_id):
    if not imdb_id:
        return
    _mem_drop(imdb_id)
    db.execute_query("DELETE FROM ui_bundle_cache WHERE imdb_id = ?", (imdb_id,))

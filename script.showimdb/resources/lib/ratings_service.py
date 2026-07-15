# -*- coding: utf-8 -*-
"""
Ciclo independente de ratings para script.showimdb.
Roda em daemon thread com polling adaptativo para que as notas
(middle, IMDb, Letterboxd, Trakt, votes, badges, reviews, weekday)
apareçam praticamente instantâneas ao focar um item.

Desacoplado do service.py principal — não interage com trailers, player,
sniper ou qualquer lógica de reprodução.
"""
import json
import threading
import time

from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcgui
from datetime import date


_DEBUG = False

_instance = None  # RatingsService singleton
_thread = None


class FineTuning:
    """Parâmetros de tempo do ciclo de ratings independente."""
    POLL_INTERVAL = 0.08            # 80ms — polling rápido logo após troca/retorno
    IDLE_POLL_INTERVAL = 0.20       # foco estável: reduz leituras de InfoLabel sem ficar lento
    FAST_POLL_WINDOW = 0.90         # janela rápida depois de mudanças de foco/estado
    HOME_PLAYBACK_POLL_INTERVAL = 0.20  # Home visível + vídeo tocando: um pouco mais lazy
    FULLSCREEN_PAUSE_POLL_INTERVAL = 1.50  # Playback fullscreen fora da Home: pausa o ciclo
    DEBOUNCE = 0.03                 # 30ms — debounce mínimo na troca de item
    REVIEWS_DEBOUNCE = 2.50         # debounce para reviews (API pesada)
    REVIEWS_DEBOUNCE_WARM = 4.00    # debounce para reviews em modo econômico
    EMPTY_GRACE = 0.35              # tolerância antes de tratar id vazio como "saiu do item"
    RATINGS_WORKERS = 2             # MDBList pode ter uma requisicao velha em andamento
    HIGHLIGHTS_WORKERS = 1          # OMDb/badges serializados para poupar rede/CPU
    KEYWORD_WORKERS = 1             # TMDb keywords em lane propria


# Props gerenciadas exclusivamente por este módulo.
RATING_PROP_KEYS = (
    "middle",
    "ds_info_imdb_rating",
    "ds_info_letterboxd_rating",
    "ds_info_trakt_rating",
    "ds_info_imdb_votes",
)

ALL_MANAGED_PROPS = (
    "middle",
    "ds_info_imdb_rating",
    "ds_info_letterboxd_rating",
    "ds_info_trakt_rating",
    "ds_info_imdb_votes",
    "ds_info_oscars",
    "ds_info_badges",
    "ds_info_awards",
    "ds_info_badges_cf",
    "Trakt.Reviews",
    "dsWeekDay",
)

BADGE_PROP_KEYS = (
    "ds_info_oscars",
    "ds_info_badges",
    "ds_info_awards",
    "ds_info_badges_cf",
)


class RatingsService:
    """Ciclo independente que observa o item em foco e publica notas/badges/reviews."""

    def __init__(self):
        self._monitor = xbmc.Monitor()
        self._player = xbmc.Player()
        self._skin_win = xbmcgui.Window(10000)
        self._skin_cache = {}
        self._cache_lock = threading.Lock()
        self._current_item_id = None
        self._session = 0
        self._session_lock = threading.Lock()
        self._empty_since = None
        self._fullscreen_pause_active = False
        self._fast_poll_until = time.time() + FineTuning.FAST_POLL_WINDOW
        self._ratings_executor = ThreadPoolExecutor(max_workers=FineTuning.RATINGS_WORKERS)
        self._highlights_executor = ThreadPoolExecutor(max_workers=FineTuning.HIGHLIGHTS_WORKERS)
        self._keywords_executor = ThreadPoolExecutor(max_workers=FineTuning.KEYWORD_WORKERS)
        self._chain_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ShowIMDBRatingsChain")
        self._keyword_lock = threading.Lock()
        self._keyword_inflight = set()
        self._keyword_results = {}
        self._review_cv = threading.Condition()
        self._review_task = None
        self._review_worker = threading.Thread(
            target=self._review_worker_loop,
            daemon=True,
            name="ShowIMDBReviewsWorker",
        )
        self._review_worker.start()

    # ── Skin props ──────────────────────────────────────────────────

    def _update_skin_props(self, props_dict):
        with self._cache_lock:
            for name, value in props_dict.items():
                val_str = str(value) if value is not None else ""
                if self._skin_cache.get(name) == val_str:
                    continue
                self._skin_cache[name] = val_str
                if val_str:
                    self._skin_win.setProperty(name, val_str)
                else:
                    self._skin_win.clearProperty(name)

    def _get_skin_props(self, keys):
        with self._cache_lock:
            return {key: self._skin_cache.get(key, "") for key in keys}

    # ── Session ─────────────────────────────────────────────────────

    def _new_session(self):
        with self._session_lock:
            self._session += 1
            return self._session

    def _is_session_valid(self, session):
        with self._session_lock:
            return self._session == session

    # ── Focus detection ─────────────────────────────────────────────

    def _info_label(self, label):
        try:
            return (xbmc.getInfoLabel(label) or "").strip()
        except Exception:
            return ""

    def _get_focused_item_id(self):
        """Lê o item_id + imdb_hint do item em foco. Retorna (item_id, imdb_hint)."""
        prop_tmdb_id = self._info_label("Window(10000).Property(ds_info_tmdb_id)")
        if prop_tmdb_id:
            imdb_id = (
                self._info_label("Window(10000).Property(ds_imdb_id)")
                or self._info_label("Window(10000).Property(ContextMenuTargetID)")
            )
            return prop_tmdb_id, imdb_id

        dbtype = self._info_label("ListItem.DBType") or self._info_label("Container(54).ListItem.DBType")
        tmdb_id = self._info_label("ListItem.UniqueID(tmdb)") or self._info_label("Container(54).ListItem.UniqueID(tmdb)")
        tvshow_tmdb_id = (
            self._info_label("ListItem.UniqueID(tvshow.tmdb)")
            or self._info_label("Container(54).ListItem.UniqueID(tvshow.tmdb)")
        )
        imdb_id = self._info_label("ListItem.IMDBNumber") or self._info_label("Container(54).ListItem.IMDBNumber")

        list_tmdb_id = tvshow_tmdb_id if dbtype.lower() in ("episode", "season") and tvshow_tmdb_id else tmdb_id

        prop_imdb_id = (
            self._info_label("Window(10000).Property(ds_imdb_id)")
            or self._info_label("Window(10000).Property(ContextMenuTargetID)")
        )
        item_id = prop_tmdb_id or list_tmdb_id or prop_imdb_id or imdb_id
        return item_id, imdb_id

    def _get_media_type(self):
        dbtype = (
            self._info_label("ListItem.DBType")
            or self._info_label("Container(54).ListItem.DBType")
            or self._info_label("Window(10000).Property(ds_info_dbtype)")
        )
        return "tv" if dbtype and dbtype.lower() in ("tv", "tvshow", "episode", "season") else "movie"

    # ── Playback gate ───────────────────────────────────────────────

    def _get_playback_state(self):
        try:
            is_playing = self._player.isPlayingVideo()
        except Exception:
            is_playing = False
        try:
            fullscreen = xbmc.getCondVisibility("Window.IsVisible(12005)")
        except Exception:
            fullscreen = False
        try:
            home_visible = xbmc.getCondVisibility("Window.IsVisible(10000)")
        except Exception:
            home_visible = False
        return is_playing, fullscreen, home_visible

    def _should_pause_for_fullscreen(self, is_playing, fullscreen, home_visible):
        return is_playing and fullscreen and not home_visible

    def _set_fullscreen_pause(self, active):
        if active == self._fullscreen_pause_active:
            return

        self._fullscreen_pause_active = active
        self._empty_since = None
        self._current_item_id = None

        if active:
            self._new_session()
            xbmc.log("[ShowIMDB][RatingsService] Pausado durante playback fullscreen", xbmc.LOGINFO)
        else:
            self._touch_fast_poll()
            xbmc.log("[ShowIMDB][RatingsService] Retomado apos playback fullscreen", xbmc.LOGINFO)

    def _touch_fast_poll(self, seconds=None):
        seconds = FineTuning.FAST_POLL_WINDOW if seconds is None else seconds
        self._fast_poll_until = max(self._fast_poll_until, time.time() + seconds)

    def _get_poll_interval(self, is_playing, home_visible):
        if is_playing and home_visible:
            return FineTuning.HOME_PLAYBACK_POLL_INTERVAL
        if time.time() < self._fast_poll_until:
            return FineTuning.POLL_INTERVAL
        return FineTuning.IDLE_POLL_INTERVAL

    def _submit(self, executor, target, *args):
        try:
            executor.submit(target, *args)
        except Exception:
            t = threading.Thread(target=target, args=args)
            t.daemon = True
            t.start()

    def _shutdown_workers(self):
        for executor in (self._ratings_executor, self._highlights_executor, self._keywords_executor, self._chain_executor):
            try:
                executor.shutdown(wait=False)
            except Exception:
                pass

    # ── Clear helpers ───────────────────────────────────────────────

    def _clear_all_managed(self):
        """Limpa todas as props gerenciadas por este módulo."""
        self._update_skin_props({key: "" for key in ALL_MANAGED_PROPS})

    # ── Cache helpers ───────────────────────────────────────────────

    def _get_cached_ui_bundle(self, imdb_id):
        if not imdb_id:
            return None
        try:
            import ui_bundle
            payload = ui_bundle.load(imdb_id)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return None

    def _get_cached_middle_rating(self, imdb_id):
        if not imdb_id:
            return False, ""
        try:
            from database import db
            row = db.fetch_one("SELECT middle_rating FROM middle_cache WHERE imdb_id = ?", (imdb_id,))
            if row:
                return True, row[0] or ""
        except Exception:
            pass
        return False, ""

    def _get_cached_ratings_data(self, imdb_id):
        if not imdb_id:
            return False, {}
        try:
            import mdblist_api
            from database import db
            row = db.fetch_one("SELECT data, timestamp FROM mdlist_data WHERE imdb_id = ?", (imdb_id,))
            if row and (time.time() - (row[1] or 0) < mdblist_api.FineTuning.CACHE_MAX_AGE):
                payload = json.loads(row[0] or "{}")
                if isinstance(payload, dict):
                    if payload.get("badges_schema_version") != mdblist_api.FineTuning.BADGES_SCHEMA_VERSION:
                        return False, {}
                    return True, payload
        except Exception:
            pass
        return False, {}

    def _calculate_middle_rating(self, data):
        if not isinstance(data, dict):
            return ""
        try:
            ratings = []
            raw_imdb = data.get("imdb_rating", "")
            raw_lb = data.get("letterboxd_rating", "")
            raw_tr = data.get("trakt_rating", "")
            try:
                v = float(raw_imdb or 0)
                if 0 < v < 10:
                    ratings.append(v)
            except Exception:
                pass
            try:
                v = float(raw_lb or 0)
                if 0 < v <= 5:
                    ratings.append(v * 2)
            except Exception:
                pass
            try:
                v = float(raw_tr or 0)
                if 0 < v <= 100:
                    ratings.append(v / 10)
            except Exception:
                pass
            if ratings:
                return "{:.1f}".format(sum(ratings) / len(ratings))
        except Exception:
            pass
        return ""

    def _build_rating_props(self, data):
        data = data or {}
        lb_rating = ""
        if data.get("letterboxd_rating"):
            try:
                lb_rating = str(float(data.get("letterboxd_rating")) * 2)
            except Exception:
                pass
        tr_rating = ""
        if data.get("trakt_rating"):
            try:
                v = float(data.get("trakt_rating"))
                tr_rating = "{:.1f}".format(v / 10 if v > 10 else v)
            except Exception:
                pass
        props = {
            "ds_info_imdb_rating": data.get("imdb_rating", ""),
            "ds_info_letterboxd_rating": lb_rating,
            "ds_info_trakt_rating": tr_rating,
            "ds_info_imdb_votes": data.get("imdb_votes", ""),
        }
        return props, lb_rating, tr_rating

    def _get_tmdb_keyword_badges(self, item_id, imdb_id, media_type):
        tmdb_id = ""
        tmdb_media_type = media_type
        try:
            import tmdb_api
            if item_id and not str(item_id).startswith("tt"):
                tmdb_id = item_id
            elif imdb_id:
                tmdb_id, resolved_media_type = tmdb_api.fetch_tmdb_id_from_imdb(imdb_id)
                tmdb_media_type = resolved_media_type or tmdb_media_type
            return tmdb_api.fetch_keyword_badges(tmdb_id, tmdb_media_type) if tmdb_id else ""
        except Exception:
            pass
        return ""

    def _merge_keyword_badges_into_props(self, props, keyword_badges):
        props = dict(props or {})
        if not keyword_badges:
            return props
        try:
            from highlights import _build_awards_payload
            badge_parts = [props.get("ds_info_badges", ""), keyword_badges]
            raw_badges = ", ".join([part for part in badge_parts if part])
            formatted_badges, awards_text, certified_fresh_badge = _build_awards_payload(
                props.get("ds_info_oscars", ""),
                raw_badges,
            )
            props.update({
                "ds_info_badges": formatted_badges,
                "ds_info_awards": awards_text,
                "ds_info_badges_cf": certified_fresh_badge,
            })
        except Exception:
            pass
        return props

    def _keyword_key(self, item_id, imdb_id, media_type):
        return (imdb_id or "", str(item_id or ""), media_type or "")

    def _publish_keyword_badges(self, item_id, imdb_id, media_type, session, keyword_badges, base_props=None):
        if not keyword_badges or not self._is_session_valid(session):
            return

        current_props = self._get_skin_props(BADGE_PROP_KEYS)
        current_props.update(base_props or {})
        merged_props = self._merge_keyword_badges_into_props(current_props, keyword_badges)
        badge_props = {
            "ds_info_badges": merged_props.get("ds_info_badges", ""),
            "ds_info_awards": merged_props.get("ds_info_awards", ""),
            "ds_info_badges_cf": merged_props.get("ds_info_badges_cf", ""),
        }
        self._update_skin_props(badge_props)
        try:
            import ui_bundle
            ui_bundle.merge(imdb_id, props=badge_props, highlights_ready=True)
        except Exception:
            pass

    def _process_tmdb_keyword_badges(self, item_id, imdb_id, media_type, session, inflight_key):
        try:
            keyword_badges = self._get_tmdb_keyword_badges(item_id, imdb_id, media_type)
            if not keyword_badges:
                return

            with self._keyword_lock:
                self._keyword_results[inflight_key] = keyword_badges
            if self._is_session_valid(session):
                self._publish_keyword_badges(item_id, imdb_id, media_type, session, keyword_badges)
        finally:
            with self._keyword_lock:
                self._keyword_inflight.discard(inflight_key)

    def _start_tmdb_keyword_badges(self, item_id, imdb_id, media_type, session, base_props):
        if not imdb_id or not self._is_session_valid(session):
            return
        inflight_key = self._keyword_key(item_id, imdb_id, media_type)
        with self._keyword_lock:
            keyword_badges = self._keyword_results.get(inflight_key)
            if not keyword_badges:
                self._keyword_inflight.add(inflight_key)
        if keyword_badges:
            self._publish_keyword_badges(item_id, imdb_id, media_type, session, keyword_badges, base_props)
            return

        self._submit(
            self._keywords_executor,
            self._process_tmdb_keyword_badges,
            item_id,
            imdb_id,
            media_type,
            session,
            inflight_key,
        )

    def _get_cached_highlight_props(self, imdb_id, fallback_badges=None):
        omdb_oscars = ""
        raw_badges = fallback_badges if fallback_badges is not None else ""
        cache_hit = False
        try:
            from database import db
            row = db.fetch_one("SELECT oscars, badges, timestamp FROM badges_data WHERE imdb_id = ?", (imdb_id,))
            cache_ttl = 30 * 24 * 60 * 60
            if row and (time.time() - (row[2] or 0) < cache_ttl):
                omdb_oscars = row[0] or ""
                cached_badges = row[1] or ""
                if fallback_badges is None:
                    raw_badges = cached_badges
                elif raw_badges != cached_badges:
                    db.execute_query(
                        "INSERT OR REPLACE INTO badges_data VALUES (?, ?, ?, ?)",
                        (imdb_id, omdb_oscars, raw_badges, time.time())
                    )
                cache_hit = True
        except Exception:
            pass

        props = {
            "ds_info_oscars": omdb_oscars,
            "ds_info_badges": "",
            "ds_info_awards": "",
            "ds_info_badges_cf": "",
        }
        if omdb_oscars or raw_badges:
            try:
                from highlights import _build_awards_payload
                formatted_badges, awards_text, certified_fresh_badge = _build_awards_payload(omdb_oscars, raw_badges)
                props.update({
                    "ds_info_badges": formatted_badges,
                    "ds_info_awards": awards_text,
                    "ds_info_badges_cf": certified_fresh_badge,
                })
            except Exception:
                pass
        return cache_hit, props

    def _get_cached_reviews(self, imdb_id):
        if not imdb_id:
            return False, ""
        try:
            import trakt_api
            from database import db
            row = db.fetch_one("SELECT data, timestamp FROM trakt_reviews WHERE imdb_id = ?", (imdb_id,))
            if row and row[0] and (time.time() - (row[1] or 0) < trakt_api.FineTuning.CACHE_MAX_AGE):
                return True, row[0] or ""
            if row and not row[0]:
                db.execute_query("DELETE FROM trakt_reviews WHERE imdb_id = ?", (imdb_id,))
        except Exception:
            pass
        return False, ""

    def _is_economy_mode(self):
        try:
            import ratings_prefetch
            return ratings_prefetch.cache_warm
        except Exception:
            return False

    def _get_reviews_debounce(self):
        return FineTuning.REVIEWS_DEBOUNCE_WARM if self._is_economy_mode() else FineTuning.REVIEWS_DEBOUNCE

    def _schedule_reviews(self, imdb_id, media_type, session, delay=None):
        if not imdb_id or not self._is_session_valid(session):
            return
        delay = self._get_reviews_debounce() if delay is None else max(0.0, float(delay))
        with self._review_cv:
            self._review_task = (time.time() + delay, imdb_id, media_type, session)
            self._review_cv.notify()

    def _review_worker_loop(self):
        while not self._monitor.abortRequested():
            with self._review_cv:
                while self._review_task is None and not self._monitor.abortRequested():
                    self._review_cv.wait(0.5)
                if self._monitor.abortRequested():
                    return

                deadline, imdb_id, media_type, session = self._review_task
                wait_time = deadline - time.time()
                if wait_time > 0:
                    self._review_cv.wait(min(wait_time, 0.5))
                    continue

                task = self._review_task
                self._review_task = None

            _deadline, imdb_id, media_type, session = task
            if self._is_session_valid(session):
                self._process_reviews_api(imdb_id, media_type, session)

    # ── Processing ──────────────────────────────────────────────────

    def _process_weekday(self, session):
        if not self._is_session_valid(session):
            return
        try:
            raw = xbmc.getInfoLabel("ListItem.Premiered")
            if not raw or raw == "0":
                self._update_skin_props({"dsWeekDay": ""})
                return
            parts = raw.split("-") if "-" in raw else raw.split("/")
            if len(parts) == 3:
                p1, p2, p3 = int(parts[0]), int(parts[1]), int(parts[2])
                dt_obj = date(p1, p2, p3) if len(parts[0]) == 4 else date(p3, p2, p1)
                days = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
                if self._is_session_valid(session):
                    self._update_skin_props({"dsWeekDay": days[dt_obj.weekday()]})
        except Exception:
            self._update_skin_props({"dsWeekDay": ""})

    def _process_ratings_api(self, imdb_id, item_id, media_type, session):
        """Busca ratings da API MDBList e publica as notas sem esperar highlights."""
        if not self._is_session_valid(session):
            return
        data = {}
        try:
            import mdblist_api
            data = mdblist_api.get_ratings(imdb_id) or {}
        except Exception:
            pass
        if not self._is_session_valid(session):
            return

        rating_props, lb_rating, tr_rating = self._build_rating_props(data)
        quick_middle = self._calculate_middle_rating(data)
        rating_skin_props = dict(rating_props)
        rating_skin_props["middle"] = quick_middle

        if self._is_session_valid(session):
            self._update_skin_props(rating_skin_props)
        try:
            import ui_bundle
            ui_bundle.merge(imdb_id, props=rating_skin_props, ratings_ready=True)
        except Exception:
            pass

        self._submit(
            self._highlights_executor,
            self._process_highlights_api,
            imdb_id,
            item_id,
            media_type,
            data,
            lb_rating,
            tr_rating,
            quick_middle,
            session,
        )

    def _process_highlights_api(self, imdb_id, item_id, media_type, data, lb_rating, tr_rating, fallback_middle, session):
        """Processa highlights/badges em lane separada para nao segurar ratings."""
        if not self._is_session_valid(session):
            return

        middle_rating = fallback_middle or ""
        omdb_oscars = ""
        formatted_badges = ""
        awards_text = ""
        certified_fresh_badge = ""
        try:
            from highlights import process_highlights
            processed_middle, omdb_oscars, formatted_badges, awards_text, certified_fresh_badge = process_highlights(
                imdb_id, data, lb_rating, tr_rating
            )
            middle_rating = processed_middle or middle_rating
        except Exception:
            pass

        highlight_props = {
            "middle": middle_rating,
            "ds_info_oscars": omdb_oscars,
            "ds_info_badges": formatted_badges,
            "ds_info_awards": awards_text,
            "ds_info_badges_cf": certified_fresh_badge,
        }
        if self._is_session_valid(session):
            self._update_skin_props(highlight_props)
        try:
            import ui_bundle
            ui_bundle.merge(
                imdb_id,
                props=highlight_props,
                highlights_ready=True,
            )
        except Exception:
            pass
        self._start_tmdb_keyword_badges(item_id, imdb_id, media_type, session, highlight_props)

    def _process_reviews_api(self, imdb_id, media_type, session):
        """Busca reviews da API Trakt e publica na skin."""
        if not self._is_session_valid(session):
            return
        try:
            import trakt_api
            import ui_bundle
            reviews = trakt_api.get_reviews_by_imdb_id(imdb_id, media_type) or ""
            if self._is_session_valid(session):
                self._update_skin_props({"Trakt.Reviews": reviews})
            try:
                if reviews:
                    ui_bundle.merge(imdb_id, props={"Trakt.Reviews": reviews}, reviews_ready=True)
                else:
                    ui_bundle.merge(imdb_id, remove_keys=["Trakt.Reviews"], reviews_ready=False)
            except Exception:
                pass
        except Exception:
            if self._is_session_valid(session):
                self._update_skin_props({"Trakt.Reviews": ""})

    def _process_chain(self, item_id, imdb_hint, media_type, session):
        """Chain principal: resolve e publica ratings/badges/reviews/weekday."""
        if not self._is_session_valid(session):
            return

        # 1. Props iniciais — limpa badges/reviews, preserva notas (v1 behavior)
        props_to_update = {
            "dsWeekDay": "",
            "ds_info_oscars": "",
            "ds_info_badges": "",
            "ds_info_awards": "",
            "ds_info_badges_cf": "",
            "Trakt.Reviews": "",
        }

        # 2. Quick bundle: tenta ui_bundle cache por imdb_id
        quick_bundle = None
        imdb_quick = imdb_hint if imdb_hint.startswith("tt") else (item_id if item_id.startswith("tt") else "")
        try:
            if imdb_quick:
                quick_bundle = self._get_cached_ui_bundle(imdb_quick)
                if isinstance(quick_bundle, dict):
                    props_to_update.update(quick_bundle.get("props", {}))
                else:
                    _middle_hit, middle_value = self._get_cached_middle_rating(imdb_quick)
                    if middle_value:
                        props_to_update["middle"] = middle_value
        except Exception:
            pass

        self._update_skin_props(props_to_update)

        # 3. Weekday
        self._process_weekday(session)

        # 4. Resolver imdb_id (se necessário, via TMDb)
        imdb_id = imdb_quick
        try:
            if not imdb_id:
                import tmdb_api
                imdb_id = tmdb_api.fetch_imdb_id(item_id, media_type)
        except Exception:
            pass

        if imdb_id and self._is_session_valid(session):
            self._start_tmdb_keyword_badges(item_id, imdb_id, media_type, session, {})
            cached_props = {}
            ratings_hit = False
            highlights_hit = False
            reviews_hit = False
            ratings_data = {}
            cached_bundle = (
                quick_bundle if imdb_id == imdb_quick and isinstance(quick_bundle, dict)
                else self._get_cached_ui_bundle(imdb_id)
            )

            if isinstance(cached_bundle, dict):
                cached_props.update(cached_bundle.get("props", {}))
                ratings_hit = bool(cached_bundle.get("ratings_ready"))
                highlights_hit = bool(cached_bundle.get("highlights_ready"))
                reviews_hit = bool(cached_bundle.get("reviews_ready") and cached_props.get("Trakt.Reviews"))

                if ratings_hit and highlights_hit and reviews_hit:
                    if self._is_session_valid(session):
                        self._update_skin_props(cached_props)
                        self._start_tmdb_keyword_badges(item_id, imdb_id, media_type, session, cached_props)
                    return

            if not ratings_hit:
                ratings_hit, ratings_data = self._get_cached_ratings_data(imdb_id)
                middle_hit, middle_value = self._get_cached_middle_rating(imdb_id)
                if ratings_hit:
                    rating_props, _lb_rating, _tr_rating = self._build_rating_props(ratings_data)
                    cached_props.update(rating_props)
                    if middle_hit:
                        cached_props["middle"] = middle_value
                    else:
                        computed_middle = self._calculate_middle_rating(ratings_data)
                        if computed_middle:
                            cached_props["middle"] = computed_middle

            if not highlights_hit:
                highlights_hit, highlight_props = self._get_cached_highlight_props(
                    imdb_id,
                    ratings_data.get("highlight_badges") if ratings_data else None,
                )
                if highlight_props:
                    cached_props.update(highlight_props)

            if not reviews_hit:
                reviews_hit, cached_reviews = self._get_cached_reviews(imdb_id)
                if reviews_hit:
                    cached_props["Trakt.Reviews"] = cached_reviews
                else:
                    cached_props["Trakt.Reviews"] = ""

            if cached_props and self._is_session_valid(session):
                self._update_skin_props(cached_props)
                if highlights_hit:
                    self._start_tmdb_keyword_badges(item_id, imdb_id, media_type, session, cached_props)

            # API calls para o que não está em cache (threads separadas)
            if (not ratings_hit or not highlights_hit) and self._is_session_valid(session):
                self._submit(
                    self._ratings_executor,
                    self._process_ratings_api,
                    imdb_id,
                    item_id,
                    media_type,
                    session,
                )

            if not reviews_hit and self._is_session_valid(session):
                self._schedule_reviews(imdb_id, media_type, session)

        elif not imdb_id:
            # Sem imdb_id — limpa notas para não deixar dados do item anterior
            if self._is_session_valid(session):
                self._update_skin_props({key: "" for key in RATING_PROP_KEYS})

    # ── Rush reviews (chamado pelo service.py no context menu) ──────

    def _rush_reviews_for(self, item_id):
        """Busca reviews imediatamente, sem debounce."""
        imdb_id = (
            self._info_label("Window(10000).Property(ds_imdb_id)")
            or self._info_label("Window(10000).Property(ContextMenuTargetID)")
        )
        if not imdb_id:
            return
        reviews_hit, _ = self._get_cached_reviews(imdb_id)
        if reviews_hit:
            return
        session = self._session
        media_type = self._get_media_type()
        self._schedule_reviews(imdb_id, media_type, session, delay=0.0)

    # ── Main loop ───────────────────────────────────────────────────

    def run(self):
        xbmc.log("[ShowIMDB][RatingsService] Ciclo de ratings independente iniciado", xbmc.LOGINFO)
        try:
            while not self._monitor.abortRequested():
                is_playing, fullscreen, home_visible = self._get_playback_state()
                if self._should_pause_for_fullscreen(is_playing, fullscreen, home_visible):
                    self._set_fullscreen_pause(True)
                    self._monitor.waitForAbort(FineTuning.FULLSCREEN_PAUSE_POLL_INTERVAL)
                    continue

                self._set_fullscreen_pause(False)
                poll_interval = self._get_poll_interval(is_playing, home_visible)
                item_id, imdb_hint = self._get_focused_item_id()

                if not item_id:
                    if self._current_item_id is not None:
                        if self._empty_since is None:
                            self._empty_since = time.time()
                        elif (time.time() - self._empty_since) >= FineTuning.EMPTY_GRACE:
                            self._current_item_id = None
                            self._empty_since = None
                            self._new_session()
                            self._clear_all_managed()
                    self._monitor.waitForAbort(poll_interval)
                    continue

                self._empty_since = None

                if item_id != self._current_item_id:
                    self._current_item_id = item_id
                    self._touch_fast_poll()
                    session = self._new_session()

                    # Debounce ultrarrápido
                    self._monitor.waitForAbort(FineTuning.DEBOUNCE)
                    if not self._is_session_valid(session):
                        continue

                    media_type = self._get_media_type()
                    # Executor reutilizável em vez de Thread nova por troca de item:
                    # em scroll rápido as chains obsoletas enfileiradas retornam cedo
                    # via _is_session_valid; 2 workers cobrem uma chain presa em rede.
                    self._submit(self._chain_executor, self._process_chain, item_id, imdb_hint, media_type, session)

                self._monitor.waitForAbort(poll_interval)
        except Exception as e:
            xbmc.log("[ShowIMDB][RatingsService] Erro fatal no ciclo: %s" % e, xbmc.LOGERROR)
        self._shutdown_workers()
        xbmc.log("[ShowIMDB][RatingsService] Ciclo encerrado", xbmc.LOGINFO)


# ── API pública ─────────────────────────────────────────────────────

def start():
    """Inicia o ciclo de ratings como daemon thread."""
    global _instance, _thread
    try:
        if _thread and _thread.is_alive():
            return
    except Exception:
        pass
    _instance = RatingsService()
    _thread = threading.Thread(target=_instance.run, daemon=True, name="ShowIMDBRatings")
    _thread.start()


def rush_reviews(item_id):
    """Chamado pelo service.py quando o context menu abre, para apressar reviews."""
    if _instance:
        _instance._rush_reviews_for(item_id)

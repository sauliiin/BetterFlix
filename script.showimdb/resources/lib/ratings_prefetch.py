# -*- coding: utf-8 -*-
"""
Ratings Pre-fetcher para script.showimdb
Percorre todas as listas MDBList do usuario e pre-popula os caches SQLite
(mdlist_data, badges_data, middle_cache) antes do usuario focar qualquer item.

Compativel com Kodi 19, 20, 21 (Matrix, Nexus, Omega)

Features:
- Adaptive worker scaling: 4 workers normais, 2 em modo economico
- Modo economico quando >=85% do cache ja esta preenchido, durante reproducao
  ou com RAM disponivel baixa (guard via /proc/meminfo; ciclo e adiado se critica)
- Check de cache triplo (mdlist_data + badges_data + trakt_reviews) para evitar downloads duplicados
- Logs detalhados por lista e resumo final
- Execucao periodica a cada 6h com state file
"""

import json
import time
import threading
import requests
import xbmc

from queue import Queue, Empty
from threading import Thread, Lock, Event

# Flag de módulo — True quando cache >= CACHE_HIT_THRESHOLD (consultada pelo service.py)
cache_warm = False
PROGRESS_MEDIA_CONDITION = 'Window.IsVisible(progress_media.xml) | Window.IsActive(progress_media.xml)'
PROGRESS_MEDIA_POLL = 0.5
PROGRESS_MEDIA_PLAYBACK_GRACE = 5.0
_progress_media_lock = Lock()
_progress_media_last_active = False
_progress_media_resume_playback_base = None

# Instâncias únicas de Monitor/Player: reinstanciar xbmc.Player()/xbmc.Monitor() a
# cada item/lista é caro no Kodi. Reusamos uma só (mesmo padrão do ratings_service).
_monitor = xbmc.Monitor()
_player = xbmc.Player()


def _available_memory_mb():
    """RAM disponivel em MB, lida de /proc/meminfo (MemAvailable). Retorna None
    se nao for possivel ler (ex.: plataforma sem /proc)."""
    try:
        with open('/proc/meminfo', 'r') as fh:
            for line in fh:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return None


def _prefetch_state_path():
    import os
    import xbmcvfs
    path = xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/')
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return os.path.join(path, 'ratings_prefetch_state.json')


def _read_prefetch_state():
    try:
        import os
        state_file = _prefetch_state_path()
        if os.path.exists(state_file):
            with open(state_file, 'r') as handle:
                state = json.load(handle)
                return state if isinstance(state, dict) else {}
    except Exception:
        pass
    return {}


def _restore_cache_warm_state():
    """Restaura o modo economico sem esperar a varredura agendada do boot."""
    global cache_warm
    state = _read_prefetch_state()
    # Compatibilidade com o estado antigo, que gravava apenas last_run: uma
    # passagem concluida significa que o cache ja foi populado ao menos uma vez.
    cache_warm = bool(state.get('cache_warm', state.get('last_run', 0)))
    return cache_warm


def is_progress_media_active():
    try:
        return xbmc.getCondVisibility(PROGRESS_MEDIA_CONDITION)
    except Exception:
        return False


def wait_for_progress_media_release(stop_event=None):
    global _progress_media_last_active, _progress_media_resume_playback_base
    while True:
        active = is_progress_media_active()
        with _progress_media_lock:
            if active:
                _progress_media_last_active = True
                _progress_media_resume_playback_base = None
            else:
                if _progress_media_last_active:
                    _progress_media_last_active = False
                    if _player.isPlayingVideo():
                        try:
                            _progress_media_resume_playback_base = float(_player.getTime())
                        except Exception:
                            _progress_media_resume_playback_base = 0.0
                    else:
                        _progress_media_resume_playback_base = None

                if _progress_media_resume_playback_base is None:
                    return True

                if not _player.isPlayingVideo():
                    _progress_media_resume_playback_base = None
                    return True

                try:
                    current_time = float(_player.getTime())
                except Exception:
                    current_time = None

                if current_time is None:
                    _progress_media_resume_playback_base = None
                    return True

                if current_time < _progress_media_resume_playback_base:
                    _progress_media_resume_playback_base = current_time
                elif current_time - _progress_media_resume_playback_base >= PROGRESS_MEDIA_PLAYBACK_GRACE:
                    _progress_media_resume_playback_base = None
                    return True

        if stop_event is not None and stop_event.is_set():
            return False
        if _monitor.waitForAbort(PROGRESS_MEDIA_POLL):
            return False


# ============================================================================
# FINE TUNING — ajuste aqui
# ============================================================================

class FineTuning:
    MDBLIST_API_KEY      = 'omqfcrbt1dm8hj98mwuvgpg9n'
    CHECK_INTERVAL_HOURS = 6
    BOOT_DELAY           = 240      # segundos aguardar Kodi inicializar

    # Workers adaptativos
    NUM_WORKERS_FULL     = 4        # modo normal (cache incompleto)
    NUM_WORKERS_LIGHT    = 2        # modo economico (>=85% cache, tocando video ou RAM baixa)
    CACHE_HIT_THRESHOLD  = 0.85     # acima disso -> modo economico

    # Guard de RAM (device alvo tem ~384MB livres; historico de OOM-kill)
    LOW_MEMORY_LIGHT_MB  = 100      # abaixo disso -> forca modo LIGHT
    LOW_MEMORY_SKIP_MB   = 80       # abaixo disso -> adia o ciclo inteiro

    # Rate limiting
    API_DELAY            = 0.8      # delay entre chamadas a API (segundos)
    API_DELAY_ECONOMY    = 2.0      # delay no modo economico (cache >= 80%)
    API_DELAY_PLAYING    = 1.5      # delay durante reproducao
    BATCH_DELAY          = 1.6      # pausa entre listas
    BATCH_DELAY_ECONOMY  = 4.0      # pausa entre listas no modo economico
    BATCH_DELAY_PLAYING  = 3.0      # pausa entre listas durante reproducao
    QUEUE_TIMEOUT        = 1

    # Cache TTLs
    RATINGS_MAX_AGE      = 15 * 24 * 3600   # 15 dias
    BADGES_MAX_AGE       = 30 * 24 * 3600   # 30 dias
    REVIEWS_MAX_AGE      = 15 * 24 * 3600   # 15 dias

    CHECK_INTERVAL_SECONDS = CHECK_INTERVAL_HOURS * 3600


# ============================================================================
# LOG
# ============================================================================

class Log:
    PREFIX = 'RatingsPrefetch'

    @staticmethod
    def info(msg):
        xbmc.log('[%s] %s' % (Log.PREFIX, msg), xbmc.LOGINFO)

    @staticmethod
    def separator():
        xbmc.log('[%s] %s' % (Log.PREFIX, '=' * 60), xbmc.LOGINFO)

    @staticmethod
    def header(msg):
        Log.separator()
        Log.info(msg)
        Log.separator()

    @staticmethod
    def list_start(name, count):
        Log.separator()
        Log.info('Lista "%s" (%d itens)' % (name, count))

    @staticmethod
    def list_complete(name, fetched, skipped, errors):
        Log.info('"%s" concluida: %d novos, %d em cache, %d erros' % (name, fetched, skipped, errors))

    @staticmethod
    def item_skip(title):
        Log.info('Cache OK: "%s"' % title)

    @staticmethod
    def item_fetch(title, imdb_id):
        Log.info('Buscado: "%s" [%s]' % (title, imdb_id))

    @staticmethod
    def item_error(title, error):
        Log.info('ERRO "%s": %s' % (title, error))

    @staticmethod
    def adaptive_mode(cache_hit_rate, workers, is_playing):
        pct = cache_hit_rate * 100
        Log.separator()
        Log.info('MODO ADAPTATIVO')
        Log.info('   Cache preenchido: %.1f%%' % pct)
        Log.info('   Workers: %d' % workers)
        if is_playing:
            Log.info('   Motivo: video reproduzindo (modo economico)')
        elif cache_hit_rate >= FineTuning.CACHE_HIT_THRESHOLD:
            Log.info('   Motivo: >90%% em cache (modo economico)')
        else:
            Log.info('   Motivo: cache incompleto (modo completo)')
        Log.separator()

    @staticmethod
    def summary(stats):
        Log.separator()
        Log.info('PREFETCH CONCLUIDO')
        Log.info('   Total de itens: %d' % stats['total'])
        Log.info('   Processados: %d' % stats['processed'])
        Log.info('   Novos (API): %d' % stats['fetched'])
        Log.info('   Em cache (skip): %d' % stats['skipped'])
        Log.info('   Erros: %d' % stats['errors'])
        Log.info('   Tempo total: %.1fs (%.1fmin)' % (stats['elapsed'], stats['elapsed'] / 60))
        if stats['processed'] > 0:
            Log.info('   Media por item: %.2fs' % (stats['elapsed'] / stats['processed']))
        Log.separator()


# ============================================================================
# PROGRESS — thread-safe
# ============================================================================

class Progress:
    def __init__(self):
        self._lock = Lock()
        self._reset()

    def _reset(self):
        self.total     = 0
        self.processed = 0
        self.fetched   = 0
        self.skipped   = 0
        self.errors    = 0
        self.start_time = None
        self._list_stats = {}

    def start(self, total):
        with self._lock:
            self._reset()
            self.total = total
            self.start_time = time.time()

    def set_list(self, name):
        with self._lock:
            if name not in self._list_stats:
                self._list_stats[name] = {'fetched': 0, 'skipped': 0, 'errors': 0}

    def add(self, fetched=False, skipped=False, error=False):
        with self._lock:
            self.processed += 1
            if fetched:
                self.fetched += 1
            if skipped:
                self.skipped += 1
            if error:
                self.errors += 1

    def add_list(self, name, fetched=False, skipped=False, error=False):
        with self._lock:
            s = self._list_stats.setdefault(name, {'fetched': 0, 'skipped': 0, 'errors': 0})
            if fetched:
                s['fetched'] += 1
            if skipped:
                s['skipped'] += 1
            if error:
                s['errors'] += 1

    def get_list(self, name):
        with self._lock:
            return dict(self._list_stats.get(name, {'fetched': 0, 'skipped': 0, 'errors': 0}))

    def get(self):
        with self._lock:
            elapsed = time.time() - self.start_time if self.start_time else 0
            pct = (self.processed / self.total * 100) if self.total > 0 else 0
            return {
                'total':     self.total,
                'processed': self.processed,
                'fetched':   self.fetched,
                'skipped':   self.skipped,
                'errors':    self.errors,
                'elapsed':   elapsed,
                'percent':   pct,
            }


# ============================================================================
# RATE LIMITER
# ============================================================================

class RateLimiter:
    def __init__(self, base_delay=1.0):
        self._base    = base_delay
        self._current = base_delay
        self._max     = 30.0
        self._lock    = Lock()
        self._playing = False

    def set_mode(self, is_playing, is_economy=False):
        with self._lock:
            self._playing = is_playing
            if is_playing:
                self._base    = FineTuning.API_DELAY_PLAYING
            elif is_economy:
                self._base    = FineTuning.API_DELAY_ECONOMY
            else:
                self._base    = FineTuning.API_DELAY
            self._current = self._base

    def wait(self):
        with self._lock:
            delay = self._current
        time.sleep(delay)

    def ok(self):
        with self._lock:
            if self._current > self._base:
                self._current = max(self._base, self._current * 0.8)

    def limit_hit(self):
        with self._lock:
            self._current = min(self._max, self._current * 3)
        Log.info('Rate limit atingido — modo soft (delay: %.1fs), aguardando 30s...' % self._current)
        time.sleep(30)


# ============================================================================
# MDBLIST CLIENT
# ============================================================================

class MDBListClient:
    BASE = 'https://api.mdblist.com'

    def __init__(self):
        self._key = FineTuning.MDBLIST_API_KEY
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_maxsize=10,
            max_retries=requests.adapters.Retry(total=3, backoff_factor=0.5)
        )
        self._session.mount('https://', adapter)

    def _get(self, endpoint, params=None):
        params = params or {}
        params['apikey'] = self._key
        try:
            r = self._session.get('%s/%s' % (self.BASE, endpoint), params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            Log.info('Erro API MDBList: %s' % e)
            return None

    def get_lists(self):
        lists = []
        user = self._get('lists/user') or []
        for lst in user:
            lst['list_type'] = 'user'
        lists.extend(user)

        external = self._get('external/lists/user') or []
        for lst in external:
            lst['list_type'] = 'external'
        lists.extend(external)

        return lists

    def get_items(self, list_id, list_type):
        endpoint = ('external/lists/%s/items' % list_id) if list_type == 'external' else ('lists/%s/items' % list_id)
        return self._get(endpoint, {'unified': 'true'}) or []


# ============================================================================
# CACHE CHECKER
# ============================================================================

class CacheChecker:
    """Verifica se os dados de um imdb_id ja estao nos caches SQLite."""

    def __init__(self):
        from database import db
        self._db = db

    def is_fully_cached(self, imdb_id):
        """Retorna True se ratings, badges e reviews estiverem frescos."""
        now = time.time()

        # JOIN das 3 tabelas numa única query: o INNER JOIN só devolve linha se
        # ratings, badges e reviews existirem (mesma semântica dos 3 SELECTs).
        row = self._db.fetch_one(
            'SELECT m.data, m.timestamp, b.timestamp, t.timestamp '
            'FROM mdlist_data m '
            'JOIN badges_data b ON b.imdb_id = m.imdb_id '
            'JOIN trakt_reviews t ON t.imdb_id = m.imdb_id '
            'WHERE m.imdb_id = ?',
            (imdb_id,)
        )
        if not row:
            return False
        m_data, m_ts, b_ts, t_ts = row
        if (now - (m_ts or 0) >= FineTuning.RATINGS_MAX_AGE
                or now - (b_ts or 0) >= FineTuning.BADGES_MAX_AGE
                or now - (t_ts or 0) >= FineTuning.REVIEWS_MAX_AGE):
            return False
        try:
            import mdblist_api
            ratings_data = json.loads(m_data or '{}')
            if ratings_data.get('badges_schema_version') != mdblist_api.FineTuning.BADGES_SCHEMA_VERSION:
                return False
        except Exception:
            return False

        return True

    def calculate_hit_rate(self, imdb_ids):
        """Retorna fracao (0.0..1.0) de ids com cache completo (batch query)."""
        if not imdb_ids:
            return 0.0
        now = time.time()
        min_ratings_ts = now - FineTuning.RATINGS_MAX_AGE
        min_badges_ts  = now - FineTuning.BADGES_MAX_AGE
        min_reviews_ts = now - FineTuning.REVIEWS_MAX_AGE
        placeholders   = ','.join('?' * len(imdb_ids))
        row = self._db.fetch_one(
            'SELECT COUNT(DISTINCT m.imdb_id) FROM mdlist_data m '
            'JOIN badges_data b ON m.imdb_id = b.imdb_id '
            'JOIN trakt_reviews t ON m.imdb_id = t.imdb_id '
            'WHERE m.imdb_id IN (%s) AND m.timestamp > ? AND b.timestamp > ? AND t.timestamp > ?' % placeholders,
            list(imdb_ids) + [min_ratings_ts, min_badges_ts, min_reviews_ts]
        )
        return (row[0] / len(imdb_ids)) if row and row[0] is not None else 0.0


# ============================================================================
# RATINGS FETCHER
# ============================================================================

class RatingsFetcher:
    """Busca e armazena ratings + badges para um item."""

    def __init__(self, progress, limiter, cache_checker):
        self._progress      = progress
        self._limiter       = limiter
        self._cache_checker = cache_checker

    def _build_rating_props(self, data):
        lb_rating = ''
        if data.get('letterboxd_rating'):
            try:
                lb_rating = str(float(data['letterboxd_rating']) * 2)
            except Exception:
                pass

        tr_rating = ''
        if data.get('trakt_rating'):
            try:
                v = float(data['trakt_rating'])
                tr_rating = '%.1f' % (v / 10 if v > 10 else v)
            except Exception:
                pass

        return {
            'ds_info_imdb_rating': data.get('imdb_rating', ''),
            'ds_info_letterboxd_rating': lb_rating,
            'ds_info_trakt_rating': tr_rating,
            'ds_info_imdb_votes': data.get('imdb_votes', ''),
        }, lb_rating, tr_rating

    def _calculate_middle_rating(self, data):
        try:
            ratings = []
            raw_imdb = data.get('imdb_rating', '')
            raw_lb = data.get('letterboxd_rating', '')
            raw_tr = data.get('trakt_rating', '')

            try:
                value = float(raw_imdb or 0)
                if 0 < value < 10:
                    ratings.append(value)
            except Exception:
                pass

            try:
                value = float(raw_lb or 0)
                if 0 < value <= 5:
                    ratings.append(value * 2)
            except Exception:
                pass

            try:
                value = float(raw_tr or 0)
                if 0 < value <= 100:
                    ratings.append(value / 10)
            except Exception:
                pass

            if ratings:
                return '%.1f' % (sum(ratings) / len(ratings))
        except Exception:
            pass
        return ''

    def _save_ui_bundle(self, imdb_id, data, middle_rating, omdb_oscars, formatted_badges, awards_text, certified_fresh_badge, reviews):
        try:
            import ui_bundle
            rating_props, _lb_rating, _tr_rating = self._build_rating_props(data or {})
            bundle_props = dict(rating_props)
            bundle_props.update({
                'middle': middle_rating or '',
                'ds_info_oscars': omdb_oscars or '',
                'ds_info_badges': formatted_badges or '',
                'ds_info_awards': awards_text or '',
                'ds_info_badges_cf': certified_fresh_badge or '',
            })
            if reviews:
                bundle_props['Trakt.Reviews'] = reviews
            ui_bundle.save(imdb_id, {
                'props': bundle_props,
                'ratings_ready': True,
                'highlights_ready': True,
                'reviews_ready': bool(reviews),
            })
        except Exception:
            pass

    def _ensure_ui_bundle_from_cache(self, imdb_id):
        try:
            import mdblist_api
            import trakt_api
            import ui_bundle
            from database import db
            from highlights import _build_awards_payload
        except Exception:
            return False

        if ui_bundle.load(imdb_id):
            return True

        now = time.time()
        ratings_row = db.fetch_one(
            'SELECT data, timestamp FROM mdlist_data WHERE imdb_id = ?',
            (imdb_id,)
        )
        badges_row = db.fetch_one(
            'SELECT oscars, badges, timestamp FROM badges_data WHERE imdb_id = ?',
            (imdb_id,)
        )
        reviews_row = db.fetch_one(
            'SELECT data, timestamp FROM trakt_reviews WHERE imdb_id = ?',
            (imdb_id,)
        )
        middle_row = db.fetch_one(
            'SELECT middle_rating FROM middle_cache WHERE imdb_id = ?',
            (imdb_id,)
        )

        if not ratings_row or not badges_row or not reviews_row or not reviews_row[0]:
            return False
        if now - (ratings_row[1] or 0) >= mdblist_api.FineTuning.CACHE_MAX_AGE:
            return False
        if now - (badges_row[2] or 0) >= FineTuning.BADGES_MAX_AGE:
            return False
        if now - (reviews_row[1] or 0) >= trakt_api.FineTuning.CACHE_MAX_AGE:
            return False

        try:
            ratings_data = json.loads(ratings_row[0] or '{}')
        except Exception:
            return False
        if not isinstance(ratings_data, dict):
            return False
        if ratings_data.get('badges_schema_version') != mdblist_api.FineTuning.BADGES_SCHEMA_VERSION:
            return False

        middle_rating = ''
        if middle_row and middle_row[0]:
            middle_rating = middle_row[0]
        if not middle_rating:
            middle_rating = self._calculate_middle_rating(ratings_data)

        omdb_oscars = badges_row[0] or ''
        raw_badges = ratings_data.get('highlight_badges', '')
        try:
            formatted_badges, awards_text, certified_fresh_badge = _build_awards_payload(omdb_oscars, raw_badges)
        except Exception:
            formatted_badges, awards_text, certified_fresh_badge = '', '', ''

        self._save_ui_bundle(
            imdb_id,
            ratings_data,
            middle_rating,
            omdb_oscars,
            formatted_badges,
            awards_text,
            certified_fresh_badge,
            reviews_row[0] or '',
        )
        return True

    def process(self, item, list_name):
        imdb_id = item.get('imdb_id', '')
        title   = item.get('title', 'Unknown')
        media_type = 'tv' if item.get('mediatype') == 'show' else 'movie'

        if not imdb_id:
            Log.item_error(title, 'sem imdb_id')
            self._progress.add(error=True)
            self._progress.add_list(list_name, error=True)
            return

        if self._cache_checker.is_fully_cached(imdb_id):
            self._ensure_ui_bundle_from_cache(imdb_id)
            #Log.item_skip(title)
            self._progress.add(skipped=True)
            self._progress.add_list(list_name, skipped=True)
            return

        try:
            self._limiter.wait()

            import mdblist_api
            data = mdblist_api.get_ratings(imdb_id) or {}
            self._limiter.ok()

            # Calcula lb_rating e tr_rating (mesma logica do service.py)
            lb_rating = ''
            if data.get('letterboxd_rating'):
                try:
                    lb_rating = str(float(data['letterboxd_rating']) * 2)
                except Exception:
                    pass

            tr_rating = ''
            if data.get('trakt_rating'):
                try:
                    v = float(data['trakt_rating'])
                    tr_rating = '%.1f' % (v / 10 if v > 10 else v)
                except Exception:
                    pass

            from highlights import process_highlights
            middle_rating, omdb_oscars, formatted_badges, awards_text, certified_fresh_badge = process_highlights(
                imdb_id,
                data,
                lb_rating,
                tr_rating
            )

            self._limiter.wait()
            import trakt_api
            reviews = trakt_api.get_reviews_by_imdb_id(imdb_id, media_type)
            self._limiter.ok()

            self._save_ui_bundle(
                imdb_id,
                data,
                middle_rating,
                omdb_oscars,
                formatted_badges,
                awards_text,
                certified_fresh_badge,
                reviews or '',
            )

            #Log.item_fetch(title, imdb_id)
            self._progress.add(fetched=True)
            self._progress.add_list(list_name, fetched=True)

        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ('429', '503', 'too many', 'unavailable')):
                self._limiter.limit_hit()
            Log.item_error(title, str(e))
            self._progress.add(error=True)
            self._progress.add_list(list_name, error=True)


# ============================================================================
# WORKER THREAD
# ============================================================================

class WorkerThread(Thread):
    def __init__(self, queue, fetcher, stop_event):
        super().__init__(daemon=True)
        self.queue   = queue
        self.fetcher = fetcher
        self.stop    = stop_event

    def run(self):
        while not self.stop.is_set():
            try:
                task = self.queue.get(timeout=FineTuning.QUEUE_TIMEOUT)
            except Empty:
                continue
            except Exception:
                continue
            try:
                if task is None:
                    return
                if not wait_for_progress_media_release(self.stop):
                    return
                item, list_name = task
                self.fetcher.process(item, list_name)
            except Exception as e:
                Log.info('Worker erro inesperado: %s' % e)
            finally:
                self.queue.task_done()


# ============================================================================
# PREFETCH SERVICE
# ============================================================================

class PrefetchService:
    def __init__(self):
        self._mdblist  = MDBListClient()
        self._progress = Progress()
        self._limiter  = RateLimiter(FineTuning.API_DELAY)
        self._checker  = CacheChecker()
        self._stop     = Event()
        self._state_file = self._get_state_path()

    def _get_state_path(self):
        return _prefetch_state_path()

    def _load_state(self):
        return _read_prefetch_state().get('last_run', 0)

    def _save_state(self):
        try:
            with open(self._state_file, 'w') as f:
                json.dump({'last_run': time.time(), 'cache_warm': bool(cache_warm)}, f)
        except Exception:
            pass

    def should_run(self):
        return time.time() - self._load_state() >= FineTuning.CHECK_INTERVAL_SECONDS

    def run(self, is_playing=False):
        Log.header('INICIANDO RATINGS PREFETCH')

        if not wait_for_progress_media_release(self._stop):
            return

        # 1. Buscar listas
        lists = self._mdblist.get_lists()
        if not lists:
            Log.info('Nenhuma lista encontrada')
            self._save_state()
            return

        user_count = sum(1 for l in lists if l.get('list_type') == 'user')
        ext_count  = len(lists) - user_count
        Log.separator()
        Log.info('Listas encontradas: %d (%d pessoais, %d externas)' % (len(lists), user_count, ext_count))
        Log.separator()

        # 2. Coletar itens unicos
        all_items  = {}   # imdb_id -> item
        list_entries = []
        collect_stats = {'raw': 0, 'supported': 0, 'unique': 0, 'duplicates': 0, 'missing_id': 0, 'unsupported': 0}

        for idx, lst in enumerate(lists):
            if not wait_for_progress_media_release(self._stop):
                return
            name      = lst.get('name', 'Unknown')
            list_id   = lst.get('id', '')
            list_type = lst.get('list_type', '')
            items = self._mdblist.get_items(list_id, list_type)

            if not items:
                continue

            entry = {
                'name': name, 'keys': [],
                'raw': 0, 'unique_added': 0, 'duplicates': 0, 'missing_id': 0, 'unsupported': 0,
                'movies_unique': 0, 'shows_unique': 0,
            }

            for item in items:
                entry['raw'] += 1
                collect_stats['raw'] += 1

                mtype = item.get('mediatype')
                if mtype not in ('movie', 'show'):
                    entry['unsupported'] += 1
                    collect_stats['unsupported'] += 1
                    continue

                imdb_id = item.get('imdb_id', '')
                if not imdb_id:
                    entry['missing_id'] += 1
                    collect_stats['missing_id'] += 1
                    continue

                collect_stats['supported'] += 1

                if imdb_id in all_items:
                    entry['duplicates'] += 1
                    collect_stats['duplicates'] += 1
                    continue

                item['list'] = name
                all_items[imdb_id] = item
                entry['keys'].append(imdb_id)
                entry['unique_added'] += 1
                collect_stats['unique'] += 1
                if mtype == 'movie':
                    entry['movies_unique'] += 1
                else:
                    entry['shows_unique'] += 1

            list_entries.append(entry)
            Log.info('%s: %d itens (%d filmes, %d series)' % (name, entry['raw'], entry['movies_unique'], entry['shows_unique']))
            if entry['duplicates'] or entry['missing_id'] or entry['unsupported']:
                Log.info('   unicos: %d, duplicados: %d, sem-id: %d, tipo-nao-suportado: %d'
                         % (entry['unique_added'], entry['duplicates'], entry['missing_id'], entry['unsupported']))
            time.sleep(FineTuning.API_DELAY)

        Log.separator()
        Log.info('Total unico: %d itens' % len(all_items))
        Log.info('Coleta: bruto=%d, suportados=%d, unicos=%d, duplicados=%d, sem-id=%d, tipo-ns=%d'
                 % (collect_stats['raw'], collect_stats['supported'], collect_stats['unique'],
                    collect_stats['duplicates'], collect_stats['missing_id'], collect_stats['unsupported']))
        Log.separator()

        if not all_items:
            Log.info('Nenhum item valido nas listas')
            self._save_state()
            return

        # 3. Sync check (pre-verifica cache)
        all_imdb_ids = list(all_items.keys())
        cache_hit_rate = self._checker.calculate_hit_rate(all_imdb_ids)
        cached_count   = int(cache_hit_rate * len(all_imdb_ids))
        Log.separator()
        Log.info('SYNC CHECK: %d/%d itens com cache completo (%.1f%%)'
                 % (cached_count, len(all_imdb_ids), cache_hit_rate * 100))
        Log.separator()

        # 4. Modo adaptativo
        import ratings_prefetch as _mod
        cache_is_warm = cache_hit_rate >= FineTuning.CACHE_HIT_THRESHOLD
        is_economy = (not is_playing) and cache_is_warm
        avail_mb = _available_memory_mb()
        low_memory = avail_mb is not None and avail_mb < FineTuning.LOW_MEMORY_LIGHT_MB
        if low_memory:
            Log.info('RAM baixa (%dMB disponivel) — forcando modo LIGHT' % avail_mb)
        if is_playing or low_memory or cache_hit_rate >= FineTuning.CACHE_HIT_THRESHOLD:
            workers = FineTuning.NUM_WORKERS_LIGHT
        else:
            workers = FineTuning.NUM_WORKERS_FULL
        # Cache quente e modo de execucao sao estados diferentes: reproduzir um
        # video reduz workers, mas nao torna frio um cache ja preenchido.
        _mod.cache_warm = cache_is_warm
        batch_delay = FineTuning.BATCH_DELAY_PLAYING if is_playing else (FineTuning.BATCH_DELAY_ECONOMY if is_economy else FineTuning.BATCH_DELAY)
        Log.adaptive_mode(cache_hit_rate, workers, is_playing)

        # 5. Iniciar workers
        self._progress.start(len(all_imdb_ids))
        self._limiter.set_mode(is_playing, is_economy)

        fetcher    = RatingsFetcher(self._progress, self._limiter, self._checker)
        stop_event = Event()
        work_queue = Queue()
        worker_threads = [WorkerThread(work_queue, fetcher, stop_event) for _ in range(workers)]
        for w in worker_threads:
            w.start()

        # 6. Processar por lista
        processed_ids = set()
        abort_requested = False

        for entry in list_entries:
            if not wait_for_progress_media_release(self._stop):
                abort_requested = True
                break
            name = entry['name']
            keys = [k for k in entry['keys'] if k not in processed_ids]
            if not keys:
                continue

            Log.list_start(name, len(keys))
            self._progress.set_list(name)

            for key in keys:
                if self._stop.is_set() or _monitor.abortRequested():
                    abort_requested = True
                    break
                if not wait_for_progress_media_release(self._stop):
                    abort_requested = True
                    break
                item = all_items.get(key)
                if item:
                    work_queue.put((item, name))
                    processed_ids.add(key)

            if abort_requested:
                break

            work_queue.join()

            s = self._progress.get_list(name)
            Log.list_complete(name, s['fetched'], s['skipped'], s['errors'])

            if abort_requested or self._stop.is_set() or _monitor.abortRequested():
                break

            time.sleep(batch_delay)

        # 7. Encerrar workers
        stop_event.set()
        for _ in worker_threads:
            work_queue.put(None)
        for w in worker_threads:
            w.join(timeout=5)

        # 8. Resumo final
        stats = self._progress.get()
        Log.summary(stats)
        self._save_state()

    def stop(self):
        self._stop.set()


# ============================================================================
# MONITOR LOOP + ENTRY POINT
# ============================================================================

def _monitor_loop():
    monitor = _monitor
    Log.info('Monitor iniciado (intervalo: %dh, boot delay: %ds)'
             % (FineTuning.CHECK_INTERVAL_HOURS, FineTuning.BOOT_DELAY))

    # Aguardar boot do Kodi (batches de 30s em vez de 1s × 240)
    remaining = FineTuning.BOOT_DELAY
    while remaining > 0:
        chunk = min(30, remaining)
        if monitor.waitForAbort(chunk):
            Log.info('Monitor encerrado (abort no boot)')
            return
        remaining -= chunk

    service = PrefetchService()

    while not monitor.abortRequested():
        is_playing = _player.isPlayingVideo()

        try:
            avail_mb = _available_memory_mb()
            if avail_mb is not None and avail_mb < FineTuning.LOW_MEMORY_SKIP_MB:
                Log.info('RAM critica (%dMB disponivel) — ciclo adiado' % avail_mb)
            elif service.should_run():
                Log.info('Intervalo de %dh atingido — executando...' % FineTuning.CHECK_INTERVAL_HOURS)
                service.run(is_playing=is_playing)
            else:
                remaining = FineTuning.CHECK_INTERVAL_SECONDS - (time.time() - service._load_state())
                Log.info('Boot quente: prefetch ignorado. Proxima execucao em %.0fmin' % (remaining / 60))
        except Exception as e:
            Log.info('Erro no monitor: %s' % e)

        # Aguardar 30min entre checagens (3 × 600s em vez de 180 × 10s)
        for _ in range(3):
            if monitor.waitForAbort(600):
                break

    service.stop()
    Log.info('Monitor finalizado')


def start():
    """Inicia o prefetch de ratings como daemon thread."""
    restored_warm = _restore_cache_warm_state()
    Log.info('Estado cache_warm restaurado: %s' % restored_warm)
    t = threading.Thread(target=_monitor_loop, daemon=True)
    t.start()
    Log.info('Thread de prefetch iniciada')

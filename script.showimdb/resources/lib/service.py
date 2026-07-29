# -*- coding: utf-8 -*-
import html
import xbmc
import xbmcgui
import xbmcvfs
import xbmcaddon
import time
import threading
import re
import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date


# Aparelhos fracos: com _DEBUG=False os logs [ShowIMDB][DEBUG] dos hot paths nem
# são montados (a f-string deixa de ser construída a cada troca de foco/trailer).
# Ligue para True para diagnosticar — aí o filtro abaixo também deixa de suprimi-los.
_DEBUG = False


def _install_showimdb_debug_log_filter():
    """Suprime apenas logs de depuracao do ShowIMDB para reduzir spam no kodi.log."""
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

tmdb_api = None
imdb_trailer_api = None
youtube_stream_resolver = None
ratings_prefetch = None

class FineTuning:
    """Parâmetros de concorrência, tempos e debounce do serviço."""
    PROGRESS_MEDIA_CONDITION = "Window.IsVisible(progress_media.xml) | Window.IsActive(progress_media.xml)"
    SPLASH_VIDEO_WIDGET_READY_COUNT = 8  # quantidade de widgets com conteúdo real antes de disparar o splash.mp4
    SPLASH_VIDEO_WIDGET_GATE_TIMEOUT = 45.0  # teto de espera pelos widgets reais antes de liberar o splash mesmo incompleto.
    SPLASH_BOOT_COVER_WIDGET_READY_COUNT = 7  # SEM video: quantos widgets reais segurar antes de liberar o boot cover pro fade-out.

    # 1 to 1 = sweet spot after many tests
    TRAILER_WORKERS = 1  # workers do pipeline de resolução de trailer.
    TRAILER_QUEUE = 1  # fila máxima
    PLAYBACK_WORKERS = 1  # workers de reprodução.
    PLAYBACK_QUEUE = 1  
    SNIPER_WORKERS = 1  # workers de validação pós-play.
    SNIPER_QUEUE = 1  # sniper na fila

    DB_FAST_THRESHOLD = 0.6  # limiar para rolagem rápida.
    DB_MED_THRESHOLD = 1.2  # limiar para rolagem média.
    TRAILER_DELAY_FAST = 8.0  # atraso em rolagem rápida.
    TRAILER_DELAY_MED = 6.0  # atraso em rolagem média.
    PRE_FETCH_LEAD = 2.0  # segundos antes do play do trailer.

    FOCUS_DEBOUNCE = 0.30  # debounce de foco antes de iniciar trailer pipeline.

    LOOP_NORMAL = 0.60  # ciclo padrão do loop principal.
    LOOP_IDLE = 1.20  # ciclo ocioso.
    LOOP_FULLSCREEN = 5.00  # ciclo ultra blaster econômico durante fullscreen de filme longo.
    LOOP_FULLSCREEN_PAUSED = 2.50  # filme pausado fullscreen: relaxa o polling do player (evita martelar a API por horas).
    EMPTY_ID_GRACE = 0.35  # tolera blips de id-vazio do container 54 antes de tratar como "saiu do item".
    VIDEO_NAV_REPUBLISH = 5.0  # rede de segurança: republica as props ds_info_* mesmo sem mudança de assinatura (container recarregado in-place).
    LOOP_SNIPER = 0.25  # ciclo do monitor de trailer.
    SNIPER_TIMEOUT = 10.00  # timeout máximo para estabilizar trailer.
    META_UPDATE_INTERVAL = 3.50  # frequência de atualização de labels de UI.
    PAUSE_POLL_INTERVAL = 2.00  # polling leve durante transicoes de reproducao (evita que o trailer se sobreponha ao filme).
    PAUSE_PLAYBACK_GRACE = 6.00  # espera curta após o progress_media se o vídeo já estiver tocando.

    IMDB_QUALITY_1080 = (1080, 720)  # modo padrão: tenta 1080 e cai para 720.
    IMDB_QUALITY_720 = (720,)  # modo econômico: usa apenas 720.


class TrailerState:
    IDLE = 0
    PLAYING_AUTO = 1


def load_runtime_modules():
    """Importa modulos pesados apenas depois do bootstrap inicial do service."""
    global tmdb_api, imdb_trailer_api, youtube_stream_resolver, ratings_prefetch

    if all((tmdb_api, imdb_trailer_api, youtube_stream_resolver, ratings_prefetch)):
        return

    tmdb_api = importlib.import_module("tmdb_api")
    imdb_trailer_api = importlib.import_module("imdb_trailer_api")
    youtube_stream_resolver = importlib.import_module("youtube_stream_resolver")
    ratings_prefetch = importlib.import_module("ratings_prefetch")


BOOT_COVER_DONE_PROPERTY = "showimdb_boot_cover_done"
HOME_SPLASH_ACTIVE_PROPERTY = "showimdb_home_splash_active"
SPLASH_VIDEO_COVER_DONE_PROPERTY = "showimdb_splash_video_cover_done"


def get_widget_boot_debug_source_path():
    widget_boot_debug_include = "1080i/script-skinshortcuts-includes.xml"
    return f"special://home/addons/{xbmc.getSkinDir()}/{widget_boot_debug_include}"


def discover_widget_boot_targets(max_targets=None):
    source_path = get_widget_boot_debug_source_path()
    translated_path = xbmcvfs.translatePath(source_path)
    if not xbmcvfs.exists(source_path):
        return []

    try:
        with open(translated_path, "r", encoding="utf-8") as handle:
            contents = handle.read()
    except Exception as exc:
        return []

    targets = []
    seen = set()

    # Descobre categoria por seção de grouplist no template do skinshortcuts.
    section_pattern = re.compile(r'<control type="grouplist" id="\d+">', re.S)
    section_starts = [m.start() for m in section_pattern.finditer(contents)]
    section_starts.append(len(contents))

    for idx in range(len(section_starts) - 1):
        section = contents[section_starts[idx] : section_starts[idx + 1]]
        category_match = re.search(
            r'String\.IsEqual\(Container\(9000\)\.ListItem\.Property\(submenuVisibility\),([^)]+)\)',
            section,
            re.S,
        )
        category = (category_match.group(1).strip() if category_match else "desconhecida")

        widget_blocks = re.findall(r'<include content="ds_widgetPanelList">(.*?)</include>', section, re.S)
        if not widget_blocks:
            widget_blocks = [section]

        for block in widget_blocks:
            list_match = re.search(r'<param name="list_id" value="(\d+)"\s*/>', block, re.S)
            header_match = re.search(r'<param name="widget_header" value="([^"]*)"\s*/>', block, re.S)
            path_match = re.search(r'<param name="(?:content_path|widgetPath)" value="([^"]*)"\s*/>', block, re.S)
            if not list_match or not header_match:
                continue

            container_id = list_match.group(1)
            header = header_match.group(1)
            if container_id in seen:
                continue
            seen.add(container_id)
            targets.append(
                {
                    "container_id": container_id,
                    "header": header.strip() or "<sem_header>",
                    "category": category,
                    "content_path": html.unescape(path_match.group(1)).strip() if path_match else "",
                }
            )
            if max_targets is not None and len(targets) >= max_targets:
                return targets

    return targets


def get_container_numitems(container_id):
    label = xbmc.getInfoLabel(f"Container({container_id}).NumItems") or ""
    try:
        return int(label)
    except (TypeError, ValueError):
        return 0


def get_container_first_label(container_id):
    label = xbmc.getInfoLabel(f"Container({container_id}).ListItemAbsolute(0).Label") or ""
    label = " ".join(label.split())
    if len(label) > 60:
        label = label[:57] + "..."
    return label


def has_real_widget_content(container_id):
    numitems = get_container_numitems(container_id)
    first_label = get_container_first_label(container_id)
    is_real = numitems > 1 or bool(first_label)
    return is_real, numitems, first_label


def get_splash_video_widget_ready_count(max_count=None):
    default_count = max(1, int(FineTuning.SPLASH_VIDEO_WIDGET_READY_COUNT))
    try:
        value = xbmc.getInfoLabel("Skin.String(dstv_splash_video_widget_ready_count)")
        if value and value.strip():
            default_count = int(float(str(value).strip().replace(",", ".")))
    except Exception:
        pass

    ready_count = max(1, default_count)
    if max_count is not None:
        ready_count = min(max_count, ready_count)
    return ready_count


def get_splash_video_widget_gate_timeout():
    default_timeout = max(1.0, float(FineTuning.SPLASH_VIDEO_WIDGET_GATE_TIMEOUT))
    try:
        value = xbmc.getInfoLabel("Skin.String(dstv_splash_video_widget_gate_timeout)")
        if value and value.strip():
            default_timeout = float(str(value).strip().replace(",", "."))
    except Exception:
        pass
    return max(1.0, default_timeout)


def wait_for_splash_video_gate(ready_count=None, targets=None):
    splash_video_poll = 0.10
    splash_video_post_widget_buffer = 0.0
    splash_video_widget_gate_timeout = get_splash_video_widget_gate_timeout()

    monitor = xbmc.Monitor()
    # Conta widgets reais de todas as categorias carregadas naturalmente pelo skin.
    # Reusa os targets já descobertos no boot (evita reler+regex o XML de includes).
    if targets is None:
        targets = discover_widget_boot_targets()
    gate_started_at = time.time()
    deadline = gate_started_at + splash_video_widget_gate_timeout
    if ready_count is None:
        ready_count_required = get_splash_video_widget_ready_count(len(targets) or None)
    else:
        ready_count_required = max(1, int(ready_count))
        if targets:
            ready_count_required = min(ready_count_required, len(targets))
    detected_widgets = []
    detected_by_container = {}
    gate_widget = None

    if not targets:
        if monitor.waitForAbort(splash_video_widget_gate_timeout):
            return False, "abort", None
        return True, "timeout_no_targets", None

    while not monitor.abortRequested():
        now = time.time()
        elapsed = now - gate_started_at

        if gate_widget is None:
            for target in targets:
                container_id = target.get("container_id")
                header = target.get("header") or "<sem_header>"
                category = target.get("category") or "desconhecida"
                is_real, numitems, first_label = has_real_widget_content(container_id)
                if not is_real:
                    continue
                if container_id in detected_by_container:
                    continue

                widget = {
                    "container_id": container_id,
                    "header": header,
                    "category": category,
                    "numitems": numitems,
                    "first_label": first_label,
                    "elapsed": elapsed,
                }
                detected_by_container[container_id] = widget
                detected_widgets.append(widget)
                if len(detected_widgets) >= ready_count_required:
                    gate_widget = widget
                    break

        if gate_widget is not None and elapsed >= (gate_widget["elapsed"] + splash_video_post_widget_buffer):
            return True, "widget_ready", gate_widget

        if now >= deadline:
            detected = gate_widget or (detected_widgets[0] if detected_widgets else None)
            return True, "timeout_with_widget" if detected is not None else "timeout_no_widget", detected

        monitor.waitForAbort(splash_video_poll)

    detected = gate_widget or (detected_widgets[0] if detected_widgets else None)
    return False, "abort", detected


def log_first_widget_ready(detected_widget=None):
    if detected_widget:
        xbmc.log(
            "[ShowIMDB][BootGate] Primeiro widget pronto: categoria=%s container=%s header=%s items=%s"
            % (
                detected_widget.get("category", "desconhecida"),
                detected_widget.get("container_id", "?"),
                detected_widget.get("header", "<sem_header>"),
                detected_widget.get("numitems", 0),
            ),
            xbmc.LOGINFO,
        )


def can_play_splash_video():
    """Valida se o overlay de splash em video pode ser usado neste boot."""
    splash_video_skin = "skin.JediForce"
    splash_video_skin_setting = "dstv_enable_splash_mp4"
    splash_video_path = "special://home/addons/script.showimdb/resources/media/splash.mp4"

    skin_dir = xbmc.getSkinDir()
    splash_setting_enabled = xbmc.getCondVisibility(
        f"Skin.HasSetting({splash_video_skin_setting})"
    )
    video_exists = xbmcvfs.exists(splash_video_path)

    if skin_dir != splash_video_skin:
        xbmc.log(f"[ShowIMDB][Splash] Skin incompativel para splash: {skin_dir}", xbmc.LOGINFO)
        return False

    if not splash_setting_enabled:
        xbmc.log("[ShowIMDB][Splash] Splash MP4 desativado nas configuracoes da skin", xbmc.LOGINFO)
        return False

    if not video_exists:
        xbmc.log(f"[ShowIMDB][Splash] Arquivo de video nao encontrado: {splash_video_path}", xbmc.LOGWARNING)
        return False

    return True


def play_splash_video(result_holder):
    """Ativa o overlay de splash na Home e toca splash.mp4 durante o boot."""
    splash_video_path = "special://home/addons/script.showimdb/resources/media/splash.mp4"
    splash_video_confirm_timeout = 15.0
    splash_video_pre_play_delay = 0.15
    splash_video_min_playback_time = 1.0
    splash_video_poll = 0.10

    monitor = xbmc.Monitor()
    player = xbmc.Player()
    skin_window = xbmcgui.Window(10000)
    result_holder["started"] = False
    result_holder["confirmed"] = False
    result_holder["boot_cover_released"] = False
    result_holder["inner_cover_released"] = False
    result_holder["fallback"] = False
    result_holder["finished"] = False

    try:
        skin_window.clearProperty(HOME_SPLASH_ACTIVE_PROPERTY)
        skin_window.clearProperty(SPLASH_VIDEO_COVER_DONE_PROPERTY)
        if not can_play_splash_video():
            xbmc.log("[ShowIMDB][Splash] Splash.mp4 indisponivel; liberando covers sem reproduzir", xbmc.LOGWARNING)
            skin_window.setProperty(BOOT_COVER_DONE_PROPERTY, "true")
            result_holder["boot_cover_released"] = True
            return

        skin_window.setProperty(HOME_SPLASH_ACTIVE_PROPERTY, "true")
        xbmc.sleep(max(0, int(splash_video_pre_play_delay * 1000)))

        xbmc.log("[ShowIMDB][Splash] player.play() chamado para splash.mp4", xbmc.LOGINFO)
        player.play(splash_video_path, windowed=True)

        start_attempt = time.time()
        deadline = time.time() + splash_video_confirm_timeout
        while not monitor.abortRequested() and time.time() < deadline:
            if player.isPlayingVideo():
                if not result_holder["started"]:
                    result_holder["started"] = True
                    xbmc.log("[ShowIMDB][Splash] Player.HasVideo detectado; aguardando 1s real de playback", xbmc.LOGINFO)
                current_time = 0.0
                try:
                    current_time = max(0.0, float(player.getTime()))
                except Exception:
                    current_time = 0.0

                if current_time >= splash_video_min_playback_time:
                    elapsed = time.time() - start_attempt
                    result_holder["confirmed"] = True
                    skin_window.setProperty(BOOT_COVER_DONE_PROPERTY, "true")
                    result_holder["boot_cover_released"] = True
                    xbmc.log(
                        "[ShowIMDB][Splash] Playback confirmado em %.2fs; liberando boot cover"
                        % elapsed,
                        xbmc.LOGINFO,
                    )
                    skin_window.setProperty(SPLASH_VIDEO_COVER_DONE_PROPERTY, "true")
                    result_holder["inner_cover_released"] = True
                    xbmc.log(
                        "[ShowIMDB][Splash] Playback confirmou 1.0s; liberando cover interno",
                        xbmc.LOGINFO,
                    )
                    break
            monitor.waitForAbort(splash_video_poll)

        if not result_holder["confirmed"]:
            result_holder["fallback"] = True
            xbmc.log(
                "[ShowIMDB][Splash] Fallback apos %.1fs sem confirmar 1s de playback; seguindo boot"
                % splash_video_confirm_timeout,
                xbmc.LOGWARNING,
            )
            skin_window.setProperty(BOOT_COVER_DONE_PROPERTY, "true")
            result_holder["boot_cover_released"] = True
            if player.isPlayingVideo():
                player.stop()
            return

        while not monitor.abortRequested() and player.isPlayingVideo():
            monitor.waitForAbort(splash_video_poll)

        result_holder["finished"] = True
    except Exception as exc:
        xbmc.log(f"[ShowIMDB][Splash] Falha ao reproduzir splash.mp4: {exc}", xbmc.LOGERROR)
    finally:
        if not result_holder.get("boot_cover_released"):
            skin_window.setProperty(BOOT_COVER_DONE_PROPERTY, "true")
        try:
            if player.isPlayingVideo():
                player.stop()
        except Exception:
            pass
        xbmc.sleep(100)
        skin_window.clearProperty(HOME_SPLASH_ACTIVE_PROPERTY)


class BoundedThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, max_workers=2, max_queue_size=10, thread_name_prefix=""):
        super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._semaphore = threading.Semaphore(max_queue_size)

    def submit(self, fn, *args, **kwargs):
        acquired = self._semaphore.acquire(blocking=False)
        if not acquired: 
            return None
        try:
            future = super().submit(fn, *args, **kwargs)
            def _release_semaphore(f):
                self._semaphore.release()
            future.add_done_callback(_release_semaphore)
            return future
        except: 
            self._semaphore.release()
            return None


class ShowImdbService(xbmc.Monitor):

    def __init__(self):
        super().__init__()
        
        self.addon = xbmcaddon.Addon()
        self.prev_tmdb_id = None
        self._focus_session_counter = 0
        self._session_lock = threading.Lock()
        self._player_action_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._sniper_lock = threading.Lock()
        self._global_lock = threading.RLock()
        self._cache_lock = threading.Lock()
        
        self.focused_item_id = None
        self.focused_session_id = 0
        self.last_focus_time = 0
        self.last_delta_t = 0
        self.cleaning_needed = True
        
        self.trailer_state = TrailerState.IDLE
        self.trailer_ativo = False
        
        self.trailer_executor = BoundedThreadPoolExecutor(
            max_workers=FineTuning.TRAILER_WORKERS, 
            max_queue_size=FineTuning.TRAILER_QUEUE, 
            thread_name_prefix="trailer_"
        )
        self.playback_executor = BoundedThreadPoolExecutor(
            max_workers=FineTuning.PLAYBACK_WORKERS, 
            max_queue_size=FineTuning.PLAYBACK_QUEUE, 
            thread_name_prefix="playback_"
        )
        self.sniper_executor = BoundedThreadPoolExecutor(
            max_workers=FineTuning.SNIPER_WORKERS, 
            max_queue_size=FineTuning.SNIPER_QUEUE, 
            thread_name_prefix="sniper_"
        )
        
        self.fetch_task = None
        self.play_task = None
        self.sniper_task = None
        
        self.player = xbmc.Player()
        self.focus_start_time = 0
        self.trailer_url_ready = None
        self.trailer_played_for_session = False
        self.is_fetching_trailer = False
        self.skin_win = xbmcgui.Window(10000)
        self._skin_cache = {}
        self.cached_dbtype = None
        self.last_dbtype_check = 0
        self.last_state_check = 0
        self._meta_interval = FineTuning.META_UPDATE_INTERVAL
        self._sniper_active_flag = False
        self.is_loading_trailer = False
        self._pause_mode_active = False
        self._pause_resume_player_time = None
        self.trailer_lockout_time = 0
        self._last_infowall_active = False
        self._empty_id_since = None  # marca quando o id ficou vazio (grace contra blips do container 54)
        self._menu_warmed_id = None  # item já aquecido enquanto o context menu está aberto (one-shot)
        self._last_published_sig = None  # assinatura (ids+label+season+episode) do último publish no video-nav
        self._last_publish_ts = 0.0

        # Pre-fetch ratings/badges para todas as listas MDBList
        ratings_prefetch.start()

    def _update_skin_props_batch(self, props_dict):
        with self._cache_lock:
            for name, value in props_dict.items():
                val_str = str(value) if value is not None else ""
                if self._skin_cache.get(name) == val_str: 
                    continue
                self._skin_cache[name] = val_str
                if val_str: 
                    self.skin_win.setProperty(name, val_str)
                else:
                    self.skin_win.clearProperty(name)

    def _generate_session_id(self):
        with self._session_lock:
            self._focus_session_counter += 1
            if self.fetch_task:  
                self.fetch_task.cancel()
                self.fetch_task = None
            if self.play_task: 
                self.play_task.cancel()
                self.play_task = None
            if self.sniper_task: 
                self.sniper_task.cancel()
                self.sniper_task = None
            return self._focus_session_counter

    def _is_session_valid(self, session_id, item_id):
        with self._state_lock:
            return (self.focused_session_id == session_id and self.focused_item_id == item_id)

    def _is_progress_media_active(self):
        try:
            return xbmc.getCondVisibility(FineTuning.PROGRESS_MEDIA_CONDITION)
        except:
            return False

    def _set_pause_mode(self, active):
        if active == self._pause_mode_active:
            return

        self._pause_mode_active = active

        if active:
            new_session = self._generate_session_id()
            self._stop_current_sniper()
            self.trailer_url_ready = None
            self.trailer_played_for_session = False
            self.is_fetching_trailer = False
            self.is_loading_trailer = False
            self.trailer_ativo = False
            with self._global_lock:
                self.trailer_state = TrailerState.IDLE
            self._update_skin_props_batch({"ds_is_trailer_playing": ""})
            with self._state_lock:
                self.focused_item_id = None
                self.focused_session_id = new_session
                self.prev_tmdb_id = None
            self._pause_resume_player_time = None
            xbmc.log("[ShowIMDB] Pause mode enabled (progress_media.xml ativo)", xbmc.LOGINFO)
        else:
            self.focus_start_time = time.time()
            playback_time = self._get_current_playback_time()
            if playback_time is not None:
                self._pause_resume_player_time = playback_time
                self.trailer_lockout_time = max(self.trailer_lockout_time, time.time() + FineTuning.PAUSE_PLAYBACK_GRACE)
                xbmc.log(
                    "[ShowIMDB] Pause mode disabled; retomando após %.0fs de reprodução"
                    % FineTuning.PAUSE_PLAYBACK_GRACE,
                    xbmc.LOGINFO,
                )
            else:
                self._pause_resume_player_time = None
                xbmc.log("[ShowIMDB] Pause mode disabled (progress_media.xml liberado)", xbmc.LOGINFO)

    def _get_current_playback_time(self):
        try:
            if not self.player.isPlayingVideo() or self._is_trailer_playing():
                return None
            return float(self.player.getTime())
        except:
            return None

    def _is_pause_resume_pending(self):
        if self._pause_resume_player_time is None:
            return False
        current_time = self._get_current_playback_time()
        if current_time is None:
            self._pause_resume_player_time = None
            return False
        if current_time < self._pause_resume_player_time:
            self._pause_resume_player_time = current_time
            return True
        if current_time - self._pause_resume_player_time >= FineTuning.PAUSE_PLAYBACK_GRACE:
            self._pause_resume_player_time = None
            return False
        return True

    def _reset_trailer_state(self):
        self.trailer_url_ready = None
        self.trailer_played_for_session = False
        self.is_fetching_trailer = False
        self.focus_start_time = time.time()
        with self._global_lock:
            self.trailer_state = TrailerState.IDLE

    def submit_safe(self, executor, fn, *args, **kwargs):
        if self._pause_mode_active:
            return None
        fut = executor.submit(fn, *args, **kwargs)
        if fut is None:
            return None
        def _cb(f):
            try: f.result()
            except: pass
        fut.add_done_callback(_cb)
        return fut

    def _get_settings(self):
        return xbmc.getCondVisibility("Skin.HasSetting(dstv_enable_auto_trailer)")

    def _is_auto_trailer_enabled(self):
        # Skin setting muda só via menu de configuração; TTL de 5s evita refazer
        # o getCondVisibility a cada tick (é lido 2-3x por ciclo com trailer ativo).
        now = time.time()
        cached = getattr(self, "_auto_trailer_cache", None)
        if cached is not None and (now - cached[1]) < 5.0:
            return cached[0]
        value = self._get_settings()
        self._auto_trailer_cache = (value, now)
        return value

    def _resolve_imdb_trailer_url(self, item_id, media_type):
        try:
            return imdb_trailer_api.fetch_trailer_url(
                item_id,
                media_type,
                quality_priority=self._get_imdb_quality_priority(),
            )
        except:
            pass
        return ""

    def _get_imdb_quality_priority(self):
        if xbmc.getCondVisibility("Skin.HasSetting(dstv_imdb_trailer_720p)"):
            return FineTuning.IMDB_QUALITY_720
        return FineTuning.IMDB_QUALITY_1080

    def _build_resolver_query(self):
        title = (
            xbmc.getInfoLabel("ListItem.OriginalTitle")
            or xbmc.getInfoLabel("ListItem.Title")
            or xbmc.getInfoLabel("ListItem.Label")
            or ""
        ).strip()
        if not title:
            return ""
        year = (xbmc.getInfoLabel("ListItem.Year") or "").strip()
        if year.isdigit():
            return "%s official trailer (%s)" % (title, year)
        return "%s official trailer" % title

    def _resolve_resolver_trailer_url(self):
        query = self._build_resolver_query()
        if not query:
            return ""
        try:
            url, _info = youtube_stream_resolver.resolve_from_search_query(query, timeout=5)
            return url or ""
        except:
            pass
        return ""

    def _get_media_type(self, video_nav_active=None):
        current_time = time.time()
        if video_nav_active is None:
            video_nav_active = self._is_video_nav_active()
        if video_nav_active:
            try:
                current_dbtype = (
                    xbmc.getInfoLabel("ListItem.DBType")
                    or xbmc.getInfoLabel("Container(54).ListItem.DBType")
                )
                if current_dbtype:
                    self.cached_dbtype = current_dbtype
                    self.last_dbtype_check = current_time
            except:
                pass
        elif current_time - self.last_dbtype_check > self._meta_interval:
            try:
                self.cached_dbtype = xbmc.getInfoLabel("Window(10000).Property(ds_info_dbtype)")
            except: self.cached_dbtype = None
            self.last_dbtype_check = current_time
        return "tv" if self.cached_dbtype and self.cached_dbtype.lower() in ("tv", "tvshow", "episode", "season") else "movie"

    def _info_label(self, label):
        try:
            return (xbmc.getInfoLabel(label) or "").strip()
        except:
            return ""

    def _is_video_nav_active(self):
        try:
            return xbmc.getCondVisibility("Window.IsActive(videos) | Window.IsVisible(10025)")
        except:
            return False

    def _is_infowall_active(self):
        try:
            return xbmc.getCondVisibility("[Window.IsActive(videos) | Window.IsVisible(videos) | Window.IsVisible(10025)] + Control.IsVisible(54)")
        except:
            return False

    def _is_home_active(self):
        try:
            return xbmc.getCondVisibility("Window.IsActive(home)")
        except:
            return False

    def _is_busy_dialog_active(self):
        try:
            return xbmc.getCondVisibility("Window.IsActive(busydialog) | Window.IsActive(busydialognocancel) | Window.IsActive(DialogBusy.xml) | Window.IsActive(10101) | Window.IsActive(10138)")
        except:
            return False

    def _sync_infowall_active_property(self, infowall_active):
        if infowall_active:
            if not self._last_infowall_active:
                self._update_skin_props_batch({"ds_infowall_active": "true"})
                self._last_infowall_active = True
            return
        if self._last_infowall_active and not self._is_busy_dialog_active():
            self._update_skin_props_batch({"ds_infowall_active": ""})
            self._last_infowall_active = False

    def _resolve_focus_ids(self, prefer_listitem=False, prop_tmdb_id=None):
        """Resolve apenas os ids de foco (item_id/tmdb/imdb/dbtype) lendo o mínimo de labels.

        Mesma ordem de fallback de _get_current_focus_info; quando prefer_listitem é
        True as props da Window não são necessárias e não são lidas. prop_tmdb_id pode
        ser passado já lido (evita reler ds_info_tmdb_id)."""
        dbtype = self._info_label("ListItem.DBType") or self._info_label("Container(54).ListItem.DBType")
        tmdb_id = self._info_label("ListItem.UniqueID(tmdb)") or self._info_label("Container(54).ListItem.UniqueID(tmdb)")
        tvshow_tmdb_id = (
            self._info_label("ListItem.UniqueID(tvshow.tmdb)")
            or self._info_label("Container(54).ListItem.UniqueID(tvshow.tmdb)")
        )
        imdb_id = self._info_label("ListItem.IMDBNumber") or self._info_label("Container(54).ListItem.IMDBNumber")

        list_tmdb_id = tvshow_tmdb_id if dbtype.lower() in ("episode", "season") and tvshow_tmdb_id else tmdb_id

        if prefer_listitem:
            item_id = list_tmdb_id or imdb_id
        else:
            if prop_tmdb_id is None:
                prop_tmdb_id = self._info_label("Window(10000).Property(ds_info_tmdb_id)")
            prop_imdb_id = (
                self._info_label("Window(10000).Property(ds_imdb_id)")
                or self._info_label("Window(10000).Property(ContextMenuTargetID)")
            )
            item_id = prop_tmdb_id or list_tmdb_id or prop_imdb_id or imdb_id

        return item_id, list_tmdb_id, imdb_id, dbtype

    def _get_focused_item_id(self, prefer_listitem=False):
        """Caminho barato do loop: só o item_id, sem montar o dict completo.

        Na Home a skin mantém Window(home).Property(ds_info_tmdb_id) sincronizado
        em todo <onfocus> e ela é a fonte de maior prioridade; se preenchida,
        retorna direto sem ler os labels de ListItem."""
        if not prefer_listitem:
            prop_tmdb_id = self._info_label("Window(10000).Property(ds_info_tmdb_id)")
            if prop_tmdb_id:
                return prop_tmdb_id
            item_id, _l, _i, _d = self._resolve_focus_ids(prefer_listitem, prop_tmdb_id=prop_tmdb_id)
            return item_id
        item_id, _l, _i, _d = self._resolve_focus_ids(prefer_listitem)
        return item_id

    def _get_current_focus_info(self, prefer_listitem=False, resolved_ids=None):
        item_id, list_tmdb_id, imdb_id, dbtype = resolved_ids or self._resolve_focus_ids(prefer_listitem)

        return {
            "item_id": item_id,
            "tmdb_id": list_tmdb_id,
            "imdb_id": imdb_id,
            "dbtype": dbtype,
            "label": self._info_label("ListItem.Label") or self._info_label("Container(54).ListItem.Label"),
            "title": self._info_label("ListItem.Title") or self._info_label("ListItem.Label"),
            "premiered": self._info_label("ListItem.Premiered"),
            "date": self._info_label("ListItem.Date"),
            "year": self._info_label("ListItem.Year"),
            "duration": self._info_label("ListItem.Duration(hh:mm:ss)"),
            "rating": self._info_label("ListItem.Rating"),
            "genre": self._info_label("ListItem.Genre"),
            "tagline": self._info_label("ListItem.Tagline"),
            "plot": self._info_label("ListItem.Plot"),
            "fanart_art": self._info_label("ListItem.Art(fanart)"),
            "fanart_prop": self._info_label("ListItem.Property(fanart)"),
            "clearlogo": self._info_label("ListItem.Art(clearlogo)"),
            "season": self._info_label("ListItem.Season"),
            "episode": self._info_label("ListItem.Episode"),
            "total_seasons": self._info_label("ListItem.Property(TotalSeasons)"),
        }

    def _publish_video_nav_focus_props(self, focus_info):
        if not focus_info or not focus_info.get("item_id"):
            return

        dbtype = focus_info.get("dbtype", "")
        tmdb_id = focus_info.get("tmdb_id", "")
        imdb_id = focus_info.get("imdb_id", "")
        stack_type = ""
        if dbtype.lower() == "movie":
            stack_type = "movie"
        elif dbtype.lower() in ("tvshow", "season", "episode"):
            stack_type = "tv"

        self._update_skin_props_batch({
            "ds_info_title": focus_info.get("label", ""),
            "ds_info_dt_premier": focus_info.get("premiered", ""),
            "ds_info_dt_date": focus_info.get("date", ""),
            "ds_info_dt_year": focus_info.get("year", ""),
            "ds_info_duration": focus_info.get("duration", ""),
            "ds_info_rating": focus_info.get("rating", ""),
            "ds_info_genre": focus_info.get("genre", ""),
            "ds_info_tagline": focus_info.get("tagline", ""),
            "ds_info_desc": focus_info.get("plot", ""),
            "ds_info_fanart_art": focus_info.get("fanart_art", ""),
            "ds_info_fanart_prop": focus_info.get("fanart_prop", ""),
            "ds_info_clearlogo": focus_info.get("clearlogo", ""),
            "ds_info_dbtype": dbtype,
            "ds_info_season": focus_info.get("season", ""),
            "ds_info_episode": focus_info.get("episode", ""),
            "ds_info_totalseasons": focus_info.get("total_seasons", ""),
            "ContextMenuTargetID": imdb_id,
            "ContextMenuTargetDBType": dbtype,
            "ds_imdb_id": imdb_id,
            "ds_tmdb_id": tmdb_id,
            "ds_info_tmdb_id": tmdb_id or imdb_id,
            "ds_stack_tmdb_id": tmdb_id,
            "ds_stack_tmdb_type": stack_type,
            "ds_last_check": focus_info.get("label", ""),
        })

    def _get_window_state(self):
        current_time = time.time()
        if current_time - self.last_state_check > self._meta_interval:
            try: 
                h = xbmc.getCondVisibility("Window.IsVisible(10000) | Window.IsActive(videos) | Window.IsVisible(10025)")
                l = xbmc.getCondVisibility(
                    "%s | Window.IsActive(10103) | Window.IsActive(10151) | Window.IsActive(12003) | "
                    "Window.IsActive(DialogVideoInfo.xml) | Window.IsActive(10101) | Window.IsActive(DialogBusy.xml)"
                    % FineTuning.PROGRESS_MEDIA_CONDITION
                )
                f = xbmc.getCondVisibility("Window.IsVisible(12005)")
                p = xbmc.getCondVisibility("Player.Paused")
                self._last_st = (h, l, f, p)
            except:
                self._last_st = (False, False, False, False)
            self.last_state_check = current_time
        return self._last_st

    def _clear_all_properties_on_thread(self):
        """Limpa props de trailer/UI. Ratings são gerenciados pelo ratings_service."""
        # Invalida a assinatura do gate do video-nav: sem isso, voltar ao mesmo
        # item após um clear deixaria as props vazias até o republish periódico.
        self._last_published_sig = None
        props = {
            "ds_is_trailer_playing": "",
            "ContextMenuTargetID": "",
            "ContextMenuTargetDBType": "",
            "ds_active_widget_id": "",
            "ds_last_check": "",
            "ds_info_title": "",
            "ds_info_dbtype": "",
            "ds_info_tmdb_id": "",
            "ds_tmdb_id": "",
            "ds_imdb_id": "",
            "ds_stack_tmdb_id": "",
            "ds_stack_tmdb_type": "",
        }
        self._update_skin_props_batch(props)


    def _stop_current_sniper(self):
        with self._sniper_lock:
            self._sniper_active_flag = False

    def _is_trailer_playing(self):
        try: 
            if self.trailer_ativo: return True
            title = xbmc.getInfoLabel("VideoPlayer.Title") or ""
            return title.startswith("TrailerPreview_")
        except:
            return False

    def _is_long_playback(self, is_playing=None, is_trailer=None):
        try:
            if is_playing is None: is_playing = self.player.isPlayingVideo()
            if not is_playing: return False
            if is_trailer is None: is_trailer = self._is_trailer_playing()
            if is_trailer: return False
            total_time = self.player.getTotalTime()
            return total_time >= 600
        except: pass
        return False

    def _get_slow_delay(self):
        try:
            val = xbmc.getInfoLabel("Skin.String(dstv_trailer_delay_slow)")
            if not val or not val.strip():
                return 5.0
            return float(str(val).strip().replace(",", "."))
        except: 
            return 5.0


    def _safe_stop(self):
        try:
            with self._player_action_lock: 
                self.is_loading_trailer = False
                if not self.player.isPlayingVideo():
                    self.trailer_ativo = False
                    self._update_skin_props_batch({"ds_is_trailer_playing": ""})
                    with self._global_lock: self.trailer_state = TrailerState.IDLE
                    return
                if self._is_long_playback(): return
                total_time = self.player.getTotalTime()
                if total_time == 0: self.player.stop()
                else: 
                    if not xbmc.getCondVisibility("Player.Paused"): self.player.pause()
                self.trailer_ativo = False
                self._update_skin_props_batch({"ds_is_trailer_playing": ""})
                with self._global_lock: self.trailer_state = TrailerState.IDLE
                self._stop_current_sniper()
        except:
            try: self.player.stop()
            except: pass
            self.trailer_ativo = False
            self.is_loading_trailer = False
            with self._global_lock: self.trailer_state = TrailerState.IDLE

    def _resolve_item_metadata(self, item_id, media_type, session_id):
        """Resolve trailer em ordem: IMDb direto, resolver de busca e TMDb."""
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] >>> _resolve_item_metadata | item_id={item_id} | media_type={media_type} | session={session_id}", xbmc.LOGINFO)
        try:
            if not self._is_auto_trailer_enabled():
                if _DEBUG: xbmc.log(
                    f"[ShowIMDB][DEBUG][Trailer] Auto trailer desligado; modo econômico ativo, abortando resolução | item_id={item_id}",
                    xbmc.LOGINFO,
                )
                return
            if not self._is_session_valid(session_id, item_id):
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Sessão inválida no início, abortando | item_id={item_id}", xbmc.LOGINFO)
                return
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Tentativa 1: IMDb direto | item_id={item_id}", xbmc.LOGINFO)
            url = self._resolve_imdb_trailer_url(item_id, media_type)
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] IMDb direto resultado | item_id={item_id} | url={url or '(vazio)'}", xbmc.LOGINFO)
            if not url and self._is_session_valid(session_id, item_id):
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Tentativa 2: YouTube search resolver | item_id={item_id}", xbmc.LOGINFO)
                url = self._resolve_resolver_trailer_url()
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Resolver resultado | item_id={item_id} | url={url or '(vazio)'}", xbmc.LOGINFO)
            if not url:
                tmdb_target = item_id
                resolved_media_type = media_type
                if item_id.startswith("tt"):
                    try:
                        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Tentativa 3: TMDb — convertendo imdb->tmdb | item_id={item_id}", xbmc.LOGINFO)
                        t_id, m_type = tmdb_api.fetch_tmdb_id_from_imdb(item_id)
                        if t_id:
                            tmdb_target, resolved_media_type = t_id, m_type
                            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] imdb->tmdb OK | imdb_id={item_id} | tmdb_id={tmdb_target} | type={resolved_media_type}", xbmc.LOGINFO)
                        else:
                            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] imdb->tmdb falhou (sem resultado) | item_id={item_id}", xbmc.LOGWARNING)
                    except Exception as e:
                        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] ERRO imdb->tmdb | item_id={item_id} | erro={e}", xbmc.LOGWARNING)
                if not self._is_session_valid(session_id, item_id):
                    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Sessão inválida antes do TMDb fetch | item_id={item_id}", xbmc.LOGINFO)
                    return
                try:
                    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Buscando trailer TMDb | tmdb_target={tmdb_target} | type={resolved_media_type}", xbmc.LOGINFO)
                    url = tmdb_api.fetch_trailer_url(tmdb_target, resolved_media_type)
                    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] TMDb resultado | item_id={item_id} | url={url or '(vazio)'}", xbmc.LOGINFO)
                except Exception as e:
                    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] ERRO TMDb fetch_trailer_url | item_id={item_id} | erro={e}", xbmc.LOGWARNING)
            if self._is_session_valid(session_id, item_id):
                with self._state_lock: self.trailer_url_ready = url
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] trailer_url_ready definido | item_id={item_id} | url={url or '(nenhum)'}", xbmc.LOGINFO)
            else:
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Sessão inválida ao finalizar, url descartada | item_id={item_id}", xbmc.LOGINFO)
        finally:
            self.is_fetching_trailer = False
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] _resolve_item_metadata finalizado | item_id={item_id}", xbmc.LOGINFO)


    def _start_sniper_task(self, target_item_id, target_session_id):
        self._stop_current_sniper()
        if self._is_long_playback(): return
        with self._sniper_lock: self._sniper_active_flag = True
        self.sniper_task = self.submit_safe(self.sniper_executor, self._sniper_worker_loop, target_item_id, target_session_id)

    def _sniper_worker_loop(self, target_item_id, target_session_id):
        start_time = time.time()
        stabilized = False
        try:
            while (time.time() - start_time) < FineTuning.SNIPER_TIMEOUT:
                time.sleep(FineTuning.LOOP_SNIPER)
                with self._global_lock:
                    if not self._sniper_active_flag: break
                if not self._is_session_valid(target_session_id, target_item_id):
                    if xbmc.getCondVisibility("Window.IsActive(10101)"): xbmc.executebuiltin("Dialog.Close(10101)")
                    self._safe_stop()
                    return
                if self.player.isPlayingVideo():
                    try: t_time = self.player.getTotalTime()
                    except: t_time = 0
                    if t_time > 0:
                        with self._state_lock:
                            if self.focused_session_id == target_session_id: self.trailer_played_for_session = True
                        if xbmc.getCondVisibility("Window.IsActive(10101)"): xbmc.executebuiltin("Dialog.Close(10101)")
                        stabilized = True
                        break
            if not stabilized: self._safe_stop()
            if stabilized:
                p2_start = time.time()
                while (time.time() - p2_start) < 5.0:
                    time.sleep(FineTuning.LOOP_SNIPER)
                    with self._global_lock:
                        if not self._sniper_active_flag: break
                    if not self._is_session_valid(target_session_id, target_item_id):
                        self._safe_stop()
                        return
        finally:
            self.is_loading_trailer = False
            with self._global_lock:
                if self.focused_session_id == target_session_id: self._sniper_active_flag = False

    def _play_trailer_worker(self, trailer_url, target_item_id, session_id):
        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] >>> _play_trailer_worker | item_id={target_item_id} | session={session_id} | url={trailer_url[:80] + '...' if len(trailer_url) > 80 else trailer_url}", xbmc.LOGINFO)
        def _isolated_play():
            try:
                if self._is_long_playback():
                    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Abortando play — reprodução longa em curso | item_id={target_item_id}", xbmc.LOGINFO)
                    self.is_loading_trailer = False
                    return
                with self._player_action_lock:
                    if not self._is_session_valid(session_id, target_item_id):
                        if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Sessão inválida antes do play | item_id={target_item_id}", xbmc.LOGINFO)
                        self.is_loading_trailer = False
                        return
                    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Iniciando reprodução do trailer | item_id={target_item_id} | url={trailer_url[:80]}", xbmc.LOGINFO)
                    self._start_sniper_task(target_item_id, session_id)
                    listitem = xbmcgui.ListItem(label=f"TrailerPreview_{target_item_id}")
                    listitem.setPath(trailer_url)
                    listitem.setInfo("video", {"title": f"TrailerPreview_{target_item_id}"})
                    self.player.stop()
                    time.sleep(0.05)
                    self.trailer_ativo = True
                    self.player.play(trailer_url, listitem, windowed=True)
                    if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] player.play() chamado | item_id={target_item_id} | url={trailer_url[:80]}", xbmc.LOGINFO)
            except Exception as e:
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] ERRO _isolated_play | item_id={target_item_id} | erro={e}", xbmc.LOGERROR)
                self.is_loading_trailer = False
                self.trailer_ativo = False

        if self._is_session_valid(session_id, target_item_id):
            self._update_skin_props_batch({"ds_is_trailer_playing": "true"})
            with self._global_lock: self.trailer_state = TrailerState.PLAYING_AUTO
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] ds_is_trailer_playing=true | item_id={target_item_id}", xbmc.LOGINFO)
            _isolated_play()
        else:
            if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Sessão inválida, play cancelado | item_id={target_item_id}", xbmc.LOGINFO)
            self.is_loading_trailer = False

    def _handle_auto_trailer_logic(self, item_id, media_type, session_id, current_delay):
        # Permite Home e infowall (container 54); bloqueia listas de vídeo comuns
        # (video-nav sem infowall) e qualquer outra janela.
        if not (self._is_home_active() or self._is_infowall_active()): return
        if xbmc.getCondVisibility("Window.IsVisible(12005)") or not self._is_auto_trailer_enabled(): return
        if (xbmc.getCondVisibility(
            "%s | Window.IsActive(DialogVideoInfo.xml) | Window.IsActive(10103) | Window.IsActive(10151)"
            % FineTuning.PROGRESS_MEDIA_CONDITION
        )) and not self.is_loading_trailer: return
        if not self._is_session_valid(session_id, item_id): return
        elapsed = time.time() - self.focus_start_time
        if elapsed > current_delay:
            with self._state_lock:
                ready, played = self.trailer_url_ready, self.trailer_played_for_session
            if ready and not played and not self.is_loading_trailer:
                if _DEBUG: xbmc.log(
                    f"[ShowIMDB][DEBUG][Trailer] AUTO-PLAY agendado | item_id={item_id} | elapsed={elapsed:.1f}s | "
                    f"delay={current_delay:.1f}s | url={ready[:80] + '...' if len(ready) > 80 else ready}",
                    xbmc.LOGINFO
                )
                self.is_loading_trailer = True
                self.trailer_ativo = True
                self.play_task = self.submit_safe(self.playback_executor, self._play_trailer_worker, ready, item_id, session_id)
            elif not ready:
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Delay atingido mas trailer_url_ready ainda vazio | item_id={item_id} | elapsed={elapsed:.1f}s", xbmc.LOGINFO)
            elif played:
                if _DEBUG: xbmc.log(f"[ShowIMDB][DEBUG][Trailer] Trailer já foi reproduzido nesta sessão | item_id={item_id}", xbmc.LOGINFO)

    def _detect_focus(self, video_nav_active):
        """Detecta o item em foco no loop.

        No video-nav lê primeiro uma assinatura barata (ids + label/season/episode —
        label+season+episode são necessários porque episódios do mesmo show partilham
        o item_id do tvshow); só quando ela muda monta o focus_info completo (~23
        labels) e publica as props ds_info_*. Rede de segurança: republica a cada
        VIDEO_NAV_REPUBLISH s mesmo sem mudança, cobrindo container recarregado
        in-place. Na Home usa o caminho barato (só item_id); o focus_info completo
        é montado sob demanda por quem precisar. Retorna (item_id, focus_info|None)."""
        if video_nav_active:
            resolved = self._resolve_focus_ids(prefer_listitem=True)
            item_id = resolved[0]
            sig = (
                item_id,
                self._info_label("ListItem.Label") or self._info_label("Container(54).ListItem.Label"),
                self._info_label("ListItem.Season"),
                self._info_label("ListItem.Episode"),
            )
            now = time.time()
            if sig == self._last_published_sig and (now - self._last_publish_ts) < FineTuning.VIDEO_NAV_REPUBLISH:
                return item_id, None
            focus_info = self._get_current_focus_info(prefer_listitem=True, resolved_ids=resolved)
            self._publish_video_nav_focus_props(focus_info)
            self._last_published_sig = sig
            self._last_publish_ts = now
            return focus_info.get("item_id"), focus_info
        return self._get_focused_item_id(prefer_listitem=False), None

    def run(self):
        try:
            while not self.abortRequested():
                if self._is_progress_media_active():
                    self._set_pause_mode(True)
                    if self.waitForAbort(FineTuning.PAUSE_POLL_INTERVAL):
                        break
                    continue
                if self._pause_mode_active:
                    self._set_pause_mode(False)
                if self._is_pause_resume_pending():
                    if self.waitForAbort(FineTuning.PAUSE_POLL_INTERVAL):
                        break
                    continue

                home, loading, fullscreen, paused = self._get_window_state()
                home_active = self._is_home_active()
                video_nav_active = self._is_video_nav_active()
                infowall_active = self._is_infowall_active()
                self._sync_infowall_active_property(infowall_active)
                auto_trailer_setting = self._is_auto_trailer_enabled()
                # Auto-trailer ativo na Home OU no infowall (container 54). Listas de
                # vídeo comuns (video-nav sem infowall) continuam sem trailer.
                auto_trailer_enabled = auto_trailer_setting and (home_active or infowall_active)

                is_playing = self.player.isPlayingVideo()
                is_trailer_playing = self._is_trailer_playing() if is_playing else False
                is_long_playing = self._is_long_playback(is_playing, is_trailer_playing)

                if loading and not self.is_loading_trailer:
                    self.focus_start_time = time.time()
                    self.trailer_lockout_time = time.time() + 30.0 if auto_trailer_enabled else 0.0
                    self._reset_trailer_state()

                if is_playing and not paused and not is_trailer_playing:
                    if is_long_playing and fullscreen:
                        item_id, focus_info = self._detect_focus(video_nav_active)
                        if item_id and item_id != self.prev_tmdb_id:
                            new_session = self._generate_session_id()
                            with self._state_lock:
                                self.focused_item_id, self.focused_session_id, self.prev_tmdb_id = item_id, new_session, item_id

                        self.waitForAbort(FineTuning.LOOP_FULLSCREEN)
                        continue
                    item_id, focus_info = self._detect_focus(video_nav_active)
                    if item_id and item_id != self.prev_tmdb_id:
                        new_session = self._generate_session_id()
                        with self._state_lock:
                            self.focused_item_id, self.focused_session_id, self.prev_tmdb_id = item_id, new_session, item_id

                    self.waitForAbort(FineTuning.LOOP_IDLE)
                    continue

                is_idle = fullscreen or (is_long_playing and not home)
                sleep_time = FineTuning.LOOP_IDLE if is_idle else FineTuning.LOOP_NORMAL
                
                if fullscreen:
                    # Filme pausado fullscreen: relaxa o polling para não martelar a API do
                    # player por horas (possível fator no crash de pausa longa). A tocar,
                    # mantém o ciclo idle normal.
                    self.waitForAbort(FineTuning.LOOP_FULLSCREEN_PAUSED if paused else sleep_time)
                    continue
                
                if xbmc.getCondVisibility("Window.IsVisible(10106) | Window.IsVisible(DialogContextMenu.xml) | Control.HasFocus(9000)"):
                    # O menu abriu SOBRE o item em foco — ele continua a ser o item atual,
                    # e o DialogVideoInfo costuma ser aberto a partir daqui. Em vez de zerar
                    # reviews/badges/ids (o que deixava a Info fria ao abrir), paramos só o
                    # trailer e PRESERVAMOS os metadados do item. Item/sessão NÃO são anulados
                    # (mesmo item sob o menu), por isso a Info abre já preenchida.
                    self._sniper_active_flag = False
                    self._stop_current_sniper()
                    self._safe_stop()
                    self._update_skin_props_batch({
                        "ds_is_trailer_playing": "",
                        "ds_active_widget_id": "",
                    })
                    # Fixa foco (corrige scroll-rápido→menu) e apressa reviews se necessário.
                    menu_item_id = self.focused_item_id or self.prev_tmdb_id
                    if menu_item_id:
                        with self._state_lock:
                            self.focused_item_id = menu_item_id
                            self.prev_tmdb_id = menu_item_id
                    if menu_item_id and not self._info_label("Window(10000).Property(Trakt.Reviews)"):
                        import ratings_service as _rs
                        _rs.rush_reviews(menu_item_id)
                    self.waitForAbort(FineTuning.LOOP_NORMAL)
                    continue

                if not home and not loading:
                    self._sniper_active_flag = False
                    new_session = self._generate_session_id()
                    with self._state_lock:
                        self.focused_item_id, self.focused_session_id = None, new_session
                        self.prev_tmdb_id = None
                    self._clear_all_properties_on_thread()
                    self.waitForAbort(FineTuning.LOOP_NORMAL)
                    continue
                
                item_id, focus_info = self._detect_focus(video_nav_active)
                if not item_id:
                    if video_nav_active and self.prev_tmdb_id is not None:
                        if self.trailer_ativo:
                            # Trailer a tocar + id vazio = ARTEFACTO da reprodução windowed:
                            # views como standard/fantastic-like deixam de expor
                            # ListItem.UniqueID enquanto o vídeo toca (a "wide" publica a
                            # propriedade persistente ds_info_tmdb_id, por isso não sofre).
                            # NÃO é "saiu do item" (o foco não mudou) — ignora o vazio e
                            # mantém prev + trailer. A saída real é detetada quando um id
                            # DIFERENTE aparecer (item_id != prev, mais abaixo) → aí o
                            # _safe_stop corre. Um id vazio nunca é sinal fiável de saída.
                            self._empty_id_since = None
                        else:
                            # Sem trailer: grace contra blips do container; passado o grace,
                            # trata como "saiu do item" e limpa (notas preservadas p/ não piscar).
                            if self._empty_id_since is None:
                                self._empty_id_since = time.time()
                            elif (time.time() - self._empty_id_since) >= FineTuning.EMPTY_ID_GRACE:
                                self.prev_tmdb_id = None
                                self._empty_id_since = None
                                self._clear_all_properties_on_thread()
                    self.waitForAbort(sleep_time)
                    continue
                self._empty_id_since = None

                if item_id != self.prev_tmdb_id:
                    now = time.time()
                    self.last_delta_t = now - self.last_focus_time
                    self.last_focus_time = now
                    self._safe_stop()
                    self._reset_trailer_state()
                    self.is_loading_trailer = False
                    self._sniper_active_flag = False

                    new_session = self._generate_session_id()
                    is_fast_scroll = self.last_delta_t < FineTuning.DB_FAST_THRESHOLD

                    # No caminho Home o focus_info ainda não foi montado (só item_id);
                    # monta agora — só na troca de item — para o log e dados completos.
                    if focus_info is None:
                        focus_info = self._get_current_focus_info(prefer_listitem=False)

                    # ── LOG: novo item em foco ── (prep só para debug; inclui um
                    # getInfoLabel extra — fica todo dentro do gate p/ não custar na navegação)
                    if _DEBUG:
                        _title_log = focus_info.get("title") or ""
                        _year_log  = focus_info.get("year") or ""
                        _dbtype_log = focus_info.get("dbtype") or self._info_label("Window(10000).Property(ds_info_dbtype)")
                        xbmc.log(
                            f"[ShowIMDB][DEBUG][Focus] NOVO ITEM | item_id={item_id} | titulo={_title_log} | "
                            f"ano={_year_log} | dbtype={_dbtype_log} | session={new_session} | "
                            f"delta_t={self.last_delta_t:.3f}s | scroll={'RAPIDO' if is_fast_scroll else 'NORMAL'}",
                            xbmc.LOGINFO
                        )

                    with self._state_lock:
                        if is_fast_scroll: self.focused_item_id = None
                        self.prev_tmdb_id = item_id
                        self.focused_item_id = item_id
                        self.focused_session_id = new_session
                        self.focus_start_time = now

                    if is_fast_scroll:
                        # Preserva as notas no scroll rápido (v1): elas só serão
                        # sobrescritas no lugar quando o foco estabilizar.
                        self._clear_all_properties_on_thread()

                    self.waitForAbort(FineTuning.FOCUS_DEBOUNCE)
                    if self.abortRequested(): break


                    self._stop_current_sniper()
                
                else:
                    elapsed = time.time() - self.focus_start_time
                    current_session = self.focused_session_id
                    media_type = self._get_media_type(video_nav_active)
                    if self.last_delta_t < FineTuning.DB_FAST_THRESHOLD: adaptive_trailer_delay = FineTuning.TRAILER_DELAY_FAST
                    elif self.last_delta_t < FineTuning.DB_MED_THRESHOLD: adaptive_trailer_delay = FineTuning.TRAILER_DELAY_MED
                    else: adaptive_trailer_delay = self._get_slow_delay()

                    with self._global_lock:
                        if self.trailer_state == TrailerState.PLAYING_AUTO and (not self.player.isPlayingVideo() or paused) and not self.is_loading_trailer:
                            self.trailer_state = TrailerState.IDLE
                            self.trailer_ativo = False
                            self._update_skin_props_batch({"ds_is_trailer_playing": ""})

                    if auto_trailer_enabled and not is_long_playing and time.time() > self.trailer_lockout_time:
                        if elapsed > (adaptive_trailer_delay - FineTuning.PRE_FETCH_LEAD) and not self.is_fetching_trailer and not self.trailer_url_ready and not self.trailer_played_for_session:
                            with self._state_lock: self.is_fetching_trailer = True
                            self.fetch_task = self.submit_safe(self.trailer_executor, self._resolve_item_metadata, item_id, media_type, current_session)
                        # Depois que o trailer já tocou (ou está carregando) p/ este item,
                        # NÃO refaz os getCondVisibility de _handle_auto_trailer_logic a cada
                        # tick — economiza polling justamente enquanto o trailer toca (device
                        # carregado). Reseta na próxima troca de foco (_reset_trailer_state).
                        if elapsed > adaptive_trailer_delay and not self.trailer_played_for_session and not self.is_loading_trailer:
                            self._handle_auto_trailer_logic(item_id, media_type, current_session, adaptive_trailer_delay)
                self.waitForAbort(sleep_time)
        finally:
            self._update_skin_props_batch({"ds_infowall_active": ""})
            self._safe_stop()
            for ex in [self.trailer_executor, self.playback_executor, self.sniper_executor]: ex.shutdown(wait=False)

if __name__ == "__main__":
    window = xbmcgui.Window(10000)
    monitor = xbmc.Monitor()
    splash_video_poll = 0.10
    
    # Limpeza inicial
    window.clearProperty(BOOT_COVER_DONE_PROPERTY)
    window.clearProperty(HOME_SPLASH_ACTIVE_PROPERTY)
    window.clearProperty(SPLASH_VIDEO_COVER_DONE_PROPERTY)

    use_splash_video = can_play_splash_video()
    preload_targets = discover_widget_boot_targets()
    if preload_targets:
        xbmc.log("[ShowIMDB][Boot] %d containers de widget detectados para o gate." % len(preload_targets), xbmc.LOGINFO)
    else:
        xbmc.log("[ShowIMDB][Boot] Nenhum container de widget detectado (xml de includes vazio ou ausente).", xbmc.LOGINFO)

    splash_thread = None
    splash_result = {"started": False, "finished": False}
    start_splash_video = False

    # Os containers do skin carregam naturalmente. O gate apenas observa quantos widgets
    # já possuem conteúdo real antes de liberar o boot cover ou iniciar o splash.
    #   - COM video: espera SPLASH_VIDEO_WIDGET_READY_COUNT (8) antes de disparar o splash.mp4.
    #   - SEM video: espera SPLASH_BOOT_COVER_WIDGET_READY_COUNT e libera o fade-out.
    if preload_targets:
        gate_count = None if use_splash_video else FineTuning.SPLASH_BOOT_COVER_WIDGET_READY_COUNT
        gate_ready, gate_reason, gate_widget = wait_for_splash_video_gate(ready_count=gate_count, targets=preload_targets)
        if gate_ready and gate_widget is not None:
            log_first_widget_ready(gate_widget)
        if not gate_ready:
            xbmc.log(f"[ShowIMDB][Boot] Gate falhou ou abortou: {gate_reason}", xbmc.LOGINFO)
        if use_splash_video and gate_ready:
            start_splash_video = True

    if preload_targets:
        if start_splash_video:
            # Com video: o splash.mp4 assume o cover e libera o BOOT_COVER_DONE ao confirmar 1s.
            splash_thread = threading.Thread(
                target=play_splash_video,
                args=(splash_result,),
                name="ShowIMDBSplashVideo"
            )
            splash_thread.daemon = True
            splash_thread.start()
        else:
            # Sem video: o boot cover segurou durante o carregamento (gate); agora libera pro fade-out.
            xbmc.log("[ShowIMDB][Boot] Sem splash video; liberando boot cover apos o gate.", xbmc.LOGINFO)
            window.setProperty(BOOT_COVER_DONE_PROPERTY, "true")
    else:
        # Sem prefetch e sem splash
        xbmc.log("[ShowIMDB][Boot] Nada para pre-carregar; liberando boot.", xbmc.LOGINFO)
        window.setProperty(BOOT_COVER_DONE_PROPERTY, "true")
        xbmc.sleep(1000)

    # Aguarda fim do splash se ele estiver rodando
    if splash_thread is not None:
        while splash_thread.is_alive():
            if monitor.waitForAbort(splash_video_poll):
                break

    if not monitor.abortRequested():
        load_runtime_modules()
        try:
            import recommendations_service
            recommendations_service.start()
        except Exception as e:
            xbmc.log("[ShowIMDB][Recs] watcher start falhou: %s" % e, xbmc.LOGWARNING)
        try:
            import ratings_service as _ratings_svc
            _ratings_svc.start()
        except Exception as e:
            xbmc.log("[ShowIMDB][Ratings] ratings_service start falhou: %s" % e, xbmc.LOGWARNING)
        ShowImdbService().run()

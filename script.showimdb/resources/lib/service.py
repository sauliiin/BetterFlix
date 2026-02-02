# -*- coding: utf-8 -*-
import xbmc
import xbmcgui
import xbmcvfs
import xbmcaddon
import time
import threading
import os
import shutil
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import tmdb_api
import mdblist_api
import trakt_api

class POVVersionChecker:
    """
    Verificador de versão do addon POV. 
    Monitora mudanças de versão e atualiza arquivos quando necessário.
    """
    KNOWN_VERSION = "6.01.18"
    VERSION_FILE = "pov_last_known_version.txt"
    CHECK_INTERVAL = 4 * 60 * 60  # 4 horas em segundos
    INITIAL_DELAY = 60  # 60 segundos de atraso inicial
    
    def __init__(self):
        self.addon = xbmcaddon.Addon()
        self.addon_path = xbmcvfs.translatePath(self.addon.getAddonInfo('path'))
        self.version_file_path = os.path.join(self.addon_path, self.VERSION_FILE)
        
        # Caminhos base
        self.kodi_addons = xbmcvfs.translatePath("special://home/addons/")
        
        # Caminhos de origem (PbU)
        self.pbu_path = os.path.join(self.kodi_addons, "script.showimdb", "resources", "PbU")
        
        # Caminhos de destino (POV)
        self.pov_lib_path = os.path.join(self.kodi_addons, "plugin.video.pov", "resources", "lib")
        self.pov_skins_path = os.path.join(self.kodi_addons, "plugin.video.pov", "resources", "skins", "Default", "1080i")
        self.pov_addon_xml = os.path.join(self.kodi_addons, "plugin.video.pov", "addon.xml")
        
        self._checker_active = True
        self._checker_thread = None
    
    def _get_stored_version(self):
        """Lê a última versão conhecida do arquivo de controle."""
        try:
            if os.path.exists(self.version_file_path):
                with open(self.version_file_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
        except Exception as e:
            xbmc.log(f"[POVChecker] Erro ao ler versão armazenada: {e}", xbmc.LOGWARNING)
        return self.KNOWN_VERSION
    
    def _save_version(self, version):
        """Salva a versão atual no arquivo de controle."""
        try:
            with open(self.version_file_path, 'w', encoding='utf-8') as f:
                f.write(version)
            xbmc.log(f"[POVChecker] Versão {version} salva com sucesso", xbmc.LOGINFO)
        except Exception as e:
            xbmc.log(f"[POVChecker] Erro ao salvar versão: {e}", xbmc.LOGERROR)
    
    def _get_current_pov_version(self):
        """Lê a versão atual do addon POV do addon.xml."""
        try:
            if not os.path.exists(self.pov_addon_xml):
                xbmc.log("[POVChecker] addon.xml do POV não encontrado", xbmc.LOGWARNING)
                return None
            
            with open(self.pov_addon_xml, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Procura por version="X.XX.XX" no addon.xml
            match = re.search(r'<addon[^>]+version="([^"]+)"', content)
            if match: 
                return match.group(1)
            
            xbmc.log("[POVChecker] Versão não encontrada no addon.xml", xbmc.LOGWARNING)
            return None
        except Exception as e:
            xbmc.log(f"[POVChecker] Erro ao ler versão do POV: {e}", xbmc.LOGERROR)
            return None
    
    def _copy_file(self, src, dst):
        """Copia um arquivo, sobrescrevendo se existir."""
        try:
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                xbmc.log(f"[POVChecker] Arquivo copiado: {src} -> {dst}", xbmc.LOGINFO)
                return True
            else:
                xbmc.log(f"[POVChecker] Arquivo origem não existe: {src}", xbmc.LOGWARNING)
                return False
        except Exception as e: 
            xbmc.log(f"[POVChecker] Erro ao copiar arquivo: {e}", xbmc.LOGERROR)
            return False
    
    def _copy_folder(self, src, dst):
        """Copia uma pasta inteira, sobrescrevendo se existir."""
        try: 
            if os.path.exists(src) and os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                xbmc.log(f"[POVChecker] Pasta copiada: {src} -> {dst}", xbmc.LOGINFO)
                return True
            else:
                xbmc.log(f"[POVChecker] Pasta origem não existe: {src}", xbmc.LOGWARNING)
                return False
        except Exception as e:
            xbmc.log(f"[POVChecker] Erro ao copiar pasta: {e}", xbmc.LOGERROR)
            return False
    
    def _ensure_init_py(self, folder_path):
        """Garante que __init__.py existe na pasta."""
        try:
            init_file = os.path.join(folder_path, "__init__.py")
            if not os.path.exists(init_file):
                with open(init_file, 'w', encoding='utf-8') as f:
                    f.write("# -*- coding: utf-8 -*-\n")
                xbmc.log(f"[POVChecker] __init__.py criado em: {folder_path}", xbmc.LOGINFO)
            return True
        except Exception as e:
            xbmc.log(f"[POVChecker] Erro ao criar __init__.py: {e}", xbmc.LOGERROR)
            return False

    def _perform_file_updates(self):
        """Executa a atualização dos arquivos quando a versão muda."""
        xbmc.log("[POVChecker] Iniciando atualização de arquivos...", xbmc.LOGINFO)
        
        success_count = 0
        
        # 1. Mover progress_media.xml para skins do POV
        src_progress = os.path.join(self.pbu_path, "progress_media.xml")
        dst_progress = os.path.join(self.pov_skins_path, "progress_media.xml")
        if self._copy_file(src_progress, dst_progress):
            success_count += 1
        
        # 2. Mover tmdb_api.py para indexers do POV
        src_tmdb = os.path.join(self.pbu_path, "tmdb_api.py")
        dst_tmdb = os.path.join(self.pov_lib_path, "indexers", "tmdb_api.py")
        if self._copy_file(src_tmdb, dst_tmdb):
            success_count += 1
        
        # 3. Mover metadata.py para indexers do POV
        src_metadata = os.path.join(self.pbu_path, "metadata.py")
        dst_metadata = os.path.join(self.pov_lib_path, "indexers", "metadata.py")
        if self._copy_file(src_metadata, dst_metadata):
            success_count += 1
        
        # 4. Mover fanarttv_api.py para indexers do POV
        src_fanarttv = os.path.join(self.pbu_path, "fanarttv_api.py")
        dst_fanarttv = os.path.join(self.pov_lib_path, "indexers", "fanarttv_api.py")
        if self._copy_file(src_fanarttv, dst_fanarttv):
            success_count += 1
        
        xbmc.log(f"[POVChecker] Atualização concluída: {success_count}/6 operações bem-sucedidas", xbmc.LOGINFO)
        return success_count > 0

    def check_and_update(self):
        """Verifica a versão e atualiza se necessário."""
        xbmc.log("[POVChecker] Verificando versão do POV...", xbmc.LOGINFO)
        
        current_version = self._get_current_pov_version()
        if not current_version: 
            xbmc.log("[POVChecker] Não foi possível obter a versão atual do POV", xbmc.LOGWARNING)
            return False
        
        stored_version = self._get_stored_version()
        
        xbmc.log(f"[POVChecker] Versão armazenada: {stored_version} | Versão atual: {current_version}", xbmc.LOGINFO)
        
        if current_version != stored_version: 
            xbmc.log(f"[POVChecker] Versão mudou de {stored_version} para {current_version}!", xbmc.LOGINFO)
            
            if self._perform_file_updates():
                self._save_version(current_version)
                xbmc.log("[POVChecker] Atualização completa!", xbmc.LOGINFO)
                return True
            else:
                xbmc.log("[POVChecker] Falha na atualização de arquivos", xbmc.LOGERROR)
                return False
        else: 
            xbmc.log("[POVChecker] Versão não mudou, nenhuma ação necessária", xbmc.LOGINFO)
            return False
    
    def start(self):
        """Inicia o thread de verificação periódica."""
        self._checker_thread = threading.Thread(target=self._checker_loop, daemon=True)
        self._checker_thread.start()
        xbmc.log("[POVChecker] Thread de verificação iniciado", xbmc.LOGINFO)
    
    def stop(self):
        """Para o verificador."""
        self._checker_active = False
        xbmc.log("[POVChecker] Verificador parado", xbmc.LOGINFO)
    
    def _checker_loop(self):
        """Loop principal de verificação."""
        xbmc.log(f"[POVChecker] Aguardando {self.INITIAL_DELAY}s antes da primeira verificação...", xbmc.LOGINFO)
        
        elapsed = 0
        while elapsed < self.INITIAL_DELAY and self._checker_active:
            time.sleep(1)
            elapsed += 1
        
        if not self._checker_active:
            return
        
        self.check_and_update()
        
        while self._checker_active:
            elapsed = 0
            while elapsed < self.CHECK_INTERVAL and self._checker_active:
                time.sleep(60)
                elapsed += 60
            
            if self._checker_active:
                self.check_and_update()

class TrailerState:
    IDLE = 0
    PLAYING_AUTO = 1

class FineTuning: 
    """
    Central de Ajustes e Performance. 
    """
    TRAILER_WORKERS = 1
    TRAILER_QUEUE = 1
    PLAYBACK_WORKERS = 1
    PLAYBACK_QUEUE = 1
    SNIPER_WORKERS = 1
    SNIPER_QUEUE = 1
    
    DB_FAST_THRESHOLD = 0.3
    DB_MED_THRESHOLD = 0.6
    
    TRAILER_DELAY_FAST = 12.0
    TRAILER_DELAY_MED = 8.0
    
    RATINGS_REVIEW_DEBOUNCE = 0.05
    
    LOOP_NORMAL = 0.10
    LOOP_IDLE = 0.25
    LOOP_SNIPER = 0.08
    SNIPER_TIMEOUT = 20.0
    
    META_UPDATE_INTERVAL = 0.30

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
        self._sniper_active_flag = False
        self._monitor_active = True
        self.is_loading_trailer = False
        self.trailer_lockout_time = 0
        
        self.pov_checker = POVVersionChecker()
        self.pov_checker.start()
        
        self._monitor_thread = threading.Thread(target=self._focus_monitor_loop, daemon=True)
        self._monitor_thread.start()

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

    def _focus_monitor_loop(self):
        last_session_id = 0
        while self._monitor_active:
            time.sleep(FineTuning.LOOP_NORMAL)
            try:
                with self._state_lock:
                    current = self.focused_session_id
                if current != last_session_id:
                    self._cleanup_stale_state()
                    last_session_id = current
            except:
                pass

    def _cleanup_stale_state(self):
        try:
            with self._state_lock:
                if not self._is_session_valid(self.focused_session_id, self.focused_item_id):
                    self.trailer_url_ready = None
                    self.is_fetching_trailer = False
        except:
            pass

    def _reset_trailer_state(self):
        self.trailer_url_ready = None
        self.trailer_played_for_session = False
        self.is_fetching_trailer = False
        self.focus_start_time = time.time()
        with self._global_lock:
            self.trailer_state = TrailerState.IDLE

    def submit_safe(self, executor, fn, *args, **kwargs):
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

    def _get_media_type(self):
        current_time = time.time()
        if current_time - self.last_dbtype_check > FineTuning.META_UPDATE_INTERVAL:
            try: self.cached_dbtype = xbmc.getInfoLabel("Window(10000).Property(ds_info_dbtype)")
            except: self.cached_dbtype = None
            self.last_dbtype_check = current_time
        return "tv" if self.cached_dbtype and self.cached_dbtype.lower() in ("tv", "tvshow", "episode", "season") else "movie"

    def _get_window_state(self):
        current_time = time.time()
        if current_time - self.last_state_check > FineTuning.META_UPDATE_INTERVAL: 
            try: 
                h = xbmc.getCondVisibility("Window.IsVisible(10000)")
                l = xbmc.getCondVisibility("Window.IsVisible(progress_media.xml) | Window.IsActive(10103) | Window.IsActive(10151) | Window.IsActive(12003) | Window.IsActive(DialogVideoInfo.xml) | Window.IsActive(10101) | Window.IsActive(DialogBusy.xml)")
                f = xbmc.getCondVisibility("Window.IsVisible(12005)")
                p = xbmc.getCondVisibility("Player.Paused")
                self._last_st = (h, l, f, p)
            except:
                self._last_st = (False, False, False, False)
            self.last_state_check = current_time
        return self._last_st

    def _clear_all_properties_on_thread(self):
        props = {
            "dsWeekDay": "", 
            "ds_is_trailer_playing": ""
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

    def _is_long_playback(self):
        try:
            if not self.player.isPlayingVideo(): return False
            if self._is_trailer_playing(): return False
            total_time = self.player.getTotalTime()
            return total_time >= 600
        except: pass
        return False

    def _get_slow_delay(self):
        try:
            val = self.addon.getSetting("dstv_trailer_delay_slow")
            if not val or not val.strip():
                return 5.0
            return float(val)
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

    def _start_metadata_chain(self, item_id, media_type, session_id, delta_t):
        if not self._is_session_valid(session_id, item_id): return
        
        self._clear_all_properties_on_thread()
        
        self._process_weekday(session_id, item_id)
        
        imdb_id = item_id if item_id.startswith("tt") else ""
        try:
            if not imdb_id: imdb_id = tmdb_api.fetch_imdb_id(item_id, media_type)
        except: pass
        
        if imdb_id and self._is_session_valid(session_id, item_id):
            t1 = threading.Thread(target=self._process_ratings, args=(imdb_id, session_id, item_id))
            t1.daemon = True
            t1.start()
            
            t3 = threading.Thread(target=self._process_reviews, args=(imdb_id, media_type, session_id, item_id))
            t3.daemon = True
            t3.start()

    def _resolve_item_metadata(self, item_id, media_type, session_id):
        try:
            if not self._is_session_valid(session_id, item_id): return
            tmdb_target = item_id
            if item_id.startswith("tt"):
                try: 
                    t_id, m_type = tmdb_api.fetch_tmdb_id_from_imdb(item_id)
                    if t_id: tmdb_target, media_type = t_id, m_type
                except: pass
            if not self._is_session_valid(session_id, item_id): return
            url = None
            try: url = tmdb_api.fetch_trailer_url(tmdb_target, media_type)
            except: pass
            if self._is_session_valid(session_id, item_id):
                with self._state_lock: self.trailer_url_ready = url
        finally:
            self.is_fetching_trailer = False

    def _process_weekday(self, session_id, item_id):
        if not self._is_session_valid(session_id, item_id): return
        try:
            raw = xbmc.getInfoLabel("ListItem.Premiered")
            if not raw or raw == "0": 
                self._update_skin_props_batch({"dsWeekDay": ""})
                return
            parts = raw.split("-") if "-" in raw else raw.split("/")
            if len(parts) == 3:
                p1, p2, p3 = int(parts[0]), int(parts[1]), int(parts[2])
                dt_obj = date(p1, p2, p3) if len(parts[0]) == 4 else date(p3, p2, p1)
                days = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
                if self._is_session_valid(session_id, item_id):
                    self._update_skin_props_batch({"dsWeekDay": days[dt_obj.weekday()]})
        except: self._update_skin_props_batch({"dsWeekDay": ""})

    def _process_ratings(self, imdb_id, session_id, item_id):
        if not self._is_session_valid(session_id, item_id): return
        data = {}
        try: data = mdblist_api.get_ratings(imdb_id) or {}
        except: pass
        if self._is_session_valid(session_id, item_id):
            lb_rating = ""
            if data.get("letterboxd_rating"):
                try: lb_rating = str(float(data.get("letterboxd_rating")) * 2)
                except: pass
            tr_rating = ""
            if data.get("trakt_rating"):
                try: 
                    v = float(data.get("trakt_rating"))
                    tr_rating = "{:.1f}".format(v / 10 if v > 10 else v)
                except: pass
            self._update_skin_props_batch({
                "ds_info_imdb_rating": data.get("imdb_rating", ""),
                "ds_info_letterboxd_rating": lb_rating,
                "ds_info_trakt_rating": tr_rating,
                "ds_info_imdb_votes": data.get("imdb_votes", "")
            })

    def _process_reviews(self, imdb_id, media_type, session_id, item_id):
        if not self._is_session_valid(session_id, item_id): return
        try:
            reviews = trakt_api.get_reviews_by_imdb_id(imdb_id, media_type) or ""
            if self._is_session_valid(session_id, item_id):
                self._update_skin_props_batch({"Trakt.Reviews": reviews})
        except: 
            if self._is_session_valid(session_id, item_id):
                self._update_skin_props_batch({"Trakt.Reviews": ""})

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
        def _isolated_play():
            try:
                if self._is_long_playback(): 
                    self.is_loading_trailer = False
                    return
                with self._player_action_lock:
                    if not self._is_session_valid(session_id, target_item_id):
                        self.is_loading_trailer = False
                        return
                    self._start_sniper_task(target_item_id, session_id)
                    listitem = xbmcgui.ListItem(label=f"TrailerPreview_{target_item_id}")
                    listitem.setPath(trailer_url)
                    self.player.stop()
                    time.sleep(0.05)
                    self.trailer_ativo = True
                    self.player.play(trailer_url, listitem, windowed=True)
            except:
                self.is_loading_trailer = False
                self.trailer_ativo = False

        if self._is_session_valid(session_id, target_item_id):
            self._update_skin_props_batch({"ds_is_trailer_playing": "true"})
            with self._global_lock: self.trailer_state = TrailerState.PLAYING_AUTO
            _isolated_play()
        else:
            self.is_loading_trailer = False

    def _handle_auto_trailer_logic(self, item_id, media_type, session_id, current_delay):
        if xbmc.getCondVisibility("Window.IsVisible(12005)") or not self._get_settings(): return
        if (xbmc.getCondVisibility("Window.IsVisible(progress_media.xml) | Window.IsActive(10103) | Window.IsActive(10151)")) and not self.is_loading_trailer: return
        if not self._is_session_valid(session_id, item_id): return
        elapsed = time.time() - self.focus_start_time
        if elapsed > current_delay:
            with self._state_lock:
                ready, played = self.trailer_url_ready, self.trailer_played_for_session
            if ready and not played and not self.is_loading_trailer:
                self.is_loading_trailer = True
                self.trailer_ativo = True
                self.play_task = self.submit_safe(self.playback_executor, self._play_trailer_worker, ready, item_id, session_id)

    def run(self):
        try:
            while not self.abortRequested():
                home, loading, fullscreen, paused = self._get_window_state()
                is_long_playing = self._is_long_playback()
                
                if loading and not self.is_loading_trailer:
                    self.focus_start_time = time.time()
                    self.trailer_lockout_time = time.time() + 30.0
                    self._reset_trailer_state()

                if self.player.isPlayingVideo() and not paused and not self._is_trailer_playing():
                    sleep_time = FineTuning.LOOP_IDLE
                    item_id = xbmc.getInfoLabel("Window(10000).Property(ds_info_tmdb_id)") or xbmc.getInfoLabel("ListItem.IMDBNumber")
                    if item_id and item_id != self.prev_tmdb_id:
                        new_session = self._generate_session_id()
                        with self._state_lock:
                            self.focused_item_id, self.focused_session_id, self.prev_tmdb_id = item_id, new_session, item_id
                        media_type = self._get_media_type()
                        t = threading.Thread(target=self._start_metadata_chain, args=(item_id, media_type, new_session, 0))
                        t.daemon = True
                        t.start()
                    self.waitForAbort(sleep_time)
                    continue

                is_idle = fullscreen or (is_long_playing and not home)
                sleep_time = FineTuning.LOOP_IDLE if is_idle else FineTuning.LOOP_NORMAL
                
                if fullscreen:
                    self.waitForAbort(sleep_time)
                    continue
                
                if xbmc.getCondVisibility("Window.IsVisible(10106) | Window.IsVisible(DialogContextMenu.xml) | Control.HasFocus(9000)"):
                    self._sniper_active_flag = False
                    new_session = self._generate_session_id()
                    with self._state_lock:  
                        self.focused_item_id, self.focused_session_id = None, new_session
                        self.prev_tmdb_id = None
                    self._clear_all_properties_on_thread()
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
                
                item_id = xbmc.getInfoLabel("Window(10000).Property(ds_info_tmdb_id)") or xbmc.getInfoLabel("ListItem.IMDBNumber")
                if not item_id: 
                    self.waitForAbort(sleep_time)
                    continue

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
                    
                    with self._state_lock:
                        if is_fast_scroll: self.focused_item_id = None
                        self.prev_tmdb_id = item_id
                        self.focused_item_id = item_id
                        self.focused_session_id = new_session
                        self.focus_start_time = now
                    
                    if is_fast_scroll: 
                        self._clear_all_properties_on_thread()

                    self.waitForAbort(FineTuning.RATINGS_REVIEW_DEBOUNCE)
                    if self.abortRequested(): break
                    
                    media_type = self._get_media_type()
                    t = threading.Thread(target=self._start_metadata_chain, args=(item_id, media_type, new_session, self.last_delta_t))
                    t.daemon = True
                    t.start()
                    self._stop_current_sniper()
                
                else:
                    elapsed = time.time() - self.focus_start_time
                    current_session = self.focused_session_id
                    if self.last_delta_t < FineTuning.DB_FAST_THRESHOLD: adaptive_trailer_delay = FineTuning.TRAILER_DELAY_FAST
                    elif self.last_delta_t < FineTuning.DB_MED_THRESHOLD: adaptive_trailer_delay = FineTuning.TRAILER_DELAY_MED
                    else: adaptive_trailer_delay = self._get_slow_delay()

                    with self._global_lock:
                        if self.trailer_state == TrailerState.PLAYING_AUTO and (not self.player.isPlayingVideo() or paused) and not self.is_loading_trailer:
                            self.trailer_state = TrailerState.IDLE
                            self.trailer_ativo = False
                            self._update_skin_props_batch({"ds_is_trailer_playing": ""})
                    
                    if not is_long_playing and time.time() > self.trailer_lockout_time: 
                        if elapsed > (adaptive_trailer_delay - 1.0) and not self.is_fetching_trailer and not self.trailer_url_ready and not self.trailer_played_for_session:
                            with self._state_lock: self.is_fetching_trailer = True
                            self.fetch_task = self.submit_safe(self.trailer_executor, self._resolve_item_metadata, item_id, self._get_media_type(), current_session)
                        if elapsed > adaptive_trailer_delay: 
                            self._handle_auto_trailer_logic(item_id, self._get_media_type(), current_session, adaptive_trailer_delay)
                self.waitForAbort(sleep_time)
        finally:
            self._monitor_active = False
            self.pov_checker.stop()
            self._safe_stop()
            for ex in [self.trailer_executor, self.playback_executor, self.sniper_executor]: ex.shutdown(wait=False)

if __name__ == "__main__":
    ShowImdbService().run()
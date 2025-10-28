# -*- coding: utf-8 -*-
import xbmc
import xbmcgui
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import tmdb_api
import mdblist_api
import trakt_api


class ShowImdbService(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.prev_tmdb_id = None
        # Executor para a tarefa principal de busca
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.focused_tmdb_id = None
        self.current_task = None
        self.active_list_id = None  # Para rastrear o widget com foco
        self._state_lock = threading.RLock()  # Para evitar condições de corrida

    def _process_item(self, item_id, media_type_from_run):
        """
        Busca todas as informações (exceto trailer) para o item focado.
        """
        # Verifica se o item ainda está em foco
        if self.focused_tmdb_id != item_id or not xbmc.getCondVisibility(
            "Window.IsVisible(home)"
        ):
            return

        imdb_id = ""
        tmdb_id = ""
        media_type = media_type_from_run

        # --- 1. Obter IDs (IMDb e TMDb) ---
        try:
            if item_id.startswith("tt"):
                imdb_id = item_id
                found_tmdb_id, found_media_type = tmdb_api.fetch_tmdb_id_from_imdb(
                    imdb_id
                )
                if found_tmdb_id:
                    tmdb_id = found_tmdb_id
                    media_type = found_media_type
            else:
                tmdb_id = item_id
                imdb_id = tmdb_api.fetch_imdb_id(tmdb_id, media_type)
        except Exception as e:
            xbmc.log(f"ShowIMDb: Falha crítica ao buscar IDs: {e}", xbmc.LOGERROR)

        # Verifica novamente se o item mudou enquanto buscava IDs
        if self.focused_tmdb_id != item_id or not xbmc.getCondVisibility(
            "Window.IsVisible(home)"
        ):
            return

        # Se não encontrou IDs, limpa as propriedades
        if not imdb_id or not tmdb_id:
            xbmc.log(f"ShowIMDb: IDs não encontrados para {item_id}", xbmc.LOGINFO)
            self._clear_all_properties_on_thread()
            return

        # --- 2. Buscar Ratings e Reviews em Paralelo ---
        ratings_data = {}
        reviews = ""
        # Executor interno para buscar dados simultaneamente
        with ThreadPoolExecutor(max_workers=2) as inner_executor:
            future_ratings = inner_executor.submit(mdblist_api.get_ratings, imdb_id)
            future_reviews = inner_executor.submit(
                trakt_api.get_reviews_by_imdb_id, imdb_id, media_type
            )

            try:
                ratings_data = future_ratings.result() or {}
            except Exception as e:
                xbmc.log(f"ShowIMDb: Erro ratings paralelo: {e}", xbmc.LOGERROR)
            try:
                reviews = future_reviews.result() or ""
            except Exception as e:
                xbmc.log(f"ShowIMDb: Erro reviews paralelo: {e}", xbmc.LOGERROR)

        # --- 3. Definir Propriedades (se o item ainda estiver em foco) ---
        if self.focused_tmdb_id == item_id and xbmc.getCondVisibility(
            "Window.IsVisible(home)"
        ):
            win = xbmcgui.Window(10000)
            win.setProperty(
                "ds_info_imdb_rating", ratings_data.get("imdb_rating", "")
            )
            win.setProperty(
                "ds_info_letterboxd_rating",
                str(float(ratings_data.get("letterboxd_rating", "0")) * 2)
                if ratings_data.get("letterboxd_rating")
                else "",
            )
            win.setProperty(
                "ds_info_trakt_rating",
                str(float(ratings_data.get("trakt_rating", "0")) / 10)
                if ratings_data.get("trakt_rating")
                else "",
            )
            win.setProperty("Trakt.Reviews", reviews)
            win.clearProperty("ds_info_imdb_votes") # Limpa esta (não é mais buscada)

    def _clear_all_properties_on_thread(self):
        """Limpa todas as properties de notas e reviews."""
        win = xbmcgui.Window(10000)
        
        # Guarda o widget atual com foco antes de limpar
        if not win.getProperty("ds_active_list_id") and self.active_list_id:
            win.setProperty("ds_active_list_id", str(self.active_list_id))

        win.clearProperty("ds_info_imdb_rating")
        win.clearProperty("ds_info_imdb_votes")
        win.clearProperty("ds_info_letterboxd_rating")
        win.clearProperty("ds_info_trakt_rating")
        win.clearProperty("Trakt.Reviews")

    def run(self):
        """Loop principal do service"""
        xbmc.log("ShowIMDb: Service (Ratings/Reviews Only) iniciado", xbmc.LOGINFO)
        
        while not self.abortRequested():
            try:
                # Se estiver em tela cheia, não faz nada
                if xbmc.getCondVisibility("Window.IsVisible(videofullscreen)"):
                    self.waitForAbort(1)
                    continue

                # Se sair da Home, reseta o ID
                if not xbmc.getCondVisibility("Window.IsVisible(home)"):
                    with self._state_lock:
                        if self.prev_tmdb_id:
                            self.prev_tmdb_id = None
                            self.focused_tmdb_id = None
                    self.waitForAbort(0.5)
                    continue
                
                # Se a janela de info estiver aberta, não faz nada
                if xbmc.getCondVisibility("Window.IsActive(dialogvideoinfo)"):
                    self.waitForAbort(0.5)
                    continue

                win = xbmcgui.Window(10000)

                # --- Leitura do Item ID ---
                item_id = xbmc.getInfoLabel("Window(home).Property(ds_info_tmdb_id)")
                if not item_id:
                    item_id = xbmc.getInfoLabel("ListItem.IMDBNumber")

                # Detecta qual widget tem o foco (apenas IDs entre 50000 e 60000)
                # (Mantido caso a skin use 'ds_active_list_id' para algo)
                focused_id = xbmc.getInfoLabel("System.CurrentControlID")
                try:
                    focused_id_int = int(focused_id)
                    if 50000 <= focused_id_int <= 60000:
                        self.active_list_id = focused_id_int
                        win.setProperty("ds_active_list_id", str(focused_id_int))
                except:
                    pass

                # --- Lógica de Mudança de Foco ---
                with self._state_lock:
                    current_prev_tmdb_id = self.prev_tmdb_id

                # 1. NOVO ITEM GANHOU FOCO
                if item_id and item_id != current_prev_tmdb_id:
                    # Define o novo item focado
                    with self._state_lock:
                        self.prev_tmdb_id = item_id
                        self.focused_tmdb_id = item_id

                    # Cancela a tarefa anterior (se houver)
                    if self.current_task and not self.current_task.done():
                        self.current_task.cancel()

                    # Determina o tipo de mídia
                    dbtype = xbmc.getInfoLabel("Window(home).Property(ds_info_dbtype)")
                    media_type_from_run = (
                        "tv"
                        if dbtype and dbtype.lower() in ("tv", "tvshow", "episode", "season")
                        else "movie"
                    )
                    
                    # Inicia a nova tarefa de busca
                    self.current_task = self.executor.submit(
                        self._process_item, item_id, media_type_from_run
                    )

                # 2. FOCO ESTÁ VAZIO (NENHUM ITEM SELECIONADO)
                elif not item_id and current_prev_tmdb_id:
                    # Reseta o estado
                    with self._state_lock:
                        self.prev_tmdb_id = None
                        self.focused_tmdb_id = None
                    
                    # Limpa as propriedades da tela
                    self._clear_all_properties_on_thread()

            except Exception as e:
                xbmc.log(f"ShowIMDb: Erro no loop principal: {e}", xbmc.LOGERROR)

            # Espera curta antes de verificar novamente
            self.waitForAbort(0.1)

        # Encerra o executor ao sair
        self.executor.shutdown(wait=False)
        xbmc.log("ShowIMDb: Service parado", xbmc.LOGINFO)


if __name__ == "__main__":
    ShowImdbService().run()
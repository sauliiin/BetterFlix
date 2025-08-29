# -*- coding: utf-8 -*-
# ARQUIVO: service.py (Versão final com ambas as conversões de nota)
import xbmc
import xbmcgui
import time
from concurrent.futures import ThreadPoolExecutor

# Importa os módulos de API, que são nossos "trabalhadores"
import tmdb_api
import mdblist_api
import trakt_api

class ShowImdbService(xbmc.Monitor):
    def __init__(self):
        super(ShowImdbService, self).__init__()
        self.prev_tmdb_id = None
        # max_workers=1 aqui é intencional para criar uma fila e evitar condições de corrida
        self.executor = ThreadPoolExecutor(max_workers=1)
        xbmc.log('ShowIMDb: Serviço Final Otimizado Iniciado', xbmc.LOGINFO)

    def _process_item(self, tmdb_id, media_type):
        """Orquestra a busca de todos os dados de forma paralela."""
        xbmc.log(f'ShowIMDb [Thread]: Processando TMDB ID: {tmdb_id}', xbmc.LOGINFO)

        # Etapa 1: Obter o IMDb ID.
        imdb_id = tmdb_api.fetch_imdb_id(tmdb_id, media_type)
        if not imdb_id:
            self._clear_all_properties_on_thread()
            return
        
        # Etapa 2: Executar as buscas de dados restantes em paralelo
        ratings_data = {}
        reviews = ''
        with ThreadPoolExecutor(max_workers=2) as inner_executor:
            future_ratings = inner_executor.submit(mdblist_api.get_ratings, imdb_id)
            future_reviews = inner_executor.submit(trakt_api.get_reviews_by_imdb_id, imdb_id, media_type)

            try: ratings_data = future_ratings.result() or {}
            except Exception as e: xbmc.log(f'ShowIMDb [Thread]: Erro no future do MDLList: {e}', xbmc.LOGERROR)
            
            try: reviews = future_reviews.result() or ''
            except Exception as e: xbmc.log(f'ShowIMDb [Thread]: Erro no future do Trakt: {e}', xbmc.LOGERROR)

        # Etapa 3: Formatar as notas recebidas
        
        # Conversão da nota do Letterboxd (0-5 para 0-10)
        letterboxd_rating_raw = ratings_data.get('letterboxd_rating', '')
        letterboxd_rating_scaled = ''
        if letterboxd_rating_raw:
            try:
                scaled_rating = float(letterboxd_rating_raw) * 2
                letterboxd_rating_scaled = f"{scaled_rating:.1f}"
            except (ValueError, TypeError):
                letterboxd_rating_scaled = ''

        # --- MUDANÇA AQUI: CONVERSÃO DA NOTA DO TRAKT (0-10 para 0-1.0) ---
        trakt_rating_raw = ratings_data.get('trakt_rating', '')
        trakt_rating_scaled = ''
        if trakt_rating_raw:
            try:
                # Converte para número, divide por 10, e formata para 2 casas decimais
                scaled_rating = float(trakt_rating_raw) / 10
                trakt_rating_scaled = f"{scaled_rating:.1f}"
            except (ValueError, TypeError):
                trakt_rating_scaled = ''
        # --- FIM DA MUDANÇA ---

        # Etapa 4: Definir as propriedades para a skin
        win = xbmcgui.Window(10000)
        win.setProperty('ds_info_imdb_rating', ratings_data.get('imdb_rating', ''))
        win.setProperty('ds_info_letterboxd_rating', letterboxd_rating_scaled)
        # Usa a nova variável com a nota do Trakt convertida
        win.setProperty('ds_info_trakt_rating', trakt_rating_scaled)
        win.setProperty('Trakt.Reviews', reviews)
        win.clearProperty('ds_info_imdb_votes') # Limpando propriedade obsoleta
        
        xbmc.log(f'ShowIMDb [Thread]: Propriedades atualizadas para {tmdb_id}', xbmc.LOGINFO)

    def _clear_all_properties_on_thread(self):
        """Limpa todas as propriedades relevantes."""
        win = xbmcgui.Window(10000)
        win.clearProperty('ds_info_imdb_rating')
        win.clearProperty('ds_info_imdb_votes')
        win.clearProperty('ds_info_letterboxd_rating')
        win.clearProperty('ds_info_trakt_rating')
        win.clearProperty('Trakt.Reviews')
        xbmc.log('ShowIMDb: Propriedades limpas.', xbmc.LOGINFO)

    def run(self):
        """Loop principal do serviço."""
        while not self.abortRequested():
            tmdb_id = xbmc.getInfoLabel('Window(home).Property(ds_info_tmdb_id)')
            if tmdb_id and tmdb_id != self.prev_tmdb_id:
                self.prev_tmdb_id = tmdb_id
                dbtype = xbmc.getInfoLabel('Window(home).Property(ds_info_dbtype)')
                media_type = 'tv' if dbtype and dbtype.lower() in ('tv', 'tvshow') else 'movie'
                self.executor.submit(self._process_item, tmdb_id, media_type)
            elif not tmdb_id and self.prev_tmdb_id:
                self.prev_tmdb_id = None
                self._clear_all_properties_on_thread()
            
            self.waitForAbort(0.25)
        self.executor.shutdown(wait=False)
        xbmc.log('ShowIMDb: Serviço Parado', xbmc.LOGINFO)

if __name__ == '__main__':
    service = ShowImdbService()
    service.run()
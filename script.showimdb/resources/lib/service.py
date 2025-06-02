# -*- coding: utf-8 -*-
import xbmc
import xbmcgui
import time
import tmdb_api
import trakt_api
import omdb_api  # <-- Importa o novo módulo

class ShowImdbService(xbmc.Monitor):
    def __init__(self):
        self.prev_tmdb_id = ''
        xbmc.log('ShowIMDb: Serviço Completo Iniciado', xbmc.LOGINFO)

    def run(self):
        while not self.abortRequested():
            tmdb_id = xbmc.getInfoLabel('Window(home).Property(ds_info_tmdb_id)')

            if tmdb_id and tmdb_id != self.prev_tmdb_id:
                self.prev_tmdb_id = tmdb_id
                dbtype = xbmc.getInfoLabel('Window(home).Property(ds_info_dbtype)')
                media_type = 'tv' if dbtype and dbtype.lower() == 'tv' else 'movie'
                
                xbmc.log(f'ShowIMDb: Foco em TMDB ID: {tmdb_id}, Tipo: {media_type}', xbmc.LOGINFO)
                
                # Etapa 1: Buscar IMDb ID (com cache)
                imdb_id = tmdb_api.fetch_imdb_id(tmdb_id, media_type)

                if imdb_id:
                    ## --- LÓGICA REINTEGRADA E OTIMIZADA --- ##
                    # 1. Define a propriedade do IMDb ID
                    xbmcgui.Window(10000).setProperty('ds_info_imdb_id', imdb_id)

                    # 2. Busca a nota no OMDb (com cache) e define a propriedade
                    rating = omdb_api.get_rating_by_imdb_id(imdb_id)
                    xbmcgui.Window(10000).setProperty('ds_info_imdb_rating', rating)
                    xbmc.log(f'ShowIMDb: Nota OMDb para {imdb_id} é {rating}.', xbmc.LOGINFO)

                    # 3. Busca os reviews no Trakt (com cache) e define a propriedade
                    reviews = trakt_api.get_reviews_by_imdb_id(imdb_id, media_type)
                    xbmcgui.Window(10000).setProperty('Trakt.Reviews', reviews)
                    xbmc.log(f'ShowIMDb: Reviews para {imdb_id} atualizados.', xbmc.LOGINFO)
                    ## ----------------------------------------- ##
                else:
                    self._clear_all_properties()

            elif not tmdb_id and self.prev_tmdb_id:
                self._clear_all_properties()

            time.sleep(1)

    def _clear_all_properties(self):
        """Função auxiliar para limpar todas as propriedades de uma vez."""
        if not self.prev_tmdb_id: return
        
        self.prev_tmdb_id = ''
        win = xbmcgui.Window(10000)
        win.clearProperty('ds_info_imdb_id')
        win.clearProperty('ds_info_imdb_rating')
        win.clearProperty('Trakt.Reviews')
        xbmc.log('ShowIMDb: Foco perdido, todas as propriedades foram limpas.', xbmc.LOGINFO)

    def abortRequested(self):
        return super(ShowImdbService, self).abortRequested()

if __name__ == '__main__':
    service = ShowImdbService()
    service.run()
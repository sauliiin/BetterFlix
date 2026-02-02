# -*- coding: utf-8 -*-
# play_trailer_script.py
# Script para acionamento MANUAL do trailer (botão Trailer)

import xbmc
import xbmcgui
import sys
import urllib.parse
import traceback
import tmdb_api

def get_params():
    param_string = ""
    parsed_params = {}
    if len(sys.argv) >= 2 and sys.argv[1].startswith('?'): param_string = sys.argv[1][1:]
    if param_string:
        try: parsed_params = dict(urllib.parse.parse_qsl(param_string))
        except: pass
    return parsed_params

if __name__ == '__main__':
    # Se o usuário clicar manualmente, assumimos o controle
    xbmc.log("PlayTrailerScript: Manual Trigger.", xbmc.LOGINFO)
    
    # Definir prop para skin mostrar loading
    win = xbmcgui.Window(10000)
    win.setProperty("ds_ondemand_trailer_loading", "true")
    
    params = get_params()
    item_id = params.get('id')
    db_type = params.get('dbtype', 'movie')
    
    if not item_id:
        win.clearProperty("ds_ondemand_trailer_loading")
        sys.exit()

    try:
        # Lógica de busca (reutiliza API otimizada)
        media_type = "tv" if db_type.lower() in ("tv", "tvshow", "episode", "season") else "movie"
        
        # Resolver ID
        final_tmdb = item_id
        if item_id.startswith("tt"):
            t_id, m_type = tmdb_api.fetch_tmdb_id_from_imdb(item_id)
            if t_id: final_tmdb = t_id; media_type = m_type
        
        url = tmdb_api.fetch_trailer_url(final_tmdb, media_type)
        
        win.clearProperty("ds_ondemand_trailer_loading")

        if url:
            player = xbmc.Player()
            listitem = xbmcgui.ListItem(path=url)
            listitem.setInfo("video", {"title": "Trailer"})
            
            # Se já estiver tocando algo (do auto-play), paramos antes de iniciar o manual
            if player.isPlaying():
                player.stop()
                
            # Toca Windowed para respeitar o XML
            player.play(url, listitem, windowed=True)
            
            win.setProperty("ds_ondemand_trailer_playing", "true")
        else:
            xbmcgui.Dialog().notification("Trailer", "Não encontrado", xbmcgui.NOTIFICATION_INFO, 3000)

    except Exception as e:
        win.clearProperty("ds_ondemand_trailer_loading")
        xbmc.log(f"PlayTrailerScript Error: {e}", xbmc.LOGERROR)
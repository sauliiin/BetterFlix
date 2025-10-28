# -*- coding: utf-8 -*-
# play_trailer_script.py

import xbmc
import xbmcgui
import sys
import time
import os
import urllib.parse
import traceback

import tmdb_api

# Nomes das propriedades (Apenas Loading e Playing)
LOADING_PROP = "ds_ondemand_trailer_loading"
PLAYING_PROP = "ds_ondemand_trailer_playing"
# Propriedades adicionadas (para supressão do busy dialog e preservação do foco)
SUPPRESS_BUSY_PROP = "ds_suppress_busy"
ACTIVE_LIST_PROP = "ds_active_list_id"

# Constantes
PLAYER_WAIT_TIMEOUT = 12.0
PLAYER_CHECK_INTERVAL = 0.2

# --- Funções Auxiliares Simplificadas ---
def preserve_active_list(win):
    """
    Tenta preservar (registrar) o widget/control atual com foco em ds_active_list_id
    usando System.CurrentControlID quando aplicável (similar ao outro código).
    """
    try:
        # Se já existe, não sobrescreve
        if win.getProperty(ACTIVE_LIST_PROP):
            return
        current_control = xbmc.getInfoLabel("System.CurrentControlID")
        if current_control and current_control.isdigit():
            cid = int(current_control)
            # faixa usada no outro código para widgets relevantes (só define se parecer um control ID de lista)
            if 50000 <= cid <= 60000:
                win.setProperty(ACTIVE_LIST_PROP, str(cid))
    except Exception:
        pass

def set_suppress_busy(win):
    """
    Define a property que indica suprimir o busy dialog e fecha qualquer busydialog aberto.
    """
    try:
        preserve_active_list(win)
        # marcar loading para evitar piscadas (mantendo similaridade)
        # Note: não mexemos no LOADING_PROP aqui — a chamada original já faz set_loading
        win.setProperty(SUPPRESS_BUSY_PROP, "true")
        # Fecha se estiver aberto
        try:
            if xbmc.getCondVisibility("Window.IsActive(busydialog)"):
                xbmc.executebuiltin("Dialog.Close(busydialog)")
        except Exception:
            pass
    except Exception:
        pass

def clear_suppress_busy(win):
    """Remover a flag de supressão do busy dialog."""
    try:
        win.clearProperty(SUPPRESS_BUSY_PROP)
    except Exception:
        pass

def clear_props(win):
    """Limpa as propriedades LOADING, PLAYING e também SUPPRESS_BUSY."""
    try:
        win.clearProperty(LOADING_PROP)
        win.clearProperty(PLAYING_PROP)
        # limpar a flag de supressão para não deixá-la presa após sair
        win.clearProperty(SUPPRESS_BUSY_PROP)
        # xbmc.log("PlayTrailerScript: Props limpas.", xbmc.LOGDEBUG) # Log opcional
    except Exception:
        pass

def set_loading(win):
    """Define o estado de 'carregamento' e ativa a supressão do busy dialog."""
    try:
        clear_props(win)
        preserve_active_list(win)
        win.setProperty(LOADING_PROP, "true")
        # ativa a supressão do busy dialog (parte copiada/adaptada)
        win.setProperty(SUPPRESS_BUSY_PROP, "true")
        # fechar qualquer busydialog aberto imediatamente
        try:
            if xbmc.getCondVisibility("Window.IsActive(busydialog)"):
                xbmc.executebuiltin("Dialog.Close(busydialog)")
        except Exception:
            pass
        xbmc.log("PlayTrailerScript: Estado -> LOADING (suppress busy set)", xbmc.LOGDEBUG)
    except Exception:
        pass

def set_playing(win):
    """Define o estado de 'a tocar'."""
    try:
        win.clearProperty(LOADING_PROP)
        # Mantemos a supressão definida até a limpeza (clear_props) para evitar piscadas
        win.setProperty(PLAYING_PROP, "true")
        xbmc.log("PlayTrailerScript: Estado -> PLAYING", xbmc.LOGDEBUG)
    except Exception:
        pass

# Função close_busy_dialog REMOVIDA (comportamento incorporado nas funções acima)

# Função get_params (lê args de RunScript - igual v1.9 / v1.4.2)
def get_params():
    param_string = ""; parsed_params = {}
    xbmc.log(f"PlayTrailerScript: DEBUG - sys.argv: {sys.argv}", xbmc.LOGINFO)
    if len(sys.argv) >= 2 and sys.argv[1].startswith('?'): param_string = sys.argv[1][1:]
    else: xbmc.log(f"PlayTrailerScript: DEBUG - Query string (?) não encontrada.", xbmc.LOGWARNING)
    if param_string:
        try: parsed_params = dict(urllib.parse.parse_qsl(param_string))
        except Exception: pass
    xbmc.log(f"PlayTrailerScript: DEBUG - Parsed params: {parsed_params}", xbmc.LOGDEBUG)
    return parsed_params
# --- Fim Funções Auxiliares ---

# --- Lógica Principal ---
if __name__ == '__main__':
    xbmc.log("PlayTrailerScript: Iniciado (v2.1 - Com Supressão Busy opcional).", xbmc.LOGINFO)

    player = xbmc.Player()
    win = xbmcgui.Window(10000)
    clear_props(win) # Limpeza inicial

    params = get_params()
    action = params.get('action')

    if action != 'play_trailer':
        xbmc.log(f"Ação '{action}' != 'play_trailer'. Saindo.", xbmc.LOGINFO); sys.exit()

    item_id_to_process = params.get('id')
    db_type_arg = params.get('dbtype')

    # Validação de ID/Tipo (igual v1.9)
    try:
        if not item_id_to_process or not (item_id_to_process.isdigit() or item_id_to_process.startswith("tt")):
             raise ValueError(f"Argumento 'id' recebido ('{item_id_to_process}') inválido ou vazio.")
        if not db_type_arg: db_type_arg = "movie"; xbmc.log(f"DBType não recebido, usando default '{db_type_arg}'", xbmc.LOGDEBUG)
        xbmc.log(f"ID a processar='{item_id_to_process}', DBType='{db_type_arg}'", xbmc.LOGINFO)
    except Exception as e_args:
        xbmc.log(f"Erro validar args: {e_args}", xbmc.LOGERROR); xbmcgui.Dialog().notification("Erro Trailer", f"Falha ID args", xbmcgui.NOTIFICATION_ERROR, 3000); sys.exit()

    set_loading(win) # Define LOADING (agora também seta ds_suppress_busy e preserva foco)

    # Processar ID e Buscar URL (igual v1.9)
    media_type = "tv" if db_type_arg and db_type_arg.lower() in ("tv", "tvshow", "episode", "season") else "movie"
    tmdb_id_final = item_id_to_process; conversion_error = False
    try:
        if item_id_to_process.startswith("tt"):
            found_tmdb_id, found_media_type = tmdb_api.fetch_tmdb_id_from_imdb(item_id_to_process)
            if found_tmdb_id: tmdb_id_final = found_tmdb_id; media_type = found_media_type if found_media_type else media_type
            else: xbmcgui.Dialog().notification("Trailer", "ID não encontrado.", xbmcgui.NOTIFICATION_WARNING, 3000); conversion_error = True
    except Exception as e_conv: xbmcgui.Dialog().notification("Erro Trailer", "Falha processar ID.", xbmcgui.NOTIFICATION_ERROR, 3000); conversion_error = True
    if conversion_error: clear_props(win); sys.exit()
    trailer_url = None
    if tmdb_id_final and tmdb_api:
        try: trailer_url = tmdb_api.fetch_trailer_url(tmdb_id_final, media_type)
        except Exception as e_fetch: xbmc.log(f"Erro buscar URL: {e_fetch}", xbmc.LOGERROR)
    if not trailer_url: xbmcgui.Dialog().notification("Trailer", "Não encontrado.", xbmcgui.NOTIFICATION_INFO, 3000); clear_props(win); sys.exit()
    xbmc.log(f"URL: {trailer_url}", xbmc.LOGINFO)

    # Parar trailer anterior (igual v1.9)
    try:
        if player.isPlayingVideo() and win.getProperty(PLAYING_PROP) == "true": player.stop(); clear_props(win); set_loading(win); time.sleep(0.3)
    except Exception: pass

    # Iniciar Novo Trailer (sem supressão ativa originalmente — agora com supressão do busy)
    player_started = False
    try:
        listitem = xbmcgui.ListItem(path=trailer_url); listitem.setInfo("video", {"title": f"Trailer On Demand"})

        # Pausa antes de play REMOVIDA
        # close_busy_dialog() REMOVIDA

        # --- Definir supressão e preservar foco antes do play (adaptação do outro código) ---
        preserve_active_list(win)
        win.setProperty(SUPPRESS_BUSY_PROP, "true")
        try:
            if xbmc.getCondVisibility("Window.IsActive(busydialog)"):
                xbmc.executebuiltin("Dialog.Close(busydialog)")
        except Exception:
            pass
        # -------------------------------------------------------------------------

        player.play(trailer_url, listitem, windowed=True); xbmc.executebuiltin("PlayerControl(RepeatOff)", True)

        # Espera Player Iniciar (ainda necessário para a skin)
        start_wait_time = time.time()
        while time.time() - start_wait_time < PLAYER_WAIT_TIMEOUT:
            # durante a espera, certifica-se de fechar o busydialog caso ele reapareça
            try:
                if xbmc.getCondVisibility("Window.IsActive(busydialog)"):
                    xbmc.executebuiltin("Dialog.Close(busydialog)")
            except Exception:
                pass

            if player.isPlaying():
                set_playing(win); # Define a propriedade para a skin mostrar o vídeo
                player_started = True;
                break
            time.sleep(PLAYER_CHECK_INTERVAL)

        if not player_started:
            xbmcgui.Dialog().notification("Trailer", "Player demorou.", xbmcgui.NOTIFICATION_WARNING, 3000); 
            # Garante limpeza (inclui remoção do SUPPRESS_BUSY_PROP)
            clear_props(win);
            try: player.stop()
            except: pass; sys.exit()

    except Exception as e_play:
         xbmc.log(f"Erro CRÍTICO ao iniciar player: {e_play}", xbmc.LOGERROR); xbmc.log(traceback.format_exc(), xbmc.LOGERROR); 
         # Limpeza incluindo supressão
         clear_props(win); 
         xbmcgui.Dialog().notification("Erro Trailer", "Falha ao iniciar.", xbmcgui.NOTIFICATION_ERROR, 3000); sys.exit(1)

    xbmc.log("PlayTrailerScript: Finalizado com sucesso.", xbmc.LOGINFO)

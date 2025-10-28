# -*- coding: utf-8 -*-
import os
import shutil
import xbmc
import xbmcgui
import traceback
import xbmcvfs

ADDON_ID = "script.showimdb"

def xbmc_log(message, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {message}", level)

def get_cache_path():
    """Retorna o caminho da pasta de cache do addon"""
    return xbmcvfs.translatePath(f"special://profile/addon_data/{ADDON_ID}/cache")

def clear_cache():
    cache_path = get_cache_path()
    dialog = xbmcgui.Dialog()

    if not os.path.exists(cache_path):
        os.makedirs(cache_path)
        xbmc_log("Cache folder did not exist. Created new cache folder.")
        xbmcgui.Dialog().notification("MDBList", "Cache folder created!", xbmcgui.NOTIFICATION_INFO, 3000)
        return

    # Pergunta de confirmação
    if not dialog.yesno("Clear Cache", "Do you really want to clear all cached data?"):
        xbmc_log("User cancelled cache clear.")
        return

    try:
        removed_any = False
        for entry in os.listdir(cache_path):
            full = os.path.join(cache_path, entry)
            try:
                if os.path.isdir(full):
                    shutil.rmtree(full)
                else:
                    os.remove(full)
                removed_any = True
            except Exception as e:
                xbmc_log(f"Failed to remove {full}: {e}", xbmc.LOGERROR)

        if removed_any:
            xbmc_log("Cache cleared successfully.")
            xbmcgui.Dialog().notification("MDBList", "Cache cleared successfully!", xbmcgui.NOTIFICATION_INFO, 3000)
        else:
            xbmc_log("Cache folder was already empty.")
            xbmcgui.Dialog().notification("MDBList", "Cache was already empty.", xbmcgui.NOTIFICATION_INFO, 3000)

    except Exception:
        err = traceback.format_exc()
        xbmc_log(f"Error clearing cache: {err}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("MDBList", "Error clearing cache. See kodi.log", xbmcgui.NOTIFICATION_ERROR, 4000)

if __name__ == "__main__":
    clear_cache()
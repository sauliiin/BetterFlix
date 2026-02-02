# -*- coding: utf-8 -*-
# ARQUIVO: mdblist_api.py
import xbmc
import requests
import time
import json
from database import db

# --- Configurações ---
API_KEY = addon.getSetting('api_key')
API_URL = 'https://mdblist.com/api/?apikey=%s&i=%s'
CACHE_MAX_AGE = 15 * 24 * 60 * 60

session = requests.Session()

def get_api_key():
    addon = xbmcaddon.Addon()
    api_key = addon.getSettingString('api_key') 
    return api_key.strip() if api_key else ''

def get_ratings(imdb_id):
    if not imdb_id: return {}

    API_KEY = get_api_key()
    
    if not API_KEY:
        xbmc.log('[MDBList] API Key não configurada!', xbmc.LOGWARNING)
        xbmcgui.Dialog().notification(
            'MDBList',
            'Configure a API Key nas configurações',
            xbmcgui.NOTIFICATION_WARNING,
            5000
        )
        return {}
    
    # 1. Leitura via DB Central
    result = db.fetch_one("SELECT data, timestamp FROM mdlist_data WHERE imdb_id = ?", (imdb_id,))
    if result and (time.time() - result[1] < CACHE_MAX_AGE):
        try:
            return json.loads(result[0])
        except:
            pass

    # 2. Rede
    data_to_cache = {}
    try:
        url = API_URL % (API_KEY, imdb_id)
        response = session.get(url, timeout=7)
        response.raise_for_status()
        json_data = response.json()
        
        for rating in json_data.get("ratings", []):
            source = rating.get("source")
            value = rating.get("value")
            if source == "imdb" and value is not None:
                data_to_cache["imdb_rating"] = str(value)
            elif source == "letterboxd" and value is not None:
                data_to_cache["letterboxd_rating"] = str(value)
            elif source == "trakt" and value is not None:
                data_to_cache["trakt_rating"] = str(value) 
                
    except Exception as e:
        xbmc.log(f"[MDbList_API] Erro: {e}", xbmc.LOGERROR)

    # 3. Escrita via DB Central
    # Só grava se tivermos encontrado algo ou para indicar cache vazio temporário
    db.execute_query(
        "INSERT OR REPLACE INTO mdlist_data VALUES (?, ?, ?)", 
        (imdb_id, json.dumps(data_to_cache), time.time())
    )
            
    return data_to_cache
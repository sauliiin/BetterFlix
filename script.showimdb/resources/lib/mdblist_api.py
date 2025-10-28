# -*- coding: utf-8 -*-
# ARQUIVO: mdblist_api.py
import xbmc
import requests
import sqlite3
import time
import os
import xbmcvfs
import json
import xbmcaddon

# --- Configurações ---
addon = xbmcaddon.Addon()
API_KEY = addon.getSetting('api_key')
API_URL = 'https://mdblist.com/api/?apikey=%s&i=%s'
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
if not xbmcvfs.exists(CACHE_DIR):
    xbmcvfs.mkdirs(CACHE_DIR)
DB_FILE = os.path.join(CACHE_DIR, 'addon_cache.db')
CACHE_MAX_AGE = 15 * 24 * 60 * 60

def _init_db():
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS mdlist_data (imdb_id TEXT PRIMARY KEY, data TEXT, timestamp REAL)')
        conn.commit()

_init_db()
session = requests.Session()

def get_ratings(imdb_id):
    """Busca dados no MDLList e extrai as notas do IMDb, Letterboxd e Trakt."""
    if not imdb_id: return {}
    
    # Otimização: Conecta apenas UMA VEZ
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    try:
        # 1. Tenta ler do cache
        cursor.execute("SELECT data, timestamp FROM mdlist_data WHERE imdb_id = ?", (imdb_id,))
        result = cursor.fetchone()
        if result and (time.time() - result[1] < CACHE_MAX_AGE):
            return json.loads(result[0])

        # 2. Cache Miss: Busca na API
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
                # Correção: Salva o valor bruto (porcentagem) para preservar a precisão
                elif source == "trakt" and value is not None:
                    data_to_cache["trakt_rating"] = str(value) 
                    
        except Exception as e:
            xbmc.log(f"[MDbList_API] Erro na chamada para {imdb_id}: {e}", xbmc.LOGERROR)

        # 3. Escreve no cache (usando a mesma conexão)
        conn.execute("INSERT OR REPLACE INTO mdlist_data VALUES (?, ?, ?)", (imdb_id, json.dumps(data_to_cache), time.time()))
        conn.commit() # Salva as mudanças
            
        return data_to_cache

    finally:
        # 4. Garante que a conexão seja sempre fechada
        conn.close()
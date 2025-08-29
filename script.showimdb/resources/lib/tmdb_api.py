# -*- coding: utf-8 -*-
# ARQUIVO: tmdb_api.py
import xbmc
import requests
import sqlite3
import time
import os
import xbmcvfs

# --- Configurações ---
API_KEY = '703cf5598b9fd74adac824baf7923126'
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
if not xbmcvfs.exists(CACHE_DIR):
    xbmcvfs.mkdirs(CACHE_DIR)
DB_FILE = os.path.join(CACHE_DIR, 'addon_cache.db')
CACHE_MAX_AGE = 90 * 24 * 60 * 60 # Cache longo, pois IDs não mudam

def _init_db():
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS tmdb_ids (cache_key TEXT PRIMARY KEY, imdb_id TEXT, timestamp REAL)')
        conn.commit()

_init_db()
session = requests.Session()

def fetch_imdb_id(tmdb_id, content_type):
    """Busca o IMDb ID a partir do TMDB ID, com cache."""
    media_type = 'tv' if content_type.lower() in ('tv', 'tvshow') else 'movie'
    cache_key = f"{media_type}_{tmdb_id}"
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute("SELECT imdb_id, timestamp FROM tmdb_ids WHERE cache_key = ?", (cache_key,))
        result = cursor.fetchone()
        if result and (time.time() - result[1] < CACHE_MAX_AGE):
            return result[0]

    url = f'https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids?api_key={API_KEY}'
    imdb_id = ''
    try:
        response = session.get(url, timeout=5)
        response.raise_for_status()
        imdb_id = response.json().get('imdb_id', '')
    except Exception as e:
        xbmc.log(f'TMDb_API: erro ao buscar IMDb ID: {e}', xbmc.LOGERROR)
    
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO tmdb_ids VALUES (?, ?, ?)", (cache_key, imdb_id, time.time()))
        
    return imdb_id
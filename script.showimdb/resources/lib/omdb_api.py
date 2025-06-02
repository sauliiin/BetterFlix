# -*- coding: utf-8 -*-
import xbmc
import requests
import json
import time
import os
import xbmcvfs

## --- Cache para Notas do OMDb ---
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
CACHE_FILE = os.path.join(CACHE_DIR, 'omdb_ratings_cache.json')
CACHE_MAX_AGE = 15 * 24 * 60 * 60  # 15 dias

def _load_omdb_cache():
    if not xbmcvfs.exists(CACHE_FILE): return {}
    try:
        with xbmcvfs.File(CACHE_FILE, 'r') as f:
            cache = json.loads(f.read())
        now = time.time()
        expired_keys = [k for k, v in cache.items() if now - v.get('ts', 0) > CACHE_MAX_AGE]
        for k in expired_keys: del cache[k]
        return cache
    except Exception: return {}

def _save_omdb_cache(cache):
    try:
        if not xbmcvfs.exists(CACHE_DIR): xbmcvfs.mkdirs(CACHE_DIR)
        with xbmcvfs.File(CACHE_FILE, 'w') as f:
            f.write(json.dumps(cache))
    except Exception as e: xbmc.log(f"[ShowIMDb] Erro ao salvar cache OMDb: {e}", xbmc.LOGWARNING)

OMDB_CACHE = _load_omdb_cache()
OMDB_API_KEY = 'b2f2fcca' # Sua chave da API OMDb
session = requests.Session()

def get_rating_by_imdb_id(imdb_id):
    """
    Busca a nota do IMDb/OMDb pelo IMDb ID, com cache.
    """
    if imdb_id in OMDB_CACHE:
        return OMDB_CACHE[imdb_id]['rating']

    url = f'http://www.omdbapi.com/?apikey={OMDB_API_KEY}&i={imdb_id}'
    try:
        response = session.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        rating = ''
        if data.get('Response') == 'True':
            rating = data.get('imdbRating', '').replace('N/A', '')

        OMDB_CACHE[imdb_id] = {'ts': time.time(), 'rating': rating}
        _save_omdb_cache(OMDB_CACHE)
        return rating
    
    except requests.exceptions.RequestException as e:
        xbmc.log(f'ShowIMDb: erro ao buscar nota no OMDb para {imdb_id}: {e}', xbmc.LOGERROR)
        return ''
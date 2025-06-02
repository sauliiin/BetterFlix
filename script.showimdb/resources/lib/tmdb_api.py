# -*- coding: utf-8 -*-
import xbmc
import requests
import json
import time
import os
import xbmcvfs  # <--- 1. IMPORTAÇÃO ADICIONADA

## --- Cache para a conversão de TMDB ID para IMDb ID ---
# vvv--- 2. CORREÇÃO FEITA AQUI ---vvv
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
CACHE_FILE = os.path.join(CACHE_DIR, 'tmdb_id_cache.json')
CACHE_MAX_AGE = 90 * 24 * 60 * 60  # Cache de 90 dias, pois IDs não mudam.

def _load_id_cache():
    if not xbmcvfs.exists(CACHE_FILE): return {}
    try:
        # Usando xbmcvfs para ler o arquivo para máxima compatibilidade
        with xbmcvfs.File(CACHE_FILE, 'r') as f:
            content = f.read()
            cache = json.loads(content)
        now = time.time()
        expired_keys = [k for k, v in cache.items() if now - v.get('ts', 0) > CACHE_MAX_AGE]
        for k in expired_keys: del cache[k]
        return cache
    except Exception: return {}

def _save_id_cache(cache):
    try:
        if not xbmcvfs.exists(CACHE_DIR): xbmcvfs.mkdirs(CACHE_DIR)
        # Usando xbmcvfs para escrever o arquivo
        with xbmcvfs.File(CACHE_FILE, 'w') as f:
            f.write(json.dumps(cache))
    except Exception as e: xbmc.log(f"[ShowIMDb] Erro ao salvar cache TMDB: {e}", xbmc.LOGWARNING)

ID_CACHE = _load_id_cache()
TMDB_API_KEY = '703cf5598b9fd74adac824baf7923126'
session = requests.Session()

def fetch_imdb_id(tmdb_id, content_type='movie'):
    """
    Busca o IMDb ID a partir do TMDB ID, com cache otimizado.
    """
    cache_key = f"{content_type}_{tmdb_id}"
    if cache_key in ID_CACHE:
        return ID_CACHE[cache_key]['imdb_id']

    if content_type == 'movie':
        url = f'https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}'
    else: # tv
        url = f'https://api.themoviedb.org/3/tv/{tmdb_id}/external_ids?api_key={TMDB_API_KEY}'

    try:
        response = session.get(url, timeout=5)
        response.raise_for_status() # Lança um erro para códigos http > 400
        data = response.json()
        imdb_id = data.get('imdb_id', '')

        # Salva no cache para futuras consultas
        ID_CACHE[cache_key] = {'ts': time.time(), 'imdb_id': imdb_id}
        _save_id_cache(ID_CACHE)
        return imdb_id

    except requests.exceptions.RequestException as e:
        xbmc.log(f'ShowIMDb: erro ao buscar IMDb ID para {tmdb_id}: {e}', xbmc.LOGERROR)
        return ''
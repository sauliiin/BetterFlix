# -*- coding: utf-8 -*-
import xbmc
import requests
import json
import time
import os
import xbmcvfs  # <--- 1. IMPORTAÇÃO ADICIONADA
from concurrent.futures import ThreadPoolExecutor

## --- Cache para Reviews do Trakt ---
# vvv--- 2. CORREÇÃO FEITA AQUI ---vvv
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
CACHE_FILE = os.path.join(CACHE_DIR, 'trakt_reviews_cache.json')
CACHE_MAX_AGE = 15 * 24 * 60 * 60  # 15 dias

def _load_reviews_cache():
    if not xbmcvfs.exists(CACHE_FILE): return {}
    try:
        with xbmcvfs.File(CACHE_FILE, 'r') as f:
            content = f.read()
            cache = json.loads(content)
        now = time.time()
        expired_keys = [k for k, v in cache.items() if now - v.get('ts', 0) > CACHE_MAX_AGE]
        for k in expired_keys: del cache[k]
        return cache
    except Exception: return {}

def _save_reviews_cache(cache):
    try:
        if not xbmcvfs.exists(CACHE_DIR): xbmcvfs.mkdirs(CACHE_DIR)
        with xbmcvfs.File(CACHE_FILE, 'w') as f:
            f.write(json.dumps(cache))
    except Exception as e: xbmc.log(f"[ShowIMDb] Erro ao salvar cache Trakt: {e}", xbmc.LOGWARNING)

REVIEWS_CACHE = _load_reviews_cache()
TRAKT_API_KEY = 'fbc2791a2609e77d4e9d1689b7332a7124428eb7d8ea46085876d8867755a357'
HEADERS = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_API_KEY}
session = requests.Session()

def get_reviews_by_imdb_id(imdb_id, media_type='movie', max_comments=20):
    """
    Busca e formata reviews do Trakt diretamente pelo IMDb ID, com sistema de cache.
    """
    if imdb_id in REVIEWS_CACHE:
        return REVIEWS_CACHE[imdb_id]['data']

    trakt_type = 'shows' if media_type == 'tv' else 'movies'
    url = f"https://api.trakt.tv/{trakt_type}/{imdb_id}/comments"
    params = {'limit': max_comments, 'sort': 'likes'}

    try:
        response = session.get(url, headers=HEADERS, params=params, timeout=5)
        if response.status_code != 200:
            REVIEWS_CACHE[imdb_id] = {'ts': time.time(), 'data': ''}
            _save_reviews_cache(REVIEWS_CACHE)
            return ''

        raw_comments = response.json()
        formatted_comments = []
        for c in raw_comments:
            comment_text = c.get('comment', '').strip()
            if not comment_text:
                continue
            
            # --- ALTERAÇÃO PRINCIPAL AQUI ---
            # 1. Adiciona o título "Análise" em negrito no início de cada comentário.
            review_block = f"[B]ANÁLISE:[/B]\n\n{comment_text}"

            # Adiciona a nota do usuário ao comentário, se existir
            if (rating := c.get('user_rating')) is not None:
                review_block += f"\n\n[B]Nota do usuário: {rating}/10[/B]"
            
            formatted_comments.append(review_block)
        
        # 2. Junta os blocos de review com duas linhas em branco (\n\n\n) de separação.
        result = '\n_________________________________________________________________\n\n'.join(formatted_comments)

        REVIEWS_CACHE[imdb_id] = {'ts': time.time(), 'data': result}
        _save_reviews_cache(REVIEWS_CACHE)
        return result

    except Exception as e:
        xbmc.log(f"[ShowIMDb] Erro na API Trakt para {imdb_id}: {e}", xbmc.LOGERROR)
        return ''

def get_reviews_for_multiple_items(items):
    """
    Função otimizada para buscar reviews de vários itens em paralelo.
    'items' deve ser uma lista de dicionários, ex: [{'imdb_id': 'tt...', 'media_type': 'movie'}, ...]
    """
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_imdb = {executor.submit(get_reviews_by_imdb_id, item['imdb_id'], item['media_type']): item['imdb_id'] for item in items}
        for future in future_to_imdb:
            imdb_id = future_to_imdb[future]
            try:
                results[imdb_id] = future.result()
            except Exception as e:
                results[imdb_id] = ''
                xbmc.log(f"[ShowIMDb] Erro na busca paralela para {imdb_id}: {e}", xbmc.LOGERROR)
    return results
# -*- coding: utf-8 -*-
# ARQUIVO: tmdb_api.py
import xbmc
import requests
import time
import json 
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from database import db

# --- Configurações ---
API_KEY = '703cf5598b9fd74adac824baf7923126'
NETWORK_TIMEOUT = 10 

# TEMPO DE VIDA DO CACHE
CACHE_MAX_AGE_TRAILER = 60 * 24 * 60 * 60   # 60 dias
CACHE_MAX_AGE_ID = 90 * 24 * 60 * 60        # 90 dias

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

def _cleanup_old_trailers():
    """Limpeza de cache antigo na inicialização."""
    try:
        limit_time_trailer = time.time() - CACHE_MAX_AGE_TRAILER
        db.execute_query("DELETE FROM tmdb_trailers WHERE timestamp < ?", (limit_time_trailer,))
    except Exception as e:
        xbmc.log(f"TMDb_API: Erro Cleanup: {e}", xbmc.LOGERROR)

_cleanup_old_trailers()

def fetch_tmdb_id_from_imdb(imdb_id):
    """Converte IMDb ID -> TMDb ID (Retorna tupla: id, media_type)"""
    if not imdb_id: return None, None
    cache_key = f"find_{imdb_id}"
    
    # 1. Leitura Inteligente (Suporta formato antigo e novo)
    result = db.fetch_one("SELECT imdb_id, timestamp FROM tmdb_ids WHERE cache_key = ?", (cache_key,))
    if result:
        try:
            raw_data = result[0]
            # Se começar com {, é o novo formato JSON. Se não, é lixo antigo ou string pura.
            if raw_data.startswith("{"):
                found_data = json.loads(raw_data)
                return found_data.get('tmdb_id'), found_data.get('media_type')
        except:
            pass # Se falhar o parse, tenta buscar na rede novamente

    # 2. Rede
    url = f'https://api.themoviedb.org/3/find/{imdb_id}?api_key={API_KEY}&external_source=imdb_id'
    tmdb_id_found = None
    media_type_found = None
    
    try:
        response = session.get(url, timeout=NETWORK_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if data.get('movie_results'):
                tmdb_id_found = data['movie_results'][0].get('id')
                media_type_found = 'movie'
            elif data.get('tv_results'):
                tmdb_id_found = data['tv_results'][0].get('id')
                media_type_found = 'tv'
    except Exception: 
        pass
    
    # 3. Escrita Padronizada (JSON)
    if tmdb_id_found:
        data_to_cache = {'tmdb_id': tmdb_id_found, 'media_type': media_type_found}
        db.execute_query(
            "INSERT OR REPLACE INTO tmdb_ids VALUES (?, ?, ?)", 
            (cache_key, json.dumps(data_to_cache), time.time())
        )
            
    return tmdb_id_found, media_type_found

def fetch_imdb_id(tmdb_id, content_type):
    """Converte TMDb ID -> IMDb ID"""
    if not tmdb_id or not content_type: return ""
    media_type = 'tv' if content_type.lower() in ('tv', 'tvshow', 'season', 'episode') else 'movie'
    cache_key = f"{media_type}_{tmdb_id}"
    
    # 1. Leitura Inteligente
    result = db.fetch_one("SELECT imdb_id FROM tmdb_ids WHERE cache_key = ?", (cache_key,))
    if result: 
        raw_val = result[0]
        # Correção Crítica: Detecta se é o formato antigo (String pura "tt...") ou novo (JSON)
        if raw_val.startswith("{"):
            try:
                return json.loads(raw_val).get('imdb_id', '')
            except:
                pass
        else:
            # Formato antigo (apenas string), retorna direto
            return raw_val

    # 2. Rede
    url = f'https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids?api_key={API_KEY}'
    imdb_id_found = ''
    try:
        response = session.get(url, timeout=NETWORK_TIMEOUT)
        if response.status_code == 200:
            imdb_id_found = response.json().get('imdb_id', '')
    except: 
        pass
    
    # 3. Escrita Padronizada (AGORA COMO JSON)
    if imdb_id_found:
        data_to_cache = {'imdb_id': imdb_id_found}
        db.execute_query(
            "INSERT OR REPLACE INTO tmdb_ids VALUES (?, ?, ?)", 
            (cache_key, json.dumps(data_to_cache), time.time())
        )
    return imdb_id_found

def fetch_trailer_url(tmdb_id, media_type):
    """Busca Trailer no Youtube via TMDb"""
    if not tmdb_id or not media_type: return ""
    media_type_clean = 'tv' if media_type.lower() in ('tv', 'tvshow', 'season', 'episode') else 'movie'
    cache_key = f"trailer_{media_type_clean}_{tmdb_id}"

    # 1. Leitura Cache
    result = db.fetch_one("SELECT trailer_url, timestamp FROM tmdb_trailers WHERE cache_key = ?", (cache_key,))
    if result and (time.time() - result[1] < CACHE_MAX_AGE_TRAILER):
        return result[0]

    # 2. Rede
    url = f"https://api.themoviedb.org/3/{media_type_clean}/{tmdb_id}/videos?api_key={API_KEY}&language=en-US,pt-BR"
    trailer_url_found = ""
    try:
        response = session.get(url, timeout=NETWORK_TIMEOUT)
        if response.status_code == 200:
            videos = response.json().get('results', [])
            if videos:
                # Sistema de Pontuação para escolher o melhor video
                def get_video_score(video):
                    if video['site'].lower() != 'youtube': return (-1, 0, 0)
                    is_trailer = 1 if video['type'].lower() == 'trailer' else 0
                    is_official = 1 if video.get('official', False) else 0
                    # Prioridade: PT-BR > EN > Outros
                    lang_score = 2 if video['iso_639_1'] == 'pt' else (1 if video['iso_639_1'] == 'en' else 0)
                    return (is_trailer, is_official, lang_score)

                sorted_videos = sorted(videos, key=get_video_score, reverse=True)
                best_trailer = sorted_videos[0]
                
                # Só aceita se for YouTube
                if best_trailer['site'].lower() == 'youtube':
                    video_key = best_trailer['key']
                    trailer_url_found = f"plugin://plugin.video.youtube/play/?video_id={video_key}"
    except Exception as e:
        xbmc.log(f'TMDb_API Error: {e}', xbmc.LOGWARNING)
    
    # 3. Escrita Cache
    # Salvamos mesmo se for vazio para evitar ficar batendo na API toda hora num filme sem trailer
    db.execute_query(
        "INSERT OR REPLACE INTO tmdb_trailers VALUES (?, ?, ?)", 
        (cache_key, trailer_url_found, time.time())
    )

    return trailer_url_found
# -*- coding: utf-8 -*-
# ARQUIVO: tmdb_api.py
import xbmc
import requests
import sqlite3
import time
import os
import xbmcvfs
import json 

# --- Configurações ---
API_KEY = '703cf5598b9fd74adac824baf7923126'
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
if not xbmcvfs.exists(CACHE_DIR):
    xbmcvfs.mkdirs(CACHE_DIR)
DB_FILE = os.path.join(CACHE_DIR, 'addon_cache.db')
CACHE_MAX_AGE = 90 * 24 * 60 * 60
CACHE_MAX_AGE_TRAILER = 30 * 24 * 60 * 60

def _init_db():
    """Inicializa o banco de dados e cria as tabelas se não existirem."""
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS tmdb_ids (cache_key TEXT PRIMARY KEY, imdb_id TEXT, timestamp REAL)')
        cursor.execute('CREATE TABLE IF NOT EXISTS tmdb_trailers (cache_key TEXT PRIMARY KEY, trailer_url TEXT, timestamp REAL)')
        conn.commit()

_init_db()
session = requests.Session()

def fetch_tmdb_id_from_imdb(imdb_id):
    """
    Busca o TMDB ID e o media_type ('movie' ou 'tv') a partir de um IMDb ID.
    Otimizado para uma única conexão de banco de dados.
    """
    if not imdb_id: return None, None
    cache_key = f"find_{imdb_id}"
    
    # Otimização: Conecta apenas UMA VEZ
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    try:
        # 1. Tenta ler do cache
        cursor.execute("SELECT imdb_id, timestamp FROM tmdb_ids WHERE cache_key = ?", (cache_key,))
        result = cursor.fetchone()
        if result and (time.time() - result[1] < CACHE_MAX_AGE):
            try:
                found_data = json.loads(result[0]) 
                return found_data.get('tmdb_id'), found_data.get('media_type')
            except Exception as e:
                xbmc.log(f'TMDb_API: Erro ao ler JSON do cache para {cache_key}: {e}', xbmc.LOGWARNING)
                # Cache corrompido, continua para buscar na API

        # 2. Cache Miss: Busca na API
        url = f'https://api.themoviedb.org/3/find/{imdb_id}?api_key={API_KEY}&external_source=imdb_id'
        tmdb_id_found = None
        media_type_found = None
        
        try:
            response = session.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            if data.get('movie_results'):
                tmdb_id_found = data['movie_results'][0].get('id')
                media_type_found = 'movie'
            elif data.get('tv_results'):
                tmdb_id_found = data['tv_results'][0].get('id')
                media_type_found = 'tv'
            
        except requests.exceptions.RequestException as e:
            xbmc.log(f'TMDb_API: Erro de rede ao buscar TMDB ID (from IMDb {imdb_id}): {e}', xbmc.LOGERROR)
        except Exception as e:
            xbmc.log(f'TMDb_API: Erro inesperado ao buscar TMDB ID (from IMDb {imdb_id}): {e}', xbmc.LOGERROR)
        
        # 3. Salva no cache (usando a mesma conexão)
        data_to_cache = {'tmdb_id': tmdb_id_found, 'media_type': media_type_found}
        try:
            conn.execute("INSERT OR REPLACE INTO tmdb_ids VALUES (?, ?, ?)", (cache_key, json.dumps(data_to_cache), time.time()))
            conn.commit()
        except Exception as e:
            xbmc.log(f'TMDb_API: Erro ao SALVAR cache para find_{imdb_id}: {e}', xbmc.LOGERROR)
            
        return tmdb_id_found, media_type_found

    finally:
        # 4. Garante que a conexão seja sempre fechada
        conn.close()


def fetch_imdb_id(tmdb_id, content_type):
    """
    Busca o IMDb ID a partir do TMDB ID e do tipo de conteúdo ('movie' ou 'tv').
    Otimizado para uma única conexão de banco de dados.
    """
    if not tmdb_id or not content_type: return ""
    
    media_type = 'tv' if content_type.lower() in ('tv', 'tvshow', 'season', 'episode') else 'movie'
    cache_key = f"{media_type}_{tmdb_id}"
    
    # Otimização: Conecta apenas UMA VEZ
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    try:
        # 1. Tenta ler do cache
        cursor.execute("SELECT imdb_id, timestamp FROM tmdb_ids WHERE cache_key = ?", (cache_key,))
        result = cursor.fetchone()
        if result and (time.time() - result[1] < CACHE_MAX_AGE):
            return result[0]

        # 2. Cache Miss: Busca na API
        url = f'https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids?api_key={API_KEY}'
        imdb_id_found = ''
        try:
            response = session.get(url, timeout=5)
            response.raise_for_status()
            imdb_id_found = response.json().get('imdb_id', '')
        except requests.exceptions.RequestException as e:
            xbmc.log(f'TMDb_API: Erro de rede ao buscar IMDb ID para {cache_key}: {e}', xbmc.LOGERROR)
        except Exception as e:
            xbmc.log(f'TMDb_API: Erro inesperado ao buscar IMDb ID para {cache_key}: {e}', xbmc.LOGERROR)
        
        # 3. Salva no cache (usando a mesma conexão)
        try:
            conn.execute("INSERT OR REPLACE INTO tmdb_ids VALUES (?, ?, ?)", (cache_key, imdb_id_found, time.time()))
            conn.commit()
        except Exception as e:
           xbmc.log(f'TMDb_API: Erro ao SALVAR cache para {cache_key}: {e}', xbmc.LOGERROR)
            
        return imdb_id_found

    finally:
        # 4. Garante que a conexão seja sempre fechada
        conn.close()

def fetch_trailer_url(tmdb_id, media_type):
    """
    Busca o trailer (preferencialmente do YouTube) para um item no TMDb.
    Otimizado para uma única conexão de BD, uso de session e lógica de seleção.
    """
    if not tmdb_id or not media_type or not API_KEY:
        return ""
    
    media_type_clean = 'tv' if media_type.lower() in ('tv', 'tvshow', 'season', 'episode') else 'movie'
    cache_key = f"trailer_{media_type_clean}_{tmdb_id}"

    # Otimização: Conecta apenas UMA VEZ
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    try:
        # --- ETAPA 1: Tentar buscar do Cache SQLite ---
        cursor.execute("SELECT trailer_url, timestamp FROM tmdb_trailers WHERE cache_key = ?", (cache_key,))
        result = cursor.fetchone()
        if result and (time.time() - result[1] < CACHE_MAX_AGE_TRAILER):
            return result[0]

        # --- ETAPA 2: Não estava no cache ou inválido, buscar na Rede ---
        url = f"https://api.themoviedb.org/3/{media_type_clean}/{tmdb_id}/videos?api_key={API_KEY}&language=en-US,pt-BR"
        trailer_url_found = ""

        try:
            # Otimização: Usar session.get() para reutilizar conexão
            response = session.get(url, timeout=5) 
            response.raise_for_status()
            videos = response.json().get('results', [])
            
            if videos:
                # Otimização: Substitui 4 loops 'for' por uma única chamada 'sorted'
                def get_video_score(video):
                    if video['site'].lower() != 'youtube':
                        return (-1, 0, 0, 0) # Ignora se não for YouTube
                    
                    is_trailer = 1 if video['type'].lower() == 'trailer' else 0
                    is_official = 1 if video.get('official', False) else 0
                    # Prioriza PT > EN > Outros
                    lang_score = 2 if video['iso_639_1'] == 'pt' else (1 if video['iso_639_1'] == 'en' else 0)
                    
                    # Retorna uma tupla de pontuação (maior é melhor)
                    return (is_trailer, is_official, lang_score)

                # Ordena os vídeos pela maior pontuação
                sorted_videos = sorted(videos, key=get_video_score, reverse=True)
                
                # O melhor vídeo é o primeiro da lista, desde que seja do YouTube
                best_trailer = sorted_videos[0]
                if best_trailer['site'].lower() == 'youtube':
                    video_key = best_trailer['key']
                    trailer_url_found = f"plugin://plugin.video.youtube/play/?video_id={video_key}"

        except requests.exceptions.RequestException as e:
            xbmc.log(f'ShowIMDb [TMDb API]: Erro de rede ao buscar trailer para {cache_key}: {e}', xbmc.LOGERROR)
            return "" # Retorna vazio em caso de erro de rede
        except Exception as e:
            xbmc.log(f'ShowIMDb [TMDb API]: Erro inesperado ao buscar trailer para {cache_key}: {e}', xbmc.LOGERROR)
            return "" # Retorna vazio em caso de erro inesperado
        
        # --- ETAPA 3: Salvar o resultado (mesmo se for vazio) no Cache SQLite ---
        try:
            conn.execute("INSERT OR REPLACE INTO tmdb_trailers VALUES (?, ?, ?)", (cache_key, trailer_url_found, time.time()))
            conn.commit()
        except Exception as e:
            xbmc.log(f'ShowIMDb [TMDb API]: Erro ao SALVAR cache de trailer para {cache_key}: {e}', xbmc.LOGERROR)

        return trailer_url_found

    finally:
        # 4. Garante que a conexão seja sempre fechada
        conn.close()
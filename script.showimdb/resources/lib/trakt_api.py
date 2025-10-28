# -*- coding: utf-8 -*-
# ARQUIVO: trakt_api.py
import xbmc
import requests
import sqlite3
import time
import os
import xbmcvfs

# --- Configurações ---
API_KEY = 'fbc2791a2609e77d4e9d1689b7332a7124428eb7d8ea46085876d8867755a357'
HEADERS = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': API_KEY}
CACHE_DIR = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/script.showimdb/'), 'cache')
if not xbmcvfs.exists(CACHE_DIR):
    xbmcvfs.mkdirs(CACHE_DIR)
DB_FILE = os.path.join(CACHE_DIR, 'addon_cache.db')
CACHE_MAX_AGE = 15 * 24 * 60 * 60 # 15 dias

def _init_db():
    """Inicializa o banco de dados e a tabela de cache, se não existirem."""
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS trakt_reviews (imdb_id TEXT PRIMARY KEY, data TEXT, timestamp REAL)')
        conn.commit()

# --- Inicialização ---
_init_db()
session = requests.Session()

def get_reviews_by_imdb_id(imdb_id, media_type):
    """Busca e formata reviews do Trakt, com cache e nota do usuário."""
    if not imdb_id:
        return ''
    
    # Otimização: Conecta apenas UMA VEZ
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    try:
        # 1. Tenta buscar do cache primeiro
        cursor.execute("SELECT data, timestamp FROM trakt_reviews WHERE imdb_id = ?", (imdb_id,))
        result = cursor.fetchone()
        if result and (time.time() - result[1] < CACHE_MAX_AGE):
            return result[0]
            
        # 2. Se não houver cache válido, busca na API
        trakt_type = 'shows' if media_type == 'tv' else 'movies'
        url = f"https://api.trakt.tv/{trakt_type}/{imdb_id}/comments"
        result_text = ''
        
        try:
            params = {'limit': 200, 'sort': 'likes'}
            response = session.get(url, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            
            raw_comments = response.json()
            formatted_comments = []

            for c in raw_comments:
                # Se já temos 40 reviews, paramos de procurar
                if len(formatted_comments) >= 40:
                    break
                    
                comment_text = c.get('comment', '').strip()
                is_spoiler = c.get('spoiler', False)
                # Otimização: Filtra por idioma
                user_lang = c.get('user', {}).get('language', 'en') # Assume 'en' se não especificado

                # Otimização: Adicionado filtro de spoiler e idioma
                if (
                    not is_spoiler and
                    comment_text and
                    len(comment_text) <= 700 and
                    user_lang in ('pt', 'en')
                ):
                    
                    # Bloco de texto para a análise
                    review_block = f"[B][COLOR FFE50914]ANÁLISE:[/COLOR][/B] {comment_text}"
                    
                    # Adiciona a nota do usuário, se existir
                    if (rating := c.get('user_rating')) is not None:
                        review_block += f"  [B]NOTA: {rating}/10[/B]"
                    
                    formatted_comments.append(review_block)

            if formatted_comments:
                result_text = '\n\n'.join(formatted_comments)
                
        except Exception as e:
            xbmc.log(f"[Trakt_API] Erro na chamada para {imdb_id}: {e}", xbmc.LOGWARNING)
            return ''
            
        # 3. Salva o novo resultado no cache (usando a mesma conexão)
        conn.execute("INSERT OR REPLACE INTO trakt_reviews VALUES (?, ?, ?)", (imdb_id, result_text, time.time()))
        conn.commit()
            
        return result_text

    finally:
        # 4. Garante que a conexão seja sempre fechada
        conn.close()
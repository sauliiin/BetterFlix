# -*- coding: utf-8 -*-
# ARQUIVO: trakt_api.py
import xbmc
import requests
import time
from database import db

# --- Configurações ---
API_KEY = 'fbc2791a2609e77d4e9d1689b7332a7124428eb7d8ea46085876d8867755a357'
HEADERS = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': API_KEY}
CACHE_MAX_AGE = 15 * 24 * 60 * 60 # 15 dias

session = requests.Session()

def get_reviews_by_imdb_id(imdb_id, media_type):
    if not imdb_id:
        return ''
    
    # 1. Leitura via DB Central
    result = db.fetch_one("SELECT data, timestamp FROM trakt_reviews WHERE imdb_id = ?", (imdb_id,))
    if result and (time.time() - result[1] < CACHE_MAX_AGE):
        return result[0]
            
    # 2. Rede
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
            if len(formatted_comments) >= 50:
                break
                
            comment_text = c.get('comment', '').strip()
            is_spoiler = c.get('spoiler', False)
            user_lang = c.get('user', {}).get('language', 'en')

            if (
                not is_spoiler and
                comment_text and
                len(comment_text) <= 600 and
                user_lang in ('pt', 'en', 'es')
            ):
                review_block = f"[B][COLOR FFE50914]ANÁLISE:[/COLOR][/B] {comment_text}"
                if (rating := c.get('user_rating')) is not None:
                    review_block += f"  [B]NOTA: {rating}/10[/B]"
                
                formatted_comments.append(review_block)

        if formatted_comments:
            result_text = '\n\n'.join(formatted_comments)
            
    except Exception as e:
        xbmc.log(f"[Trakt_API] Erro para {imdb_id}: {e}", xbmc.LOGWARNING)
        return ''
        
    # 3. Escrita via DB Central
    db.execute_query(
        "INSERT OR REPLACE INTO trakt_reviews VALUES (?, ?, ?)", 
        (imdb_id, result_text, time.time())
    )
        
    return result_text
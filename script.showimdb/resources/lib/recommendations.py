# -*- coding: utf-8 -*-
"""Fileira "Para Voce" (recomendacoes TMDb) servida pelo script.showimdb.

Arquivo unico e centralizado. Mantido propositalmente leve: no caminho de
cache-hit importa apenas xbmc*, sqlite (via database.db) e xbmcaddon. O modulo
indexers/requests (via tmdb_api) so e importado no cache-miss, quando ha rede a
fazer. Isso evita o custo do grafo de imports gigante do plugin.video.pov, que
era re-importado a cada abertura do dialogo (reuselanguageinvoker=false).

Os itens montados aqui apontam de volta para o plugin.video.pov (URLs como
strings) e replicam exatamente o comportamento de clique/menu de contexto de
menus/fast_recommendations.py do POV, sem importar o POV.
"""
import json
import time
from urllib.parse import urlencode

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

from database import db

TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/%s%s'
POV_ID = 'plugin.video.pov'
RECS_TABLE = 'recommendations'
# 1 ano, igual ao EXPIRES_1_YEAR usado pelo POV nas recomendacoes.
CACHE_MAX_AGE = 365 * 24 * 60 * 60
# Idioma das recomendacoes: o POV usava en-US. Trocar para 'pt-BR' se quiser
# titulos/sinopses em portugues na fileira.
LANGUAGE = 'en-US'

# Mapa de settings.get_resolution() do POV: indice image_resolutions -> (poster, fanart).
_RES_MAP = (
    ('w185', 'w300'),
    ('w342', 'w780'),
    ('w500', 'original'),
    ('w780', 'original'),
)

_pov = None
# Caches por-processo: como o pluginsource roda num processo novo a cada chamada
# (reuselanguageinvoker=false), estes vivem so durante uma montagem, mas evitam
# repetir getSetting/getLocalizedString/getInfoLabel cross-addon para cada um dos
# 10 itens (eram ~40 chamadas por abertura; passam a ~4).
_pov_setting_cache = {}
_pov_string_cache = {}
_kodi_major_cached = None


def _pov_addon():
    global _pov
    if _pov is None:
        _pov = xbmcaddon.Addon(POV_ID)
    return _pov


def _pov_setting(key, default=''):
    if key in _pov_setting_cache:
        return _pov_setting_cache[key]
    try:
        value = _pov_addon().getSetting(key)
        value = value if value != '' else default
    except Exception:
        value = default
    _pov_setting_cache[key] = value
    return value


def _pov_string(string_id):
    if string_id in _pov_string_cache:
        return _pov_string_cache[string_id]
    try:
        value = _pov_addon().getLocalizedString(string_id) or ''
    except Exception:
        value = ''
    _pov_string_cache[string_id] = value
    return value


def _kodi_major():
    global _kodi_major_cached
    if _kodi_major_cached is None:
        try:
            _kodi_major_cached = int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])
        except Exception:
            _kodi_major_cached = 20
    return _kodi_major_cached


def _resolution():
    try:
        idx = int(_pov_setting('image_resolutions', '2'))
    except Exception:
        idx = 2
    if not (0 <= idx < len(_RES_MAP)):
        idx = 2
    return _RES_MAP[idx]


def _pov_url(params):
    return 'plugin://%s/?%s' % (POV_ID, urlencode(params))


def _extras_open(mediatype):
    """Replica settings.extras_open_action(mediatype) do POV. Setting enum
    extras.open_action (0/1/2/3): abre o menu de extras para movie em (1,3) e
    para tvshow em (2,3)."""
    try:
        value = int(_pov_setting('extras.open_action', '0'))
    except Exception:
        value = 0
    return value in ((1, 3) if mediatype == 'movie' else (2, 3))


def _target(mediatype, tmdb_id):
    """Replica _target() do POV: define o que o clique no item faz.

    is_widget e fixado em 'false' porque, no POV, era external_browse() avaliado
    enquanto o POV preenchia o container do dialogo (Container.PluginName=POV) ->
    False. Mantemos esse valor para preservar o comportamento atual.
    """
    if _extras_open(mediatype):
        url = _pov_url({
            'mode': 'extras_menu_choice', 'mediatype': mediatype,
            'tmdb_id': tmdb_id, 'is_widget': 'false', 'is_home': 'true'
        })
        return url, False
    if mediatype == 'movie':
        return _pov_url({'mode': 'play_media', 'mediatype': 'movie', 'tmdb_id': tmdb_id}), False
    return _pov_url({'mode': 'build_season_list', 'tmdb_id': tmdb_id}), True


def _context_menu(mediatype, tmdb_id):
    """Replica _context_menu() do POV (Opcoes / Play|Navegar / Extras)."""
    extras_url = _pov_url({
        'mode': 'extras_menu_choice', 'mediatype': mediatype,
        'tmdb_id': tmdb_id, 'is_widget': 'false', 'is_home': 'true'
    })
    options_url = _pov_url({
        'mode': 'options_menu_choice', 'mediatype': mediatype,
        'tmdb_id': tmdb_id, 'is_widget': 'false'
    })
    cm = [
        (_pov_string(32646), 'RunPlugin(%s)' % options_url),
        (_pov_string(32645), 'RunPlugin(%s)' % extras_url),
    ]
    if mediatype == 'movie':
        play_url = _pov_url({'mode': 'play_media', 'mediatype': 'movie', 'tmdb_id': tmdb_id})
        cm.insert(1, ('[B]%s...[/B]' % _pov_string(32174), 'RunPlugin(%s)' % play_url))
    else:
        browse_url = _pov_url({'mode': 'build_season_list', 'tmdb_id': tmdb_id})
        cm.insert(1, (_pov_string(32652), 'Container.Update(%s)' % browse_url))
    return cm


def _recommendations_data(media_type, tmdb_id):
    """Recomendacoes TMDb com cache-first no addon_cache.db proprio.

    Cache-hit nao toca em rede nem importa requests. So no miss importamos
    tmdb_api (sessao + API key) e fazemos o GET, cacheando o resultado.
    """
    mt = 'tv' if media_type == 'tvshow' else 'movie'
    cache_key = '%s_%s' % (mt, tmdb_id)
    row = db.fetch_one(
        'SELECT data, timestamp FROM %s WHERE cache_key = ?' % RECS_TABLE, (cache_key,)
    )
    if row and (time.time() - (row[1] or 0) < CACHE_MAX_AGE):
        try:
            data = json.loads(row[0]) or []
            xbmc.log('[ShowIMDB][Recs] CACHE HIT %s/%s itens=%d' % (mt, tmdb_id, len(data)), xbmc.LOGINFO)
            return data
        except Exception:
            pass

    # Import tardio: requests/urllib3/ssl so entram no caminho de cache-miss.
    from tmdb_api import session, FineTuning
    url = (
        'https://api.themoviedb.org/3/%s/%s/recommendations'
        '?api_key=%s&language=%s&page=1' % (mt, tmdb_id, FineTuning.API_KEY, LANGUAGE)
    )
    results = []
    t0 = time.time()
    try:
        response = session.get(url, timeout=FineTuning.NETWORK_TIMEOUT)
        if response.status_code == 200:
            results = response.json().get('results', []) or []
    except Exception:
        results = []
    xbmc.log('[ShowIMDB][Recs] CACHE MISS %s/%s rede=%.2fs itens=%d' % (
        mt, tmdb_id, time.time() - t0, len(results)), xbmc.LOGINFO)
    try:
        db.execute_query(
            'INSERT OR REPLACE INTO %s VALUES (?, ?, ?)' % RECS_TABLE,
            (cache_key, json.dumps(results), time.time())
        )
    except Exception:
        pass
    return results


def _set_video_info(listitem, mediatype, title, tmdb_id, year, plot, rating):
    """Replica _set_video_info() do POV (compat Kodi <20 e >=20)."""
    try:
        if _kodi_major() < 20:
            listitem.setInfo('video', {
                'title': title, 'mediatype': mediatype,
                'year': int(year or 0), 'plot': plot, 'rating': rating
            })
            listitem.setUniqueIDs({'tmdb': str(tmdb_id)})
            return
        tag = listitem.getVideoInfoTag(offscreen=True)
        tag.setTitle(title)
        tag.setMediaType(mediatype)
        tag.setUniqueIDs({'tmdb': str(tmdb_id)})
        if year:
            tag.setYear(int(year or 0))
        if plot:
            tag.setPlot(plot)
        if rating:
            tag.setRating(float(rating))
    except Exception:
        pass


def recommendation_fields(media_type, tmdb_id, limit=10):
    """Lista de dicts simples (sem ListItem) para o worker do servico publicar
    como window properties. media_type: 'movie'|'tv'|'tvshow'. Usa o mesmo
    cache-first de _recommendations_data, entao cache-hit nao toca em rede."""
    if media_type == 'tv':
        media_type = 'tvshow'
    if media_type not in ('movie', 'tvshow') or not tmdb_id:
        return []
    poster_size, fanart_size = _resolution()
    name_key = 'title' if media_type == 'movie' else 'name'
    date_key = 'release_date' if media_type == 'movie' else 'first_air_date'
    try:
        limit = max(1, min(int(limit), 20))
    except Exception:
        limit = 10
    data = _recommendations_data(media_type, tmdb_id)
    out = []
    for item in data[:limit]:
        try:
            rec_id = item.get('id')
            title = item.get(name_key) or ''
            if not rec_id or not title:
                continue
            out.append({
                'tmdb_id': str(rec_id),
                'type': media_type,
                'title': title,
                'poster': TMDB_IMAGE_BASE % (poster_size, item['poster_path']) if item.get('poster_path') else '',
                'fanart': TMDB_IMAGE_BASE % (fanart_size, item['backdrop_path']) if item.get('backdrop_path') else '',
                'year': str(item.get(date_key) or '')[:4],
                'plot': item.get('overview') or '',
                'rating': str(round(float(item.get('vote_average') or 0), 1)),
            })
        except Exception:
            continue
    return out


def build(handle, params):
    t_start = time.time()
    media_type = params.get('mediatype', 'movie')
    if media_type == 'tv':
        media_type = 'tvshow'
    if media_type not in ('movie', 'tvshow'):
        media_type = 'movie'
    tmdb_id = params.get('tmdb_id')
    try:
        limit = max(1, min(int(params.get('limit', 10)), 20))
    except Exception:
        limit = 10

    poster_size, fanart_size = _resolution()
    name_key = 'title' if media_type == 'movie' else 'name'
    date_key = 'release_date' if media_type == 'movie' else 'first_air_date'
    try:
        fanart_fallback = _pov_addon().getAddonInfo('fanart') or ''
    except Exception:
        fanart_fallback = ''

    data = _recommendations_data(media_type, tmdb_id) if tmdb_id else []
    items = []
    for item in data[:limit]:
        try:
            rec_id = item.get('id')
            title = item.get(name_key) or ''
            if not rec_id or not title:
                continue
            poster = TMDB_IMAGE_BASE % (poster_size, item['poster_path']) if item.get('poster_path') else ''
            backdrop = TMDB_IMAGE_BASE % (fanart_size, item['backdrop_path']) if item.get('backdrop_path') else fanart_fallback
            year = str(item.get(date_key) or '')[:4]
            plot = item.get('overview') or ''
            rating = float(item.get('vote_average') or 0)
            url, is_folder = _target(media_type, rec_id)
            listitem = xbmcgui.ListItem(label=title, offscreen=True)
            listitem.addContextMenuItems(_context_menu(media_type, rec_id))
            listitem.setArt({
                'icon': poster, 'poster': poster, 'thumb': poster,
                'fanart': backdrop, 'landscape': backdrop, 'banner': poster
            })
            listitem.setProperties({
                'poster': poster, 'thumb': poster, 'fanart': backdrop, 'landscape': backdrop,
                'tmdb_id': str(rec_id), 'DBType': media_type, 'Plot': plot, 'Overview': plot,
                'Year': year, 'Rating': str(rating), 'source_tmdb_id': str(tmdb_id)
            })
            _set_video_info(listitem, media_type, title, rec_id, year, plot, rating)
            items.append((url, listitem, is_folder))
        except Exception:
            pass

    if items:
        xbmcplugin.addDirectoryItems(handle, items, len(items))
    xbmcplugin.setContent(handle, 'movies' if media_type == 'movie' else 'tvshows')
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
    xbmc.log('[ShowIMDB][Recs] build %s tmdb=%s itens=%d total=%.2fs' % (
        media_type, tmdb_id, len(items), time.time() - t_start), xbmc.LOGINFO)

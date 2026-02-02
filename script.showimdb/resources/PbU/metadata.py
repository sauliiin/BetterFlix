from indexers import tmdb_api as tmdb, fanarttv_api as fanarttv
from caches.meta_cache import MetaCache
from modules.utils import jsondate_to_datetime, subtract_dates, TaskPool
from modules.kodi_utils import logger

movie_data, tvshow_data, tmdb_english_translation = tmdb.movie_details, tmdb.tvshow_details, tmdb.english_translation
movie_external_id, tvshow_external_id, season_episodes_details = tmdb.movie_external_id, tmdb.tvshow_external_id, tmdb.season_episodes_details
default_fanarttv_data, fanarttv_get, fanarttv_add = fanarttv.default_fanart_nometa, fanarttv.get, fanarttv.add
subtract_dates_function, jsondate_to_datetime_function = subtract_dates, jsondate_to_datetime
backup_resolutions, writer_credits = {'poster': 'w780', 'fanart': 'w1280', 'still': 'original', 'profile': 'h632', 'logo': 'original'}, ('Author', 'Writer', 'Screenplay', 'Characters')
alt_titles_test, trailers_test, finished_show_check, empty_value_check = ('US', 'GB', 'UK', ''), ('Trailer', 'Teaser'), ('Ended', 'Canceled'), ('', 'None', None)
tmdb_image_base, youtube_url, date_format = tmdb.tmdb_image_base, 'plugin://plugin.video.youtube/play/?video_id=%s', '%Y-%m-%d'
EXPIRES_2_DAYS, EXPIRES_4_DAYS, EXPIRES_7_DAYS, EXPIRES_14_DAYS, EXPIRES_182_DAYS = 2, 4, 7, 14, 182
# Idioma preferido fixo para FanartTV (só aceita 'pt', não pt-BR ou pt-PT)
PREFERRED_LANGUAGE = 'pt'

def select_best_clearlogo(tmdb_options, fanart_options, image_resolution):
    """
    Seleciona o melhor clearlogo seguindo a ordem de prioridade:
    1. TMDB pt-BR (mais votado)
    2. TMDB pt-PT (mais votado)
    3. TMDB pt (mais votado)
    4. FanartTV pt (mais likes)
    5. FanartTV en (mais likes)
    6. FanartTV qualquer idioma (mais likes)
    7. TMDB en (mais votado)
    8. TMDB qualquer idioma (mais votado)
    """
    logo_resolution = image_resolution.get('logo', 'original')
    
    # 1. TMDB pt-BR
    if tmdb_options and tmdb_options.get('pt-BR'):
        return tmdb_image_base % (logo_resolution, tmdb_options['pt-BR'][0][0])
    
    # 2. TMDB pt-PT
    if tmdb_options and tmdb_options.get('pt-PT'):
        return tmdb_image_base % (logo_resolution, tmdb_options['pt-PT'][0][0])
    
    # 3. TMDB pt
    if tmdb_options and tmdb_options.get('pt'):
        return tmdb_image_base % (logo_resolution, tmdb_options['pt'][0][0])
    
    # 4. FanartTV pt
    if fanart_options and fanart_options.get('pt'):
        return fanart_options['pt'][0][0]
    
    # 5. FanartTV en
    if fanart_options and fanart_options.get('en'):
        return fanart_options['en'][0][0]
    
    # 6. FanartTV qualquer idioma
    if fanart_options and fanart_options.get('other'):
        return fanart_options['other'][0][0]
    
    # 7. TMDB en
    if tmdb_options and tmdb_options.get('en'):
        return tmdb_image_base % (logo_resolution, tmdb_options['en'][0][0])
    
    # 8. TMDB qualquer idioma
    if tmdb_options and tmdb_options.get('other'):
        return tmdb_image_base % (logo_resolution, tmdb_options['other'][0][0])
    
    return ''

def select_best_poster(tmdb_options, fanart_options, image_resolution):
    """
    Seleciona o melhor poster seguindo a ordem de prioridade:
    1. TMDB pt-BR (mais votado)
    2. TMDB pt-PT (mais votado)
    3. TMDB pt (mais votado)
    4. FanartTV pt (mais likes)
    5. FanartTV en (mais likes)
    6. FanartTV qualquer idioma (mais likes)
    7. TMDB en (mais votado)
    8. TMDB qualquer idioma (mais votado)
    """
    poster_resolution = image_resolution.get('poster', 'w780')
    
    # 1. TMDB pt-BR
    if tmdb_options and tmdb_options.get('pt-BR'):
        return tmdb_image_base % (poster_resolution, tmdb_options['pt-BR'][0][0])
    
    # 2. TMDB pt-PT
    if tmdb_options and tmdb_options.get('pt-PT'):
        return tmdb_image_base % (poster_resolution, tmdb_options['pt-PT'][0][0])
    
    # 3. TMDB pt
    if tmdb_options and tmdb_options.get('pt'):
        return tmdb_image_base % (poster_resolution, tmdb_options['pt'][0][0])
    
    # 4. FanartTV pt
    if fanart_options and fanart_options.get('pt'):
        return fanart_options['pt'][0][0]
    
    # 5. FanartTV en
    if fanart_options and fanart_options.get('en'):
        return fanart_options['en'][0][0]
    
    # 6. FanartTV qualquer idioma
    if fanart_options and fanart_options.get('other'):
        return fanart_options['other'][0][0]
    
    # 7. TMDB en
    if tmdb_options and tmdb_options.get('en'):
        return tmdb_image_base % (poster_resolution, tmdb_options['en'][0][0])
    
    # 8. TMDB qualquer idioma
    if tmdb_options and tmdb_options.get('other'):
        return tmdb_image_base % (poster_resolution, tmdb_options['other'][0][0])
    
    return ''

def select_best_image(tmdb_pt, tmdb_fallback, fanart_data, fanart_key):
    """Função legada para compatibilidade com outros tipos de imagem"""
    # Prioridade: 1) TMDB em português, 2) FanartTV (já filtrado por idioma), 3) TMDB fallback (inglês)
    if tmdb_pt:
        return tmdb_pt
    if fanart_data and fanart_data.get(fanart_key):
        return fanart_data[fanart_key]
    if tmdb_fallback:
        return tmdb_fallback
    return ''

def movie_meta(id_type, media_id, user_info, current_date):
    if id_type in ('trakt_dict', 'traktdict'):
        if media_id.get('tmdb'):
            id_type, media_id = 'tmdb_id', media_id['tmdb']
        elif media_id.get('imdb'):
            id_type, media_id = 'imdb_id', media_id['imdb']
        else:
            id_type, media_id = None, None
    if media_id is None:
        return {}
    
    if id_type == 'tmdb_id' and str(media_id).startswith('tt'):
        id_type = 'imdb_id'
        
    metacache = MetaCache()
    metacache_get, metacache_set = metacache.get, metacache.set
    language = user_info['language']
    extra_fanart_enabled = user_info['extra_fanart_enabled']
    fanart_client_key = user_info['fanart_client_key']
    
    # Usa idioma fixo (pt) para FanartTV, ignorando configuração do usuário
    fanart_lang = PREFERRED_LANGUAGE

    fanarttv_data = None
    meta = metacache_get('movie', id_type, media_id)
    if meta:
        if 'tmdb_id' in meta:
            # Retorna o cache diretamente - a lógica de priorização já está em select_best_clearlogo/poster
            return meta
        else:
            fanarttv_data = dict(meta)
    try:
        tmdb_api = user_info['tmdb_api']
        if id_type == 'tmdb_id':
            data = movie_data(media_id, language, tmdb_api)
        elif id_type == 'imdb_id':
            external_result = movie_external_id('imdb_id', media_id, tmdb_api)
            if not external_result:
                return None
            data = movie_data(external_result['id'], language, tmdb_api)
        else:
            external_result = movie_external_id(id_type, media_id, tmdb_api)
            if not external_result:
                return None
            data = movie_data(external_result['id'], language, tmdb_api)
        if not data or not isinstance(data, dict):
            return None
        if data.get('success', True) is False:
            return None
        if 'id' not in data:
            return None

        if language != 'en':
            if data.get('overview') in empty_value_check:
                media_id_temp = data['id']
                eng_data = movie_data(media_id_temp, 'en', tmdb_api)
                if eng_data and isinstance(eng_data, dict):
                    eng_overview = eng_data.get('overview', '')
                    data['overview'] = eng_overview
                    trailer_test = False
                    if 'videos' in data:
                        all_trailers = data['videos'].get('results', [])
                        if all_trailers:
                            try:
                                trailer_test = [i for i in all_trailers if i.get('site') == 'YouTube' and i.get('type') in trailers_test]
                            except:
                                pass
                    if not trailer_test:
                        if 'videos' in eng_data:
                            eng_all_trailers = eng_data['videos'].get('results', [])
                            if eng_all_trailers:
                                if 'videos' not in data:
                                    data['videos'] = {}
                                if 'results' not in data['videos']:
                                    data['videos']['results'] = []
                                data['videos']['results'] = eng_all_trailers
        # Sempre busca FanartTV para posters e clearlogos (ignora extra_fanart_enabled)
        if not fanarttv_data:
            fanarttv_data = fanarttv_get('movies', fanart_lang, data['id'], fanart_client_key)
        meta = build_movie_meta(data, user_info, fanarttv_data)
        metacache_set('movie', id_type, meta, movie_expiry(current_date, meta))
        return meta
    except Exception as e:
        logger('POV', f'[MOVIE_META] EXCEÇÃO: {str(e)}')
        return None

def tvshow_meta(id_type, media_id, user_info, current_date):
    if id_type in ('trakt_dict', 'traktdict'):
        if media_id.get('tmdb'):
            id_type, media_id = 'tmdb_id', media_id['tmdb']
        elif media_id.get('imdb'):
            id_type, media_id = 'imdb_id', media_id['imdb']
        elif media_id.get('tvdb'):
            id_type, media_id = 'tvdb_id', media_id['tvdb']
        else:
            id_type, media_id = None, None
    if media_id is None:
        return {}
        
    if id_type == 'tmdb_id' and str(media_id).startswith('tt'):
        id_type = 'imdb_id'
        
    metacache = MetaCache()
    metacache_get, metacache_set = metacache.get, metacache.set
    language = user_info['language']
    extra_fanart_enabled = user_info['extra_fanart_enabled']
    fanart_client_key = user_info['fanart_client_key']
    
    # Usa idioma fixo (pt) para FanartTV, ignorando configuração do usuário
    fanart_lang = PREFERRED_LANGUAGE

    fanarttv_data = None
    meta = metacache_get('tvshow', id_type, media_id)
    if meta:
        if 'tmdb_id' in meta:
            # Retorna o cache diretamente - a lógica de priorização já está em select_best_clearlogo/poster
            return meta
        else:
            fanarttv_data = dict(meta)
    try:
        tmdb_api = user_info['tmdb_api']
        if id_type == 'tmdb_id':
            data = tvshow_data(media_id, language, tmdb_api)
        elif id_type == 'imdb_id':
            external_result = tvshow_external_id('imdb_id', media_id, tmdb_api)
            if not external_result:
                return None
            data = tvshow_data(external_result['id'], language, tmdb_api)
        else:
            external_result = tvshow_external_id(id_type, media_id, tmdb_api)
            if not external_result:
                return None
            data = tvshow_data(external_result['id'], language, tmdb_api)
        if not data or not isinstance(data, dict):
            return None
        if data.get('success', True) is False:
            return None
        if 'id' not in data:
            return None

        if language != 'en':
            if data.get('overview') in empty_value_check:
                media_id_temp = data['id']
                eng_data = tvshow_data(media_id_temp, 'en', tmdb_api)
                if eng_data and isinstance(eng_data, dict):
                    eng_overview = eng_data.get('overview', '')
                    data['overview'] = eng_overview
                    trailer_test = False
                    if 'videos' in data:
                        all_trailers = data['videos'].get('results', [])
                        if all_trailers:
                            try:
                                trailer_test = [i for i in all_trailers if i.get('site') == 'YouTube' and i.get('type') in trailers_test]
                            except:
                                pass
                    if not trailer_test:
                        if 'videos' in eng_data:
                            eng_all_trailers = eng_data['videos'].get('results', [])
                            if eng_all_trailers:
                                if 'videos' not in data:
                                    data['videos'] = {}
                                if 'results' not in data['videos']:
                                    data['videos']['results'] = []
                                data['videos']['results'] = eng_all_trailers
        # Sempre busca FanartTV para posters e clearlogos (ignora extra_fanart_enabled)
        if not fanarttv_data:
            external_ids = data.get('external_ids', {})
            tvdb_id = external_ids.get('tvdb_id')
            if tvdb_id:
                fanarttv_data = fanarttv_get('tv', fanart_lang, tvdb_id, fanart_client_key)
        meta = build_tvshow_meta(data, user_info, fanarttv_data)
        metacache_set('tvshow', id_type, meta, tvshow_expiry(current_date, meta))
        return meta
    except Exception as e:
        logger('POV', f'[TVSHOW_META] EXCEÇÃO: {str(e)}')
        return None

def season_episodes_meta(season, meta, user_info):
    def _process():
        for ep_data in data:
            writer, director, guest_stars = '', '', []
            ep_data_get = ep_data.get
            title, plot, premiered = ep_data_get('name'), ep_data_get('overview'), ep_data_get('air_date')
            season, episode, ep_type = ep_data_get('season_number'), ep_data_get('episode_number'), ep_data_get('episode_type')
            rating, votes, still_path = ep_data_get('vote_average'), ep_data_get('vote_count'), ep_data_get('still_path')
            ep_type = ep_details.get(ep_type) or ep_details.get(episode) or ep_type or ''
            if ep_type == 'mid_season_finale': ep_details[episode + 1] = 'mid_season_premiere'
            if still_path: thumb = tmdb_image_base % (still_resolution, still_path)
            else: thumb = None
            try: duration = ep_data_get('runtime') * 60
            except: duration = 60 * 60
            guest_stars_list = ep_data_get('guest_stars')
            if guest_stars_list:
                try: guest_stars = [
                    {'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_base % (profile_resolution, i['profile_path']) if i['profile_path'] else ''}
                    for i in guest_stars_list
                ]
                except: pass
            crew = ep_data_get('crew')
            if crew:
                try: writer = ', '.join([i['name'] for i in crew if i['job'] in writer_credits])
                except: pass
                try: director = [i['name'] for i in crew if i['job'] == 'Director'][0]
                except: pass
            yield {
                'thumb': thumb, 'title': title, 'guest_stars': guest_stars, 'plot': plot, 'premiered': premiered,
                'director': director, 'writer': writer, 'rating': rating, 'votes': votes, 'mediatype': 'episode',
                'episode_type': ep_type, 'season': season, 'episode': episode, 'duration': duration
            }
    metacache = MetaCache()
    metacache_get, metacache_set = metacache.get, metacache.set
    media_id, data = meta['tmdb_id'], None
    string = '%s_%s' % (media_id, season)
    data = metacache_get('season', 'tmdb_id', string)
    if data: return data
    try:
        show_ended, total_seasons = meta['status'] in finished_show_check, meta['total_seasons']
        expiration = EXPIRES_182_DAYS if show_ended or total_seasons > int(season) else EXPIRES_4_DAYS
        premiere = 'series_premiere' if int(season) == 1 else 'season_premiere'
        finale = 'series_finale' if show_ended and int(season) == total_seasons else 'season_finale'
        ep_details = {1: premiere, 'mid_season': 'mid_season_finale', 'finale': finale}
        image_resolution = user_info.get('image_resolution', backup_resolutions)
        still_resolution, profile_resolution = image_resolution['still'], image_resolution['profile']
        data = season_episodes_details(media_id, season, user_info['language'], user_info['tmdb_api'])['episodes']
        data = list(_process())
        metacache_set('season', 'tmdb_id', data, expiration, string)
    except: pass
    return data

def all_episodes_meta(meta, user_info, Thread):
    def _get_tmdb_episodes(season):
        try: data.extend(season_episodes_meta(season, meta, user_info))
        except: pass
    try:
        data = []
        seasons = [(i['season_number'],) for i in meta['season_data']]
        for i in TaskPool().tasks(_get_tmdb_episodes, seasons, Thread): i.join()
    except: pass
    return data

def english_translation(media_type, media_id, user_info):
    key = 'title' if media_type == 'movie' else 'name'
    translations = tmdb_english_translation(media_type, media_id, user_info['tmdb_api'])
    try: english = [i['data'][key] for i in translations if i['iso_639_1'] == 'en'][0]
    except: english = ''
    return english

def movie_expiry(current_date, meta):
    try:
        difference = subtract_dates_function(current_date, jsondate_to_datetime_function(meta['premiered'], date_format, remove_time=True))
        if difference < 0: expiration = abs(difference) + 1
        elif difference <= 14: expiration = EXPIRES_7_DAYS
        elif difference <= 30: expiration = EXPIRES_14_DAYS
        else: expiration = EXPIRES_182_DAYS
    except: return EXPIRES_7_DAYS
    return max(expiration, EXPIRES_7_DAYS)

def tvshow_expiry(current_date, meta):
    try:
        if meta['status'] in finished_show_check: return EXPIRES_182_DAYS
        next_episode_to_air = meta['extra_info'].get('next_episode_to_air')
        if not next_episode_to_air: return EXPIRES_7_DAYS
        expiration = subtract_dates_function(jsondate_to_datetime_function(next_episode_to_air['air_date'], date_format, remove_time=True), current_date)
    except: return EXPIRES_4_DAYS
    return max(expiration, EXPIRES_4_DAYS)

def build_movie_meta(data, user_info, fanarttv_data=None):
    image_resolution = user_info.get('image_resolution', backup_resolutions)
    data_get = data.get
    cast, all_trailers, country, country_codes = [], [], [], []
    writer, mpaa, director, trailer, studio = '', '', '', '', ''
    tmdb_id, imdb_id = data_get('id', ''), data_get('imdb_id', '')
    rating, votes = data_get('vote_average', ''), data_get('vote_count', '')
    plot, tagline, premiered = data_get('overview', ''), data_get('tagline', ''), data_get('release_date', '')
    
    backdrop_path = data_get('backdrop_path')
    if backdrop_path: fanart = tmdb_image_base % (image_resolution['fanart'], backdrop_path)
    else: fanart = ''
    
    path_poster_pt = data_get('poster_path')
    path_poster_fallback = data_get('poster_path_fallback')
    if path_poster_pt:
        tmdbposter_pt = tmdb_image_base % (image_resolution['poster'], path_poster_pt)
    else:
        tmdbposter_pt = ''
    if path_poster_fallback:
        tmdbposter_fallback = tmdb_image_base % (image_resolution['poster'], path_poster_fallback)
    else:
        tmdbposter_fallback = ''
    
    path_logo_pt = data_get('clearlogo_path')
    path_logo_fallback = data_get('clearlogo_path_fallback')
    if path_logo_pt:
        logo_resolution = image_resolution.get('logo', 'original')
        tmdblogo_pt = tmdb_image_base % (logo_resolution, path_logo_pt)
    else: 
        tmdblogo_pt = ''
    if path_logo_fallback:
        logo_resolution = image_resolution.get('logo', 'original')
        tmdblogo_fallback = tmdb_image_base % (logo_resolution, path_logo_fallback)
    else: 
        tmdblogo_fallback = ''

    title, original_title = data_get('title'), data_get('original_title')
    try: english_title = [i['data']['title'] for i in data_get('translations')['translations'] if i['iso_639_1'] == 'en'][0]
    except: english_title = None
    try: year = str(data_get('release_date').split('-')[0] or 0)
    except: year = ''
    try: duration = int(data_get('runtime', '90') * 60)
    except: duration = 0
    try: genre = ', '.join([i['name'] for i in data_get('genres')])
    except: genre = ''
    rootname = '%s (%s)' % (title, year)
    companies = data_get('production_companies')
    if companies:
        if len(companies) == 1: studio = [i['name'] for i in companies][0]
        else:
            try: studio = [i['name'] for i in companies if i['logo_path'] not in empty_value_check][0] or [i['name'] for i in companies][0]
            except: pass
    production_countries = data_get('production_countries')
    if production_countries:
        country = [i['name'] for i in production_countries]
        country_codes = [i['iso_3166_1'] for i in production_countries]
    release_dates = data_get('release_dates')
    if release_dates:
        try: mpaa = [
            x['certification']
            for i in release_dates['results']
            for x in i['release_dates']
            if i['iso_3166_1'] == 'US' and x['certification']
        ][0]
        except: pass
    credits = data_get('credits')
    if credits:
        all_cast = credits.get('cast')
        if all_cast:
            try: cast = [
                {'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_base % (image_resolution['profile'], i['profile_path']) if i['profile_path'] else ''}
                for i in all_cast
            ]
            except: pass
        crew = credits.get('crew')
        if crew:
            try: writer = ', '.join([i['name'] for i in crew if i['job'] in writer_credits])
            except: pass
            try: director = [i['name'] for i in crew if i['job'] == 'Director'][0]
            except: pass
    alternative_titles = data_get('alternative_titles')
    if alternative_titles:
        alternatives = alternative_titles['titles']
        alternative_titles = [i['title'] for i in alternatives if i['iso_3166_1'] in alt_titles_test]
    videos = data_get('videos')
    if videos:
        all_trailers = videos['results']
        try: trailer = [youtube_url % i['key'] for i in all_trailers if i['site'] == 'YouTube' and i['type'] in trailers_test][0]
        except: pass
    status, homepage = data_get('status', 'N/A'), data_get('homepage', 'N/A')
    belongs_to_collection = data_get('belongs_to_collection')
    if belongs_to_collection: ei_collection_name, ei_collection_id = belongs_to_collection['name'], belongs_to_collection['id']
    else: ei_collection_name, ei_collection_id = None, None
    try: ei_budget = '${:,}'.format(data_get('budget'))
    except: ei_budget = '$0'
    try: ei_revenue = '${:,}'.format(data_get('revenue'))
    except: ei_revenue = '$0'
    extra_info = {'status': status, 'collection_name': ei_collection_name, 'collection_id': ei_collection_id, 'budget': ei_budget, 'revenue': ei_revenue, 'homepage': homepage}
    meta_dict = {
        'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': 'None', 'imdbnumber': imdb_id, 'tmdblogo': tmdblogo_pt,
        'poster': tmdbposter_pt, 'fanart': fanart, 'year': year, 'title': title, 'rootname': rootname,
        'original_title': original_title, 'english_title': english_title, 'alternative_titles': alternative_titles,
        'tagline': tagline, 'plot': plot, 'mpaa': mpaa, 'studio': studio, 'director': director, 'writer': writer,
        'duration': duration, 'premiered': premiered, 'genre': genre, 'rating': rating, 'votes': votes,
        'country': country, 'country_codes': country_codes, 'trailer': trailer, 'all_trailers': all_trailers,
        'cast': cast, 'extra_info': extra_info, 'mediatype': 'movie', 'meta_language': user_info.get('language', ''),
        'tmdb_clearlogo': tmdblogo_pt
    }
    
    # Atualiza com dados do FanartTV
    if fanarttv_data: 
        meta_dict.update(fanarttv_data)
        # Se extra_fanart_enabled estiver desativado, limpa as artes extras (mantém poster e clearlogo)
        if not user_info.get('extra_fanart_enabled', False):
            meta_dict['fanart2'] = ''
            meta_dict['banner'] = ''
            meta_dict['clearart'] = ''
            meta_dict['landscape'] = ''
            meta_dict['discart'] = ''
            meta_dict['fanart_added'] = False
    else: 
        meta_dict.update(default_fanarttv_data)
    
    # Usa a nova função para poster com ordem de prioridade completa (sempre, independente de extra_fanart_enabled)
    tmdb_poster_options = data.get('poster_options', {})
    fanart_poster_options = fanarttv_data.get('poster2_options', {}) if fanarttv_data else {}
    meta_dict['poster'] = select_best_poster(tmdb_poster_options, fanart_poster_options, image_resolution)
    
    # Usa a nova função para clearlogo com ordem de prioridade completa (sempre, independente de extra_fanart_enabled)
    tmdb_clearlogo_options = data.get('clearlogo_options', {})
    fanart_clearlogo_options = fanarttv_data.get('clearlogo_options', {}) if fanarttv_data else {}
    meta_dict['clearlogo'] = select_best_clearlogo(tmdb_clearlogo_options, fanart_clearlogo_options, image_resolution)
        
    return meta_dict

def build_tvshow_meta(data, user_info, fanarttv_data=None):
    image_resolution = user_info.get('image_resolution', backup_resolutions)
    data_get = data.get
    cast, all_trailers, country, country_codes = [], [], [], []
    writer, mpaa, director, trailer, studio = '', '', '', '', ''
    external_ids = data_get('external_ids')
    tmdb_id, imdb_id, tvdb_id = data_get('id', ''), external_ids.get('imdb_id', ''), external_ids.get('tvdb_id', 'None')
    rating, votes = data_get('vote_average', ''), data_get('vote_count', '')
    plot, tagline, premiered = data_get('overview', ''), data_get('tagline', ''), data_get('first_air_date', '')
    season_data, total_seasons, total_aired_eps = data_get('seasons'), data_get('number_of_seasons'), data_get('number_of_episodes')
    
    backdrop_path = data_get('backdrop_path')
    if backdrop_path: fanart = tmdb_image_base % (image_resolution['fanart'], backdrop_path)
    else: fanart = ''
    
    path_poster_pt = data_get('poster_path')
    path_poster_fallback = data_get('poster_path_fallback')
    if path_poster_pt:
        tmdbposter_pt = tmdb_image_base % (image_resolution['poster'], path_poster_pt)
    else:
        tmdbposter_pt = ''
    if path_poster_fallback:
        tmdbposter_fallback = tmdb_image_base % (image_resolution['poster'], path_poster_fallback)
    else:
        tmdbposter_fallback = ''
    
    path_logo_pt = data_get('clearlogo_path')
    path_logo_fallback = data_get('clearlogo_path_fallback')
    if path_logo_pt:
        logo_resolution = image_resolution.get('logo', 'original')
        tmdblogo_pt = tmdb_image_base % (logo_resolution, path_logo_pt)
    else: 
        tmdblogo_pt = ''
    if path_logo_fallback:
        logo_resolution = image_resolution.get('logo', 'original')
        tmdblogo_fallback = tmdb_image_base % (logo_resolution, path_logo_fallback)
    else: 
        tmdblogo_fallback = ''

    title, original_title = data_get('name'), data_get('original_name')
    try: english_title = [i['data']['name'] for i in data_get('translations')['translations'] if i['iso_639_1'] == 'en'][0]
    except: english_title = None
    try: year = str(data_get('first_air_date').split('-')[0] or 0)
    except: year = ''
    try: duration = min(data_get('episode_run_time')) * 60
    except: duration = 0
    try: genre = ', '.join([i['name'] for i in data_get('genres')])
    except: genre = ''
    rootname = '%s (%s)' % (title, year)
    networks = data_get('networks')
    if networks:
        if len(networks) == 1: studio = [i['name'] for i in networks][0]
        else:
            try: studio = [i['name'] for i in networks if i['logo_path'] not in empty_value_check][0] or [i['name'] for i in networks][0]
            except: pass
    production_countries = data_get('production_countries')
    if production_countries:
        country = [i['name'] for i in production_countries]
        country_codes = [i['iso_3166_1'] for i in production_countries]
    content_ratings = data_get('content_ratings')
    release_dates = data_get('release_dates')
    if content_ratings:
        try: mpaa = [i['rating'] for i in content_ratings['results'] if i['iso_3166_1'] == 'US'][0]
        except: pass
    elif release_dates:
        try: mpaa = [i['release_dates'][0]['certification'] for i in release_dates['results'] if i['iso_3166_1'] == 'US'][0]
        except: pass
    credits = data_get('credits')
    if credits:
        all_cast = credits.get('cast')
        if all_cast:
            try: cast = [
                {'name': i['name'], 'role': i['character'], 'thumbnail': tmdb_image_base % (image_resolution['profile'], i['profile_path']) if i['profile_path'] else ''}
                for i in all_cast
            ]
            except: pass
        crew = credits.get('crew')
        if crew:
            try: writer = ', '.join([i['name'] for i in crew if i['job'] in writer_credits])
            except: pass
            try: director = [i['name'] for i in crew if i['job'] == 'Director'][0]
            except: pass
    alternative_titles = data_get('alternative_titles')
    if alternative_titles:
        alternatives = alternative_titles['results']
        alternative_titles = [i['title'] for i in alternatives if i['iso_3166_1'] in alt_titles_test]
    videos = data_get('videos')
    if videos:
        all_trailers = videos['results']
        try: trailer = [youtube_url % i['key'] for i in all_trailers if i['site'] == 'YouTube' and i['type'] in trailers_test][0]
        except: pass
    status, _type, homepage = data_get('status', 'N/A'), data_get('type', 'N/A'), data_get('homepage', 'N/A')
    created_by = data_get('created_by')
    if created_by:
        try: ei_created_by = ', '.join([i['name'] for i in created_by])
        except: ei_created_by = 'N/A'
    else: ei_created_by = 'N/A'
    ei_next_ep = data_get('next_episode_to_air')
    ei_last_ep = data_get('last_episode_to_air')
    if ei_last_ep and not status in finished_show_check: total_aired_eps = ei_last_ep['episode_number'] + sum([
            i['episode_count'] for i in season_data if 0 < i['season_number'] < ei_last_ep['season_number']
        ])
    extra_info = {'status': status, 'type': _type, 'homepage': homepage, 'created_by': ei_created_by, 'next_episode_to_air': ei_next_ep, 'last_episode_to_air': ei_last_ep}
    meta_dict = {
        'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': tvdb_id, 'imdbnumber': imdb_id, 'tmdblogo': tmdblogo_pt,
        'poster': tmdbposter_pt, 'fanart': fanart, 'year': year, 'title': title, 'rootname': rootname, 'tvshowtitle': title,
        'original_title': original_title, 'english_title': english_title, 'alternative_titles': alternative_titles,
        'tagline': tagline, 'plot': plot, 'mpaa': mpaa, 'studio': studio, 'director': director, 'writer': writer,
        'duration': duration, 'premiered': premiered, 'genre': genre, 'rating': rating, 'votes': votes,
        'country': country, 'country_codes': country_codes, 'trailer': trailer, 'all_trailers': all_trailers,
        'cast': cast, 'extra_info': extra_info, 'mediatype': 'tvshow', 'meta_language': user_info.get('language', ''),
        'status': status, 'total_aired_eps': total_aired_eps, 'total_seasons': total_seasons, 'season_data': season_data,
        'tmdb_clearlogo': tmdblogo_pt
    }
    
    # Atualiza com dados do FanartTV
    if fanarttv_data: 
        meta_dict.update(fanarttv_data)
        # Se extra_fanart_enabled estiver desativado, limpa as artes extras (mantém poster e clearlogo)
        if not user_info.get('extra_fanart_enabled', False):
            meta_dict['fanart2'] = ''
            meta_dict['banner'] = ''
            meta_dict['clearart'] = ''
            meta_dict['landscape'] = ''
            meta_dict['discart'] = ''
            meta_dict['fanart_added'] = False
    else: 
        meta_dict.update(default_fanarttv_data)
    
    # Usa a nova função para poster com ordem de prioridade completa (sempre, independente de extra_fanart_enabled)
    tmdb_poster_options = data.get('poster_options', {})
    fanart_poster_options = fanarttv_data.get('poster2_options', {}) if fanarttv_data else {}
    meta_dict['poster'] = select_best_poster(tmdb_poster_options, fanart_poster_options, image_resolution)
    
    # Usa a nova função para clearlogo com ordem de prioridade completa (sempre, independente de extra_fanart_enabled)
    tmdb_clearlogo_options = data.get('clearlogo_options', {})
    fanart_clearlogo_options = fanarttv_data.get('clearlogo_options', {}) if fanarttv_data else {}
    meta_dict['clearlogo'] = select_best_clearlogo(tmdb_clearlogo_options, fanart_clearlogo_options, image_resolution)

    return meta_dict

def get_title(meta, language=None):
    if 'custom_title' in meta: return meta['custom_title']
    if not language: language = meta.get('meta_language', '')
    if language == 'en': title = meta['title']
    else: title = meta.get('english_title')
    if not title:
        try:
            from settings import metadata_user_info
            meta_user_info = metadata_user_info()
            media_type = 'movie' if meta['media_type'] == 'movie' else 'tv'
            english_title = tmdb_english_translation(media_type, meta['tmdb_id'], meta_user_info)
            if english_title: title = english_title
            else: title = meta['original_title']
        except: pass
    if not title: title = meta['original_title']
    if '(' in title: title = title.split('(')[0]
    if '/' in title: title = title.replace('/', ' ')
    return title

def rpdb_get(media_type, media_id, api_key):
    if api_key and media_id:
        if media_id.startswith('tt'): id_type = 'imdb'
        else: id_type, media_id = 'tmdb', '%s-%s' % (media_type, media_id)
        url = 'https://api.ratingposterdb.com/%s/%s/poster-default/%s.jpg'
        rpdb_data = {'rpdb': url % (api_key, id_type, media_id), 'rpdb_added': True}
    else: rpdb_data = {'rpdb': '', 'rpdb_added': False}
    return rpdb_data
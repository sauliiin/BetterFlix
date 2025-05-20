##############################

import requests
from typing import Union
from urllib.parse import quote_plus

import scripts.helper as H
import scripts.modules.cache as cache

##############################

__API_KEY = "f8af8898eb005a02fb5c962811436a60"
__API_BASEURL = "https://api.themoviedb.org/3"
__CACHE_LIFE = H.Settings.getCacheTimeout()
__KEY_FMT = "tmdb_api_%s_%s"
""" %s1 = key, %s2 = param """
__TMDB_SEARCH = __API_BASEURL + "/search/%s?api_key=" + __API_KEY + "&query="
""" %s = dbtype, suffix = query param """
__TMDB_EXTERNAL_IDS = __API_BASEURL + "/%s/%s/external_ids?api_key=" + __API_KEY
""" %s1 = dbtype, %s2 = tmdb_id """

##############################

def GetIMDbFromTMDbSearchByTitle(title:str, dbtype:str) -> Union[str, None]:
    """
    Searches TMDb for the given Title of given dbType and attempts to return the IMDb ID.
    """
    title = quote_plus(title)

    key1 = __KEY_FMT % ("search", title)
    tmdb_id = cache.get(key1, __CACHE_LIFE, __tmdb_search, title, dbtype)
    if not tmdb_id or tmdb_id == None:
        return None
    
    key2 = __KEY_FMT % ("external_ids", tmdb_id)
    imdb_id = cache.get(key2, __CACHE_LIFE, __tmdb_external_ids, tmdb_id, dbtype)
    if not imdb_id or imdb_id == None:
        return None
    
    return str(imdb_id)
# end of GetIMDbFromTMDb() function

##############################

def __api_call_get(url:str) -> dict:
    """
    Request GET call on url. Returns dictionary if successful, throws error if unsuccessful.
    """
    request = requests.get(url)
    if request.status_code != 200:
        raise Exception("Request Status Code: " + request.status_code)
    response = dict(request.json())
    request.close()
    return response
# end of __api_call_get()

##############################

def __tmdb_search(title:str, dbtype:str) -> Union[str, None]:
    """
    TMDb API call to search for the given title and dbType. Returns TMDb id as string if success, None otherwise.
    """
    url = (__TMDB_SEARCH % dbtype) + title
    H.logdebug("TMDb Search call: %s - %s" % (dbtype, title))

    try:
        data = __api_call_get(url)
    except Exception as e:
        H.logdebug("ERROR: " + repr(e))
        return None
    
    results = data.get("results", [])
    if not results or results == [] or len(results) == 0:
        return None
    
    first_result = H.WrapDict(results[0])
    tmdb_id = first_result.get("id", None)
    H.logdebug("TMDb Search call result: " + repr(tmdb_id))
    return tmdb_id
# end of __tmdb_search()

def __tmdb_external_ids(tmdb_id:str, dbtype:str) -> Union[str, None]:
    """
    TMDB API call to get external IDs of given tmdb_id and dbType. Returns IMDb id as string if success, None otherwise.
    """
    url = __TMDB_EXTERNAL_IDS % (dbtype, tmdb_id)
    H.logdebug("TMDb ExternalIDs call: %s - %s" % (dbtype, tmdb_id))

    try:
        data = __api_call_get(url)
    except Exception as e:
        H.logdebug("ERROR: " + repr(e))
        return None
    
    imdb_id = data.get("imdb_id", None)
    H.logdebug("TMDb ExternalIDs call result: " + repr(imdb_id))
    return imdb_id
# end of __tmdb_external_ids()

##############################

##############################

from sqlite3 import dbapi2 as db, OperationalError
from typing import Union
import json
import os
import time
import xbmcvfs
import xbmc

import scripts.helper as H

##############################

METACACHE = 'metacache'
CACHE_CLEANUP = 'cache.lastclean'

__makeFile = xbmcvfs.mkdir
__transPath = xbmc.translatePath if H.GetKodiVersion() < 19 else xbmcvfs.translatePath
__dataPath = __transPath(H.DATA_PATH)
__cacheFile = os.path.join(__dataPath, 'cache.db')

__cache_table = 'caches'
__metadata_table = 'metadatas'
__tablesDict = {
    __cache_table: "CREATE TABLE IF NOT EXISTS %s (key TEXT, value TEXT, time INTEGER, UNIQUE(key));" % __cache_table,
    __metadata_table: "CREATE TABLE IF NOT EXISTS %s (imdb TEXT, tmdb TEXT, meta TEXT, time INTEGER, UNIQUE(imdb, tmdb));" % __metadata_table
}

##############################

def CleanupIfRequired():
    """
    Cleans up the cache file once every day.
    """
    try:
        tnow = int(time.time())
        try:
            lastCleanup = int(H.Settings.getSetting(CACHE_CLEANUP))
        except:
            lastCleanup = tnow
        keepfor = 60 * 60 * 24 # one day (seconds * minutes * hours)
        
        if (lastCleanup + keepfor) < tnow:
            if H.DEBUG:
                H.log("Cache cleanup: clearing out old data...")
            cache_clear()
            H.Settings.setSetting(CACHE_CLEANUP, tnow)
    except Exception as e:
        if H.DEBUG:
            H.logerror(e)
# end of CheckIfCleanup() function

def get(cache_key:str, cachelife_seconds:int, freshcall_func, *args, **argz) -> Union[object, None]:
    """
    Attempts to retrieve data from cache if it exists and isn't expired
    else attempts to retrieve new data.

    Returns an Object, otherwise None if errored.

    Logs errors if DEBUG enabled.
    """
    try:
        H.log(f"Cache: Fetching data for '{cache_key}'..")
        cached = cache_get(cache_key)
        # if cache exists, check if it's expired
        if cached:
            __logd("Cache exists.")
            tnow = int(time.time())
            ctime = int(cached.get('time', 0))
            elapsed_time = (tnow - ctime)
            value = cached.get('value', '')
            value = json.loads(value)
            
            if (elapsed_time <= cachelife_seconds) and (value != '') and (value != []) and (value != {}) and (value != [{}]):
                __logd(f"Cache still valid for {str(int(cachelife_seconds - elapsed_time))} seconds, returning cached value..")
                return value
            else:
                __logd("Cache expired..")
    except Exception as e:
        if H.DEBUG:
            H.logerror(f"Trying to cache.Get({cache_key}, {str(cachelife_seconds)}):")
            H.logerror(e)
        pass

    __logd("Retrieving new data..")

    success = False
    try:
        response = freshcall_func(*args)
        success = True
    except Exception as e:
        if H.DEBUG:
            H.logerror(f"Error trying to retrieve fresh data using cache.Get({cache_key}, {str(cachelife_seconds)}):")
            H.logerror(e)
        return None
    
    try:
        if success and (response != '') and (response != []) and (response != {}) and (response != [{}]):
            jstr = json.dumps(response)
            cache_insert(cache_key, jstr)
            __logd("Cached new data, returning results..")
            return response
        else:
            if (response != '') and (response != []) and (response != {}) and (response != [{}]):
                __logd(f"Request failed, response was: {response}")
            else:
                __logd("Request failed, response was empty.")
    except Exception as e:
        if H.DEBUG:
            H.logerror(f"Error trying to cache new data using cache.Get({cache_key}, {str(cachelife_seconds)}):")
            H.logerror(e)

    return None
# end of get function

def getMetadata(items:list) -> list:
    """
    Attempts to populate the given list with metadata and return it.
    Receives and returns list[dict].
    """
    try:
        tnow = int(time.time())
        cursor = _get_connection_cursor()
        cursor.execute(__tablesDict.get(__metadata_table))
    except:
        return items

    r = 0
    s = 0
    query = "SELECT * FROM %s WHERE (imdb = ? and imdb != '0') OR (tmdb = ? and tmdb != '0');" % __metadata_table
    for i in range(0, len(items)):
        try:
            itemD = __wrapDict(items[i])
            cursor.execute(query, [itemD.get('imdb', '0'), itemD.get('tmdb', '0')])
            # result should be a dictionary
            result = __wrapDict(cursor.fetchone())

            ctime = int(result.get('time', 0))
            elapsed_time = (tnow - ctime)
            doUpdate = (elapsed_time >= H.Settings.getCacheTimeout())
            if doUpdate == True: raise Exception("Skip this item's metadata, it's old.")
            # else update the item's metadata
            meta = __wrapDict(json.loads(result.get('meta')))
            meta = dict((k,v) for k, v in meta.items() if v != '0')

            itemD.update(meta)
            itemD.update({METACACHE: True})
            r += 1
        except:
            s += 1
            pass
    __logd(f"getMetadata(): retrieved: {str(r)}, skipped: {str(s)}.")
    return items
# end of getMetadata function

def storeMetadata(metadata:list):
    """
    Takes a list of metadata [dict] and attempts to cache them.
    """
    try:
        tnow = int(time.time())
        cursor = _get_connection_cursor()
        cursor.execute(__tablesDict.get(__metadata_table))

        inserted = 0
        errored = 0
        query = "INSERT OR REPLACE INTO %s (imdb, tmdb, meta, time) VALUES (?, ?, ?, ?);" % __metadata_table
        for m in metadata:
            try:
                meta = __wrapDict(m)
                imdb = meta.get('imdb', '0')
                tmdb = meta.get('tmdb', '0')
                if imdb == '0' and tmdb == '0':
                    continue
                cursor.execute(query, [imdb, tmdb, json.dumps(meta), tnow])
                inserted += 1
            except:
                errored += 1

        cursor.connection.commit()
        __logd(f"storeMetadata(): inserted/updated: {str(inserted)}, errored: {str(errored)}.")
    except Exception as ex:
        if H.DEBUG:
            H.logerror(ex)
        return
# end of storeMetadata function

def cache_clear():
    """
    Clears all of this addons cache from the created cache file.
    """
    try:
        cursor = _get_connection_cursor()
        con = cursor.connection

        for t in __tablesDict.keys():
            try:
                cursor.execute(__tablesDict.get(t))
                cursor.execute("DELETE FROM %s;" % t)
                con.commit()
            except :
                pass
        
        cursor.execute("VACUUM;")
        con.commit()
    except Exception as e2:
        pass
# end of cache_clear method

def cache_get(key) -> dict:
    """
    Returns dict{key, value, time} or None
    """
    try:
        cursor = _get_connection_cursor()
        cursor.execute(__tablesDict.get(__cache_table))
        cursor.execute("SELECT * FROM %s WHERE key = ?" % __cache_table, [key])
        return cursor.fetchone()
    except OperationalError:
        return None
# end of cache_get method

def cache_insert(key:str, value:str) -> None:
    """
    Attempts to first update an existing record with the same key,
    else creates a new one
    """
    cursor = _get_connection_cursor()
    now = int(time.time())
    cursor.execute(__tablesDict.get(__cache_table))
    update_result = cursor.execute(
        "UPDATE %s SET value=?, time=? WHERE key=?"
        % __cache_table, (value, now, key))

    if update_result.rowcount == 0:
        cursor.execute(
            "INSERT INTO %s (key, value, time) Values (?, ?, ?)"
            % __cache_table, (key, value, now)
        )

    cursor.connection.commit()
# end of cache_insert method

def _get_connection_cursor() -> db.Cursor:
    conn = _get_connection()
    return conn.cursor()

def _get_connection() -> db.Connection:
    __makeFile(__dataPath)
    conn = db.connect(__cacheFile)
    conn.row_factory = _dict_factory
    return conn

def _dict_factory(cursor:db.Cursor, row) -> dict:
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def __logd(str:str):
    H.logdebug("Cache: " + str)

def __wrapDict(d:dict) -> dict:
    return H.WrapDict(d)

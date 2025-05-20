##############################

import scripts.modules.cache as cache
import scripts.helper as Helper

##############################
#      Helper Functions      #
##############################

def handleMissingParam(params, param:str, debug:bool):
    erMsg = f"A '{param}' must be passed in as a parameter. Params: " + repr(params)
    Helper.log(erMsg)
    if debug:
        Helper.DialogOK(erMsg)
    return

##############################
#      Script Functions      #
##############################

def cache_clear(params=None):
    yes = Helper.DialogYesNo("Are you sure you want to clear the cache?")
    if not yes:
        return
    cache.cache_clear()
    Helper.DialogNotif("Cache cleared!")
    return

def get_debug_param(params:dict) -> bool:
    return (True if str(params.pop('debug', 'false')).lower() == 'true' else False) or Helper.DEBUG

##############################

def getkodisettings(params:dict):
    """
    getkodisettings
        - Gets the value(s) of the Kodi setting(s) asked for and sets them to the window property
        - If no window_id is given, will default to Home aka 10000
        - List of Kodi Settings: https://github.com/xbmc/xbmc/blob/master/system/settings/settings.xml
        - List of Kodi Window IDs: https://kodi.wiki/view/Window_IDs
        - PARAMS:
            - action=getkodisettings
            - {property_name}={kodi.setting} - 1 or more property=setting pairs
            - [window_id={window_id_to_set_value_to_to}] - optional window ID
            - [debug=true] - enable debug output for this call
        - EXAMPLES:
            - set 2 settings in the Home window:
                - RunScript(script.dstealth.helper,
                    action=getkodisettings,
                    ds_kodisetting_language=locale.language,
                    ds_kodisetting_country=locale.country)
            - set 1 setting in the SkinSettings window with debug enabled:
                - RunScript(script.dstealth.helper,
                    action=getkodisettings,
                    window_id=10035,
                    my_custom_property_name=locale.language,
                    debug=true)
    """
    ACTION = str("getkodisettings()")
    WIN_HOME = int(10000)

    debug = get_debug_param(params)
    if debug:
        Helper.log(f"{ACTION} started.")

    # remove non-settings key=value-pairs
    params.pop('action', ACTION)
    window_param = params.pop('window_id', WIN_HOME)
    try:
        window_id = int(window_param)
    except:
        window_id = WIN_HOME
        errMsg = f"{ACTION} unable to parse window_id={window_param}, using {WIN_HOME} (Home) instead. Given window_id must be a number. Refer to https://kodi.wiki/view/Window_IDs for window IDs."
        Helper.log(errMsg)

    if len(params) == 0:
        msg = f"{ACTION} did not receive any window_property=kodi.setting parameters."
        Helper.log(msg)
        if debug:
            Helper.DialogOK(msg)
        return
    
    if (debug):
        Helper.log(f"{ACTION} trying: window_id={str(window_id)}, {repr(params)}")

    # loop through all our window_property=kodi.setting and get n set their values
    errCount = int(0)
    errList = []
    for window_prop in params:
        try:
            kodi_setting = params.get(window_prop)
            json_query = Helper.JsonRPC('Settings.GetSettingValue', params={'setting': kodi_setting})

            result = json_query['result']
            result = result.get('value')
            result = str(result)
            if result.startswith('[') and result.endswith(']'):
                result = result[1:-1]

            Helper.Window.setProp(window_prop, result, window_id)
            if (debug):
                Helper.log(f"{ACTION} Window({str(window_id)}).Property({window_prop}) set to '{result}'.")
        except Exception as e:
            errCount += 1
            errList.append(e)
            try:
                Helper.Window.clearProp(window_prop, window_id)
                if (debug):
                    Helper.log(f"{ACTION} Window({str(window_id)}).Property({window_prop}) cleared due to error.")
            except:
                pass
    
    # error report
    if errCount > 0:
        Helper.log(f"{ACTION} {str(errCount)} errors occurred. If debug is enabled, will output errors next.")
        if debug:
            for err in errList:
                Helper.logerror(err)
    
    if debug:
        Helper.log(f"{ACTION} finished.")
    return

##############################

def setkodisetting(params:dict):
    """
    setkodisetting
        - sets the value of various Kodi Settings
        - List of Kodi Settings: https://github.com/xbmc/xbmc/blob/master/system/settings/settings.xml
        - PARAMS:
            - setting=<kodi.setting>
            - value=<value>
        - EXAMPLE:
            - RunScript(script.dstealth.helper,
                action=setkodisetting,
                setting=input.enablemouse,
                value=false)
    """
    k_setting = 'setting'
    k_value = 'value'
    debug = get_debug_param(params)
    if k_setting not in params:
        handleMissingParam(params, k_setting, debug)
        return
    if k_value not in params:
        handleMissingParam(params, k_value, debug)
        return

    setting = params.get(k_setting, '')
    valueStr = params.get(k_value, '')

    try:
        value = int(valueStr)
    except Exception:
        if valueStr.lower() == 'true':
            value = True
        elif valueStr.lower() == 'false':
            value = False
        else:
            value = valueStr

    if(debug):
        Helper.log("trying... setkodisetting(): {} = {}".format(setting, str(value)))

    Helper.JsonRPC('Settings.SetSettingValue',
              params={'setting': setting, 'value': value},
              debug=debug)
    return

##############################

def trailer(params:dict) -> None:
    import scripts.modules.imdb_trailers as IMDB

    debug = params.get('debug', False)
    imdb = params.get('imdb', '')
    title = params.get('title', '')
    year = params.get('year', imdb)
    dbtype = 'movie' if str(params.get('dbtype', '')).lower() == 'movie' else 'tv'
    autoplay = True if str(params.get('autoplay', '')).lower() == 'true' else False

    tlist = None
    try:
        isIMDbID = str(imdb).lower().strip().startswith('tt')
        if isIMDbID:
            # IMDb ID
            tlist = IMDB.GetIMDBTrailers(imdb)
        
        if not isIMDbID or not tlist or tlist == []:
            import scripts.modules.tmdb_api as TMDB
            imdb_id = TMDB.GetIMDbFromTMDbSearchByTitle(title, dbtype)
            if not imdb_id or imdb_id == None:
                raise Exception()
            tlist = IMDB.GetIMDBTrailers(imdb_id)
    except Exception as e:
        Helper.logerror(e)
        tlist = None
        pass

    if not tlist or tlist == []:
        Helper.DialogOK("Could not find any trailers for " + ('%s (%s)' % (title, year)))
        return

    # sort the list by official trailers first
    tlist = [v for v in tlist if 'official' in v['title'].lower()] + \
            [v for v in tlist if 'trailer' in v['title'].lower() and 'official' not in v['title'].lower()] + \
            [v for v in tlist if 'trailer' not in v['title'].lower() and 'official' not in v['title'].lower()]

    if autoplay:
        selected = tlist[0]
    else:
        vids = []
        for t in tlist:
            mi = Helper.GUI.MenuItem(t['title'], t['duration'])
            icon = t['icon']
            mi.setArt(icon=icon, thumb=icon, poster=icon)
            vids.append(mi.getListItem())

        response = Helper.DialogSelect("Select a Trailer", vids, useDetails=True)
        if response == -1:
            return # cancelled
        selected = tlist[response]
    
    icon = selected['icon']
    menuitem = Helper.GUI.MenuItem(selected['title'], selected['duration'], selected['video'])
    menuitem.setArt(icon=icon, thumb=icon, poster=icon)
    menuitem.setProperties({'isFolder': 'false', 'IsPlayable': 'true'})

    Helper.Play(selected['video'], menuitem.getListItem(), debug=debug)
    return

##############################

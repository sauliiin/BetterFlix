##############################

import json
import traceback

import xbmc
import xbmcaddon
import xbmcgui

from typing import List

##############################

SCRIPT_ID = "dstealth-helper.py"
PLUGIN_ID = "plugin://script.dstealth.helper/"

ADDON = xbmcaddon.Addon()
ADDON_ICON = ADDON.getAddonInfo('icon')
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
DATA_PATH = ADDON.getAddonInfo('profile')

DEBUG = True if xbmcaddon.Addon().getSetting("debug.enabled").lower() == 'true' else False

##############################

XBMC_DEBUG = xbmc.LOGDEBUG       # 0
XBMC_INFO = xbmc.LOGINFO         # 1
XBMC_WARNING = xbmc.LOGWARNING   # 2
XBMC_ERROR = xbmc.LOGERROR       # 3

def log(txt, loglevel=XBMC_INFO):
    message = u'[%s] %s' % (ADDON_ID, repr(txt))
    xbmc.log(msg=message, level=loglevel)

def logdebug(txt):
    if DEBUG:
        log("DEBUG: " + repr(txt))

def logwarn(txt):
    log("WARNING: " + repr(txt), XBMC_WARNING)

def logerror(txt):
    log("ERROR: " + repr(txt), XBMC_ERROR)
    if isinstance(txt, Exception):
        traceback.print_exc()

def debugMsg(debug=False, ex=None, msg="", prefix=""):
    if (debug):
        log("Displaying DebugMsg()")
        errMsg = ""
        if ex != None:
            logerror(ex)
            errMsg = "ErrMsg: " + repr(ex) + "\n"
        if (prefix != ""):
            prefix = prefix + ":\n"
        if (ex == None and prefix == "" and msg == ""):
            msg = "No message provided."
        DialogOK(prefix + errMsg + msg)
    return

##############################

def DialogNotif(msg:str, time:int=5000):
    xbmcgui.Dialog().notification(ADDON_NAME, msg, icon=ADDON_ICON, time=time)
    return

def DialogOK(msg:str):
    xbmcgui.Dialog().ok(ADDON_NAME, msg)
    return

def DialogSelect(title:str="", choices:List[str or xbmcgui.ListItem]=[], useDetails:bool=False) -> int:
    return xbmcgui.Dialog().select(ADDON_NAME + " " + title, list=choices, useDetails=useDetails)

def DialogYesNo(msg:str, nolabel:str="", yeslabel:str="", autoclose:int=0) -> bool:
    return xbmcgui.Dialog().yesno(ADDON_NAME, msg, nolabel, yeslabel, autoclose)

def GetKodiVersion(as_str=False):
    if as_str:
        return xbmc.getInfoLabel('System.BuildVersion').split()[0]
    return int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])

def JsonRPC(method, properties=None, sort=None,
            query_filter=None, limit=None, params=None,
            item=None, options=None, limits=None, debug=False):
    CALL_NAME = f"Helper.JsonRPC({str(method)})"
    if (debug):
        log(CALL_NAME)

    json_string = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': method,
        'params': {}
    }

    if properties is not None:
        json_string['params']['properties'] = properties

    if limit is not None:
        json_string['params']['limits'] = {'start': 0, 'end': int(limit)}

    if sort is not None:
        json_string['params']['sort'] = sort

    if query_filter is not None:
        json_string['params']['filter'] = query_filter

    if options is not None:
        json_string['params']['options'] = options

    if limits is not None:
        json_string['params']['limits'] = limits

    if item is not None:
        json_string['params']['item'] = item

    if params is not None:
        json_string['params'].update(params)

    try:
        jsonrpc_call = json.dumps(json_string)
        result = xbmc.executeJSONRPC(jsonrpc_call)
        result = json.loads(result)
    except Exception as e:
        debugMsg(debug, e, prefix=CALL_NAME)
        debug = True

    if debug or ('error' in result):
        log(CALL_NAME + ' JSON STRING: ' + repr(json_string))
        log(CALL_NAME + ' JSON RESULT: ' + repr(result))

    return result

def Play(url:str, listItem=None, debug:bool=False):
    try:
        import xbmc
        xbmc.Player().play(url, listItem)
        # xbmc.executeJSONRPC('{"jsonrpc":"2.0", "method":"Player.Open", "params":{"item":{"file": "%s"}},"id":1}' % selected['video'])
    except Exception as e:
        logerror(e)
        debugMsg(debug, e, prefix="Helper.Play()")
    return

def WrapDict(x:dict) -> dict:
    return x

def WrapList(x:list) -> list:
    return x

##############################

class GUI():
    class MenuItem():
        def __init__(self, label:str="", label2:str="", path:str="", offscreen:bool=False) -> None:
            self.ListItem = xbmcgui.ListItem(label, label2, path, offscreen)
        def getListItem(self) -> xbmcgui.ListItem:
            return self.ListItem
        def setArt(self, art:dict=None, icon="", thumb="", poster="", fanart="", landscape="", banner=""):
            if art == None:
                art = {'icon':icon, 'thumb':thumb, 'poster':poster, 'fanart':fanart, 'landscape':landscape, 'banner':banner}
            self.ListItem.setArt(art)
        def setContextMenu(self, cm:list):
            self.ListItem.addContextMenuItems(cm)
        def setDateTime(self, w3cDateTime):
            self.ListItem.setDateTime(w3cDateTime)
        def setInfo(self, type:str, infolabel:dict):
            self.ListItem.setInfo(type, infolabel) # infolabel is { key: value }
        def setLabel(self, text):
            self.ListItem.setLabel(text)
        def setLabel2(self, text):
            self.ListItem.setLabel2(text)
        def setPath(self, path):
            self.ListItem.setPath(path)
        def setProperties(self, props:dict):
            self.ListItem.setProperties(props)
        def setStreamInfo(self, stream:str, info:dict):
            self.ListItem.addStreamInfo(stream, info)
    # end of MenuItem class
# end of GUI class

##############################

class Settings:
    @staticmethod
    def getCacheTimeout() -> int:
        return int(Settings.getSetting("cache.timeout")) * 60

    @staticmethod
    def getSetting(setting: str) -> str:
        return xbmcaddon.Addon().getSetting(setting)

    @staticmethod
    def setSetting(setting: str, value):
        xbmcaddon.Addon().setSetting(setting, str(value))
        return
    
    @staticmethod
    def openSettings(categoryPos: int = 0, settingPos: int = 0):
        try:
            exec = xbmc.executebuiltin
            exec('Addon.OpenSettings(%s)' % ADDON_ID)
            exec('SetFocus(%i)' % (categoryPos - 100))
            exec('SetFocus(%i)' % (settingPos - 80))
        except Exception as e:
            logerror(e)
            return
# end of Settings class

##############################

class Window:
    @staticmethod
    def clearProp(key, window_id=10000):
        window = xbmcgui.Window(window_id)
        window.clearProperty(key)

    @staticmethod
    def getProp(key, window_id=10000):
        window = xbmcgui.Window(window_id)
        result = window.getProperty(key.replace('.json','').replace('.bool',''))
        if result:
            if key.endswith('.json'):
                result = json.loads(result)
            elif key.endswith('.bool'):
                result = result in ('true', '1')
        return result
    
    @staticmethod
    def setProp(key, value, window_id=10000):
        window = xbmcgui.Window(window_id)
        if key.endswith('.json'):
            key = key.replace('.json', '')
            value = json.dumps(value)
        elif key.endswith('.bool'):
            key = key.replace('.bool', '')
            value = 'true' if value else 'false'
        window.setProperty(key, value)
# end of Window class

#!/usr/bin/python

# Replacement for the old script.skin.helper
# Written by: DStealth
# Inspired by Embuary Helper: https://github.com/sualfred/script.embuary.helper/wiki
# Kodi Docs: https://xbmc.github.io/docs.kodi.tv/master/kodi-base/
# Kodi Dev: https://xbmc.github.io/docs.kodi.tv/master/kodi-dev-kit/

##############################
#       Documentation        #
##############################
"""
USAGE:
    RunScript(script.dstealth.helper,
              action=<action>,
              [parameters as key=value]
             )

    OPTIONAL Parameters:
        debug=true
            - passing this as a parameter will do two things:
                1) display more verbose INFO logs
                2) display Dialog.OK prompts if errors occur
"""
##############################

import xbmcgui
import sys

import scripts.modules.cache as cache
import scripts.helper as Helper
from scripts.router import *

##############################
def run():    
    # This "if" Allows you to execute code when the file runs
    #   as a Script but not when it's imported as a Module.
    if __name__ == '__main__':
        cache.CleanupIfRequired()
        Main()
    return

class Main:
    def __init__(self):
        self.pluginMode = False
        self.debug = Helper.DEBUG
        self.action = False

        self.parseArgs()

        mode = "Plugin" if self.pluginMode else "Script"
        Helper.log(f"DStealth Helper ran in {mode} mode: action = {str(self.action)}")
        if (self.debug):
            sysArgs = {}
            if len(sys.argv) >= 1:
                sysArgs["arg0"] = sys.argv[0]
                sysArgs["action"] = self.action
            else:
                sysArgs = sys.argv
            Helper.log("DSH args: " + repr(sysArgs))
            Helper.log("DSH params: " + repr(self.params))
            if len(self.errors) > 0:
                Helper.log(f"DSH {len(self.errors)} errors.")
                for err in self.errors:
                    Helper.logerror(err)

        if self.action:
            self.runAction()
        else:
            Helper.logerror("No action provided.")
        return

    def parseArgs(self):
        self.params = {}
        self.errors = []
        args = sys.argv
        
        i = -1
        for arg in args:
            i += 1
            if arg.lower() == Helper.SCRIPT_ID:
                continue
            if arg.lower() == Helper.PLUGIN_ID:
                self.pluginMode = True
                self.action = "plugin"
                continue

            # this is debug for the Script feature, Plugin feature passes debug as a param
            if arg.lower().strip() == "debug":
                self.params['debug'] = True
                self.debug = True
                continue

            if arg.lower().startswith('action='):
                self.action = arg[7:].lower()
            else:
                try:
                    if self.pluginMode:
                        if i == 1:
                            # plugin:// handle
                            self.params["handle"] = int(arg)
                        if (i == 2 and arg.startswith('?')):
                            # plugin://script.dstealth.helper?paramstring
                            paramstring = arg[1:].replace('&amp;', '&') # incase xml string
                            for param in paramstring.split('&'):
                                if '=' not in param: continue                                
                                key = param.split("=")[0].lower().strip()
                                vals = param.split("=")[1:]
                                val = "=".join(vals).strip()
                                from urllib.parse import unquote_plus
                                self.params[unquote_plus(key)] = unquote_plus(val)
                    elif ('=' in arg):
                        key = arg.split("=")[0].lower().strip()
                        vals = arg.split("=")[1:]
                        val = "=".join(vals).strip()
                        self.params[key] = val
                except Exception as e:
                    self.errors.append(e)
                    continue
        return

    def runAction(self):
        try:
            if (self.pluginMode):
                Helper.log("No Plugin mode actions to perform for: " + repr(self.params))
                return
            else:
                import scripts.router as Router
                action = getattr(Router, self.action)
            action(self.params)
        except Exception as e:
            Helper.debugMsg(self.debug, e, prefix="Helper.runAction()")
            Helper.logerror(e)
        return
# end of Main class

run()
# -*- coding: utf-8 -*-
"""Entry-point leve do pluginsource do script.showimdb.

Usado pela skin como fonte de conteudo (plugin://script.showimdb/?mode=...).
Mantido minimo de proposito: nada de imports pesados no topo; cada modo importa
so o que precisa. Hoje serve a fileira "Para Voce" (recomendacoes).
"""
import sys
from urllib.parse import parse_qsl


def _run():
    handle = int(sys.argv[1])
    try:
        params = dict(parse_qsl(sys.argv[2][1:]))
    except Exception:
        params = {}
    mode = params.get('mode', '')
    if mode == 'recommendations':
        import recommendations
        recommendations.build(handle, params)
    else:
        import xbmcplugin
        xbmcplugin.endOfDirectory(handle, succeeded=False)


if __name__ == '__main__':
    _run()

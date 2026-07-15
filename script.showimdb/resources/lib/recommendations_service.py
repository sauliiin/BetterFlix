# -*- coding: utf-8 -*-
"""Worker do servico que pre-monta a fileira "Para Voce" do DialogVideoInfo em
window properties, para a skin exibir uma lista estatica (sem chamar plugin).

Roda como thread daemon, iniciada pelo service.py. Detecta o DialogVideoInfo
aberto, le ds_dialog_tmdb_id/type (setados pela skin no onload), busca as
recomendacoes (cache-first via recommendations.recommendation_fields) e publica
ds_recs_* na janela Home (10000). Como pre-aquece assim que o dialogo abre, ao
clicar em "Para Voce" os dados ja estao la -> a fileira aparece instantanea, sem
spawn de processo de plugin.
"""
import time
import threading

import xbmc
import xbmcgui

MAX_SLOTS = 10
ACTIVE_POLL_SECONDS = 0.25
IDLE_POLL_SECONDS = 2.0
_SUFFIXES = ('title', 'poster', 'fanart', 'tmdb', 'type', 'year', 'rating', 'plot')


class RecommendationsWatcher(threading.Thread):
    def __init__(self):
        super(RecommendationsWatcher, self).__init__(name='ShowIMDBRecsWatcher')
        self.daemon = True
        self.monitor = xbmc.Monitor()
        self.window = xbmcgui.Window(10000)
        self.dialog_window = xbmcgui.Window(12003)
        self._last_key = None

    def run(self):
        while not self.monitor.abortRequested():
            dialog_visible = False
            try:
                dialog_visible = xbmc.getCondVisibility('Window.IsVisible(movieinformation)')
                self._tick(dialog_visible)
            except Exception as e:
                xbmc.log('[ShowIMDB][Recs][watch] erro: %s' % e, xbmc.LOGWARNING)
            # Fora do unico dialogo consumidor, acorda oito vezes menos. Dentro
            # dele preserva os 250 ms para publicar recomendacoes rapidamente.
            interval = ACTIVE_POLL_SECONDS if dialog_visible else IDLE_POLL_SECONDS
            if self.monitor.waitForAbort(interval):
                break

    def _tick(self, dialog_visible):
        if not dialog_visible:
            if self._last_key is not None:
                self._clear()
                self._last_key = None
            return
        try:
            tmdb_id = (self.dialog_window.getProperty('ds_dialog_tmdb_id') or '').strip()
            tmdb_type = (self.dialog_window.getProperty('ds_dialog_tmdb_type') or '').strip()
        except Exception:
            tmdb_id = ''
            tmdb_type = ''
        if not tmdb_id:
            tmdb_id = (self.window.getProperty('ds_dialog_tmdb_id') or '').strip()
        if not tmdb_type:
            tmdb_type = (self.window.getProperty('ds_dialog_tmdb_type') or '').strip()
        if not tmdb_id or not tmdb_type:
            return
        key = '%s:%s' % (tmdb_type, tmdb_id)
        if key == self._last_key:
            return
        media_type = 'tvshow' if tmdb_type == 'tv' else 'movie'
        import recommendations
        t0 = time.time()
        fields = recommendations.recommendation_fields(media_type, tmdb_id, MAX_SLOTS)
        self._publish(tmdb_id, fields)
        self._last_key = key
        xbmc.log('[ShowIMDB][Recs][watch] publicado %s/%s itens=%d em %.2fs' % (
            media_type, tmdb_id, len(fields), time.time() - t0), xbmc.LOGINFO)

    def _publish(self, source_tmdb, fields):
        w = self.window
        w.setProperty('ds_recs_source_tmdb', str(source_tmdb))
        w.setProperty('ds_recs_count', str(len(fields)))
        for i in range(MAX_SLOTS):
            if i < len(fields):
                f = fields[i]
                w.setProperty('ds_recs_%d_title' % i, f['title'])
                w.setProperty('ds_recs_%d_poster' % i, f['poster'])
                w.setProperty('ds_recs_%d_fanart' % i, f['fanart'])
                w.setProperty('ds_recs_%d_tmdb' % i, f['tmdb_id'])
                w.setProperty('ds_recs_%d_type' % i, f['type'])
                w.setProperty('ds_recs_%d_year' % i, f['year'])
                w.setProperty('ds_recs_%d_rating' % i, f['rating'])
                w.setProperty('ds_recs_%d_plot' % i, f['plot'])
            else:
                for suffix in _SUFFIXES:
                    w.clearProperty('ds_recs_%d_%s' % (i, suffix))

    def _clear(self):
        w = self.window
        w.clearProperty('ds_recs_source_tmdb')
        w.clearProperty('ds_recs_count')
        for i in range(MAX_SLOTS):
            for suffix in _SUFFIXES:
                w.clearProperty('ds_recs_%d_%s' % (i, suffix))


_watcher = None


def start():
    """Inicia o watcher (idempotente). Chamado pelo service.py."""
    global _watcher
    if _watcher is None:
        _watcher = RecommendationsWatcher()
        _watcher.start()
        xbmc.log('[ShowIMDB][Recs][watch] watcher iniciado', xbmc.LOGINFO)
    return _watcher

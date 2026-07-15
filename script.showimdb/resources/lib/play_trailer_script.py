# -*- coding: utf-8 -*-
import sys
import urllib.parse

import xbmc
import xbmcgui

import imdb_trailer_api
import mdblist_api
import tmdb_api
import youtube_stream_resolver


class FineTuning:
    """Parâmetros de resolução para acionamento manual de trailer."""
    RESOLVER_TIMEOUT = 5
    IMDB_QUALITY_1080 = (1080, 720)
    IMDB_QUALITY_720 = (720,)


def get_params():
    param_string = ""
    parsed_params = {}
    if len(sys.argv) >= 2 and sys.argv[1].startswith("?"):
        param_string = sys.argv[1][1:]
    if param_string:
        try:
            parsed_params = dict(urllib.parse.parse_qsl(param_string))
        except Exception:
            pass
    return parsed_params


def info_label(label):
    try:
        return (xbmc.getInfoLabel(label) or "").strip()
    except Exception:
        return ""


def is_video_info_active():
    try:
        return xbmc.getCondVisibility("Window.IsActive(DialogVideoInfo.xml)")
    except Exception:
        return False


def is_infowall_visible():
    try:
        return xbmc.getCondVisibility("[Window.IsActive(videos) | Window.IsVisible(videos)] + Control.IsVisible(54)")
    except Exception:
        return False


def get_focused_item():
    db_type = (
        info_label("ListItem.DBType")
        or info_label("Window(home).Property(ContextMenuTargetDBType)")
        or info_label("Window(10000).Property(ds_info_dbtype)")
        or "movie"
    )
    tmdb_id = info_label("ListItem.UniqueID(tmdb)")
    tvshow_tmdb_id = info_label("ListItem.UniqueID(tvshow.tmdb)")
    imdb_id = info_label("ListItem.IMDBNumber")
    if db_type.lower() in ("episode", "season") and tvshow_tmdb_id:
        tmdb_id = tvshow_tmdb_id
    item_id = tmdb_id or imdb_id
    return item_id, db_type


def resolve_imdb_direct_url(item_id, media_type):
    if not item_id:
        return ""
    try:
        return imdb_trailer_api.fetch_trailer_url(
            item_id,
            media_type,
            quality_priority=get_imdb_quality_priority(),
        )
    except Exception:
        return ""


def get_imdb_quality_priority():
    if xbmc.getCondVisibility("Skin.HasSetting(dstv_imdb_trailer_720p)"):
        return FineTuning.IMDB_QUALITY_720
    return FineTuning.IMDB_QUALITY_1080


def build_resolver_query():
    title = (
        xbmc.getInfoLabel("ListItem.OriginalTitle")
        or xbmc.getInfoLabel("ListItem.Title")
        or xbmc.getInfoLabel("ListItem.Label")
        or ""
    ).strip()
    if not title:
        return ""
    year = (xbmc.getInfoLabel("ListItem.Year") or "").strip()
    if year.isdigit():
        return "%s official trailer (%s)" % (title, year)
    return "%s official trailer" % title


def get_current_title():
    return (
        xbmc.getInfoLabel("Window(home).Property(ContextMenuTargetTitle)")
        or xbmc.getInfoLabel("Window(10000).Property(ds_info_title)")
        or xbmc.getInfoLabel("ListItem.OriginalTitle")
        or xbmc.getInfoLabel("ListItem.Title")
        or xbmc.getInfoLabel("ListItem.Label")
        or ""
    ).strip()


def normalize_mdblist_media_type(db_type):
    db_type = (db_type or "").lower()
    if db_type in ("movie", "movies"):
        return "movies"
    return "shows"


def normalize_video_info_media_type(db_type):
    db_type = (db_type or "").lower()
    if db_type in ("tv", "tvshow", "shows", "season", "episode"):
        return "tvshow"
    return "movie"


def kodi_major():
    try:
        return int((xbmc.getInfoLabel("System.BuildVersion") or "19").split(".")[0])
    except Exception:
        return 19


def new_listitem(label):
    try:
        return xbmcgui.ListItem(label=label, offscreen=True)
    except TypeError:
        return xbmcgui.ListItem(label=label)


def focused_info(label, fallback=""):
    value = info_label(label)
    return value if value else fallback


def container_info(list_id, label, fallback=""):
    if not list_id:
        return fallback
    return focused_info("Container(%s).%s" % (list_id, label), fallback)


def first_value(*values):
    for value in values:
        if value:
            return value
    return ""


def set_home_props(props):
    win = xbmcgui.Window(10000)
    for key, value in props.items():
        try:
            if value is None:
                value = ""
            win.setProperty(key, str(value))
        except Exception:
            pass


def prepare_home_props_for_info(mediatype, tmdb_id, title, year, plot, rating, fanart):
    stack_type = "tv" if mediatype == "tvshow" else "movie"
    props = {
        "Trakt.Reviews": "",
        "middle": "",
        "ds_info_imdb_rating": "",
        "ds_info_letterboxd_rating": "",
        "ds_info_trakt_rating": "",
        "ds_info_imdb_votes": "",
        "ds_info_oscars": "",
        "ds_info_badges": "",
        "ds_info_awards": "",
        "ds_info_badges_cf": "",
        "ds_info_clearlogo": "",
        "budget": "",
        "revenue": "",
        "mpaa": "",
        "imdb_combined": "",
        "infobackground": fanart,
        "ds_info_title": title,
        "ds_info_dt_year": year,
        "ds_info_rating": rating,
        "ds_info_desc": plot,
        "ds_info_fanart_art": fanart,
        "ds_info_fanart_prop": fanart,
        "ds_info_dbtype": mediatype,
        "ds_tmdb_id": tmdb_id,
        "ds_info_tmdb_id": tmdb_id,
        "ds_stack_tmdb_id": tmdb_id,
        "ds_stack_tmdb_type": stack_type,
        "ds_last_check": title,
    }
    set_home_props(props)


def set_video_info(listitem, mediatype, title, tmdb_id, year, plot, rating):
    info = {"title": title, "mediatype": mediatype, "plot": plot}
    if year.isdigit():
        info["year"] = int(year)
    try:
        if rating:
            info["rating"] = float(rating)
    except Exception:
        pass

    try:
        if kodi_major() < 20:
            listitem.setInfo("video", info)
            if tmdb_id:
                listitem.setUniqueIDs({"tmdb": str(tmdb_id)})
            return

        tag = listitem.getVideoInfoTag()
        tag.setTitle(title)
        tag.setMediaType(mediatype)
        if tmdb_id:
            tag.setUniqueIDs({"tmdb": str(tmdb_id)})
        if year.isdigit():
            tag.setYear(int(year))
        if plot:
            tag.setPlot(plot)
        if rating:
            try:
                tag.setRating(float(rating))
            except Exception:
                pass
    except Exception:
        try:
            listitem.setInfo("video", info)
            if tmdb_id:
                listitem.setUniqueIDs({"tmdb": str(tmdb_id)})
        except Exception:
            pass


def open_video_info(params):
    list_id = params.get("list_id", "").strip()
    db_type = (
        params.get("dbtype")
        or container_info(list_id, "ListItem.Property(DBType)")
        or focused_info("ListItem.Property(DBType)")
        or focused_info("ListItem.DBType")
        or "movie"
    )
    mediatype = normalize_video_info_media_type(db_type)
    tmdb_id = (
        params.get("tmdb_id")
        or container_info(list_id, "ListItem.Property(tmdb_id)")
        or container_info(list_id, "ListItem.UniqueID(tmdb)")
        or focused_info("ListItem.UniqueID(tmdb)")
        or focused_info("ListItem.Property(tmdb_id)")
        or focused_info("ListItem.Property(tmdb)")
    )
    if not tmdb_id:
        xbmc.log("ShowIMDB: open_info sem tmdb_id.", xbmc.LOGINFO)
        return

    title = first_value(
        container_info(list_id, "ListItem.Title"),
        container_info(list_id, "ListItem.Label"),
        focused_info("ListItem.Title"),
        focused_info("ListItem.Label"),
    )
    year = first_value(
        container_info(list_id, "ListItem.Year"),
        container_info(list_id, "ListItem.Property(Year)"),
        focused_info("ListItem.Year"),
        focused_info("ListItem.Property(Year)"),
    )
    plot = first_value(
        container_info(list_id, "ListItem.Plot"),
        container_info(list_id, "ListItem.Property(Plot)"),
        container_info(list_id, "ListItem.Property(Overview)"),
        focused_info("ListItem.Plot"),
        focused_info("ListItem.Property(Plot)"),
        focused_info("ListItem.Property(Overview)"),
    )
    rating = first_value(
        container_info(list_id, "ListItem.Rating"),
        container_info(list_id, "ListItem.Property(Rating)"),
        focused_info("ListItem.Rating"),
        focused_info("ListItem.Property(Rating)"),
    )
    poster = first_value(
        container_info(list_id, "ListItem.Art(poster)"),
        container_info(list_id, "ListItem.Art(thumb)"),
        container_info(list_id, "ListItem.Icon"),
        container_info(list_id, "ListItem.Property(poster)"),
        container_info(list_id, "ListItem.Property(thumb)"),
        focused_info("ListItem.Art(poster)"),
        focused_info("ListItem.Art(thumb)"),
        focused_info("ListItem.Icon"),
        focused_info("ListItem.Property(poster)"),
        focused_info("ListItem.Property(thumb)"),
    )
    fanart = first_value(
        container_info(list_id, "ListItem.Art(fanart)"),
        container_info(list_id, "ListItem.Property(fanart)"),
        focused_info("ListItem.Art(fanart)"),
        focused_info("ListItem.Property(fanart)"),
    )
    if not title:
        title = tmdb_id

    listitem = new_listitem(title)
    listitem.setArt({
        "icon": poster,
        "thumb": poster,
        "poster": poster,
        "fanart": fanart,
        "landscape": fanart,
    })
    listitem.setProperties({
        "tmdb_id": str(tmdb_id),
        "DBType": mediatype,
        "Plot": plot,
        "Overview": plot,
        "Year": year,
        "Rating": rating,
    })
    set_video_info(listitem, mediatype, title, tmdb_id, year, plot, rating)

    if mediatype == "movie":
        listitem.setPath("plugin://plugin.video.pov/?mode=play_media&mediatype=movie&tmdb_id=%s" % tmdb_id)
    else:
        listitem.setPath("plugin://plugin.video.pov/?mode=build_season_list&tmdb_id=%s" % tmdb_id)

    xbmc.executebuiltin("Dialog.Close(MovieInformation)")
    xbmc.sleep(100)
    prepare_home_props_for_info(mediatype, str(tmdb_id), title, year, plot, rating, fanart)
    xbmcgui.Dialog().info(listitem)


def notify_mdblist(action, title, level=xbmcgui.NOTIFICATION_INFO):
    verb = "adicionado(a) à coleção" if action == "add" else "removido(a) da coleção"
    message = ("%s %s" % (title, verb)).strip() if title else verb.capitalize()
    xbmcgui.Dialog().notification("MDBList", message, level)


def update_mdblist_collection(params):
    collection_action = (params.get("collection_action") or "").lower()
    item_id = params.get("id", "")
    db_type = params.get("dbtype", "")
    focused_id, focused_db_type = get_focused_item()

    if is_video_info_active() and focused_id:
        item_id = focused_id
        db_type = focused_db_type
    else:
        item_id = item_id or focused_id or info_label("Window(home).Property(ds_imdb_id)") or info_label("Window(10000).Property(ds_info_tmdb_id)")
        db_type = db_type or focused_db_type

    tmdb_id = params.get("tmdb_id", "")
    tvdb_id = params.get("tvdb_id", "")
    imdb_id = params.get("imdb_id", "")
    if item_id.startswith("tt"):
        imdb_id = item_id
    elif item_id:
        tmdb_id = item_id

    try:
        result = mdblist_api.update_collection(
            collection_action,
            normalize_mdblist_media_type(db_type),
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
        )
        title = get_current_title()
        if result.get("changed") or not result.get("not_found"):
            notify_mdblist(collection_action, title)
        else:
            xbmcgui.Dialog().notification("MDBList", "Título não encontrado na coleção.", xbmcgui.NOTIFICATION_WARNING)
    except Exception as e:
        xbmc.log("[ShowIMDB][MDBList] Erro ao atualizar coleção: %s" % e, xbmc.LOGERROR)
        xbmcgui.Dialog().notification("MDBList", "Erro ao atualizar coleção.", xbmcgui.NOTIFICATION_ERROR)


def resolve_via_search():
    query = build_resolver_query()
    if not query:
        return ""
    try:
        url, _info = youtube_stream_resolver.resolve_from_search_query(query, timeout=FineTuning.RESOLVER_TIMEOUT)
        return url or ""
    except Exception:
        return ""


if __name__ == "__main__":
    xbmc.log("PlayTrailerScript: Manual Trigger.", xbmc.LOGINFO)
    win = xbmcgui.Window(10000)

    params = get_params()

    if params.get("action") == "mdblist_collection":
        update_mdblist_collection(params)
        sys.exit()

    if params.get("action") == "open_info":
        open_video_info(params)
        sys.exit()

    win.clearProperty("ds_ondemand_trailer_playing")
    win.setProperty("ds_ondemand_trailer_loading", "true")

    item_id = params.get("id")
    db_type = params.get("dbtype", "movie")
    focused_id, focused_db_type = get_focused_item()
    if is_video_info_active() and focused_id:
        item_id = focused_id
        db_type = focused_db_type
    else:
        item_id = item_id or focused_id or info_label("Window(home).Property(ds_imdb_id)") or info_label("Window(10000).Property(ds_info_tmdb_id)")
        db_type = db_type or focused_db_type
    if not item_id:
        win.clearProperty("ds_ondemand_trailer_loading")
        xbmc.log("PlayTrailerScript: nenhum id encontrado para o item atual.", xbmc.LOGINFO)
        sys.exit()

    try:
        play_in_infowall = is_infowall_visible() and not is_video_info_active()
        media_type = "tv" if db_type.lower() in ("tv", "tvshow", "episode", "season") else "movie"
        url = resolve_imdb_direct_url(item_id, media_type)
        if not url:
            url = resolve_via_search()
        if not url:
            final_tmdb = item_id
            if item_id.startswith("tt"):
                tmdb_id, resolved_media_type = tmdb_api.fetch_tmdb_id_from_imdb(item_id)
                if tmdb_id:
                    final_tmdb = tmdb_id
                    media_type = resolved_media_type
            url = tmdb_api.fetch_trailer_url(final_tmdb, media_type)

        win.clearProperty("ds_ondemand_trailer_loading")
        if url:
            player = xbmc.Player()
            listitem = xbmcgui.ListItem(label="ManualTrailer")
            listitem.setPath(url)
            listitem.setInfo("video", {"title": "ManualTrailer"})
            if player.isPlaying():
                player.stop()
                xbmc.sleep(200)
            xbmc.executebuiltin("Dialog.Close(all,true)")
            xbmc.sleep(100)
            if play_in_infowall:
                player.play(url, listitem, windowed=True)
            else:
                player.play(url, listitem, windowed=False)
                xbmc.executebuiltin("ActivateWindow(fullscreenvideo)")
            win.setProperty("ds_ondemand_trailer_playing", "true")
        else:
            xbmc.log("PlayTrailerScript: trailer nao encontrado.", xbmc.LOGINFO)
    except Exception as e:
        win.clearProperty("ds_ondemand_trailer_loading")
        xbmc.log(f"PlayTrailerScript Error: {e}", xbmc.LOGERROR)

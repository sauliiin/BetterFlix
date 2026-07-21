# -*- coding: utf-8 -*-
import xbmc
import xbmcgui
import xbmcvfs
import os
import sys

LAYOUT_CHOICES = ["standard", "wide", "fentastic", "onlyfun", "onlyfun_wide"]
LAYOUT_LABELS = {
    "standard": "Standard",
    "wide": "Wide",
    "fentastic": "FENtasticLike",
    "onlyfun": "OnlyFun",
    "onlyfun_wide": "OnlyFun Wide",
}
LAYOUT_ALIASES = {
    "fentasticlike": "fentastic",
    "fentastic_like": "fentastic",
    "fentastic-like": "fentastic",
    "only_fun": "onlyfun",
    "only-fun": "onlyfun",
    "onlyfunwide": "onlyfun_wide",
    "onlyfun wide": "onlyfun_wide",
    "onlyfun-wide": "onlyfun_wide",
    "only_fun_wide": "onlyfun_wide",
}


def normalize_layout(layout_type):
    layout = (layout_type or "").strip().lower()
    return LAYOUT_ALIASES.get(layout, layout)


def choose_layout(current_layout):
    dialog = xbmcgui.Dialog()
    
    thumb_map = {
        "wide": "dstv/wide.png",
        "fentastic": "dstv/FENtasticLike.png",
        "onlyfun": "dstv/OnlyFun.png",
        "onlyfun_wide": "dstv/print_onlyfunWide.png",
        "standard": "dstv/standard.png"
    }
    
    options = []
    for key in LAYOUT_CHOICES:
        item = xbmcgui.ListItem(LAYOUT_LABELS[key])
        item.setArt({'icon': thumb_map.get(key, 'DefaultAddonNone.png')})
        options.append(item)

    current = normalize_layout(current_layout)
    preselect = LAYOUT_CHOICES.index(current) if current in LAYOUT_CHOICES else 0
    selected = dialog.select("Escolha o estilo do layout", options, preselect=preselect, useDetails=True)
    if selected < 0:
        return None
    return LAYOUT_CHOICES[selected]


def first_existing_path(paths):
    for path in paths:
        if xbmcvfs.exists(path) or os.path.exists(path):
            return path
    return paths[0]


def copy_layout_files(layout_type):
    """
    Copia os arquivos de layout da pasta gif para seus destinos finais,
    renomeando-os conforme esperado pela skin dstelthv.
    
    Args:
        layout_type (str): 'standard', 'wide', 'fentastic', 'onlyfun' ou 'onlyfun_wide'
    """
    skin_candidates = []
    active_skin = xbmc.getInfoLabel("System.SkinID")
    if active_skin:
        skin_candidates.append(active_skin)
    skin_candidates.extend(["skin.JediForce", "skin.dstealthtv"])

    skin_path = ""
    for skin_id in skin_candidates:
        candidate = xbmcvfs.translatePath(f"special://home/addons/{skin_id}/")
        if candidate and xbmcvfs.exists(candidate):
            skin_path = candidate
            break

    if not skin_path:
        xbmc.log("layout_switcher.py: Nenhuma skin compatível encontrada.", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("Erro", "Skin alvo não encontrada para alternar layout.", xbmcgui.NOTIFICATION_ERROR)
        return False

    # Obter os caminhos usando o xbmcvfs.translatePath
    gif_path = os.path.join(skin_path, "gif")
    
    # Arquivos de Origem
    src_includes = first_existing_path([
        os.path.join(gif_path, f"Includes_DSTV_Home-{layout_type}.xml"),
        os.path.join(gif_path, f"Includes_DSTV_Home_{layout_type}.xml"),
    ])
    src_template = first_existing_path([
        os.path.join(gif_path, f"template-{layout_type}.xml"),
        os.path.join(gif_path, f"template_{layout_type}.xml"),
    ])
    
    # Arquivos de Destino
    dest_includes = os.path.join(skin_path, "1080i", "Includes_DSTV_Home.xml")
    dest_template = os.path.join(skin_path, "shortcuts", "template.xml")
    
    # Verifica se os arquivos de origem existem
    if not (xbmcvfs.exists(src_includes) or os.path.exists(src_includes)):
        xbmc.log(f"layout_switcher.py: Origem nao encontrada -> {src_includes}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("Erro", f"Arquivo {layout_type} (Includes) não encontrado!", xbmcgui.NOTIFICATION_ERROR)
        return False
        
    if not (xbmcvfs.exists(src_template) or os.path.exists(src_template)):
        xbmc.log(f"layout_switcher.py: Origem nao encontrada -> {src_template}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("Erro", f"Arquivo {layout_type} (template) não encontrado!", xbmcgui.NOTIFICATION_ERROR)
        return False

    try:
        # Copia Includes_DSTV_Home (sobrescrevendo se existir)
        xbmcvfs.copy(src_includes, dest_includes)
        xbmc.log(f"layout_switcher.py: Copiado {src_includes} -> {dest_includes}", xbmc.LOGINFO)
        
        # Cria a pasta shortcuts se nao existir
        shortcuts_dir = os.path.join(skin_path, "shortcuts")
        if not os.path.exists(shortcuts_dir):
            os.makedirs(shortcuts_dir)
            
        # Copia template (sobrescrevendo se existir)
        xbmcvfs.copy(src_template, dest_template)
        xbmc.log(f"layout_switcher.py: Copiado {src_template} -> {dest_template}", xbmc.LOGINFO)
        
        return True
    except Exception as e:
        xbmc.log(f"layout_switcher.py Error copying files: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("Erro", "Falha ao copiar arquivos de layout.", xbmcgui.NOTIFICATION_ERROR)
        return False


if __name__ == '__main__':
    layout = normalize_layout(sys.argv[1]) if len(sys.argv) > 1 else "choose"

    if layout == "choose":
        current_layout = normalize_layout(xbmc.getInfoLabel("Skin.String(dstv_layout)"))
        layout = choose_layout(current_layout)
        if not layout:
            xbmc.log("layout_switcher.py: Seleção de layout cancelada pelo usuário.", xbmc.LOGINFO)
            raise SystemExit

    if layout in LAYOUT_CHOICES:
        xbmc.log(
            f"layout_switcher.py: Alternando para o layout {LAYOUT_LABELS.get(layout, layout)}",
            xbmc.LOGINFO,
        )
        success = copy_layout_files(layout)

        if success:
            xbmc.executebuiltin(f"Skin.SetString(dstv_layout,{layout})")
            xbmc.executebuiltin("RunScript(script.skinshortcuts,type=buildxml&mainmenuID=9000&options=noGroups)")
            xbmc.sleep(500)
            xbmc.executebuiltin("ReloadSkin()")
    else:
        xbmc.log(
            f"layout_switcher.py: Argumento invalido '{layout}'. Use 'standard', 'wide', 'fentastic', 'onlyfun', 'onlyfun_wide' ou 'choose'.",
            xbmc.LOGERROR,
        )

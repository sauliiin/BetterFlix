# -*- coding: utf-8 -*-
# ARQUIVO: highlights.py
# Propositos: Processar destaques extras para filmes na Skin Kodi.
# 1. Calcula a media justa ('middle') das notas, ignorando 0 e 10.
# 2. Puxa premios (Oscars) via OMDb API.
# 3. Organiza os selos (badges) processados pelo MDBList.
# 4. Grava/Le os textos traduzidos no banco de dados com validade de 30 dias.

import time

from database import db

_omdb_session = None  # session lazy: reusa conexão HTTP entre chamadas OMDb sem pesar o bootstrap

def _get_omdb_session():
    global _omdb_session
    if _omdb_session is None:
        import requests
        _omdb_session = requests.Session()
    return _omdb_session


def _split_badges(raw_badges):
    if not raw_badges:
        return []
    badges = [item.strip() for item in raw_badges.split(",")]
    seen = set()
    unique_badges = []
    for badge in badges:
        if not badge:
            continue
        key = badge.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_badges.append(badge)
    return unique_badges


_ALLOWED_MDB_BADGES = {
    "top 250 imdb": "Top 250 IMDb",
    "top 100 imdb": "Top 100 IMDb",
    "top 250 letterboxd": "Top 250 Letterboxd",
    "must see metacritic": "Must See Metacritic",
    "certified fresh": "Certified Fresh",
    "certified hot": "Certified Hot",
    "tao bom que virou patrimonio": "Tao Bom que Virou Patrimonio",
    "vencedor de melhor filme": "Vencedor de Melhor Filme",
    "indicado a melhor filme": "Indicado a Melhor Filme",
    "vencedor do oscar": "Vencedor do Oscar",
    "indicado ao oscar": "Indicado ao Oscar",
    "pertence a coleção": "Pertence a Coleção",
    "pertence a colecao": "Pertence a Coleção",
    "finalizada": "Finalizada",
    "classico cult": "Classico Cult",
    "baseado em história real": "Baseado em História Real",
    "baseado em historia real": "Baseado em História Real",
    "prequel": "Prequel",
    "prelúdio": "Prequel",
    "preludio": "Prequel",
    "sequência": "Sequência",
    "sequencia": "Sequência",
    "spin-off": "Spin-off",
}


def _normalize_allowed_badge(badge):
    if not badge:
        return ""
    return _ALLOWED_MDB_BADGES.get(badge.strip().lower(), "")


def _prune_duplicate_badges(badges, omdb_oscars):
    labels = list(badges or [])
    lower_labels = {label.lower() for label in labels}
    has_omdb_oscar = "oscar" in (omdb_oscars or "").lower()

    remove = set()
    if "top 100 imdb" in lower_labels:
        remove.add("Top 250 IMDb")
    if "vencedor de melhor filme" in lower_labels:
        remove.update(["Indicado a Melhor Filme", "Vencedor do Oscar", "Indicado ao Oscar"])
    elif "indicado a melhor filme" in lower_labels:
        remove.add("Indicado ao Oscar")
    if "vencedor do oscar" in lower_labels:
        remove.add("Indicado ao Oscar")
    if has_omdb_oscar:
        remove.update(["Vencedor do Oscar", "Indicado ao Oscar"])

    return [label for label in labels if label not in remove]


def _build_awards_payload(omdb_oscars, raw_badges):
    badges = _split_badges(raw_badges)
    filtered_badges = []
    seen = set()
    for badge in badges:
        normalized = _normalize_allowed_badge(badge)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        filtered_badges.append(normalized)
    filtered_badges = _prune_duplicate_badges(filtered_badges, omdb_oscars)

    certified_fresh = ""
    text_badges = []
    for badge in filtered_badges:
        if badge.lower() == "certified fresh":
            certified_fresh = "Certified Fresh"
            continue
        text_badges.append(badge)

    # Regra de display:
    # - Se Certified Fresh vier junto de outro texto, fica so no icone.
    # - Se vier sozinho, aparece no texto tambem.
    if certified_fresh and not omdb_oscars and not text_badges:
        text_badges.append(certified_fresh)

    formatted_badges = ", ".join(filtered_badges)
    display_badges = ", ".join(text_badges)

    if omdb_oscars and display_badges:
        awards_text = "%s - %s" % (omdb_oscars, display_badges)
    elif omdb_oscars:
        awards_text = omdb_oscars
    else:
        awards_text = display_badges

    # O icone do tomate deve aparecer somente quando o texto exibido
    # for exatamente "Certified Fresh".
    certified_fresh_for_icon = certified_fresh if awards_text == "Certified Fresh" else ""

    return formatted_badges, awards_text, certified_fresh_for_icon

def process_highlights(imdb_id, data, lb_rating, tr_rating):
    """
    Processa e retorna as seguintes propriedades extras (destaques):
    - middle_rating: Média das 3 notas principais (IMDb, Trakt, Letterboxd)
    - omdb_oscars: Texto detalhando indicações ou vitórias no Oscar.
    - formatted_badges: Selos qualitativos do MDBList, sem badges de indicacao.
    - awards_text: Texto final unificado (OMDb + MDBList filtrado).
    - certified_fresh_badge: Badge isolado para controle de UI.

    ====================================================================
    COMO UTILIZAR EM OUTROS CÓDIGOS (EXEMPLO DE USO NO SERVICE.PY):
    ====================================================================
    Você deve importar e chamar esta função no final do script onde você
    possui acesso ao `imdb_id` do filme atual, o dicionário `data` do MDBList,
    e as notas do Letterboxd e Trakt.

    Passo 1. Fazer o import:
        from highlights import process_highlights

    Passo 2. Chamar a função e resgatar as variaveis:
        middle_rating, omdb_oscars, formatted_badges, awards_text, certified_fresh_badge = process_highlights(imdb_id, data, lb_rating, tr_rating)

    Passo 3. Enviar as variáveis recém-processadas para a interface do Kodi,
    junto com suas outras atualizações de tela:
        xbmcgui.Window(10000).setProperty("middle", middle_rating)
        xbmcgui.Window(10000).setProperty("ds_info_oscars", omdb_oscars)
        xbmcgui.Window(10000).setProperty("ds_info_badges", formatted_badges)
        xbmcgui.Window(10000).setProperty("ds_info_awards", awards_text)
        xbmcgui.Window(10000).setProperty("ds_info_badges_cf", certified_fresh_badge)
    """

    # 1. Calcular Média (normalizada para escala 0-10)
    middle_rating = ""
    try:
        ratings = []
        raw_imdb = data.get("imdb_rating", "")
        raw_lb = data.get("letterboxd_rating", "")
        raw_tr = data.get("trakt_rating", "")

        # IMDb: escala 0 a 10 — usar direto
        try:
            v = float(raw_imdb or 0)
            if 0 < v < 10:
                ratings.append(v)
        except Exception: pass

        # Letterboxd: escala 0 a 5 — multiplicar por 2
        try:
            v = float(raw_lb or 0)
            if 0 < v <= 5:
                ratings.append(v * 2)
        except Exception: pass

        # Trakt: escala 0 a 100 — dividir por 10
        try:
            v = float(raw_tr or 0)
            if 0 < v <= 100:
                ratings.append(v / 10)
        except Exception: pass

        if ratings:
            avg = sum(ratings) / len(ratings)
            middle_rating = "{:.1f}".format(avg)
    except Exception: pass

    # Salvar middle no cache SQLite
    if middle_rating and imdb_id:
        try:
            db.execute_query(
                "INSERT OR REPLACE INTO middle_cache VALUES (?, ?, ?)",
                (imdb_id, middle_rating, time.time())
            )
        except Exception: pass

    # 2. Resgatar Badges e Oscars (Cache SQLite - Validade 30 dias)
    CACHE_30_DAYS = 30 * 24 * 60 * 60
    omdb_oscars = ""
    raw_badges = data.get("highlight_badges", "")
    incoming_badges_are_current = data.get("badges_schema_version") == 2

    # Verifica se o filme já passou pelo processo e está salvo no banco nos últimos 30 dias
    cached_badges = db.fetch_one("SELECT oscars, badges, timestamp FROM badges_data WHERE imdb_id = ?", (imdb_id,))
    
    if cached_badges and (time.time() - cached_badges[2] < CACHE_30_DAYS):
        # Le instantaneamente do cache:
        omdb_oscars = cached_badges[0]
        cached_raw_badges = cached_badges[1] or ""
        if not incoming_badges_are_current:
            raw_badges = cached_raw_badges
        elif raw_badges != cached_raw_badges:
            db.execute_query(
                "INSERT OR REPLACE INTO badges_data VALUES (?, ?, ?, ?)",
                (imdb_id, omdb_oscars, raw_badges, time.time())
            )
    else:
        # Se nao existe no banco, ou se passou de 30 dias, faz a requisicao na API.
        if imdb_id:
            try:
                omdb_url = f"http://www.omdbapi.com/?apikey=b2f2fcca&i={imdb_id}"
                resp = _get_omdb_session().get(omdb_url, timeout=5)
                if resp.status_code == 200:
                    awd = resp.json().get("Awards", "")
                    if awd and "oscar" in awd.lower():
                        for s in awd.split("."):
                            if "oscar" in s.lower():
                                omdb_oscars = s.strip()
                                # Traduzir string crua do OMDb
                                omdb_oscars = omdb_oscars.replace("Won", "Venceu").replace("Nominated for", "Indicado a")
                                break
            except Exception: pass
        
        # Salva o resultado no banco e reseta o cronometro para +30 dias.
        db.execute_query(
            "INSERT OR REPLACE INTO badges_data VALUES (?, ?, ?, ?)", 
            (imdb_id, omdb_oscars, raw_badges, time.time())
        )

    formatted_badges, awards_text, certified_fresh_badge = _build_awards_payload(omdb_oscars, raw_badges)

    return middle_rating, omdb_oscars, formatted_badges, awards_text, certified_fresh_badge

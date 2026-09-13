#!/usr/bin/env python3
"""
pm-edge-watch — collecte longue durée pour trancher les 2 dernieres pistes Polymarket.

Piste 1 (PUISSANCE) : segment POLITIQUE. Le crible du 02/08/2026 sur 3 semaines de donnees
  n'a rien trouve mais n'avait que 54 sagas independantes -> ne detectait qu'un edge >7-10%.
  Il faut des MOIS pour descendre le seuil de detection a quelques %.

Piste 2 (INFO EXTERNE) : le seul axe jamais mene a terme. On enregistre la DISPERSION
  d'ensemble GFS (ecart-type entre 31 membres) en parallele des prix de marche. L'hypothese
  testable plus tard : les market makers integrent lentement les CHOCS DE VARIANCE
  (pas la prevision moyenne, deja dans le prix).

Sortie : CSV gzippes commites dans le repo. Aucune infra, aucun cout, PC eteint.
Usage : python collect_once.py [--resolve]
"""
import csv, gzip, io, json, os, statistics, sys, time
from datetime import datetime, timezone, timedelta
import urllib.request, urllib.parse, urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"

TAGS = ["politics", "geopolitics", "elections", "world", "weather", "temperature", "climate",
        "crypto-prices"]  # TEMOIN, pas une piste -- voir WITNESS_TAGS plus bas
MIN_LIQUIDITY = 500.0        # ecarte la longue traine morte (marches 2028 sans carnet)
MAX_HORIZON_DAYS = 150       # au-dela, le marche ne resoudra pas dans la fenetre d'etude

# --- CORRECTIF 31/08/2026 : la PISTE 2 etait INTESTABLE ------------------------
# On collectait la dispersion d'ensemble GFS de 10 villes US depuis le 02/08 SANS
# jamais collecter le moindre marche meteo en face : le tag "weather" n'etait pas
# dans TAGS. 665 marches "Will it rain in <ville> on <date>?" existent, ouverts,
# avec carnet, sur exactement ces villes. Recensement du 31/08 : 0 capte.
# Ils resolvent en 1 JOUR -> 1 observation quasi INDEPENDANTE par ville et par jour,
# ce qui est precisement ce qui manque au dossier (2 381 resolus = ~44 semaines).
# Leur liquidite mesuree va de 1 a 600 $ : le seuil de 500 $ en tuerait ~90 %.
# On leur applique donc un seuil dedie. La friction reste mesurable (bid/ask/spread
# sont deja collectes) -- c'est elle qui avait tue le tennis malgre un edge reel.
WEATHER_TAGS = {"weather", "temperature", "climate"}
MIN_LIQUIDITY_WEATHER = 20.0

# --- AJOUT 31/08/2026 : GROUPE TEMOIN (decision Dan) ---------------------------
# Le dossier a produit SIX faux signaux en une soiree (10/08) faute de pouvoir
# distinguer un edge d'un artefact de methode. On ajoute donc un marche ou l'edge
# est structurellement IMPOSSIBLE : le crypto. La "source externe" y est le prix
# spot -- la meme information que celle du marche, publique a la microseconde et
# deja arbitree par des bots pro. Mesure du 30/08 : spread median 0,010, 92 % des
# marches sous 2c, liquidite mediane 11 859 $ (le meilleur profil du site).
#   => Si l'analyse de janvier trouve un edge ICI, c'est la METHODE qui est cassee.
# C'est le "tester le filtre contre des cas connus" qui a manque a tout le dossier.
#
# On EXCLUT les tranches de 5 minutes ("Bitcoin Up or Down - 7:30PM-7:35PM ET") :
# 288 marches/jour/actif feraient ~37 000 lignes de referentiel d'ici janvier pour
# aucune valeur. Le filtre porte sur la DUREE DE VIE du marche, pas sur le temps
# restant : filtrer sur le temps restant amputerait chaque marche de ses derniers
# snapshots, justement ceux ou le prix converge.
WITNESS_TAGS = {"crypto-prices"}
# Recensement du 31/08 : il n'existe AUCUN marche de prix crypto non recurrent a
# echeance 1-150 j (verifie sur crypto-prices, bitcoin, ethereum -> 0). Tous les
# marches de prix sont des series intraday. Le temoin ne peut donc etre qu'une de
# ces series -- et c'est tres bien : le spot y est l'information, publique a la
# microseconde, donc l'edge y est structurellement impossible. C'est le but.
# Granularites ouvertes mesurees : 5M=371, 1H=128, 15M=124.
# On retient 1H : assez d'observations independantes pour calibrer (~24/j/actif),
# assez peu pour ne rien couter (+1 % de lignes par snapshot).
# 5M et 15M sont ecartes : 288 marches/jour/actif pour la meme information.
WITNESS_GRANULARITY = "1H"

# --- PATCH 13/09/2026 : mode simulation ---------------------------------------
# `--dry-run` execute TOUT (appels reseau, appariement des carnets, previsions)
# mais n'ecrit aucun fichier. Sert a valider un patch sur un runner sans risquer
# d'abimer une collecte irremplacable : l'API ne permet pas de rejouer le passe
# (verifie le 31/08 : /trades plafonne a 50 000, prices-history ~1 mois glissant).
DRY_RUN = False

# --- PATCH 13/09/2026 : les villes -------------------------------------------
# On collectait la dispersion GFS de 10 villes US alors que les marches meteo
# Polymarket portent sur 51 villes (recensement du referentiel, 33 456 marches).
# Boston et Phoenix n'ont AUCUN marche en face : on les garde quand meme, leur
# serie tourne depuis le 02/08 et les couper ne rapporterait rien (3 Ko/run).
# Les 10 villes historiques gardent leurs COORDONNEES D'ORIGINE : on ne deplace
# pas le point d'interrogation d'une serie en cours, meme de 400 m.
#
# Format : cle -> (lat, lon, tz, unite, libelle tel qu'il apparait dans les
# questions Polymarket). L'unite est celle du MARCHE, mesuree sur le referentiel :
# toutes les villes US sont en F, toutes les autres en C, aucune ville mixte.
# Le libelle est la CLE DE JOINTURE prix <-> prevision : sans lui il faudrait
# redeviner la ville a partir du texte de la question au moment de l'analyse.
# Coordonnees des 43 nouvelles villes : geocodees via l'API Open-Meteo, pas
# tapees de memoire (Panama City -> Panama et non Floride, Seoul (Incheon) ->
# Incheon KR : deux pieges verifies).
CITIES = {
    "nyc":         (  40.710,   -74.010, "America/New_York", "F", "New York City"),
    "losangeles":  (  34.050,  -118.240, "America/Los_Angeles", "F", "Los Angeles"),
    "chicago":     (  41.880,   -87.630, "America/Chicago", "F", "Chicago"),
    "miami":       (  25.770,   -80.190, "America/New_York", "F", "Miami"),
    "phoenix":     (  33.450,  -112.070, "America/Phoenix", "F", None),
    "denver":      (  39.740,  -104.980, "America/Denver", "F", "Denver"),
    "seattle":     (  47.610,  -122.330, "America/Los_Angeles", "F", "Seattle"),
    "austin":      (  30.270,   -97.740, "America/Chicago", "F", "Austin"),
    "boston":      (  42.360,   -71.060, "America/New_York", "F", None),
    "atlanta":     (  33.750,   -84.390, "America/New_York", "F", "Atlanta"),
    "hongkong":    (  22.278,   114.175, "Asia/Hong_Kong", "C", "Hong Kong"),
    "london":      (  51.509,    -0.126, "Europe/London", "C", "London"),
    "munich":      (  48.137,    11.575, "Europe/Berlin", "C", "Munich"),
    "milan":       (  45.464,     9.190, "Europe/Rome", "C", "Milan"),
    "amsterdam":   (  52.374,     4.890, "Europe/Amsterdam", "C", "Amsterdam"),
    "dallas":      (  32.783,   -96.807, "America/Chicago", "F", "Dallas"),
    "houston":     (  29.763,   -95.363, "America/Chicago", "F", "Houston"),
    "sanfrancisco":(  37.775,  -122.419, "America/Los_Angeles", "F", "San Francisco"),
    "mexicocity":  (  19.428,   -99.128, "America/Mexico_City", "C", "Mexico City"),
    "saopaulo":    ( -23.547,   -46.636, "America/Sao_Paulo", "C", "Sao Paulo"),
    "buenosaires": ( -34.613,   -58.377, "America/Argentina/Buenos_Aires", "C", "Buenos Aires"),
    "toronto":     (  43.706,   -79.399, "America/Toronto", "C", "Toronto"),
    "wellington":  ( -41.287,   174.776, "Pacific/Auckland", "C", "Wellington"),
    "paris":       (  48.853,     2.349, "Europe/Paris", "C", "Paris"),
    "ankara":      (  39.920,    32.854, "Europe/Istanbul", "C", "Ankara"),
    "helsinki":    (  60.170,    24.935, "Europe/Helsinki", "C", "Helsinki"),
    "madrid":      (  40.416,    -3.703, "Europe/Madrid", "C", "Madrid"),
    "capetown":    ( -33.926,    18.423, "Africa/Johannesburg", "C", "Cape Town"),
    "jeddah":      (  21.490,    39.186, "Asia/Riyadh", "C", "Jeddah"),
    "warsaw":      (  52.230,    21.012, "Europe/Warsaw", "C", "Warsaw"),
    "telaviv":     (  32.081,    34.781, "Asia/Jerusalem", "C", "Tel Aviv"),
    "istanbul":    (  41.014,    28.950, "Europe/Istanbul", "C", "Istanbul"),
    "seoulincheon":(  37.456,   126.705, "Asia/Seoul", "C", "Seoul (Incheon)"),
    "tokyo":       (  35.690,   139.692, "Asia/Tokyo", "C", "Tokyo"),
    "shanghai":    (  31.222,   121.458, "Asia/Shanghai", "C", "Shanghai"),
    "singapore":   (   1.290,   103.850, "Asia/Singapore", "C", "Singapore"),
    "shenzhen":    (  22.546,   114.068, "Asia/Shanghai", "C", "Shenzhen"),
    "beijing":     (  39.907,   116.397, "Asia/Shanghai", "C", "Beijing"),
    "kualalumpur": (   3.141,   101.687, "Asia/Kuala_Lumpur", "C", "Kuala Lumpur"),
    "guangzhou":   (  23.117,   113.250, "Asia/Shanghai", "C", "Guangzhou"),
    "chengdu":     (  30.667,   104.067, "Asia/Shanghai", "C", "Chengdu"),
    "taipei":      (  25.053,   121.526, "Asia/Taipei", "C", "Taipei"),
    "busan":       (  35.102,   129.030, "Asia/Seoul", "C", "Busan"),
    "qingdao":     (  36.065,   120.380, "Asia/Shanghai", "C", "Qingdao"),
    "wuhan":       (  30.583,   114.267, "Asia/Shanghai", "C", "Wuhan"),
    "karachi":     (  24.861,    67.010, "Asia/Karachi", "C", "Karachi"),
    "chongqing":   (  29.560,   106.558, "Asia/Shanghai", "C", "Chongqing"),
    "lucknow":     (  26.839,    80.923, "Asia/Kolkata", "C", "Lucknow"),
    "manila":      (  14.604,   120.982, "Asia/Manila", "C", "Manila"),
    "moscow":      (  55.752,    37.618, "Europe/Moscow", "C", "Moscow"),
    "zhengzhou":   (  34.758,   113.649, "Asia/Shanghai", "C", "Zhengzhou"),
    "jinan":       (  36.668,   116.997, "Asia/Shanghai", "C", "Jinan"),
    "panamacity":  (   8.994,   -79.520, "America/Panama", "C", "Panama City"),
}


def get_json(url, tries=4, timeout=45):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    print(f"  ! echec apres {tries} essais : {url[:110]} -> {last}", file=sys.stderr)
    return None


def fnum(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def fetch_events(tag, closed=False, max_pages=12, page=100):
    """Pagine les events d'un tag. Gamma plafonne a ~100/appel.
    Pour les marches FERMES on trie par date de fin decroissante, pas par volume :
    sinon un marche peu liquide qui vient de resoudre ne remonte jamais dans le top
    volume et sa resolution serait perdue."""
    order = "endDate" if closed else "volume"
    out, offset = [], 0
    for _ in range(max_pages):
        url = (f"{GAMMA}/events?closed={'true' if closed else 'false'}"
               f"&limit={page}&offset={offset}&tag_slug={urllib.parse.quote(tag)}"
               f"&order={order}&ascending=false")
        batch = get_json(url)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return out


def collect_markets(closed=False):
    """Snapshot de tous les marches politiques (dedupliques par conditionId)."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=MAX_HORIZON_DAYS)
    seen, rows = set(), []

    for tag in TAGS:
        events = fetch_events(tag, closed=closed)
        min_liq = MIN_LIQUIDITY_WEATHER if tag in WEATHER_TAGS else MIN_LIQUIDITY
        print(f"  tag={tag:<12} events={len(events)}")
        for ev in events:
            ev_tags = ",".join(sorted({t.get("slug", "") for t in (ev.get("tags") or [])}))
            for m in (ev.get("markets") or []):
                cid = m.get("conditionId")
                if not cid or cid in seen:
                    continue

                if not closed:
                    if not m.get("enableOrderBook"):
                        continue
                    liq = fnum(m.get("liquidityNum"), 0.0) or 0.0
                    if liq < min_liq:
                        continue
                    ed = m.get("endDate") or ""
                    try:
                        if ed and datetime.fromisoformat(ed.replace("Z", "+00:00")) > horizon:
                            continue
                    except ValueError:
                        pass
                    if tag in WITNESS_TAGS and WITNESS_GRANULARITY not in ev_tags.split(","):
                        continue

                seen.add(cid)
                bid, ask = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except json.JSONDecodeError:
                        prices = None

                rows.append({
                    "ts": now.isoformat(),
                    "condition_id": cid,
                    "question": (m.get("question") or "").replace("\n", " ").strip(),
                    "event_tags": ev_tags,
                    "best_bid": bid,
                    "best_ask": ask,
                    "spread": (round(ask - bid, 6) if (bid is not None and ask is not None) else None),
                    "last_trade": fnum(m.get("lastTradePrice")),
                    "volume": fnum(m.get("volumeNum")),
                    "volume_24h": fnum(m.get("volume24hr")),
                    "liquidity": fnum(m.get("liquidityNum")),
                    "end_date": m.get("endDate"),
                    "closed": bool(m.get("closed")),
                    "neg_risk": bool(m.get("negRisk")),
                    "clob_token_ids": m.get("clobTokenIds") or "",
                    "outcome_prices": json.dumps(prices) if prices else None,
                })
    return rows


def post_json(url, payload, tries=3, timeout=60):
    last = None
    for i in range(tries):
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={
                "User-Agent": UA, "Accept": "application/json",
                "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    print(f"  ! POST echoue apres {tries} essais : {url[:70]} -> {last}", file=sys.stderr)
    return None


# --- PATCH 13/09/2026 : LA PROFONDEUR AU MEILLEUR PRIX -----------------------
# Mesure du 13/09 sur 27 carnets reels : `liquidityNum` (seule colonne de
# profondeur collectee jusqu'ici) est la profondeur TOTALE du carnet -- r = +1,00,
# c'est litteralement le meme nombre. Sur l'EXECUTABLE au meilleur prix, r = +0,15 :
# aucune information. Carnet total median 17 546 $ pour 12,4 $ au meilleur prix,
# et "Will 1 Fed rate cut happen in 2026 ?" affiche 308 270 $ de carnet pour
# 0,80 $ reellement disponibles.
#   => Le filtre "spread <= 2c ET profondeur >= 100 $" qui a demasque le faux
#      signal sports du 31/08 portait donc sur le carnet TOTAL : il laissait
#      passer des marches ou il n'y a rien a executer. Sans ces colonnes,
#      l'analyse de janvier ne pourra pas faire mieux.
#
# Cout mesure le 13/09 : GET /book un par un = 0,263 s/marche -> 43 min pour
# 9 900 marches (le workflow expire a 25). POST /books groupe = 0,22 s les
# 50 tokens -> 198 appels, 0,7 min par snapshot. C'est la seule voie tenable.
BOOKS_BATCH = 50   # MESURE : au-dela, l'API TRONQUE SANS RIEN DIRE
                   # (100 demandes -> 51 recus, 500 -> 218). Ne pas augmenter
                   # sans reverifier la completude lot par lot.


def collect_depth(rows):
    """Enrichit chaque ligne avec le carnet au meilleur prix, via POST /books.

    Appariement par `asset_id` et JAMAIS par l'ordre de la reponse : verifie le
    13/09 (50/50 apparies). Une jointure supposee est l'erreur la plus chere du
    dossier -- ici elle est testee a chaque run par le compteur d'apparies.
    """
    par_token = {}
    for r in rows:
        raw = r.get("clob_token_ids") or ""
        try:
            toks = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if toks:
            par_token[str(toks[0])] = r      # token YES
    tokens = list(par_token)
    if not tokens:
        print("  profondeur : aucun token -> etape sautee")
        return 0, 0

    demandes = apparies = 0
    for i in range(0, len(tokens), BOOKS_BATCH):
        lot = tokens[i:i + BOOKS_BATCH]
        rep = post_json(f"{CLOB}/books", [{"token_id": t} for t in lot])
        demandes += len(lot)
        if not rep:
            continue
        items = rep if isinstance(rep, list) else (rep.get("books") or rep.get("data") or [])
        for it in items:
            if not isinstance(it, dict):
                continue
            r = par_token.get(str(it.get("asset_id") or it.get("token_id") or ""))
            if r is None:
                continue          # carnet renvoye pour un token non demande : on ignore
            apparies += 1
            bids, asks = it.get("bids") or [], it.get("asks") or []
            if bids:
                tb = max(float(x["price"]) for x in bids)
                sz = sum(float(x["size"]) for x in bids if float(x["price"]) == tb)
                r["book_bid"], r["bid_size_usd"] = round(tb, 4), round(tb * sz, 2)
            if asks:
                ta = min(float(x["price"]) for x in asks)
                sz = sum(float(x["size"]) for x in asks if float(x["price"]) == ta)
                r["book_ask"], r["ask_size_usd"] = round(ta, 4), round(ta * sz, 2)
            if not bids and not asks:
                r["book_bid"] = r["book_ask"] = ""
                r["bid_size_usd"] = r["ask_size_usd"] = 0.0
        time.sleep(0.15)

    pct = round(100.0 * apparies / demandes) if demandes else 0
    print(f"  profondeur : {apparies}/{demandes} carnets apparies ({pct}%)")
    if demandes and pct < 60:
        # Pas une erreur fatale -- le snapshot de prix reste bon -- mais il faut
        # que ca se VOIE dans les logs le jour ou l'API change de comportement.
        print(f"  ! appariement faible ({pct}%) : verifier BOOKS_BATCH et /books",
              file=sys.stderr)
    return demandes, apparies


def collect_forecasts():
    """Dispersion d'ensemble GFS par ville — le signal 'info externe' jamais teste.

    PATCH 13/09/2026 : chaque ville est desormais interrogee dans l'unite de SON
    marche (F pour les villes US, C pour les autres). Les lignes du fichier ne
    partagent donc plus la meme unite -> la colonne `unit` devient obligatoire.
    Sans elle, comparer une prevision a un prix serait une jointure entre deux
    echelles differentes -- l'erreur type du dossier (19 vs 22 momme, UTC vs Paris).
    """
    now = datetime.now(timezone.utc)
    rows = []
    for city, (lat, lon, tz, unite, label) in CITIES.items():
        u = "fahrenheit" if unite == "F" else "celsius"
        url = (f"{ENSEMBLE}?latitude={lat}&longitude={lon}&models=gfs025"
               f"&daily=temperature_2m_max,temperature_2m_min&forecast_days=7"
               f"&temperature_unit={u}&timezone={urllib.parse.quote(tz)}")
        d = get_json(url)
        if not d or "daily" not in d:
            continue
        daily = d["daily"]
        days = daily.get("time") or []
        for var in ("temperature_2m_max", "temperature_2m_min"):
            members = [k for k in daily if k.startswith(var)]
            if not members:
                continue
            for i, day in enumerate(days):
                vals = []
                for k in members:
                    seq = daily.get(k) or []
                    if i < len(seq) and seq[i] is not None:
                        vals.append(float(seq[i]))
                if len(vals) < 5:
                    continue
                mean = statistics.fmean(vals)
                rows.append({
                    "ts": now.isoformat(),
                    "city": city,
                    "city_label": label or "",   # cle de jointure avec les marches
                    "unit": unite,               # F ou C — varie d'une ligne a l'autre
                    "variable": var,
                    "target_day": day,
                    "lead_days": i,
                    "n_members": len(vals),
                    "mean": round(mean, 3),
                    "sd": round(statistics.pstdev(vals), 4),   # <- le signal
                    "min": round(min(vals), 2),
                    "max": round(max(vals), 2),
                    "p10": round(sorted(vals)[max(0, int(0.10 * len(vals)) - 1)], 2),
                    "p90": round(sorted(vals)[min(len(vals) - 1, int(0.90 * len(vals)))], 2),
                })
        time.sleep(0.6)   # courtoisie envers une API gratuite
    return rows


# Colonnes conservees dans chaque snapshot. `idx` = entier court attribue par le
# referentiel : un condition_id fait 66 caracteres hex, le repeter a chaque snapshot
# represente ~70% du poids du fichier.
SNAP_COLS = ["idx", "best_bid", "best_ask", "spread",
             "last_trade", "volume", "volume_24h", "liquidity",
             # PATCH 13/09 : l'EXECUTABLE au meilleur prix. `liquidity` est le
             # carnet TOTAL et ne dit rien de ce qu'on peut passer maintenant.
             # book_bid/book_ask sont les prix vus par le CLOB au meme instant :
             # ils permettent de CONTROLER que la taille va bien avec le prix
             # (Gamma et le CLOB peuvent diverger de quelques secondes).
             "book_bid", "book_ask", "bid_size_usd", "ask_size_usd"]
REF_PATH = "refs/markets_ref.csv"
REF_COLS = ["idx", "condition_id", "question", "event_tags", "end_date",
            "neg_risk", "first_seen", "last_seen",
            # PATCH 13/09 : sans les token_ids on ne peut pas re-interroger le
            # carnet d'un marche apres coup. Ecrit une seule fois par marche
            # (referentiel), jamais dans les snapshots. Les marches deja resolus
            # resteront vides : ils ne repasseront plus, et leur carnet n'existe
            # de toute facon plus.
            "clob_token_ids"]


def update_ref(rows):
    """Referentiel idx <-> cid + libelle. Upsert, CSV clair (git delta-compresse bien).
    Enrichit `rows` sur place avec l'idx. L'idx est attribue a la 1re apparition et
    ne bouge jamais : les snapshots passes restent lisibles."""
    ref = {}
    max_idx = 0
    if os.path.exists(REF_PATH):
        with open(REF_PATH, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ref[r["condition_id"]] = r
                max_idx = max(max_idx, int(r["idx"]))
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for r in rows:
        cid = r["condition_id"]
        if cid in ref:
            ref[cid]["last_seen"] = now
            if not ref[cid].get("clob_token_ids"):
                ref[cid]["clob_token_ids"] = r.get("clob_token_ids", "")
        else:
            max_idx += 1
            ref[cid] = {"idx": max_idx, "condition_id": cid, "question": r["question"],
                        "event_tags": r["event_tags"], "end_date": r["end_date"],
                        "neg_risk": r["neg_risk"], "first_seen": now, "last_seen": now,
                        "clob_token_ids": r.get("clob_token_ids", "")}
            added += 1
        r["idx"] = ref[cid]["idx"]
    if DRY_RUN:
        print(f"  [dry-run] {REF_PATH} : {len(ref)} marches, +{added} nouveaux, NON ecrit")
        return
    os.makedirs(os.path.dirname(REF_PATH), exist_ok=True)
    with open(REF_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REF_COLS, extrasaction="ignore")
        w.writeheader()
        for cid in sorted(ref, key=lambda c: int(ref[c]["idx"])):   # ordre stable = diff git minimal
            w.writerow(ref[cid])
    print(f"  -> {REF_PATH}  ({len(ref)} marches connus, +{added} nouveaux)")


RES_PATH = "refs/resolutions.csv"
RES_COLS = ["idx", "condition_id", "outcome_prices", "end_date", "seen_at"]


def save_resolutions(closed_rows):
    """N'enregistre que la resolution des marches qu'on a REELLEMENT observes.
    Sans ce filtre on re-telecharge ~29k marches historiques a chaque run pour rien.
    Fichier cumulatif en CSV clair : il grandit de quelques lignes par jour."""
    if not os.path.exists(REF_PATH):
        print("  (pas de referentiel : rien a resoudre)")
        return
    known = {}
    with open(REF_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            known[r["condition_id"]] = r["idx"]

    done = {}
    if os.path.exists(RES_PATH):
        with open(RES_PATH, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                done[r["condition_id"]] = r

    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for r in closed_rows:
        cid = r["condition_id"]
        if cid not in known or cid in done or not r.get("outcome_prices"):
            continue
        done[cid] = {"idx": known[cid], "condition_id": cid,
                     "outcome_prices": r["outcome_prices"],
                     "end_date": r["end_date"], "seen_at": now}
        added += 1

    os.makedirs(os.path.dirname(RES_PATH), exist_ok=True)
    with open(RES_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RES_COLS, extrasaction="ignore")
        w.writeheader()
        for cid in sorted(done, key=lambda c: int(done[c]["idx"])):
            w.writerow(done[cid])
    print(f"  -> {RES_PATH}  ({len(done)} resolutions connues, +{added} nouvelles)")


def write_gz(path, rows):
    if DRY_RUN:
        print(f"  [dry-run] {path} : {len(rows)} lignes, NON ecrit")
        return
    if not rows:
        print(f"  (rien a ecrire pour {path})")
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    print(f"  -> {path}  ({len(rows)} lignes, {os.path.getsize(path) / 1024:.1f} Ko)")
    return True


def controle_dry_run(rows, fcasts):
    """Controle de CONTENU, pas de reussite d'appel.

    "9 559 carnets apparies" dit qu'on a retrouve les carnets, pas que les colonnes
    sont remplies ni que la jointure ville tient. Le dossier a paye assez cher les
    jointures supposees (protocole du 20/08) : on les teste ici, a chaque simulation.
    """
    print("")
    print("=== CONTROLE DU PATCH ===")
    n = len(rows)
    for c in ("book_bid", "book_ask", "bid_size_usd", "ask_size_usd", "clob_token_ids"):
        plein = sum(1 for r in rows if r.get(c) not in (None, ""))
        print(f"  {c:16s} rempli sur {plein:5d}/{n} ({100*plein//max(n,1)}%)")

    # 1. La taille va-t-elle avec le prix ? book_bid (CLOB) doit coller a best_bid
    #    (Gamma). Si les deux divergent, la taille decrit un autre instant que le prix.
    ecarts = [abs(float(r["book_bid"]) - float(r["best_bid"]))
              for r in rows
              if r.get("book_bid") not in (None, "") and r.get("best_bid") is not None]
    if ecarts:
        ecarts.sort()
        gros = sum(1 for e in ecarts if e > 0.02)
        print(f"  |book_bid - best_bid| : median {ecarts[len(ecarts)//2]:.4f}"
              f" | p95 {ecarts[int(0.95*len(ecarts))]:.4f}"
              f" | > 2c : {gros} ({100*gros//len(ecarts)}%)")

    # 2. Ce qui motive tout le patch : l'executable est-il vraiment petit ?
    tops = sorted(float(r["bid_size_usd"]) for r in rows
                  if r.get("bid_size_usd") not in (None, ""))
    liqs = sorted(float(r["liquidity"]) for r in rows if r.get("liquidity") is not None)
    if tops and liqs:
        print(f"  executable au meilleur bid : median {tops[len(tops)//2]:.1f} $"
              f" | >= 100 $ : {100*sum(1 for t in tops if t >= 100)//len(tops)}%")
        print(f"  liquidity (carnet total)   : median {liqs[len(liqs)//2]:.0f} $"
              f" | >= 100 $ : {100*sum(1 for l in liqs if l >= 100)//len(liqs)}%")
        print("  (l'ecart entre ces deux lignes EST la raison du patch)")

    # 3. JOINTURE ville : chaque libelle de prevision retrouve-t-il des marches ?
    labels = {v[4] for v in CITIES.values() if v[4]}
    vus = {f.get("city_label") for f in fcasts if f.get("city_label")}
    print(f"  villes prevues avec libelle : {len(labels)} | presentes dans les previsions : {len(vus)}")
    qs = [r.get("question", "") for r in rows]
    sans = [l for l in labels if not any((" in " + l + " be ") in q for q in qs)]
    print(f"  libelles SANS aucun marche en face ce run : {len(sans)}"
          + (f" -> {sorted(sans)[:6]}" if sans else ""))
    unites = {}
    for f in fcasts:
        unites[f.get("unit")] = unites.get(f.get("unit"), 0) + 1
    print(f"  previsions par unite : {unites}")



def main():
    global DRY_RUN
    DRY_RUN = "--dry-run" in sys.argv
    if DRY_RUN:
        print("### MODE SIMULATION : aucun fichier ne sera ecrit ###")
    resolve = "--resolve" in sys.argv
    now = datetime.now(timezone.utc)
    day, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y%m%dT%H%M")

    if resolve:
        print("[RESOLUTIONS] marches politiques fermes")
        save_resolutions(collect_markets(closed=True))
        return

    print("[SNAPSHOT] marches politiques")
    rows = collect_markets(closed=False)

    # PATCH 13/09 : la profondeur au meilleur prix, via POST /books groupe.
    # Se place APRES collect_markets (qui fournit les token_ids) et AVANT l'ecriture.
    print("[SNAPSHOT] profondeur des carnets")
    collect_depth(rows)

    # Le libelle d'un marche ne change jamais : on le sort des snapshots vers un
    # referentiel unique en CSV clair (que git delta-compresse tres bien d'un commit
    # a l'autre). Les snapshots ne gardent que ce qui bouge -> ~5x plus leger.
    update_ref(rows)
    # .get() et non r[k] : un marche dont le carnet n'a pas ete apparie n'a tout
    # simplement pas les colonnes de profondeur. Une case vide est une information
    # honnete ; un KeyError ferait sauter tout le snapshot pour un carnet manquant.
    light = [{k: r.get(k, "") for k in SNAP_COLS} for r in rows]
    write_gz(f"snaps/{day}/{stamp}_markets.csv.gz", light)

    print("[SNAPSHOT] dispersion d'ensemble GFS")
    fcasts = collect_forecasts()
    write_gz(f"forecasts/{day}/{stamp}_ensemble.csv.gz", fcasts)

    if DRY_RUN:
        controle_dry_run(rows, fcasts)


if __name__ == "__main__":
    main()

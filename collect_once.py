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

# Villes US utilisees par les marches meteo Polymarket (lat, lon, tz)
CITIES = {
    "nyc":     (40.71,  -74.01, "America/New_York"),
    "losangeles": (34.05, -118.24, "America/Los_Angeles"),
    "chicago": (41.88,  -87.63, "America/Chicago"),
    "miami":   (25.77,  -80.19, "America/New_York"),
    "phoenix": (33.45, -112.07, "America/Phoenix"),
    "denver":  (39.74, -104.98, "America/Denver"),
    "seattle": (47.61, -122.33, "America/Los_Angeles"),
    "austin":  (30.27,  -97.74, "America/Chicago"),
    "boston":  (42.36,  -71.06, "America/New_York"),
    "atlanta": (33.75,  -84.39, "America/New_York"),
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
                    "outcome_prices": json.dumps(prices) if prices else None,
                })
    return rows


def collect_forecasts():
    """Dispersion d'ensemble GFS par ville — le signal 'info externe' jamais teste."""
    now = datetime.now(timezone.utc)
    rows = []
    for city, (lat, lon, tz) in CITIES.items():
        url = (f"{ENSEMBLE}?latitude={lat}&longitude={lon}&models=gfs025"
               f"&daily=temperature_2m_max,temperature_2m_min&forecast_days=7"
               f"&temperature_unit=fahrenheit&timezone={urllib.parse.quote(tz)}")
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
             "last_trade", "volume", "volume_24h", "liquidity"]
REF_PATH = "refs/markets_ref.csv"
REF_COLS = ["idx", "condition_id", "question", "event_tags", "end_date",
            "neg_risk", "first_seen", "last_seen"]


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
        else:
            max_idx += 1
            ref[cid] = {"idx": max_idx, "condition_id": cid, "question": r["question"],
                        "event_tags": r["event_tags"], "end_date": r["end_date"],
                        "neg_risk": r["neg_risk"], "first_seen": now, "last_seen": now}
            added += 1
        r["idx"] = ref[cid]["idx"]
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


def main():
    resolve = "--resolve" in sys.argv
    now = datetime.now(timezone.utc)
    day, stamp = now.strftime("%Y-%m-%d"), now.strftime("%Y%m%dT%H%M")

    if resolve:
        print("[RESOLUTIONS] marches politiques fermes")
        save_resolutions(collect_markets(closed=True))
        return

    print("[SNAPSHOT] marches politiques")
    rows = collect_markets(closed=False)

    # Le libelle d'un marche ne change jamais : on le sort des snapshots vers un
    # referentiel unique en CSV clair (que git delta-compresse tres bien d'un commit
    # a l'autre). Les snapshots ne gardent que ce qui bouge -> ~5x plus leger.
    update_ref(rows)
    light = [{k: r[k] for k in SNAP_COLS} for r in rows]
    write_gz(f"snaps/{day}/{stamp}_markets.csv.gz", light)

    print("[SNAPSHOT] dispersion d'ensemble GFS")
    write_gz(f"forecasts/{day}/{stamp}_ensemble.csv.gz", collect_forecasts())


if __name__ == "__main__":
    main()

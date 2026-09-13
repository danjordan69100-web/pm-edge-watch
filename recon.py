#!/usr/bin/env python3
"""
recon.py — RECONNAISSANCE SEULE. N'ECRIT RIEN, NE COMMIT RIEN.

Repond a deux questions laissees ouvertes le 13/09/2026 :

  Q1. Les categories Polymarket JAMAIS instruites (entertainment, companies,
      science-technology, health, commodities) ont-elles la cadence et la friction
      qui justifieraient de les ajouter a la collecte ?
      Critere etabli le 31/08 : cadence + friction + ASYMETRIE D'INFORMATION.
      Ce script mesure les deux premieres. La troisieme ne se mesure pas, elle se juge.

  Q2. `liquidityNum` (seule colonne de profondeur collectee aujourd'hui) est-il un
      proxy acceptable de la PROFONDEUR REELLE au meilleur prix ?
      C'est le filtre "spread <= 2c ET profondeur >= 100 $" qui a fait tomber le faux
      signal sports du 31/08 de -57 pts a -0,8 pt. Sans lui, l'analyse de janvier ne
      peut pas distinguer un edge d'un carnet vide.

METHODE — garde-fous du dossier appliques :
  * On NE DEVINE PAS les slugs : on les recense via /tags, et on VERIFIE pour chaque
    tag que les events renvoyes le portent vraiment (le 31/08, un filtre par duree de
    vie ne filtrait rien et personne ne l'avait teste).
  * Un echantillon sert a DECOUVRIR, jamais a ELIMINER (lecon n.7). Les chiffres
    sortis ici ouvrent une piste ou posent une question : aucun ne ferme une piste.
  * Chaque nombre imprime est [M], mesure par ce script.
"""
import json, statistics, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MAX_HORIZON_DAYS = 150

# Deja collecte par collect_once.py -- sert de TEMOIN DE CALIBRATION : si la mesure
# ne retrouve pas le profil connu de ces tags, c'est l'instrument qui est casse,
# pas le marche. (Tester le filtre contre des cas connus AVANT de lui faire tuer
# une piste -- lecon n.6 du dossier.)
DEJA_COLLECTE = ["politics", "elections", "weather", "crypto-prices"]

# Jamais instruit sur Polymarket au 13/09/2026. "sports" est inclus DELIBEREMENT :
# il a ete tranche le 31/08 (carnets vides, ecart -0,8 pt sur le tradable), donc il
# sert de second temoin -- on doit retrouver ce verdict.
CANDIDATS = ["entertainment", "companies", "science-technology", "health",
             "commodities", "business", "tech", "movies", "music", "awards",
             "earnings", "economics", "pop-culture", "sports"]


def get_json(url, tries=4, timeout=45):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    print("    ! echec %dx : %s -> %s" % (tries, url[:100], last), file=sys.stderr)
    return None


def fnum(v, d=None):
    try:
        if v is None or v == "":
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


def med(x):
    return statistics.median(x) if x else None


# ---------------------------------------------------------------------------
# 0. RECENSEMENT DES TAGS -- on ne devine pas, on lit le catalogue.
# ---------------------------------------------------------------------------
def recenser_tags():
    print("\n=== 0. CATALOGUE DES TAGS (recense, pas devine) ===", flush=True)
    tags, offset = [], 0
    while len(tags) < 3000:
        b = get_json("%s/tags?limit=100&offset=%d" % (GAMMA, offset))
        if not b:
            break
        tags.extend(b)
        if len(b) < 100:
            break
        offset += 100
    if not tags:
        print("  /tags indisponible -> on retombe sur les slugs candidats,")
        print("  chacun VERIFIE individuellement ci-dessous (colonne 'tag ok%').")
        return None
    print("  tags au catalogue : %d [M]" % len(tags))
    slugs = set(t.get("slug") for t in tags if t.get("slug"))
    for c in CANDIDATS:
        print("    %-20s %s" % (c, "PRESENT" if c in slugs else "ABSENT du catalogue"))
    return slugs


# ---------------------------------------------------------------------------
# 1. CADENCE + FRICTION PAR TAG
# ---------------------------------------------------------------------------
def fetch_events(tag, max_pages=10, page=100):
    out, offset = [], 0
    for _ in range(max_pages):
        url = ("%s/events?closed=false&limit=%d&offset=%d&tag_slug=%s"
               "&order=volume&ascending=false"
               % (GAMMA, page, offset, urllib.parse.quote(tag)))
        b = get_json(url)
        if not b:
            break
        out.extend(b)
        if len(b) < page:
            break
        offset += page
    return out


def mesurer_tag(tag):
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=MAX_HORIZON_DAYS)
    events = fetch_events(tag)
    if not events:
        return {"tag": tag, "events": 0, "note": "0 event -> tag inexistant ou vide"}

    # LE FILTRE A-T-IL FILTRE ? Si pct_porte est bas, l'API a ignore tag_slug et
    # tout ce qui suit porte sur autre chose que le tag demande.
    porte = sum(1 for ev in events
                if tag in set(t.get("slug", "") for t in (ev.get("tags") or [])))
    pct_porte = int(round(100.0 * porte / len(events)))

    seen = set()
    sp, liq, hz, ech = [], [], [], []
    vol = 0.0
    sans_carnet = hors_horizon = 0
    for ev in events:
        for m in (ev.get("markets") or []):
            cid = m.get("conditionId")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            if not m.get("enableOrderBook"):
                sans_carnet += 1
                continue
            ed = m.get("endDate") or ""
            try:
                d = datetime.fromisoformat(ed.replace("Z", "+00:00"))
                if d > horizon:
                    hors_horizon += 1
                    continue
                hz.append((d - now).total_seconds() / 86400.0)
            except ValueError:
                pass
            b, a = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
            vol += fnum(m.get("volumeNum"), 0.0) or 0.0
            l = fnum(m.get("liquidityNum"), 0.0) or 0.0
            liq.append(l)
            if b is not None and a is not None and a > 0:
                sp.append(a - b)
                if m.get("clobTokenIds") and len(ech) < 40:
                    ech.append({"cid": cid, "q": (m.get("question") or "")[:44],
                                "tok": m["clobTokenIds"], "liq": l})
    return {"tag": tag, "events": len(events), "pct_porte": pct_porte,
            "marches": len(seen), "sans_carnet": sans_carnet,
            "hors_horizon": hors_horizon, "avec_prix": len(sp),
            "spread_med": med(sp),
            "pct_2c": (int(round(100.0 * sum(1 for x in sp if x <= 0.02) / len(sp)))
                       if sp else None),
            "liq_med": med(liq), "horizon_med": med(hz), "volume": vol, "ech": ech}


# ---------------------------------------------------------------------------
# 2. PROFONDEUR REELLE vs liquidityNum
# ---------------------------------------------------------------------------
def profondeur(ech, n=30):
    """Tire le carnet CLOB et compare la profondeur reelle a liquidityNum."""
    out = []
    for e in ech[:n]:
        try:
            toks = json.loads(e["tok"]) if isinstance(e["tok"], str) else e["tok"]
        except Exception:
            continue
        if not toks:
            continue
        b = get_json("%s/book?token_id=%s" % (CLOB, toks[0]), tries=2, timeout=30)
        if not b:
            continue
        bids = b.get("bids") or []
        asks = b.get("asks") or []
        if not bids or not asks:
            out.append(dict(e, prof_top=0.0, prof_tot=0.0,
                            niv="%d/%d" % (len(bids), len(asks)), spread_reel=float("nan")))
            continue
        tb = max(float(x["price"]) for x in bids)
        ta = min(float(x["price"]) for x in asks)
        szb = sum(float(x["size"]) for x in bids if float(x["price"]) == tb)
        sza = sum(float(x["size"]) for x in asks if float(x["price"]) == ta)
        tot = (sum(float(x["size"]) * float(x["price"]) for x in bids)
               + sum(float(x["size"]) * (1.0 - float(x["price"])) for x in asks))
        out.append(dict(e, prof_top=min(szb * tb, sza * ta), prof_tot=tot,
                        niv="%d/%d" % (len(bids), len(asks)),
                        spread_reel=round(ta - tb, 4)))
        time.sleep(0.25)
    return out


def correlation(a, b):
    if len(a) < 8:
        return None
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a)
    return cov / (sa * sb)


def main():
    print("recon.py — reconnaissance Polymarket, LECTURE SEULE")
    print("lance le %s UTC" % datetime.now(timezone.utc).isoformat())
    recenser_tags()

    entete = ("\n%-22s%7s%8s%8s%10s%8s%7s%9s%11s"
              % ("tag", "events", "tag ok%", "marches", "ss carnet",
                 "spread", "<=2c%", "liqNum", "echeance j"))
    print("\n=== 1. TEMOIN DE CALIBRATION (tags deja collectes) ===")
    print("   si ces lignes ne ressemblent pas au profil connu, l'instrument est casse")
    print(entete)

    tous = []
    for t in DEJA_COLLECTE + CANDIDATS:
        if t == CANDIDATS[0]:
            print("\n=== 2. CANDIDATS JAMAIS INSTRUITS (+ sports, 2e temoin) ===")
            print(entete)
        r = mesurer_tag(t)
        tous.append(r)
        if r.get("events", 0) == 0:
            print("%-22s%7d   %s" % (t, 0, r.get("note", "")), flush=True)
            continue
        print("%-22s%7d%8d%8d%10d%8.3f%7d%9.0f%11.1f"
              % (t, r["events"], r["pct_porte"], r["marches"], r["sans_carnet"],
                 r["spread_med"] if r["spread_med"] is not None else float("nan"),
                 r["pct_2c"] if r["pct_2c"] is not None else -1,
                 r["liq_med"] or 0.0, r["horizon_med"] or 0.0), flush=True)

    print("\n=== 3. PROFONDEUR REELLE vs liquidityNum ===")
    print("   liquidityNum est la SEULE colonne de profondeur collectee aujourd'hui.")
    print("   Question : suffit-elle a rejouer le filtre 'profondeur >= 100 $' ?")
    ech = []
    for r in tous:
        for e in (r.get("ech") or [])[:4]:
            ech.append(dict(e, tag=r["tag"]))
    res = profondeur(ech, n=30)
    if not res:
        print("   AUCUN carnet lu -> question NON tranchee. Ne rien conclure.")
        return

    print("\n%-16s%9s%10s%10s%8s%8s  question"
          % ("tag", "liqNum", "prof_top$", "prof_tot$", "spread", "niv"))
    for r in res:
        print("%-16s%9.0f%10.1f%10.0f%8.3f%8s  %s"
              % (r["tag"], r["liq"], r["prof_top"], r["prof_tot"],
                 r.get("spread_reel", float("nan")), r["niv"], r["q"]))

    a = [r["liq"] for r in res]
    b = [r["prof_top"] for r in res]
    c = [r["prof_tot"] for r in res]
    print("\n  n carnets lus             : %d [M]" % len(res))
    print("  liquidityNum median       : %.0f $" % med(a))
    print("  profondeur au TOP mediane : %.1f $" % med(b))
    print("  profondeur TOTALE mediane : %.0f $" % med(c))
    r_top, r_tot = correlation(a, b), correlation(a, c)
    if r_tot is None:
        print("  echantillon trop petit pour une correlation -> question NON tranchee.")
        return
    print("  correlation liquidityNum ~ profondeur TOP    : r = %+.2f" % r_top)
    print("  correlation liquidityNum ~ profondeur TOTALE : r = %+.2f" % r_tot)
    print("\n  Lecture : r eleve sur la profondeur TOTALE => liquidityNum suffit comme proxy.")
    print("            r faible => il FAUT ajouter les colonnes de taille a la collecte,")
    print("            sinon le filtre anti-carnet-vide sera INAPPLICABLE en janvier.")
    print("\n(reconnaissance terminee — aucun fichier ecrit, aucun commit)")


if __name__ == "__main__":
    main()

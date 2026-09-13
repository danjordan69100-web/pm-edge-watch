#!/usr/bin/env python3
"""
recon_trades.py — LECTURE SEULE. Une seule question.

Le 31/08 on a conclu que les 38 Go de trades (`trades_fast.db` 26 Go + `history_trades.db`
12 Go, supprimes le 01/07) etaient irrecuperables, parce que `/trades` renvoie HTTP 400
des l'offset 50 000.

MAIS le collecteur d'origine (`hermes_scripts/fast_trades.py`, retrouve le 14/09 dans le
backup) ne paginait PAS globalement : il interrogeait
    data-api.polymarket.com/trades?market=<condition_id>&limit=1000&offset=N
soit **marche par marche**, chacun ayant peu de trades. Son en-tete dit meme :
"/trades garde TOUT l'historique (teste jusqu'a 1 an : 5/5 a toutes les epoques)".

Les deux constats ne se contredisent pas forcement : ce qui plafonne peut etre la
pagination GLOBALE, pas la pagination PAR MARCHE. Si c'est le cas, les 38 Go sont
re-telechargeables tels quels, sans passer par un noeud archive on-chain.

Ce script tranche. Il ne conclut pas sur un seul marche : il teste des marches
d'AGES DIFFERENTS, parce que le risque est une purge par anciennete.

Aucune ecriture, aucun commit.
"""
import json, time, urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com/trades"


def get(url, timeout=45, tries=3):
    last = None
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")), None
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    return None, str(last)[:90]


def marches_resolus(n=90):
    """Marches FERMES. CORRECTIF 14/09 : trier par endDate DESCENDANT ne ramenait que des
    marches de moins de 3 mois -- l'age est precisement ce qu'on veut tester. On tire donc
    les DEUX extremites (ascendant = les plus vieux marches de Polymarket) et on y ajoute
    des condition_id tires de nos propres bases conservees, c'est-a-dire exactement les
    marches qui etaient dans les 38 Go supprimes."""
    out = []
    for asc in ("true", "false"):
        for offset in (0, 100, 200, 400, 800):
            b, err = get("%s/events?closed=true&limit=100&offset=%d&order=endDate&ascending=%s"
                         % (GAMMA, offset, asc))
            if not b:
                print("  ! gamma %s/%d : %s" % (asc, offset, err))
                continue
            for ev in b:
                for m in (ev.get("markets") or []):
                    cid = m.get("conditionId")
                    ed = m.get("endDate") or ""
                    if cid and ed:
                        out.append((cid, ed, (m.get("question") or "")[:40]))
    # Nos propres marches perdus
    try:
        import os
        if os.path.exists("cids_anciens.json"):
            for r in json.load(open("cids_anciens.json")):
                out.append((r["cid"], r["resolved_at"], "[base locale] " + (r.get("cat") or "")))
            print("  + %d condition_id tires de nos bases conservees" % len(
                json.load(open("cids_anciens.json"))))
    except Exception as e:
        print("  ! cids_anciens.json : %s" % str(e)[:60])
    return out


def _inutilise(n=0):
    out = []
    for offset in (0,):
        b, err = get("%s/events?closed=true&limit=100&offset=%d&order=endDate&ascending=false"
                     % (GAMMA, offset))
        if not b:
            print("  ! gamma offset %d : %s" % (offset, err))
            continue
        for ev in b:
            for m in (ev.get("markets") or []):
                cid = m.get("conditionId")
                ed = m.get("endDate") or ""
                if cid and ed:
                    out.append((cid, ed, (m.get("question") or "")[:40]))
        if len(out) >= n * 6:
            break
    return out


def main():
    print("recon_trades.py — %s UTC" % datetime.now(timezone.utc).isoformat())
    now = datetime.now(timezone.utc)

    print("\n=== 0. RAPPEL : la pagination GLOBALE plafonne-t-elle toujours ? ===")
    for off in (0, 10000, 49000, 50000, 60000):
        b, err = get("%s?limit=100&offset=%d" % (DATA, off), tries=1)
        print("  offset %6d : %s" % (off, ("%d trades" % len(b)) if isinstance(b, list)
                                          else ("ECHEC " + str(err)[:55])))
        time.sleep(0.4)

    print("\n=== 1. Marches resolus, par tranche d'age ===")
    tous = marches_resolus()
    print("  marches fermes recuperes : %d" % len(tous))
    if not tous:
        print("  -> impossible de tester, ne rien conclure")
        return

    # Repartir par age : c'est l'age qui fait peur, pas le nombre.
    tranches = {"< 1 mois": [], "1-3 mois": [], "3-6 mois": [], "6-12 mois": [], "> 12 mois": []}
    for cid, ed, q in tous:
        try:
            d = datetime.fromisoformat(ed.replace("Z", "+00:00"))
        except ValueError:
            continue
        j = (now - d).days
        if j < 0:
            continue
        k = ("< 1 mois" if j < 30 else "1-3 mois" if j < 90 else "3-6 mois" if j < 180
             else "6-12 mois" if j < 365 else "> 12 mois")
        if len(tranches[k]) < 5:
            tranches[k].append((cid, j, q))
    for k, v in tranches.items():
        print("  %-10s : %d marches disponibles pour le test" % (k, len(v)))

    print("\n=== 2. /trades?market=<cid> — LE test ===")
    print("  %-11s %5s  %7s  %9s  %s" % ("tranche", "age j", "trades", "verdict", "question"))
    bilan = {}
    for k, v in tranches.items():
        for cid, j, q in v:
            b, err = get("%s?market=%s&limit=1000&offset=0" % (DATA, cid), tries=2)
            if b is None:
                verdict, n = "ECHEC", "-"
            elif not isinstance(b, list):
                verdict, n = "FORMAT?", "-"
            elif len(b) == 0:
                verdict, n = "VIDE", 0
            else:
                verdict, n = "OK", len(b)
            bilan.setdefault(k, []).append(verdict)
            print("  %-11s %5d  %7s  %9s  %s" % (k, j, n, verdict, q))
            time.sleep(0.35)

    print("\n=== 3. Pagination PAR MARCHE (offset sur un marche fourni) ===")
    gros = None
    for k in ("3-6 mois", "1-3 mois", "< 1 mois"):
        for cid, j, q in tranches.get(k, []):
            b, _ = get("%s?market=%s&limit=1000&offset=0" % (DATA, cid), tries=1)
            if isinstance(b, list) and len(b) >= 1000:
                gros = (cid, q)
                break
        if gros:
            break
    if not gros:
        print("  aucun marche a >= 1000 trades dans l'echantillon -> pagination non testee")
    else:
        cid, q = gros
        print("  marche : %s" % q)
        for off in (0, 1000, 2000, 5000):
            b, err = get("%s?market=%s&limit=1000&offset=%d" % (DATA, cid, off), tries=1)
            print("    offset %5d : %s" % (off, ("%d trades" % len(b)) if isinstance(b, list)
                                                else ("ECHEC " + str(err)[:50])))
            time.sleep(0.4)

    print("\n=== VERDICT ===")
    for k, v in bilan.items():
        ok = sum(1 for x in v if x == "OK")
        print("  %-11s : %d/%d marches rendent des trades" % (k, ok, len(v)))
    total_ok = sum(1 for v in bilan.values() for x in v if x == "OK")
    total = sum(len(v) for v in bilan.values())
    print("\n  %d/%d au total." % (total_ok, total))
    print("  Si les tranches ANCIENNES rendent des trades, les 38 Go sont re-telechargeables")
    print("  marche par marche, sans noeud archive. Si seules les recentes repondent, la")
    print("  purge est reelle et seule la voie on-chain reste.")
    print("\n(termine — aucun fichier ecrit, aucun commit)")


if __name__ == "__main__":
    main()

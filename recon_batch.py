#!/usr/bin/env python3
"""
recon_batch.py — LECTURE SEULE. Dernier test avant d'ecrire le patch profondeur.

Acquis du 13/09 :
  * Gamma ne porte AUCUNE taille de carnet (orderMinSize / rewardsMinSize sont des
    parametres de configuration -- mon premier test les avait pris pour des tailles).
  * GET /book un par un = 0,263 s/marche -> 43 min pour 9 900 marches. Intenable :
    le workflow collect expire a 25 min.
  * POST /books repond en 0,21 s pour 20 tokens.

Ce script mesure ce qui decide de l'architecture du patch :
  1. jusqu'a combien de tokens POST /books accepte-t-il ?
  2. la reponse est-elle COMPLETE (autant de carnets que de tokens demandes) ?
  3. la reponse est-elle APPARIABLE (sait-on quel carnet va avec quel token) ?
     -> c'est la question qui compte : une jointure non testee est l'erreur la plus
        chere du dossier (protocole du 20/08).
  4. cout extrapole pour 9 900 marches.

Aucune ecriture, aucun commit.
"""
import json, time, urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TAGS = ["politics", "elections", "weather", "crypto-prices"]


def req(url, data=None, timeout=60):
    h = {"User-Agent": UA, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collecter_tokens(cible=700):
    """Tokens reels, pris sur les tags qu'on collecte deja."""
    toks = []
    for tag in TAGS:
        for offset in (0, 100, 200):
            if len(toks) >= cible:
                break
            try:
                evs = req("%s/events?closed=false&limit=100&offset=%d&tag_slug=%s"
                          "&order=volume&ascending=false" % (GAMMA, offset, tag))
            except Exception:
                break
            if not evs:
                break
            for ev in evs:
                for m in (ev.get("markets") or []):
                    if not m.get("enableOrderBook") or not m.get("clobTokenIds"):
                        continue
                    try:
                        t = m["clobTokenIds"]
                        t = json.loads(t) if isinstance(t, str) else t
                    except Exception:
                        continue
                    if t:
                        toks.append(t[0])
    # dedoublonne en gardant l'ordre
    vu, out = set(), []
    for t in toks:
        if t not in vu:
            vu.add(t)
            out.append(t)
    return out


def structure(rep):
    """Normalise la reponse de /books et dit si elle est appariable."""
    if isinstance(rep, dict):
        for k in ("books", "data", "results"):
            if isinstance(rep.get(k), list):
                return rep[k], "dict->%s" % k
        return [rep], "dict nu"
    if isinstance(rep, list):
        return rep, "liste"
    return [], type(rep).__name__


def main():
    print("recon_batch.py — %s UTC" % datetime.now(timezone.utc).isoformat())
    toks = collecter_tokens()
    print("\ntokens reels recoltes : %d [M]" % len(toks))
    if len(toks) < 40:
        print("pas assez de tokens -> test impossible, ne rien conclure")
        return

    print("\n=== 1-2-3. TAILLE DE LOT, COMPLETUDE, APPARIABILITE ===")
    print("%8s %8s %10s %10s %14s  %s"
          % ("demandes", "recus", "latence", "s/marche", "structure", "appariable"))
    meilleur = None
    for n in (20, 50, 100, 200, 300, 500):
        if n > len(toks):
            break
        lot = toks[:n]
        t0 = time.time()
        try:
            rep = req("%s/books" % CLOB, data=[{"token_id": t} for t in lot])
        except Exception as e:
            print("%8d %8s %10s %10s %14s  %s" % (n, "-", "-", "-", "ECHEC", str(e)[:40]))
            break
        dt = time.time() - t0
        items, forme = structure(rep)
        # APPARIABILITE : chaque carnet porte-t-il son token_id, et est-ce bien
        # un des tokens demandes ? (test de jointure -- protocole du 20/08)
        cles = set()
        for it in items:
            if isinstance(it, dict):
                cles |= set(it.keys())
        champ = None
        for c in ("asset_id", "token_id", "assetId", "tokenId"):
            if c in cles:
                champ = c
                break
        if champ:
            renvoyes = set(str(it.get(champ)) for it in items if isinstance(it, dict))
            inter = len(renvoyes & set(lot))
            app = "OUI via %s (%d/%d apparies)" % (champ, inter, n)
            ok_join = inter == len(items) and inter > 0
        else:
            app = "NON : aucun champ d'identite -> ordre seul, NON VERIFIABLE"
            ok_join = False
        print("%8d %8d %9.2fs %9.4fs %14s  %s"
              % (n, len(items), dt, dt / max(len(items), 1), forme, app))
        if len(items) == n and ok_join:
            meilleur = (n, dt)
        time.sleep(0.6)

    print("\n=== 4. COUT EXTRAPOLE ===")
    if not meilleur:
        print("  aucun lot complet ET appariable -> le patch NE PEUT PAS s'appuyer")
        print("  sur /books tel quel. Ne pas coder a l'aveugle : restreindre le")
        print("  perimetre (meteo + temoin ~400 marches, 1,8 min/snapshot un par un).")
        return
    n, dt = meilleur
    par_lot = dt
    print("  plus grand lot complet et appariable : %d tokens en %.2f s [M]" % (n, par_lot))
    for nm in (400, 2700, 9900, 12000):
        lots = (nm + n - 1) // n
        print("     %5d marches -> %4d appels -> %5.1f min par snapshot"
              % (nm, lots, lots * par_lot / 60.0))
    print("\n  Rappel : le workflow collect expire a 25 min et un snapshot doit tenir")
    print("  largement dedans (GitHub saute deja 2 creneaux/jour sur 7).")

    print("\n=== 5. CONTENU D'UN CARNET RENVOYE (verification a l'oeil) ===")
    try:
        rep = req("%s/books" % CLOB, data=[{"token_id": t} for t in toks[:3]])
        items, _ = structure(rep)
        for it in items[:2]:
            if not isinstance(it, dict):
                continue
            bids, asks = it.get("bids") or [], it.get("asks") or []
            print("  asset=%s... bids=%d asks=%d"
                  % (str(it.get("asset_id") or it.get("token_id"))[:18], len(bids), len(asks)))
            if bids:
                tb = max(float(x["price"]) for x in bids)
                szb = sum(float(x["size"]) for x in bids if float(x["price"]) == tb)
                print("     meilleur bid %.3f  taille %.1f  -> %.2f $ executables" % (tb, szb, tb * szb))
            if asks:
                ta = min(float(x["price"]) for x in asks)
                sza = sum(float(x["size"]) for x in asks if float(x["price"]) == ta)
                print("     meilleur ask %.3f  taille %.1f  -> %.2f $ executables" % (ta, sza, ta * sza))
    except Exception as e:
        print("  echec : %s" % str(e)[:70])

    print("\n(termine — aucun fichier ecrit, aucun commit)")


if __name__ == "__main__":
    main()

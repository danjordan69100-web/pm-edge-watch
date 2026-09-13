#!/usr/bin/env python3
"""
recon_capacites.py — LECTURE SEULE. Prepare le patch "profondeur au meilleur prix".

Le 13/09/2026 on a mesure que `liquidityNum` = profondeur TOTALE du carnet (r=+1,00)
et n'apprend RIEN sur l'executable au meilleur prix (r=+0,15 ; 17 546 $ de carnet
pour 12,4 $ au top, median). Il faut donc collecter la taille au meilleur prix.

Avant d'ecrire ce patch, trois choses doivent etre MESUREES, pas supposees :

  A. Gamma renvoie-t-il deja une taille ? (si oui, le patch est gratuit)
  B. Le CLOB a-t-il un appel GROUPE ? Un appel par marche x 9 900 marches x 6/jour
     ne tient pas dans un runner. Sans appel groupe, il faudra restreindre le
     perimetre -- et savoir a quoi.
  C. Combien de temps coute reellement la collecte de profondeur, mesure ici.

Aucune ecriture, aucun commit.
"""
import json, time, urllib.parse, urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def req(url, data=None, timeout=45, method=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def essai(label, fn):
    t0 = time.time()
    try:
        out = fn()
        print("  %-46s OK   %5.2f s" % (label, time.time() - t0))
        return out
    except Exception as e:
        print("  %-46s ECHEC (%s)" % (label, str(e)[:60]))
        return None


def main():
    print("recon_capacites.py — %s UTC" % datetime.now(timezone.utc).isoformat())

    # ---------------------------------------------------------------
    print("\n=== A. GAMMA renvoie-t-il deja une taille ? ===")
    evs = req("%s/events?closed=false&limit=6&tag_slug=weather&order=volume&ascending=false" % GAMMA)
    mkt = None
    for ev in (evs or []):
        for m in (ev.get("markets") or []):
            if m.get("enableOrderBook") and m.get("clobTokenIds"):
                mkt = m
                break
        if mkt:
            break
    if not mkt:
        print("  aucun marche exploitable trouve -> test A impossible")
        return
    cles = sorted(mkt.keys())
    print("  champs Gamma d'un marche : %d" % len(cles))
    interessants = [k for k in cles
                    if any(s in k.lower() for s in ("size", "depth", "amount", "qty", "quantity"))]
    print("  champs contenant size/depth/amount/qty : %s"
          % (interessants if interessants else "AUCUN"))
    print("  bestBid=%s bestAsk=%s liquidityNum=%s"
          % (mkt.get("bestBid"), mkt.get("bestAsk"), mkt.get("liquidityNum")))
    print("  -> conclusion A : %s"
          % ("Gamma porte une taille, patch gratuit" if interessants
             else "Gamma ne porte AUCUNE taille, il faut passer par le CLOB"))

    toks = json.loads(mkt["clobTokenIds"]) if isinstance(mkt["clobTokenIds"], str) else mkt["clobTokenIds"]
    tok = toks[0]

    # ---------------------------------------------------------------
    print("\n=== B. Le CLOB a-t-il un appel GROUPE ? ===")
    b1 = essai("GET /book?token_id=... (1 marche)",
               lambda: req("%s/book?token_id=%s" % (CLOB, tok)))
    if b1:
        print("      niveaux : %d bids / %d asks"
              % (len(b1.get("bids") or []), len(b1.get("asks") or [])))

    # Recolte d'une poignee de tokens pour tester le groupage
    tokens = []
    for ev in (evs or []):
        for m in (ev.get("markets") or []):
            if not m.get("clobTokenIds"):
                continue
            try:
                t = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
            except Exception:
                continue
            if t:
                tokens.append(t[0])
    tokens = tokens[:20]
    print("  tokens de test disponibles : %d" % len(tokens))

    essai("POST /books  [{token_id}]",
          lambda: req("%s/books" % CLOB, data=[{"token_id": t} for t in tokens]))
    essai("GET  /books?token_ids=a,b,c",
          lambda: req("%s/books?token_ids=%s" % (CLOB, urllib.parse.quote(",".join(tokens[:5])))))
    essai("GET  /prices?token_id=...",
          lambda: req("%s/prices?token_id=%s" % (CLOB, tok)))
    essai("POST /prices [{token_id,side}]",
          lambda: req("%s/prices" % CLOB,
                      data=[{"token_id": t, "side": s} for t in tokens[:10] for s in ("BUY", "SELL")]))
    essai("GET  /midpoints?token_ids=...",
          lambda: req("%s/midpoints?token_ids=%s" % (CLOB, urllib.parse.quote(",".join(tokens[:5])))))
    essai("GET  /simplified-markets",
          lambda: req("%s/simplified-markets" % CLOB))

    # ---------------------------------------------------------------
    print("\n=== C. COUT REEL d'une collecte de profondeur ===")
    n = min(25, len(tokens))
    t0 = time.time()
    ok = 0
    for t in tokens[:n]:
        try:
            req("%s/book?token_id=%s" % (CLOB, t), timeout=20)
            ok += 1
        except Exception:
            pass
    dt = time.time() - t0
    if ok:
        par = dt / ok
        print("  %d carnets lus un par un en %.1f s -> %.3f s / marche [M]" % (ok, dt, par))
        for nm in (400, 2700, 9900):
            print("     %5d marches -> %6.1f min par snapshot" % (nm, nm * par / 60.0))
        print("  (budget runner : le workflow collect a un timeout de 25 min,")
        print("   et un snapshot doit tenir largement dedans pour ne pas sauter de creneau)")
    else:
        print("  aucun carnet lu -> cout NON mesure, ne rien conclure")

    print("\n(termine — aucun fichier ecrit, aucun commit)")


if __name__ == "__main__":
    main()

# Maintien de la collecte — à lire avant octobre 2026

## Le risque
GitHub **désactive automatiquement les workflows planifiés (`schedule`) après
60 jours sans activité de commit** sur le dépôt. Les commits poussés par
`actions-user` (le bot du workflow) ne réinitialisent pas de façon fiable ce
compteur — c'est précisément pour cela que des actions « keepalive » existent.

Si le workflow s'arrête, **la collecte cesse silencieusement** : aucune alerte,
aucun mail, les fichiers cessent simplement d'arriver.

## Pourquoi c'est critique ici
La collecte doit courir jusqu'à **~novembre 2026** pour atteindre la puissance
statistique nécessaire. Mesure du 10/08/2026 sur les données de juin :

| Zone de prix | Edge minimum détectable |
|---|---|
| Longshots 5-35 ¢ | **20,7 %** |
| Milieu 35-65 ¢ | 11,4 % |
| Favoris 65-95 ¢ | 3,9 % |

Détecter un edge de 2 % demanderait ~139 000 marchés indépendants. Trois semaines
de données en fournissent 1 296. **Une interruption de deux mois au milieu de la
fenêtre rendrait l'ensemble inexploitable.**

## La procédure — un commit manuel tous les 50 jours

```bash
cd C:/Users/danjo/Desktop/pm_edge_watch
git pull --rebase
# éditer la ligne ci-dessous, puis :
git commit -am "keepalive" && git push
```

**Dernier commit manuel : 2026-08-31** → prochain avant le **2026-10-20**.

## Vérifier que la collecte tourne toujours

```bash
gh run list --repo danjordan69100-web/pm-edge-watch --limit 5
gh api repos/danjordan69100-web/pm-edge-watch/actions/workflows --jq '.workflows[] | "\(.name): \(.state)"'
```

L'état doit être `active`. S'il affiche `disabled_inactivity` :

```bash
gh api -X PUT repos/danjordan69100-web/pm-edge-watch/actions/workflows/<ID>/enable
```

## Ne pas analyser avant DECEMBRE 2026 (corrige le 31/08)
Analyser trop tôt reproduit l'erreur de puissance déjà commise deux fois sur ce
dossier : le 2 août (54 sagas, seuil ~7-10 %) et le 10 août (seuil 20,7 % sur
les longshots). Voir le README pour le protocole d'analyse et le gate.


---

## CORRECTIF DU 31/08/2026 — deux erreurs de calendrier et une piste morte

### 1. La date d'analyse etait fausse
Recensement des echeances du referentiel (10 468 marches) :
`sept 1 563 · oct 1 873 · **nov 2 342** · dec 1 183`
**Pic unique : 1 930 marches le 03/11/2026** (midterms US).
Latence mesuree echeance -> resolution captee (n=2 381) : mediane 0,8 j, **p90 11,4 j**,
14 % arrivent plus de 7 jours apres.
=> Analyser « en novembre » revient a analyser **avant** la moisson principale.
=> **Fenetre d'analyse : mi-decembre 2026 au plus tot, janvier 2027 de preference.**

### 2. Keepalives restants
- 31/08/2026 fait (`8904e47`)  -> prochain **avant le 20/10/2026**
- puis un **3e vers le 10/12/2026**, sinon le cron meurt entre le pic et l'analyse.

### 3. La piste 2 etait INTESTABLE — corrigee
On collectait la dispersion d'ensemble GFS de 10 villes US depuis le 02/08 **sans aucun
marche meteo en face** : le tag `weather` manquait dans `TAGS`. 29 jours de previsions
sans contrepartie de prix.
Corrige : ajout de `weather`, `temperature`, `climate` + seuil de liquidite dedie
(`MIN_LIQUIDITY_WEATHER = 20 $`, car ces marches pesent 1-600 $ et le seuil de 500 $
en tuait ~90 %).
Verifie apres patch : **2 676 marches meteo captes**, **10/10 villes appariees**
prix <-> prevision, spread median 0,020 et **53 % sous 0,02** (zone tradable).
Interet decisif : ils resolvent **en 1 jour** => une observation quasi independante par
ville et par jour, exactement ce qui manque au dossier (2 381 resolus = ~44 semaines seulement).

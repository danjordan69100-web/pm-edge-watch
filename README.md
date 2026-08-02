# pm-edge-watch

Collecte longue durée sur Polymarket, pour trancher les **deux dernières pistes** restées ouvertes après la clôture du bot (01/07/2026) et le crible du 02/08/2026.

Tourne seul sur GitHub Actions. Zéro infrastructure, zéro coût, PC éteint.

## Pourquoi

Seize pistes ont été testées et réfutées (taker directionnel, tennis H2H, foot, météo, calibration prix×temps, déséquilibre de carnet, arbitrage multi-issues, market making, copy-trading…). Il reste exactement deux angles non tranchés :

**1. Puissance statistique sur le segment politique.**
Le crible du 02/08 n'a trouvé aucun edge — mais il ne disposait que de 3 semaines de données, soit **54 événements indépendants**. À cette taille, seul un edge supérieur à ~7-10 % serait détectable. Les edges réels observés dans ce projet sont de l'ordre de quelques pourcents : **ils étaient invisibles par construction**. Il faut des mois pour abaisser le seuil de détection.

**2. Information externe — le seul axe jamais mené à terme.**
Toutes les pistes précédentes cherchaient un signal *interne* aux prix et carnets de Polymarket. Conclusion méta du projet : le site est efficient sur lui-même, tout signal interne est absent ou mangé par la friction. Le seul angle restant est de le battre **sur le fond**, avec une information qu'il n'a pas encore intégrée.

Ici : la **dispersion d'ensemble GFS** (écart-type entre les 31 membres du modèle), enregistrée en parallèle des prix. L'hypothèse testable est que les market makers intègrent lentement les *chocs de variance* — pas la prévision moyenne, qui est déjà dans le prix.

## Ce qui est collecté

| Quoi | Fréquence | Où |
|---|---|---|
| Marchés politiques / géopolitiques / élections (prix, spread, volume, liquidité) | toutes les 4 h | `snaps/AAAA-MM-JJ/*.csv.gz` |
| Dispersion d'ensemble GFS, 10 villes US, échéances J+0 à J+6 | toutes les 4 h | `forecasts/AAAA-MM-JJ/*.csv.gz` |
| Libellés des marchés (référentiel, `idx` ↔ `condition_id`) | continu | `refs/markets_ref.csv` |
| Résolutions des marchés observés | 1×/jour | `refs/resolutions.csv` |

Filtres : carnet actif, liquidité ≥ 500 $, échéance ≤ 150 jours (au-delà, le marché ne résoudra pas dans la fenêtre d'étude).

**L'horodatage d'un snapshot est dans son nom de fichier** (`AAAAMMJJTHHMM`, UTC) — toutes les lignes d'un fichier le partagent. Les snapshots ne stockent qu'un `idx` entier : un `condition_id` fait 66 caractères hex et le répéter représentait ~70 % du poids (455 Ko → 87 Ko par snapshot).

Volume attendu : ~0,5 Mo/jour, soit ~45 Mo sur 3 mois.

## Analyse prévue (pas avant plusieurs mois de collecte)

1. **Calibration favori-longshot**, point-in-time (premier snapshot par marché), entrée à l'**ask** — jamais au mid, qui a déjà produit un faux edge dans ce projet.
2. **Cluster-bootstrap par saga**, pas par snapshot ni par marché : « ceasefire by June 12 / 15 / 30 » sont le même événement sous-jacent. Ignorer cette corrélation gonfle artificiellement la significativité.
3. **Choc de variance** : corréler Δ(écart-type d'ensemble) avec le mouvement de prix des marchés météo dans les heures qui suivent.
4. Gate : intervalle de confiance à 95 % **entièrement** positif, net de friction. Sinon la piste est close.

## Règle du projet

> Prouver le signal **avant** de construire quoi que ce soit. Aucun capital, aucune infrastructure tant que l'edge n'a pas survécu en forward.

C'est la leçon la plus chère de tout le dossier.

## Manuel

```bash
python collect_once.py            # snapshot marchés + ensemble
python collect_once.py --resolve  # balayage des résolutions
```

Aucune dépendance hors bibliothèque standard.

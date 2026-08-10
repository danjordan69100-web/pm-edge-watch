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

**Dernier commit manuel : 2026-08-10** → prochain avant le **2026-09-29**.

## Vérifier que la collecte tourne toujours

```bash
gh run list --repo danjordan69100-web/pm-edge-watch --limit 5
gh api repos/danjordan69100-web/pm-edge-watch/actions/workflows --jq '.workflows[] | "\(.name): \(.state)"'
```

L'état doit être `active`. S'il affiche `disabled_inactivity` :

```bash
gh api -X PUT repos/danjordan69100-web/pm-edge-watch/actions/workflows/<ID>/enable
```

## Ne pas analyser avant novembre 2026
Analyser trop tôt reproduit l'erreur de puissance déjà commise deux fois sur ce
dossier : le 2 août (54 sagas, seuil ~7-10 %) et le 10 août (seuil 20,7 % sur
les longshots). Voir le README pour le protocole d'analyse et le gate.

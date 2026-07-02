# ShadBeez ⋮ Édition Produits

Application Streamlit interne pour gérer l'édition de produits via l'intégrateur
de flux [BeezUP](https://www.beezup.com) :

1. **Génération de template** — produit un fichier Excel listant les produits d'une
   catégorie d'un canal de vente avec leurs attributs (Required / Recommended /
   Optional), valeurs existantes préremplies et menus déroulants pour les listes
   bornées.
2. **Réintégration de template** — relit le template complété, calcule le diff avec
   l'état actuel du catalogue et applique les modifications dans BeezUP via des
   *overrides* produit.

## Démarrage rapide

Prérequis : [uv](https://docs.astral.sh/uv/) (Python 3.13 géré automatiquement).

```
uv sync                        # installe l'environnement
uv run streamlit run app.py    # lance l'app (ou run.bat sous Windows)
uv run pytest                  # lance les tests
```

La connexion se fait avec des identifiants BeezUP (email / mot de passe).

## Architecture

```
app.py                  # Point d'entrée, sidebar, onglets
beezup_client.py        # Client HTTP (auth, retry 429/503 via tenacity)
api_services.py         # Appels métier BeezUP
data_processing.py      # Traitement pandas : template, diff, normalisation
excel_utils.py          # Génération du fichier Excel (xlsxwriter)
session_manager.py      # session_state : defaults et resets
views/                  # Une vue par section de l'interface
tests/                  # pytest — logique pure + régressions sur données réelles
```

**⚠️ Lire `CONTEXT.md` avant toute modification** : il documente les pièges de
l'API BeezUP (URL d'export non documentée, JSON déguisé en CSV, chemins de
catégories tronqués, PUT overrides destructif...) qui ne sont pas évidents à la
lecture du code.

## Dépendances

Gérées par uv (`pyproject.toml` + `uv.lock`). Le `requirements.txt` est **généré**
pour le déploiement Streamlit Community Cloud — ne pas l'éditer à la main :

```
uv export --format requirements.txt --no-hashes --no-annotate --no-dev -o requirements.txt
```

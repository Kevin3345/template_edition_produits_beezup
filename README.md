# ShadBeez ⋮ Édition Produits

Application Streamlit interne pour gérer l'édition de produits via l'intégrateur
de flux [BeezUP](https://www.beezup.com) :

1. **Génération par catégorie** — un fichier Excel listant les produits d'une
   catégorie avec leurs attributs (Required / Recommended / Optional), valeurs
   préremplies et menus déroulants pour les listes bornées.
2. **Génération par SKUs** — une liste de SKUs toutes catégories confondues →
   un template par catégorie, livrés dans un ZIP (canaux configurés dans
   `marketplace_config.json`).
3. **Génération par attributs** — des attributs choisis un par un, sans notion
   de catégorie → un template unique (cas chirurgical multi-catégories).
4. **Réintégration de template** — relit un template complété (quel que soit le
   workflow d'origine), calcule le diff avec l'export BeezUP et applique les
   modifications via des *overrides* produit.

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

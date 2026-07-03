# CONTEXT.md — ShadBeez Édition Produits v4

Contexte technique et décisions d'architecture pour ce projet. À lire avant toute
modification, notamment pour comprendre les contournements liés à l'API BeezUP qui
ne sont pas évidents à la lecture du code seul.

---

## Objectif de l'application

Application Streamlit interne (usage : l'équipe et moi-même) qui gère quatre workflows
autour de l'intégrateur de flux **BeezUP** :

1. **Génération par catégorie** : produit un fichier Excel listant des produits et leurs
   attributs (avec valeurs manquantes à compléter) pour une catégorie d'un canal de vente.
   La sélection d'attributs se fait par **statut uniquement** (Required/Recommended/
   Optional) — la notion de Source (Channel/Cross/Category) reste interne. Les
   `excluded_attributes` du canal sont filtrés en amont, invisibles même en sélection
   manuelle (interdit = interdit).
2. **Génération par SKUs** : l'utilisateur colle une liste de SKUs (toutes catégories
   confondues) ; l'app regroupe les produits par catégorie via la **colonne catégorie de
   l'export BeezUP** (jointure par `channelCategoryCode`, pas par chemin — immunisé
   contre les chemins tronqués du piège n° 6) et génère un template par catégorie,
   livrés dans un ZIP. Disponible uniquement pour les canaux dont `category_column`
   est renseignée dans `marketplace_config.json` (ex. Maxeda : `online_hybris_category`).
3. **Génération par attributs** : l'utilisateur choisit des attributs un par un dans le
   référentiel complet du canal (sans notion de catégorie) et colle des SKUs → un
   template unique, au format standard. Cas d'usage chirurgical : corriger 2 attributs
   sur des SKUs éparpillés sur N catégories.
4. **Réintégration de template** : relit le template complété (issu de n'importe quel
   workflow de génération — le format est identique) et applique les modifications
   dans BeezUP via des *overrides* produit.

**Principe partagé** : les valeurs préremplies viennent toujours de l'export BeezUP,
jamais d'appels API produit par produit. L'export contient les valeurs **finales**
envoyées à la marketplace (overrides déjà appliqués, mélangés aux valeurs d'origine) :
c'est la source de vérité, à la fois pour le préremplissage et pour le diff de
réintégration.

Toute l'application repose sur l'API BeezUP (`https://api.beezup.com`).

---

## Architecture des fichiers

```
app.py                  # Point d'entrée, sidebar, onglets, orchestration des vues
session_manager.py      # get_defaults() + fonctions de reset du session_state
beezup_client.py        # Client HTTP bas niveau (auth, _request, méthodes get/post/put/delete)
api_services.py         # Appels métier BeezUP (au-dessus du client)
data_processing.py      # Traitement pandas : template, diff, normalisation
excel_utils.py          # Génération du fichier Excel (xlsxwriter)
logger_utils.py         # Configuration loguru (console + fichier log.txt)
marketplace_config.py   # Lecture de marketplace_config.json (config par canal)
marketplace_config.json # Par canal (clé = suffixe store_name) : required_attributes,
                        # excluded_attributes, category_column (None = génération par
                        # SKUs indisponible pour ce canal)
views/
  __init__.py           # Vide — nécessaire pour que 'views' soit un package
  login_view.py
  settings_view.py      # Rendu dans la SIDEBAR (la boutique est un état de session)
  category_view.py
  attributes_view.py
  export_view.py        # Génération par catégorie
  export_by_skus_view.py # Génération par SKUs (multi-templates + ZIP)
  export_by_attributes_view.py # Génération par attributs (template unique)
  import_view.py        # Réintégration de template
```

**Convention** : les fonctions publiques sont définies en premier dans chaque fichier,
les helpers privés (préfixe `_`) en dessous.

---

## Pièges BeezUP spécifiques (IMPORTANT)

### 1. URL d'export non documentée

L'export complet du catalogue est téléchargé via une URL **non documentée publiquement** :

```
https://export2.beezup.com/v2/user/channelCatalogs/export/X/X/{catalog_id}
```

Le `catalog_id` fait office d'authentification (pas de token nécessaire sur cette URL).
Les `X/X` sont des placeholders qui font partie de l'URL fonctionnelle.

**Trois alternatives documentées ont été testées et écartées (mai 2026) :**
- `GET /exportations/cache` → `feedUrl` : retourne un JSON **delta** (modifications
  depuis le dernier export uniquement), donc incomplet. Inutilisable pour le diff.
- `POST /products/export` : retourne **404** sur nos catalogues (endpoint non activé
  sur le compte, malgré sa présence dans le swagger).

Conclusion : **conserver l'URL hardcodée**, c'est la seule route retournant l'export complet.

### 2. L'export peut être un JSON déguisé en CSV

Selon la marketplace, l'URL d'export peut retourner soit un vrai CSV, soit un **JSON
avec une extension `.csv`**. La structure JSON est :

```json
[
  {
    "sku": "409375_...",
    "messageType": "publish",
    "properties": [
      { "key": "sku", "value": "409375_..." },
      { "key": "ean", "value": "0657419692298" },
      ...
    ]
  }
]
```

`download_export_file` détecte le format (le contenu commence par `[` ou `{` → JSON) et
appelle `_parse_json_export` qui met la structure à plat : chaque `key` des `properties`
devient une colonne. Le `sku` présent à la racine ET dans `properties` n'est pas un
problème (même valeur, la propriété écrase la racine).

**Multi-pays** : la marketplace PHH (Pigu) couvre 4 pays. Les attributs par pays ont des
clés distinctes (`sell_price`, `sell_price_lv`, `sell_price_fi`, `sell_price_ee`) et
deviennent des colonnes distinctes. `compute_diff` ne compare que les colonnes présentes
dans le `DataInfo` du template, donc les colonnes des autres pays sont ignorées
automatiquement — rien de spécial à gérer.

### 3. BOM et caractères invisibles

Les fichiers d'export commencent souvent par un **BOM UTF-8** (`\ufeff`), ce qui casse la
détection JSON (`text.startswith("[")` échoue). `download_export_file` décode en
`utf-8-sig` quand l'encodage est UTF-8 et applique un strip élargi des caractères
invisibles (`_INVISIBLE_CHARS`) pour couvrir aussi les zero-width spaces.

### 4. Le PUT /overrides remplace TOUT

`PUT /channelCatalogs/{id}/products/{productId}/overrides` remplace l'intégralité de
l'objet overrides du produit. Il faut donc **récupérer les overrides existants avant
d'envoyer** et fusionner (les valeurs du template gagnent). C'est fait dans
`_execute_overrides_with_progress` via `get_existing_overrides_by_product_id`.
Une valeur `""` dans le template écrase volontairement l'override existant (suppression).

### 5. Attributs non mappés → création d'un custom column

Si un attribut du template n'a pas de mapping dans BeezUP (`to_map` non vide), la
réintégration est **bloquée** jusqu'à création d'un champ personnalisé vide + mapping.
C'est déclenché manuellement par l'utilisateur (bouton "Créer le mapping"), jamais
automatiquement (ça modifie la structure du catalogue de façon permanente).

**Piège de temporalité** : après création du mapping, on ne relance PAS l'analyse. Le
diff se base sur l'export CSV, qui ne reflétera le nouveau mapping qu'au prochain cycle
de génération BeezUP (hors de notre contrôle). On utilise donc un flag `mapping_done`
en session pour débloquer le bouton "Appliquer" sans re-analyser.

**Piège doublon** : `create_custom_column` vérifie d'abord si un champ du même
`userColumnName` existe déjà (via `get_custom_columns_list`) et réutilise son ID.
Sinon BeezUP renvoie un 500 `"User column null already exist"`.

La route de création est `PUT /catalogs/{storeId}/customColumns/{columnId}/decrypted`,
fournie directement par BeezUP (non publique). Nécessite le header
`X-BeezUP-Decrypted-Expression: true` (déjà posé dans la session par le client).

### 6. channelFullCategoryPath peut être tronqué (route /attributes)

Sur certaines marketplaces (constaté sur **Cultura**, juillet 2026), le
`channelFullCategoryPath` retourné par `GET /channelCatalogs/{id}/attributes` est
**tronqué** : il manque un ou plusieurs niveaux terminaux (sur 265 catégories : 52
complets, 66 avec le dernier niveau manquant, 147 avec deux niveaux manquants). Le nom
du dernier niveau reste disponible dans `channelOriginCategoryName`. La route du
mapping (`GET /channelCatalogs/{id}/categories`), elle, retourne toujours le chemin
complet — c'est elle qui alimente la sélection de catégorie dans l'UI.

Conséquence : une comparaison stricte entre le chemin sélectionné et
`channelFullCategoryPath` ne matche jamais → aucun attribut `Source="Category"`.
Le match est fait par `_is_selected_category` (api_services.py) : égalité stricte, OU
(le chemin sélectionné prolonge le chemin tronqué ET son dernier segment ==
`channelOriginCategoryName`). Vérifié sans ambiguïté sur données réelles Cultura et
Brico Dépôt (bijection parfaite, zéro doublon). La route mapping n'expose pas de code
catégorie (le `channelCategoryCode` n'existe que côté /attributes). Piste de
durcissement si le match par chaîne casse un jour : `GET /channels/{channelId}/categories`
retourne la taxonomie complète du canal avec `channelCategoryChannelCode` + chemin
complet, ce qui permettrait une jointure par code (au prix d'un appel API en plus).

### 7. Rate limiting variable selon le compte

Mon compte : 500 req/min. Les comptes de mes collègues : **100 req/min**. Deux
mécanismes complémentaires :
- **Throttle proactif** : la boucle d'overrides applique un `time.sleep(0.65)` après
  chaque requête (100/min = 1 req toutes les 0,6 s). Un lot de 100 produits ≈ 65 s.
- **Retry automatique** (tenacity, dans `beezup_client._send`) : les statuts 429 et
  503 sont retentés jusqu'à 5 fois, en respectant le header `Retry-After` s'il est
  présent, sinon backoff exponentiel (2, 4, 8... plafonné à 30 s). Les 4xx ne sont
  jamais retentés. Le swagger ne documente pas le `Retry-After` sur les routes
  produits (seulement sur 3 routes annexes), d'où le fallback backoff.

---

## Conventions de traitement des données

### Colonnes EAN (détection par motif)

L'EAN doit rester une chaîne de **13 caractères avec zéros de tête**. Deux endroits :
- **Génération** : `normalize_export_data` applique `zfill(13)` sur les colonnes EAN,
  uniquement sur les valeurs non vides (sinon un EAN vide devient `"0000000000000"`).
- **Réintégration** : `run_comparison_for_file` lit le template avec un `converter`
  (`_ean_converter`) appliqué aux colonnes EAN **au moment du `pd.read_excel`**. C'est
  crucial : sans converter, pandas infère un type numérique et supprime le zéro de tête
  avant même qu'on puisse le corriger.

Les colonnes EAN sont détectées par **motif sur le label** (`_is_ean_column` :
ean/gtin/upc/barcode/code-barre, avec garde-fous contre les faux positifs type
"Jeanne" ou "Barre de son"). L'ancienne règle positionnelle (index 4) a été abandonnée
avec l'arrivée de la génération par attributs, où n'importe quel attribut peut occuper
cette position. Si un canal nomme son attribut code-barres de façon exotique (hors
motif), le symptôme est la perte des zéros de tête dans le template → ajouter le
motif dans `_EAN_COLUMN_PATTERN`.

### Structure du template Excel (3 onglets)

- **Template** : les données produits (colonnes techniques masquées + attributs colorés
  par statut Required/Recommended/Optional, dropdowns pour les listes bornées).
- **DataInfo** : mapping `Label → Attribute Code`, lu à la réintégration pour retrouver
  quel attribut BeezUP correspond à chaque colonne. **Un template = une catégorie = son
  propre DataInfo.**
- **ListOfValues** : valeurs bornées des dropdowns.

### Réintégration : traitement PAR FICHIER

`run_comparison_for_file` traite **un fichier à la fois** (un fichier = un catalogue = une
catégorie). Ne JAMAIS concaténer plusieurs fichiers avant le diff : chaque catégorie a ses
propres attributs, et concaténer perdrait l'info de mapping spécifique (le `DataInfo` de
chaque fichier). Les résultats sont stockés par nom de fichier dans
`st.session_state["import_results"][filename]`.

### normalize_value (comparaison du diff)

Normalise les cellules avant comparaison : `NaN`/`None` → `""`, float entier → sans `.0`,
`"Code | Label"` → `"Code"`. Attention : `bool` est traité comme str (pas comme int, car
en Python `isinstance(True, int)` est vrai).

---

## session_state

Toutes les clés persistantes sont déclarées dans `session_manager.get_defaults()`.
**Toute nouvelle clé doit y être ajoutée** pour être correctement initialisée ET
réinitialisée par les resets. Piège rencontré : `import_results` initialisé à `None`
dans les defaults → tester `if st.session_state.get("import_results"):` (valeur truthy)
et non `if "import_results" in st.session_state:` (toujours vrai depuis l'ajout aux defaults).

Trois niveaux de reset :
- `reset_to_new_template` : garde la boutique, réinitialise le template.
- `reset_to_new_catalog` : garde la session, réinitialise boutique + template + vide le cache.
- `st.session_state.clear()` : déconnexion complète.

---

## Logging

loguru, configuré dans `logger_utils.setup_logging()` (appelé une seule fois au démarrage,
gardé par `logger_initialized`). Console en INFO, fichier `log.txt` en DEBUG (rotation
10 MB, rétention 5 fichiers, compression zip).

`get_log_context()` retourne un logger enrichi avec `user` (prénom) et `store` (nom de
boutique) lus depuis le session_state. **Ne jamais appeler au niveau module** (le contexte
Streamlit n'existe pas encore) — toujours dans une fonction déclenchée par Streamlit.

Une tentative d'externalisation vers Better Stack a été abandonnée (intégration loguru ↔
LogtailHandler non concluante). Les logs Streamlit Cloud restent consultables via
Manage app → Logs.

---

## Environnement

- Dépôt GitHub (privé) : https://github.com/merchantshadowbdx/shadbeez-edition-produits
- App déployée (Streamlit Community Cloud, privée, accès par invitation email) :
  https://shadbeez-edition-pr0duits.streamlit.app — attention au `0` de `pr0duits` :
  l'ancien sous-domaine est resté verrouillé après un delete/recreate trop rapide.
  Ne jamais supprimer/recréer l'app en réutilisant immédiatement le même sous-domaine.
- Python 3.13, géré par **uv** (`pyproject.toml` + `uv.lock`)
- **Certificats SSL** : `truststore.inject_into_ssl()` est appelé en tout début
  d'`app.py`, avant tout import réseau. Les certificats sont validés via le magasin
  de l'OS (nécessaire derrière le proxy d'entreprise avec inspection SSL sur les
  postes Windows ; sans effet particulier sur Linux/Streamlit Cloud). Remplace
  l'ancien `python-certifi-win32`. L'injection est globale : elle couvre aussi
  l'URL d'export `export2.beezup.com`.
- Commandes : `uv sync` (installer/mettre à jour le venv), `uv run streamlit run app.py`
  ou `run.bat` (lancer l'app), `uv run pytest` (lancer les tests)
- **Tests** (`tests/`) : logique pure de `data_processing` (normalize_value, compute_diff,
  EAN...), parseurs d'`api_services` (JSON déguisé, match catégories) et retry du client.
  Les fixtures `tests/fixtures/*.json` sont des extractions API réelles **allégées**
  (Cultura = chemins tronqués, Brico Dépôt = cas nominal) qui servent de tests de
  régression au correctif `_is_selected_category`. À lancer avant tout commit.
- **Si le dossier du projet est copié ou déplacé** : lancer `uv sync --reinstall`. Les
  lanceurs `.exe` du venv (streamlit.exe...) embarquent des chemins absolus et
  continueraient d'exécuter le python de l'ancien emplacement (vécu en juillet 2026 :
  venv copié depuis PycharmProjects → l'app tournait sur l'ancien venv sans truststore).
- pandas **2.3.3** (NE PAS passer en 3.x sans tests — changements cassants)
- `requirements.txt` est **généré** depuis le lockfile (ne pas éditer à la main) :
  `uv export --format requirements.txt --no-hashes --no-annotate --no-dev -o requirements.txt`
  Conservé uniquement pour le déploiement Streamlit Community Cloud — à régénérer
  après tout changement de dépendances.

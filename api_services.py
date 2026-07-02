import json
import uuid
from io import StringIO
from typing import Optional

import pandas as pd
import requests
import streamlit as st
from loguru import logger

# Caractères invisibles parfois présents en tête/queue d'export (BOM, zero-width spaces...)
_INVISIBLE_CHARS = "\ufeff\u200b\u200c\u200d\u00a0"


def get_user_identity(client) -> dict[str, str]:
    """
    Récupère le prénom et le nom de l'utilisateur connecté via l'API BeezUP.
    """
    response = client.get("v2/user/customer/account")

    if not response:
        logger.warning("get_user_identity : réponse vide, retour des valeurs par défaut.")
        return {"firstName": "Utilisateur", "lastName": "Inconnu"}

    info = response.get("personalInfo", {})
    first_name = info.get("firstName") or "Utilisateur"
    last_name = info.get("lastName") or "Inconnu"

    return {"firstName": first_name, "lastName": last_name}


def get_catalog_infos(client, catalog_id: str) -> dict[str, str]:
    """
    Récupère le storeId et le channelId pour un catalogue spécifique.
    """
    response = client.get(f"v2/user/channelCatalogs/{catalog_id}")

    if not response:
        raise RuntimeError(f"Impossible d'extraire les données du catalogue : {catalog_id}")

    store_id = response.get("storeId")
    channel_id = response.get("channelId")

    if not store_id or not channel_id:
        raise ValueError(f"Données du catalogue incomplètes pour {catalog_id} (storeId ou channelId manquant).")

    logger.info(f"Infos catalogue récupérées : catalog={catalog_id} | store={store_id} | channel={channel_id}")

    return {"storeId": store_id, "channelId": channel_id}


def get_store_name(client, store_id: str) -> str:
    """
    Récupère le nom de la boutique BeezUP associée à un store_id.
    """
    response = client.get("v2/user/marketplaces/channelCatalogs/", params={"storeId": store_id})

    if not response:
        logger.warning(f"get_store_name : réponse vide pour store_id={store_id}.")
        return "Boutique inconnue"

    catalogs = response.get("marketplaceChannelCatalogs", [])
    store_name = next((c.get("beezUPStoreName") for c in catalogs if c.get("beezUPStoreName")), None)

    if not store_name:
        logger.warning(f"get_store_name : aucun nom trouvé pour store_id={store_id}.")
        return "Boutique sans nom"

    logger.info(f"Boutique identifiée : {store_name} (store_id={store_id})")

    return store_name


@st.cache_data(ttl=1800, show_spinner=False)
def get_catalog_categories(_client, store_id: str) -> Optional[pd.DataFrame]:
    """
    Récupère les catégories présentes dans le catalogue vendeur et le nombre de produits dans chacune.
    Colonnes retournées : Catalog Category, Total Product Count.
    """
    response = _client.get(f"v2/user/catalogs/{store_id}/categories")

    if not response:
        raise ConnectionError(f"Impossible d'extraire les catégories du catalogue (store_id={store_id}).")

    categories = response.get("categories", [])
    data = []

    for c in categories:
        category_id = c.get("categoryId")
        path = c.get("categoryPath", [])

        if path and category_id:
            data.append({
                "Catalog Category": path[-1],
                "Total Product Count": c.get("totalProductCount", 0)
            })

    if not data:
        raise ValueError(f"Aucune catégorie trouvée dans le catalogue (store_id={store_id}).")

    df = pd.DataFrame(data)
    df["Total Product Count"] = pd.to_numeric(df["Total Product Count"], errors="coerce").fillna(0).astype(int)

    logger.info(f"Catégories catalogue extraites : {len(df)} catégories (store_id={store_id}).")

    return df


@st.cache_data(ttl=1800, show_spinner=False)
def get_category_mapping(_client, catalog_id: str) -> Optional[pd.DataFrame]:
    """
    Récupère le mapping catégories entre le catalogue vendeur et le canal de vente.
    Colonnes retournées : Catalog Category, Channel Category Path.
    """
    response = _client.get(f"v2/user/channelCatalogs/{catalog_id}/categories")

    if not response:
        raise ConnectionError(f"Impossible d'extraire le mapping catégories (catalog_id={catalog_id}).")

    mapping = response.get("channelCatalogCategoryConfigurations", [])

    if not mapping:
        raise ValueError(f"Aucune catégorie mappée pour le catalogue : {catalog_id}.")

    data = []
    skipped = 0

    for m in mapping:
        catalog_path = m.get("catalogCategoryPath", [])
        channel_path = m.get("channelCategoryPath", [])

        # On ignore les entrées sans chemin catalogue valide
        if not catalog_path:
            skipped += 1
            continue

        data.append({
            "Catalog Category": catalog_path[-1],
            "Channel Category Path": " > ".join(channel_path)
        })

    if skipped:
        logger.warning(f"get_category_mapping : {skipped} entrée(s) ignorée(s) (catalogCategoryPath vide).")

    if not data:
        raise ValueError(f"Aucune catégorie mappée valide pour le catalogue : {catalog_id}.")

    df = pd.DataFrame(data)
    logger.info(f"Mapping catégories extrait : {len(df)} catégories mappées (catalog_id={catalog_id}).")

    return df


@st.cache_data(ttl=1800, show_spinner=False)
def get_channel_attributes(_client, channel_id: str) -> Optional[pd.DataFrame]:
    """
    Récupère la liste des attributs spécifiques au canal de vente.
    """
    response = _client.post(f"v2/user/channels/{channel_id}/columns", json=[])

    if not response:
        raise ConnectionError(f"Impossible d'extraire les attributs du canal (channel_id={channel_id}).")

    data = []
    skipped = 0

    for item in response:
        config = item.get("configuration", {})
        attr_id = item.get("channelColumnId")

        # On ignore les items sans identifiant d'attribut
        if not attr_id:
            skipped += 1
            continue

        data.append({
            "Source": "Channel",
            "Channel Attribute Id": attr_id,
            "Attribute Name": item.get("channelColumnName"),
            "Attribute Code": item.get("channelColumnCode"),
            "Attribute Description": item.get("channelColumnDescription"),
            "Status": config.get("columnImportance"),
            "Type Value": config.get("columnDataType")
        })

    if skipped:
        logger.warning(f"get_channel_attributes : {skipped} attribut(s) ignoré(s) (channelColumnId manquant).")

    df = pd.DataFrame(data)
    logger.info(f"Attributs canal extraits : {len(df)} attributs (channel_id={channel_id}).")

    return df


def get_channel_category_attributes(client, catalog_id: str, selected_channel_path: str) -> Optional[pd.DataFrame]:
    """
    Récupère les attributs spécifiques à une catégorie (et les attributs Cross Categories).
    """
    response = client.get(f"v2/user/channelCatalogs/{catalog_id}/attributes")

    if not response:
        raise ConnectionError(f"Impossible d'extraire les attributs de la catégorie (catalog_id={catalog_id}).")

    data = []

    for category in response:
        channel_full_category_path = category.get("channelFullCategoryPath")

        if channel_full_category_path == "Cross Categories":
            source = "Cross Categories"
        elif _is_selected_category(
                selected_channel_path,
                channel_full_category_path,
                category.get("channelOriginCategoryName")
        ):
            source = "Category"
        else:
            continue

        for attribute in category.get("attributes", []):
            data.append({
                "Source": source,
                # Pour "Category", on stocke le chemin sélectionné (complet) plutôt que
                # channelFullCategoryPath qui peut être tronqué (voir _is_selected_category)
                "Channel Category Path": (
                    channel_full_category_path if source == "Cross Categories" else selected_channel_path
                ),
                "Channel Attribute Id": attribute.get("channelAttributeId"),
                "Attribute Name": attribute.get("attributeName"),
                "Attribute Code": attribute.get("attributeCode"),
                "Attribute Description": attribute.get("attributeDescription"),
                "Status": attribute.get("status"),
                "Type Value": attribute.get("typeValue"),
                "Attribute Value List Code": attribute.get("attributeValueListCode"),
                "Default Value": attribute.get("defaultValue")
            })

    if not data:
        logger.warning(
            f"get_channel_category_attributes : aucun attribut trouvé pour "
            f"'{selected_channel_path}' (catalog_id={catalog_id})."
        )

    df = pd.DataFrame(data)
    logger.info(
        f"Attributs catégorie extraits : {len(df)} attributs "
        f"pour '{selected_channel_path}' (catalog_id={catalog_id})."
    )

    return df


def _is_selected_category(selected_path: str, full_path: Optional[str], origin_name: Optional[str]) -> bool:
    """
    Détermine si une entrée de la route /attributes correspond au chemin sélectionné.

    Sur certaines marketplaces (ex. Cultura), channelFullCategoryPath est tronqué :
    un ou plusieurs niveaux terminaux manquent, mais le nom du dernier niveau reste
    disponible dans channelOriginCategoryName. On accepte donc aussi le cas où le
    chemin sélectionné prolonge le chemin tronqué ET se termine par ce dernier niveau.
    Vérifié sans ambiguïté sur données réelles (Cultura : 265 catégories, aucun doublon).
    """
    if not full_path:
        return False

    if full_path == selected_path:
        return True

    return (
        bool(origin_name)
        and selected_path.startswith(full_path + " > ")
        and selected_path.rsplit(" > ", 1)[-1] == origin_name
    )


@st.cache_data(ttl=1800, show_spinner=False)
def get_all_channel_category_attributes(_client, catalog_id: str) -> Optional[pd.DataFrame]:
    """
    Récupère les attributs de TOUTES les catégories du catalogue, avec leur
    channelCategoryCode. Utilisé par la génération par SKUs : la jointure
    produit → catégorie se fait par code (colonne catégorie de l'export),
    ce qui évite le problème des channelFullCategoryPath tronqués.

    Colonnes retournées : Source (Cross Categories/Category), Category Code,
    Channel Category Path + les champs attribut habituels.
    """
    response = _client.get(f"v2/user/channelCatalogs/{catalog_id}/attributes")

    if not response:
        raise ConnectionError(f"Impossible d'extraire les attributs (catalog_id={catalog_id}).")

    data = []

    for category in response:
        channel_full_category_path = category.get("channelFullCategoryPath")
        is_cross = channel_full_category_path == "Cross Categories"

        for attribute in category.get("attributes", []):
            data.append({
                "Source": "Cross Categories" if is_cross else "Category",
                "Category Code": None if is_cross else category.get("channelCategoryCode"),
                "Channel Category Path": channel_full_category_path,
                "Channel Attribute Id": attribute.get("channelAttributeId"),
                "Attribute Name": attribute.get("attributeName"),
                "Attribute Code": attribute.get("attributeCode"),
                "Attribute Description": attribute.get("attributeDescription"),
                "Status": attribute.get("status"),
                "Type Value": attribute.get("typeValue"),
                "Attribute Value List Code": attribute.get("attributeValueListCode"),
                "Default Value": attribute.get("defaultValue")
            })

    df = pd.DataFrame(data)
    nb_categories = df["Category Code"].nunique() if not df.empty else 0
    logger.info(
        f"Attributs toutes catégories extraits : {len(df)} attributs, "
        f"{nb_categories} catégories (catalog_id={catalog_id})."
    )

    return df


@st.cache_data(ttl=1800, show_spinner=False)
def get_column_mapping_dict(_client, catalog_id: str) -> dict:
    """
    Récupère le mapping attributs entre le catalogue vendeur et le canal de vente.
    Retourne un dict { channelColumnId (lowercase) : catalogColumnId (lowercase) }.
    """
    response = _client.get(f"v2/user/channelCatalogs/{catalog_id}")

    if not response:
        raise ConnectionError(f"Impossible d'extraire le mapping attributs (catalog_id={catalog_id}).")

    mapping_list = response.get("columnMappings", [])

    if not mapping_list:
        logger.info(f"get_column_mapping_dict : aucun mapping existant pour catalog_id={catalog_id}.")
        return {}

    data = {
        c.get("channelColumnId").lower().strip(): (
            c.get("catalogColumnId").lower().strip() if c.get("catalogColumnId") else None
        )
        for c in mapping_list
        if c.get("channelColumnId")
    }

    mapped_count = sum(1 for v in data.values() if v is not None)
    logger.info(f"Mapping attributs extrait : {mapped_count}/{len(data)} attributs mappés (catalog_id={catalog_id}).")

    return data


def get_product_ids(
        client,
        catalog_id: str,
        selected_channel_path: str = None,
        skus_list: list = None
) -> Optional[pd.DataFrame]:
    """
    Récupère les Product Id et les SKU présents dans le catalogue.
    Filtre par catégorie si selected_channel_path est fourni (génération par
    catégorie), sinon interroge tout le catalogue (génération par SKUs).
    Gère automatiquement la pagination (pageSize = 1000).
    """
    endpoint = f"v2/user/channelCatalogs/{catalog_id}/products"
    data = []
    page_number = 1

    while True:
        payload = {
            "pageNumber": page_number,
            "pageSize": 1000,
            "criteria": {
                "logic": "cumulative",
                "exist": True,
                "uncategorized": False,
                "excluded": False,
                "disabled": False
            }
        }

        if selected_channel_path:
            payload["channelCategoryFilter"] = {
                "categoryPath": selected_channel_path.split(" > ")
            }

        if skus_list:
            payload["productFilters"] = {"channelSkus": skus_list}

        response = client.post(endpoint, json=payload)

        if not response:
            raise ConnectionError(f"Impossible d'extraire les produits (catalog_id={catalog_id}, page {page_number}).")

        product_infos = response.get("productInfos", [])

        for product in product_infos:
            data.append({
                "Product Id": product.get("productId"),
                "sku": product.get("productSku")
            })

        pagination_result = response.get("paginationResult", {})
        page_count = pagination_result.get("pageCount", 1)

        logger.debug(f"get_product_ids : page {page_number}/{page_count} — {len(product_infos)} produits.")

        if page_number >= page_count:
            break

        page_number += 1

    scope = f"la catégorie '{selected_channel_path}'" if selected_channel_path else "les SKUs demandés"

    if not data:
        raise ValueError(f"Aucun produit trouvé pour {scope}.")

    df = pd.DataFrame(data)
    logger.info(f"{len(df)} produits extraits pour {scope} (catalog_id={catalog_id}).")

    return df


@st.cache_data(ttl=1800, show_spinner=False)
def download_export_file(catalog_id: str) -> Optional[pd.DataFrame]:
    url = f"https://export2.beezup.com/v2/user/channelCatalogs/export/X/X/{catalog_id}"
    logger.info(f"Téléchargement de l'export BeezUP : {url}")

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    if not response.content:
        raise ValueError(f"Le fichier d'export est vide (catalog_id={catalog_id}).")

    encoding = response.encoding or response.apparent_encoding or "utf-8"
    if encoding.lower().replace("-", "") in ("utf8", "utf8sig"):
        encoding = "utf-8-sig"

    text = response.content.decode(encoding, errors="replace").strip().strip(_INVISIBLE_CHARS).strip()
    logger.debug(f"Encodage utilisé : {encoding}")

    if text.startswith("[") or text.startswith("{"):
        df = _parse_json_export(text, catalog_id)
    else:
        sep = _detect_csv_separator(text.split("\n")[0])
        df = pd.read_csv(StringIO(text), sep=sep, on_bad_lines="warn")

        if len(df.columns) < 3:
            logger.warning(
                f"Export CSV suspect : seulement {len(df.columns)} colonne(s) détectée(s) "
                f"(catalog_id={catalog_id}). Séparateur peut-être incorrect."
            )

    logger.info(f"Export téléchargé : {len(df)} produits, {len(df.columns)} colonnes (catalog_id={catalog_id}).")
    return df


def _detect_csv_separator(first_line: str) -> str:
    """
    Détecte le séparateur le plus probable en comptant les occurrences
    des candidats courants sur la première ligne du CSV.
    """
    candidates = {",": first_line.count(","), ";": first_line.count(";"), "\t": first_line.count("\t")}
    sep = max(candidates, key=candidates.get)
    logger.debug(f"Séparateur détecté : '{sep}' ({candidates})")
    return sep


def _parse_json_export(text: str, catalog_id: str) -> pd.DataFrame:
    """
    Normalise un export BeezUP au format JSON en DataFrame plat.

    Structure attendue :
    [
      {
        "sku": "...",
        "messageType": "publish",
        "properties": [{"key": "attr_code", "value": "val"}, ...]
      },
      ...
    ]

    Chaque propriété devient une colonne dans le DataFrame.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Export non parseable (ni CSV ni JSON valide) pour catalog_id={catalog_id} : {e}")

    if not isinstance(data, list):
        raise ValueError(f"Format JSON inattendu (attendu : liste de produits) pour catalog_id={catalog_id}.")

    records = []
    for product in data:
        row = {"sku": product.get("sku")}
        for prop in product.get("properties", []):
            key = prop.get("key")
            value = prop.get("value")
            if key:
                row[key] = value
        records.append(row)

    df = pd.DataFrame(records)
    logger.debug(f"_parse_json_export : {len(df)} produits normalisés depuis JSON.")
    return df


def get_attribute_values(client, catalog_id: str, attribute_id: str) -> list[dict]:
    """
    Récupère la liste de valeurs bornées pour un attribut donné.
    Retourne une liste vide si aucune valeur n'est disponible.
    """
    response = client.get(f"v2/user/channelCatalogs/{catalog_id}/attributes/{attribute_id}/mapping")

    if not response:
        raise ConnectionError(
            f"Impossible d'extraire les valeurs bornées "
            f"(catalog_id={catalog_id}, attribute_id={attribute_id})."
        )

    values = response.get("channelAttributeValuesWithMapping", [])
    logger.debug(f"Valeurs bornées : {len(values)} valeurs pour attribute_id={attribute_id}.")

    return values


def build_dropdown_dataframe(client, catalog_id: str, df_selected_attributes: pd.DataFrame) -> pd.DataFrame:
    """
    Récupère les listes de valeurs bornées des attributs concernés et les regroupe dans un DataFrame.
    """
    df_list_attributes = df_selected_attributes[df_selected_attributes["Attribute Value List Code"].notnull()]
    value_dict = {}
    nb_total = len(df_list_attributes)

    for _, row in df_list_attributes.iterrows():
        attribute_id = row["Channel Attribute Id"]
        column_name = row["Label"]

        mapped_values = get_attribute_values(client, catalog_id, attribute_id)

        if mapped_values:
            value_dict[column_name] = [
                f"{v.get('code')} | {v.get('label')}"
                for v in mapped_values
                if v.get("code") is not None
            ]
        else:
            logger.debug(f"Aucune valeur bornée pour l'attribut '{column_name}' ({attribute_id}).")

    df = pd.DataFrame({k: pd.Series(v, dtype="string") for k, v in value_dict.items()})
    logger.info(
        f"Listes de valeurs bornées générées : {len(value_dict)}/{nb_total} attributs "
        f"avec liste (catalog_id={catalog_id})."
    )

    return df


def get_custom_columns_list(client, store_id: str) -> dict[str, str]:
    """
    Récupère la liste des champs personnalisés de la boutique.
    Retourne un dict { userColumnName: columnId }.

    Note : l'API BeezUP peut renvoyer la clé avec typo "userColumName".
    On gère les deux variantes avec un fallback.
    """
    response = client.get(f"v2/user/catalogs/{store_id}/customColumns")

    if not response:
        raise ConnectionError(f"Impossible d'extraire les champs personnalisés (store_id={store_id}).")

    custom_columns = response.get("customColumns", [])
    result = {}

    for col in custom_columns:
        column_name = col.get("userColumName") or col.get("userColumnName")
        column_id = col.get("id")

        if column_name and column_id:
            result[column_name] = column_id

    logger.info(f"Champs personnalisés récupérés : {len(result)} champs (store_id={store_id}).")

    return result


def create_custom_column(client, store_id: str, attr_name: str = "Champ perso vide généré par API") -> str:
    """
    Crée un champ personnalisé vide pour permettre le mapping d'un attribut non encore lié.
    Retourne l'ID du champ personnalisé créé.
    """
    # Vérification préalable : réutiliser si le champ existe déjà
    existing_columns = get_custom_columns_list(client, store_id)

    if attr_name in existing_columns:
        col_id = existing_columns[attr_name]
        logger.info(f"Champ personnalisé existant réutilisé : '{attr_name}' (id={col_id}, store_id={store_id}).")
        return col_id

    # Création d'un nouveau champ
    column_id = str(uuid.uuid4())
    endpoint = f"v2/user/catalogs/{store_id}/customColumns/{column_id}/decrypted"

    body = {
        "displayGroupName": "Personnalised Fields",
        "blocklyExpression": (
            "<block xmlns=\"http://www.w3.org/1999/xhtml\" type=\"beezup_start\" deletable=\"false\">"
            "<value name=\"startValue\">"
            "<block type=\"text\">"
            "<field name=\"TEXT\"></field>"
            "</block>"
            "</value>"
            "</block>"
        ),
        "expression": "\"\"",
        "userColumnName": attr_name,
    }

    client.put(endpoint, json=body)
    logger.success(f"Champ personnalisé créé : '{attr_name}' (id={column_id}, store_id={store_id}).")

    return column_id


def map_attributes_group(client, catalog_id: str, channel_attr_ids: list[str], custom_col_id: str):
    """
    Associe une liste d'attributs channel à un même champ personnalisé catalogue.
    Préserve les mappings existants et complète ceux qui manquent.
    """
    if not channel_attr_ids:
        logger.info("map_attributes_group : aucun attribut à mapper.")
        return

    response = client.get(f"v2/user/channelCatalogs/{catalog_id}")

    if not response or "columnMappings" not in response:
        raise ConnectionError(f"Impossible de récupérer le mapping actuel (catalog_id={catalog_id}).")

    current_mappings = response.get("columnMappings", [])
    attr_ids_to_map = {str(a).lower().strip() for a in channel_attr_ids if a}

    updated_mappings = []
    mapped_count = 0
    seen_attrs = set()

    for mapping in current_mappings:
        attr_id = str(mapping.get("channelColumnId", "")).lower().strip()

        if attr_id in attr_ids_to_map:
            # Créer un nouveau dict plutôt que de muter celui de la réponse API
            updated_mappings.append({**mapping, "catalogColumnId": custom_col_id})
            seen_attrs.add(attr_id)
            mapped_count += 1
        else:
            updated_mappings.append(mapping)

    # Ajouter les attributs absents du mapping existant
    for attr_id in attr_ids_to_map:
        if attr_id not in seen_attrs:
            updated_mappings.append({
                "channelColumnId": attr_id,
                "catalogColumnId": custom_col_id
            })
            mapped_count += 1

    client.configure_channel_catalog_column_mappings(catalog_id, updated_mappings)
    logger.success(
        f"Mapping groupé terminé : {mapped_count} attribut(s) liés à {custom_col_id} "
        f"(catalog_id={catalog_id})."
    )


def _extract_override_value(raw_value) -> str:
    """
    Normalise la valeur d'un override BeezUP.
    L'API peut renvoyer soit une valeur simple, soit un dict {"override": "...", "catalogValue": "..."}.
    """
    if isinstance(raw_value, dict):
        override_value = raw_value.get("override")
        return "" if override_value is None else override_value

    return "" if raw_value is None else raw_value


def get_existing_overrides_by_product_id(client, catalog_id: str, skus: list[str]) -> dict[str, dict]:
    """
    Récupère les overrides existants pour une liste de SKUs, par chunks de 100 avec pagination.

    Retourne un dict { product_id: { attribute_id: override_value } }.
    Les produits sans override ne sont pas retournés par l'API (overridden=True) : c'est normal.
    """
    if not skus:
        return {}

    result = {}
    chunk_size = 100
    nb_chunks = (len(skus) + chunk_size - 1) // chunk_size

    for chunk_index, start in enumerate(range(0, len(skus), chunk_size)):
        sku_chunk = skus[start:start + chunk_size]
        page_number = 1

        logger.debug(f"get_existing_overrides : chunk {chunk_index + 1}/{nb_chunks} ({len(sku_chunk)} SKUs).")

        while True:
            payload = {
                "pageNumber": page_number,
                "pageSize": 100,
                "criteria": {
                    "logic": "cumulative",
                    "exist": True,
                    "uncategorized": False,
                    "excluded": False,
                    "disabled": False
                },
                "overridden": True,
                "productFilters": {"channelSkus": sku_chunk}
            }

            response = client.get_channel_catalog_product_information_list(
                catalog_id=catalog_id,
                payload=payload
            )

            if not response:
                raise ConnectionError(
                    f"Impossible de récupérer les overrides existants "
                    f"(catalog_id={catalog_id}, chunk {chunk_index + 1}, page {page_number})."
                )

            for product in response.get("productInfos", []):
                product_id = product.get("productId")
                overrides = product.get("overrides", {}) or {}

                if not product_id:
                    continue

                flat_overrides = {
                    attr_id: _extract_override_value(raw_value)
                    for attr_id, raw_value in overrides.items()
                }

                if flat_overrides:
                    result[product_id] = flat_overrides

            pagination_result = response.get("paginationResult", {}) or {}
            page_count = pagination_result.get("pageCount", 1)

            if page_number >= page_count:
                break

            page_number += 1

    logger.info(
        f"Overrides existants récupérés : {len(result)} produit(s) avec overrides "
        f"sur {len(skus)} SKUs (catalog_id={catalog_id})."
    )

    return result

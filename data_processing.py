import warnings
from typing import Optional

import pandas as pd
from loguru import logger

import api_services as api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ean_converter(x) -> str:
    """
    Appliqué via `converters` lors du pd.read_excel du template.
    Garantit que l'EAN est toujours lu comme une chaîne à 13 caractères,
    même si pandas a inféré un type numérique sur la cellule.
    """
    if pd.isna(x) or str(x).strip() in ("", "nan", "None"):
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(13)


# ---------------------------------------------------------------------------
# Fonctions de traitement (partie génération de template)
# ---------------------------------------------------------------------------

def get_available_categories(df_categories: pd.DataFrame, df_mapping: pd.DataFrame) -> pd.DataFrame:
    """
    Fusionne les catégories du catalogue et le mapping canal, puis agrège le nombre de produits.
    Retourne les colonnes : Channel Category Path, Total Product Count.
    """
    df = pd.merge(df_categories, df_mapping, on="Catalog Category", how="inner")
    df_grouped = df.groupby("Channel Category Path")["Total Product Count"].sum().reset_index()
    df_result = df_grouped[["Channel Category Path", "Total Product Count"]]

    logger.debug(f"get_available_categories : {len(df_result)} catégorie(s) disponibles après fusion.")

    return df_result.sort_values(by="Total Product Count", ascending=False).reset_index(drop=True)


def normalize_attributes_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalisation commune du DataFrame d'attributs (canal + catégories) :
    IDs en minuscules, Status/Type Value en Title Case, Attribute Code complété
    par Attribute Name si vide.
    """
    df = df.copy()

    df["Channel Attribute Id"] = (
        df["Channel Attribute Id"].astype(str).str.lower().str.strip()
    )
    df["Status"] = df["Status"].str.title()
    df["Type Value"] = df["Type Value"].str.title()
    df["Attribute Code"] = (
        df["Attribute Code"].fillna(df["Attribute Name"]).astype(str).str.strip()
    )

    return df


def select_attributes_for_category(
        df_attrs: pd.DataFrame,
        category_code: str,
        selected_statuses: list[str],
        required_codes: list[str],
        excluded_codes: list[str]
) -> pd.DataFrame:
    """
    Sélectionne les attributs applicables à une catégorie pour la génération par SKUs.

    La jointure se fait par code catégorie (et non par chemin) : on garde les lignes
    dont le Category Code correspond, plus celles sans code (attributs Channel et
    Cross Categories, valables pour toutes les catégories). Les codes sont comparés
    via normalize_value car l'export peut livrer un code numérique (12345.0).

    Sélection finale : attributs obligatoires (marqués Source="Obligatory")
    + attributs des statuts choisis, moins les exclus (les exclusions gagnent,
    comme dans le PoC d'origine).
    """
    code_series = df_attrs["Category Code"]
    target = normalize_value(category_code)

    subset = df_attrs[
        code_series.isna() | (code_series.apply(normalize_value) == target)
    ]
    df_dedup = dedupe_keep_most_restrictive(subset)

    required = {str(c).strip() for c in required_codes}
    excluded = {str(c).strip() for c in excluded_codes}

    mask_obl = df_dedup["Attribute Code"].isin(required)
    df_dedup.loc[mask_obl, "Source"] = "Obligatory"

    keep = (mask_obl | df_dedup["Status"].isin(selected_statuses))
    keep &= ~df_dedup["Attribute Code"].isin(excluded)

    return df_dedup[keep].reset_index(drop=True)


def get_category_path_by_code(df_attrs: pd.DataFrame, category_code: str) -> Optional[str]:
    """
    Retrouve le Channel Category Path associé à un code catégorie.
    Retourne None si le code est inconnu de la route /attributes.
    """
    code_series = df_attrs["Category Code"]
    target = normalize_value(category_code)

    matches = df_attrs.loc[
        code_series.notna() & (code_series.apply(normalize_value) == target),
        "Channel Category Path"
    ]

    return matches.iloc[0] if not matches.empty else None


def dedupe_keep_most_restrictive(df: pd.DataFrame) -> pd.DataFrame:
    """
    Supprime les doublons d'attributs en conservant la version la plus restrictive
    (Required > Recommended > Optional).
    Ajoute une colonne 'Label' comme identifiant unique.
    """
    df_temp = df.copy()

    df_temp["Label"] = (
            df_temp["Attribute Name"].astype(str).str.strip()
            + " | "
            + df_temp["Channel Attribute Id"].astype(str).str.strip()
    )

    rank_map = {"Required": 0, "Recommended": 1, "Optional": 2}
    df_temp["_status_rank"] = df_temp["Status"].map(rank_map).fillna(99).astype(int)

    df_dedup = (
        df_temp.sort_values(by=["Label", "_status_rank"], ascending=[True, True])
        .drop_duplicates(subset=["Label"], keep="first")
        .drop(columns=["_status_rank"])
        .reset_index(drop=True)
    )

    removed = len(df_temp) - len(df_dedup)
    if removed:
        logger.debug(
            f"dedupe_keep_most_restrictive : {removed} doublon(s) supprimé(s), "
            f"{len(df_dedup)} attributs conservés."
        )

    return df_dedup


def merge_export_data(df_ids: pd.DataFrame, df_values: pd.DataFrame) -> pd.DataFrame:
    """
    Fusionne les Product Id avec les valeurs d'attributs de l'export BeezUP via le SKU.
    """
    if df_ids.empty or df_values.empty:
        raise ValueError("Données insuffisantes pour la fusion (un des tableaux est vide).")

    if "Product Id" in df_values.columns:
        df_values = df_values.drop(columns=["Product Id"])

    df_merged = pd.merge(df_ids, df_values, on="sku", how="inner")

    if df_merged.empty:
        raise ValueError("La fusion n'a retourné aucun résultat. Vérifiez que les SKUs correspondent.")

    unmatched = len(df_ids) - len(df_merged)
    if unmatched:
        logger.warning(f"merge_export_data : {unmatched} produit(s) sans correspondance dans l'export BeezUP.")

    logger.debug(f"merge_export_data : {len(df_merged)} lignes fusionnées sur {len(df_ids)} produits.")

    return df_merged


def filter_export_columns(df_merged: pd.DataFrame, attribute_codes: list) -> pd.DataFrame:
    """
    Filtre les colonnes du DataFrame fusionné pour ne conserver que les attributs sélectionnés.
    """
    base_cols = ["Product Id", "sku"]
    cols_to_extract = list(dict.fromkeys(base_cols + attribute_codes))
    available_cols = [c for c in cols_to_extract if c in df_merged.columns]

    missing = set(attribute_codes) - set(df_merged.columns)
    if missing:
        logger.debug(
            f"filter_export_columns : {len(missing)} attribut(s) absents de l'export ignorés : {missing}"
        )

    return df_merged.reindex(columns=available_cols).copy()


def format_final_template(
        df_merged: pd.DataFrame,
        df_selected_attributes: pd.DataFrame,
        catalog_id: str,
        selected_path: str,
        code_to_label: dict,
        obl_codes: list
) -> pd.DataFrame:
    """
    Orchestre le tri des colonnes, l'insertion des métadonnées (Catalog Id, Channel Category Path)
    et le renommage final code → label.
    """
    df = df_merged.copy()

    product_id_col = next((c for c in df.columns if "product" in c.lower() and "id" in c.lower()), None)

    if product_id_col is None:
        logger.error(f"format_final_template : colonnes disponibles = {list(df.columns)}")
        raise KeyError("La colonne 'Product Id' est introuvable dans le DataFrame fusionné.")

    if product_id_col != "Product Id":
        df = df.rename(columns={product_id_col: "Product Id"})

    idx = df.columns.get_loc("Product Id")
    df.insert(loc=idx + 1, column="Catalog Id", value=catalog_id)
    df.insert(loc=idx + 2, column="Channel Category Path", value=selected_path)

    def get_sorted_codes(status):
        subset = df_selected_attributes[df_selected_attributes["Status"] == status]
        return subset.sort_values(by="Attribute Name")["Attribute Code"].tolist()

    req_codes_all = get_sorted_codes("Required")
    rec_codes_all = get_sorted_codes("Recommended")
    opt_codes_all = get_sorted_codes("Optional")

    req_codes = [c for c in req_codes_all if c not in obl_codes]
    rec_codes = [c for c in rec_codes_all if c not in obl_codes and c not in req_codes]
    opt_codes = [c for c in opt_codes_all if c not in obl_codes and c not in req_codes and c not in rec_codes]

    first_cols = ["Product Id", "Catalog Id", "Channel Category Path", "sku"]
    ordered_attribute_cols = obl_codes + req_codes + rec_codes + opt_codes

    for code in obl_codes:
        if code not in df.columns:
            df[code] = pd.NA

    df = df.reindex(columns=first_cols + ordered_attribute_cols)
    df = df.rename(columns={**code_to_label, "sku": "SKU"})

    logger.debug(
        f"format_final_template : {len(df)} lignes, {len(df.columns)} colonnes "
        f"({len(obl_codes)} obligatoires, {len(req_codes)} requises, "
        f"{len(rec_codes)} recommandées, {len(opt_codes)} optionnelles)."
    )

    return df


def normalize_export_data(df_template: pd.DataFrame, df_list_of_values: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoyage et normalisation des types de données selon la structure fixe du template.
    La colonne à l'index 4 (EAN/GTIN) est traitée comme une chaîne à 13 caractères.
    """
    df = df_template.copy()

    if df.columns.duplicated().any():
        cols_doubles = df.columns[df.columns.duplicated()].unique().tolist()
        logger.warning(f"normalize_export_data : colonnes dupliquées supprimées : {cols_doubles}")
        df = df.loc[:, ~df.columns.duplicated()]

    for i, col in enumerate(df.columns):
        if i == 4:
            # Colonne EAN : format texte 13 caractères avec zéros de tête.
            # zfill appliqué uniquement sur les valeurs non vides pour éviter "0000000000000".
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
                .replace(["nan", "None", "<NA>"], "")
                .apply(lambda x: x.zfill(13) if x.strip() else "")
            )
            continue

        converted_col = pd.to_numeric(df[col], errors="coerce")
        if not converted_col.isna().all():
            df[col] = converted_col

    df = df.convert_dtypes()

    for col in df_list_of_values.columns:
        if col in df.columns:
            df[col] = df[col].astype(str).replace(["<NA>", "nan", "None"], "")

    return df


def map_codes_to_labels(df_template: pd.DataFrame, df_list_of_values: pd.DataFrame) -> pd.DataFrame:
    """
    Remplace les codes bruts par le format 'code | label' dans les colonnes à liste bornée.
    """
    df_mapped = df_template.copy()

    for col in df_list_of_values.columns:
        if col in df_mapped.columns:
            mapping_dict = {
                str(val).split(" | ")[0]: val
                for val in df_list_of_values[col].dropna()
            }
            df_mapped[col] = df_mapped[col].astype(str).replace(mapping_dict)
            df_mapped[col] = df_mapped[col].replace({"nan": "", "None": ""})

    return df_mapped


# ---------------------------------------------------------------------------
# Fonctions utilitaires partagées (génération + réintégration)
# ---------------------------------------------------------------------------

def normalize_value(val) -> str:
    """
    Normalise une valeur de cellule pour la comparaison dans compute_diff :
    - NaN / None           → ""
    - float entier (1.0)   → "1"
    - bool                 → traité comme str ("True"/"False"), pas comme int
    - "Code | Label"       → "Code"
    - autres               → str stripped
    """
    if pd.isna(val) or val is None:
        return ""

    if isinstance(val, bool):
        return str(val).strip()

    if isinstance(val, (float, int)):
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        return str(val).strip()

    s = str(val).strip()

    if " | " in s:
        return s.split(" | ")[0].strip()

    return s


def extract_attr_id(column_name: str) -> str | None:
    """
    Extrait l'UUID d'attribut depuis le nom de colonne Excel.
    Format attendu : "Nom attribut | uuid-..."
    Retourne None si la colonne n'est pas un attribut (pas de '|').
    """
    if "|" not in column_name:
        return None

    parts = column_name.split("|")
    if len(parts) < 2:
        return None

    attr_id = parts[1].strip()
    return attr_id if attr_id else None


# ---------------------------------------------------------------------------
# Diff et réintégration
# ---------------------------------------------------------------------------

def compute_diff(df_api: pd.DataFrame, df_excel: pd.DataFrame, attr_mapping: dict) -> tuple[list, list]:
    """
    Compare l'export BeezUP actuel (df_api) avec le template complété (df_excel).

    Règles appliquées colonne par colonne :
    - export vide  + template rempli   → update
    - export rempli + template différent → update
    - export rempli + template vide    → update avec "" (suppression volontaire)
    - attribut absent de l'export      → ajouté à to_map ; update préparé si valeur non vide
    - valeur identique                 → ignorée
    """
    df_api = df_api.copy()
    df_excel = df_excel.copy()

    sku_col_excel = next((c for c in df_excel.columns if c.lower() == "sku"), None)
    if not sku_col_excel:
        raise ValueError("La colonne SKU est introuvable dans le template.")
    if "sku" not in df_api.columns:
        raise ValueError("La colonne 'sku' est introuvable dans l'export BeezUP.")
    if "Product Id" not in df_excel.columns:
        raise ValueError("La colonne 'Product Id' est introuvable dans le template.")

    df_excel[sku_col_excel] = df_excel[sku_col_excel].apply(normalize_value)
    df_api["sku"] = df_api["sku"].apply(normalize_value)

    df_excel = df_excel.drop_duplicates(subset=[sku_col_excel], keep="last")
    df_api = df_api.drop_duplicates(subset=["sku"], keep="last")

    df_excel = df_excel.set_index(sku_col_excel)
    df_api = df_api.set_index("sku")

    updates = []
    attributes_to_map = set()

    technical_cols = {"Product Id", "Catalog Id", "Channel Category Path", "SKU", "sku"}
    attribute_cols = [c for c in df_excel.columns if c not in technical_cols]

    for col_excel in attribute_cols:
        attr_code = attr_mapping.get(col_excel)
        attr_id = extract_attr_id(col_excel)

        if not attr_code or not attr_id:
            logger.debug(f"compute_diff : colonne '{col_excel}' ignorée (absente du mapping DataInfo).")
            continue

        column_exists_in_export = attr_code in df_api.columns

        if not column_exists_in_export:
            attributes_to_map.add(attr_id)

        for sku in df_excel.index:
            if sku not in df_api.index:
                continue

            product_id = normalize_value(df_excel.at[sku, "Product Id"])
            if not product_id:
                continue

            new_val = normalize_value(df_excel.at[sku, col_excel])

            if column_exists_in_export:
                old_val = normalize_value(df_api.at[sku, attr_code])
                if new_val != old_val:
                    updates.append({
                        "sku": sku,
                        "product_id": product_id,
                        "attribute_id": attr_id,
                        "label": col_excel.split("|")[0].strip(),
                        "old_value": old_val,
                        "new_value": new_val
                    })
            else:
                if new_val:
                    updates.append({
                        "sku": sku,
                        "product_id": product_id,
                        "attribute_id": attr_id,
                        "label": col_excel.split("|")[0].strip(),
                        "old_value": "",
                        "new_value": new_val
                    })

    logger.info(
        f"compute_diff : {len(attribute_cols)} attribut(s) comparés → "
        f"{len(updates)} modification(s), {len(attributes_to_map)} attribut(s) à mapper."
    )

    return updates, sorted(attributes_to_map)


def get_excel_attribute_mapping(excel_file) -> dict:
    """
    Lit l'onglet DataInfo pour créer un dictionnaire { Label: Attribute Code }.
    Exemple : { "Couleur | 58b8a1b4-...": "color" }
    Lève ValueError si les colonnes attendues sont absentes.
    """
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
        df_info = pd.read_excel(excel_file, sheet_name="DataInfo")

    required_cols = {"Label", "Attribute Code"}
    missing_cols = required_cols - set(df_info.columns)

    if missing_cols:
        raise ValueError(f"Colonnes manquantes dans l'onglet DataInfo : {', '.join(missing_cols)}")

    mapping = dict(zip(df_info["Label"], df_info["Attribute Code"]))
    logger.debug(f"get_excel_attribute_mapping : {len(mapping)} entrée(s) lues depuis DataInfo.")

    return mapping


def run_comparison_for_file(client, excel_file) -> dict:
    """
    Analyse un template Excel et prépare les modifications à réintégrer dans BeezUP.
    Traitement unitaire : un fichier = un catalogue = une catégorie.

    Le mapping des attributs est lu depuis l'onglet DataInfo propre à chaque fichier,
    ce qui garantit qu'aucune information de catégorie n'est partagée entre fichiers.

    :param client:     Le client BeezUP authentifié.
    :param excel_file: L'objet UploadedFile Streamlit.
    :return: Dict avec les clés : catalog_id, store_id, updates, to_map, nb_products.
    :raises ValueError:  Fichier invalide (structure, Catalog Id vide, DataInfo manquant).
    :raises Exception:   Échec d'un appel API.
    """
    # --- Lecture de l'en-tête pour préparer le converter EAN (index 4) ---
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
        header = pd.read_excel(excel_file, sheet_name="Template", nrows=0)

    converters = {header.columns[4]: _ean_converter} if len(header.columns) > 4 else {}

    # --- Lecture complète du template avec converter EAN ---
    if hasattr(excel_file, "seek"):
        excel_file.seek(0)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
        df = pd.read_excel(excel_file, sheet_name="Template", converters=converters)

    if "Catalog Id" not in df.columns:
        raise ValueError("Le fichier ne contient pas de colonne 'Catalog Id'.")

    df["Catalog Id"] = df["Catalog Id"].apply(normalize_value)
    catalog_ids = [cid for cid in df["Catalog Id"].dropna().unique() if cid]

    if not catalog_ids:
        raise ValueError("Le fichier contient un Catalog Id vide ou illisible.")

    cat_id = catalog_ids[0]
    logger.info(f"Début d'analyse : fichier='{excel_file.name}', catalog_id={cat_id}, {len(df)} produit(s).")

    # --- Lecture du mapping attributs depuis DataInfo ---
    attr_mapping = get_excel_attribute_mapping(excel_file)

    if not attr_mapping:
        raise ValueError(f"L'onglet DataInfo est vide ou illisible pour le fichier '{excel_file.name}'.")

    # --- Appels API ---
    infos = api.get_catalog_infos(client, cat_id)
    store_id = infos["storeId"]

    df_api_snapshot = api.download_export_file(cat_id)

    # --- Calcul du diff ---
    updates, to_map = compute_diff(df_api_snapshot, df, attr_mapping)

    logger.info(
        f"Analyse terminée : fichier='{excel_file.name}' → "
        f"{len(updates)} modification(s), {len(to_map)} attribut(s) à mapper."
    )

    return {
        "catalog_id": cat_id,
        "store_id": store_id,
        "updates": updates,
        "to_map": to_map,
        "nb_products": len(df)
    }

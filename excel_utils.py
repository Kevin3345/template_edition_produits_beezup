import pandas as pd
from loguru import logger
from xlsxwriter.utility import xl_col_to_name


def _get_excel_formats(workbook) -> dict:
    """Définit et retourne les formats de header utilisés dans l'onglet Template."""
    base = {"bold": True, "align": "left", "valign": "vcenter"}
    return {
        "fixed": workbook.add_format({**base, "bg_color": "#EAEDF6", "font_color": "#313A4A"}),
        "required": workbook.add_format({**base, "bg_color": "#F2BBC0", "font_color": "#66141B"}),
        "recommended": workbook.add_format({**base, "bg_color": "#FFEBDB", "font_color": "#8B4711"}),
        "optional": workbook.add_format({**base, "bg_color": "#DBFAEC", "font_color": "#0B5E41"}),
    }


def _apply_template_styling(ws, df_template, df_datainfo, df_list_of_values, formats):
    """
    Applique sur l'onglet Template :
    - le masquage des colonnes techniques (Product Id, Catalog Id)
    - les couleurs de header selon le statut de l'attribut
    - les menus déroulants pour les attributs à liste bornée
    """
    # Mapping Label → statut (lowercase) pour la colorisation des headers
    attr_info_map = {
        row["Label"]: str(row.get("Status", "")).strip().lower()
        for _, row in df_datainfo.iterrows()
    }

    fixed_cols = {"Product Id", "Catalog Id", "Channel Category Path", "SKU"}
    cols_to_hide = {"Product Id", "Catalog Id"}

    for col_idx, col_name in enumerate(df_template.columns):

        # 1. Largeur et masquage
        is_hidden = col_name in cols_to_hide
        width = 20 if col_name in fixed_cols else 25

        # On passe None comme format pour éviter un AttributeError xlsxwriter
        # sur le paramètre d'options (comportement connu de la librairie)
        ws.set_column(col_idx, col_idx, width, None, {"hidden": True} if is_hidden else None)

        # 2. Couleur du header
        if col_name in fixed_cols:
            ws.write(0, col_idx, col_name, formats["fixed"])

        elif col_name in attr_info_map:
            status = attr_info_map[col_name]
            if status in formats:
                ws.write(0, col_idx, col_name, formats[status])
            else:
                logger.debug(f"_apply_template_styling : statut inconnu '{status}' pour la colonne '{col_name}'.")

            # 3. Menu déroulant (si l'attribut a une liste de valeurs bornées)
            if col_name in df_list_of_values.columns:
                col_values = df_list_of_values[col_name].dropna()
                if not col_values.empty:
                    list_col_letter = xl_col_to_name(df_list_of_values.columns.get_loc(col_name))
                    dropdown_range = (
                        f"ListOfValues!${list_col_letter}$2"
                        f":${list_col_letter}${len(col_values) + 1}"
                    )
                    ws.data_validation(1, col_idx, len(df_template) + 100, col_idx, {
                        "validate": "list",
                        "source": dropdown_range,
                        "error_title": "Valeur non valide",
                        "error_message": "Veuillez choisir une valeur dans la liste."
                    })

        else:
            logger.debug(
                f"_apply_template_styling : colonne '{col_name}' absente du DataInfo — "
                f"header non stylisé."
            )


def _add_styled_table(ws, df: pd.DataFrame, name: str, style: str):
    """
    Ajoute un tableau natif Excel sur la feuille donnée.
    Gère le cas d'un DataFrame vide (0 colonnes ou 0 lignes).
    """
    if df.empty or len(df.columns) == 0:
        logger.warning(f"_add_styled_table : DataFrame vide pour le tableau '{name}', aucun tableau ajouté.")
        return

    last_row = max(len(df), 1)
    ws.add_table(0, 0, last_row, len(df.columns) - 1, {
        "name": name,
        "style": style,
        "columns": [{"header": col} for col in df.columns]
    })


def build_and_export_excel(
        df_template: pd.DataFrame,
        df_datainfo: pd.DataFrame,
        df_list_of_values: pd.DataFrame,
        output_file
) -> None:
    """
    Génère le fichier Excel final avec trois onglets :
    - Template       : données produits + mise en forme métier (couleurs, dropdowns, masquage)
    - DataInfo       : mapping Label → Attribute Code utilisé lors de la réintégration
    - ListOfValues   : valeurs bornées pour les menus déroulants
    """
    logger.debug(
        f"build_and_export_excel : génération démarrée — "
        f"{len(df_template)} lignes, {len(df_template.columns)} colonnes."
    )

    engine_kwargs = {"options": {"nan_inf_to_errors": True}}

    with pd.ExcelWriter(output_file, engine="xlsxwriter", engine_kwargs=engine_kwargs) as writer:
        # Étape 1 : Écriture des données brutes
        df_template.to_excel(writer, sheet_name="Template", index=False)
        df_datainfo.to_excel(writer, sheet_name="DataInfo", index=False)
        df_list_of_values.to_excel(writer, sheet_name="ListOfValues", index=False)

        # Étape 2 : Récupération des objets workbook/worksheet
        workbook = writer.book
        ws_template = writer.sheets["Template"]
        ws_datainfo = writer.sheets["DataInfo"]
        ws_list_of_values = writer.sheets["ListOfValues"]
        formats = _get_excel_formats(workbook)

        # Étape 3 : Tableaux natifs Excel
        _add_styled_table(ws_template, df_template, "TemplateTable", "Table Style Medium 2")
        _add_styled_table(ws_datainfo, df_datainfo, "DataInfoTable", "Table Style Medium 3")
        _add_styled_table(ws_list_of_values, df_list_of_values, "ListOfValuesTable", "Table Style Medium 5")

        # Étape 4 : Mise en forme métier de l'onglet Template
        ws_template.freeze_panes(1, 0)
        _apply_template_styling(ws_template, df_template, df_datainfo, df_list_of_values, formats)

    logger.debug(
        f"build_and_export_excel : fichier généré — "
        f"{len(df_list_of_values.columns)} colonne(s) avec liste bornée."
    )

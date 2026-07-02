import io
import re
import zipfile

import pandas as pd
import streamlit as st

import api_services as api
import data_processing as proc
import excel_utils as excel
from logger_utils import get_log_context
from marketplace_config import get_channel_config


def render():
    """
    Génération par liste de SKUs : l'utilisateur colle des SKUs (toutes catégories
    confondues), l'app regroupe les produits par catégorie via la colonne catégorie
    de l'export BeezUP et génère un template par catégorie, livrés dans un ZIP.
    """
    logger = get_log_context()

    st.html("<style>.st-key-skus_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, gap="medium", key="skus_container"):
        st.subheader("🧾 Génération par liste de SKUs")

        if not st.session_state.get("catalog_id"):
            st.info("Sélectionnez d'abord une boutique ci-dessus.")
            return

        config = get_channel_config(st.session_state.store_name)

        if not config["category_column"]:
            st.warning(
                f"⚠️ La génération par SKUs n'est pas encore configurée pour le canal "
                f"**{config['sales_channel']}**. Renseignez `category_column` (le nom de la "
                f"colonne catégorie de l'export BeezUP) dans `marketplace_config.json`, "
                f"ou utilisez la génération par catégorie."
            )
            return

        catalog_id = st.session_state.catalog_id

        col_skus, col_options = st.columns([1.5, 1])

        with col_skus:
            raw_skus = st.text_area(
                "SKUs à traiter (un par ligne, toutes catégories confondues)",
                key=f"skus_gen_input_{catalog_id}",
                height=220
            )
            skus_list = list(dict.fromkeys(s.strip() for s in raw_skus.splitlines() if s.strip()))

        with col_options:
            st.markdown("**Statuts à inclure :**")
            selected_statuses = st.pills(
                "Statuts",
                label_visibility="collapsed",
                options=["Required", "Recommended", "Optional"],
                selection_mode="multi",
                default=["Required"],
                key=f"skus_gen_statuses_{catalog_id}"
            )
            st.caption(
                f"{len(skus_list)} SKU(s) saisi(s). Les attributs obligatoires du canal "
                f"sont toujours inclus, quel que soit le statut."
            )

        # Invalidation des résultats si les paramètres ont changé depuis la génération
        skus_hash = hash(tuple(skus_list))
        current_key = f"{catalog_id}_{skus_hash}_{'-'.join(sorted(selected_statuses or []))}"

        if st.session_state.get("last_skus_generation_key") not in (None, current_key):
            st.session_state.skus_generation_results = None
            st.session_state.last_skus_generation_key = None

        if st.button(
                label="Générer les templates",
                type="primary",
                width=250,
                key="skus_generate",
                icon=":material/stacks:",
                disabled=not skus_list
        ):
            results = _generate_templates(
                logger, config, catalog_id, skus_list, selected_statuses or []
            )
            st.session_state.skus_generation_results = results
            st.session_state.last_skus_generation_key = current_key if results else None

        if st.session_state.get("skus_generation_results"):
            _display_results(st.session_state.skus_generation_results)


def _generate_templates(logger, config, catalog_id, skus_list, selected_statuses):
    """
    Orchestre la génération multi-catégories. Retourne un dict
    { zip_bytes, summary, missing_skus, uncategorized_skus } ou None en cas d'échec.
    """
    client = st.session_state.client
    category_column = config["category_column"]

    try:
        with st.status("Génération des templates...", expanded=True) as status:

            # --- Export BeezUP et vérification des SKUs ---
            st.write("⬇️ Téléchargement de l'export BeezUP...")
            df_values = api.download_export_file(catalog_id)

            if category_column not in df_values.columns:
                status.update(label="Génération échouée.", state="error")
                st.error(
                    f"La colonne catégorie '{category_column}' est absente de l'export "
                    f"BeezUP. Vérifiez `marketplace_config.json` pour ce canal."
                )
                return None

            export_skus = set(df_values["sku"].astype(str))
            missing_skus = [s for s in skus_list if s not in export_skus]
            found_skus = [s for s in skus_list if s in export_skus]

            if missing_skus:
                st.write(f"⚠️ {len(missing_skus)} SKU(s) absent(s) de l'export.")

            if not found_skus:
                status.update(label="Génération échouée.", state="error")
                st.error("Aucun des SKUs saisis n'est présent dans l'export BeezUP.")
                return None

            df_values_filtered = df_values[df_values["sku"].astype(str).isin(found_skus)]

            # --- Product Ids et fusion ---
            st.write(f"🔗 Récupération des Product Ids ({len(found_skus)} SKUs)...")
            df_ids = api.get_product_ids(client, catalog_id, skus_list=found_skus)
            df_merged = proc.merge_export_data(df_ids, df_values_filtered)

            # --- Attributs (canal + toutes catégories, jointure par code) ---
            st.write("📋 Extraction des attributs...")
            df_chan = api.get_channel_attributes(client, st.session_state.channel_id)
            df_all_cat = api.get_all_channel_category_attributes(client, catalog_id)
            df_attrs = proc.normalize_attributes_dataframe(
                pd.concat([df_chan, df_all_cat], ignore_index=True)
            )

            # --- Un template par catégorie ---
            excluded = set(config["excluded_attributes"])
            obl_codes = [c for c in config["required_attributes"] if c not in excluded]

            category_codes = df_merged[category_column].apply(proc.normalize_value)
            uncategorized_skus = df_merged.loc[category_codes == "", "sku"].tolist()

            templates = []
            summary = []
            groups = [(code, grp) for code, grp in df_merged.groupby(category_codes) if code]

            for code, df_group in groups:
                category_path = proc.get_category_path_by_code(df_attrs, code)
                display_path = category_path or f"Catégorie {code}"
                st.write(f"⚙️ **{display_path}** — {len(df_group)} produit(s)...")

                df_selected = proc.select_attributes_for_category(
                    df_attrs, code, selected_statuses,
                    config["required_attributes"], config["excluded_attributes"]
                )

                if df_selected.empty:
                    logger.warning(
                        f"Génération par SKUs : aucun attribut sélectionné pour la "
                        f"catégorie '{code}' — template ignoré."
                    )
                    summary.append({
                        "Catégorie": display_path, "Produits": len(df_group),
                        "Attributs": 0, "Fichier": "— (aucun attribut)"
                    })
                    continue

                filename, file_bytes = _build_template_file(
                    client, catalog_id, df_group, df_selected,
                    category_path, code, obl_codes
                )
                templates.append((filename, file_bytes))
                summary.append({
                    "Catégorie": display_path, "Produits": len(df_group),
                    "Attributs": len(df_selected), "Fichier": filename
                })

            if not templates:
                status.update(label="Génération échouée.", state="error")
                st.error("Aucun template n'a pu être généré (aucune catégorie exploitable).")
                return None

            # --- ZIP final ---
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for filename, file_bytes in templates:
                    zf.writestr(filename, file_bytes)

            status.update(
                label=f"Génération terminée — {len(templates)} template(s).",
                state="complete"
            )

            logger.success(
                f"Génération par SKUs : {len(templates)} template(s), "
                f"{len(found_skus)} SKU(s) traités, {len(missing_skus)} absent(s), "
                f"{len(uncategorized_skus)} sans catégorie (catalog_id={catalog_id})."
            )

            return {
                "zip_bytes": zip_buffer.getvalue(),
                "summary": summary,
                "missing_skus": missing_skus,
                "uncategorized_skus": uncategorized_skus,
            }

    except ValueError as e:
        logger.warning(f"Génération par SKUs — données invalides : {e}")
        st.warning(f"Problème avec les données : {e}")
        return None

    except Exception as e:
        logger.error(
            f"Génération par SKUs — erreur (catalog_id={catalog_id}) : "
            f"{type(e).__name__}: {e}"
        )
        st.error(f"Une erreur technique est survenue : {e}")
        return None


def _build_template_file(client, catalog_id, df_group, df_selected,
                         category_path, code, obl_codes) -> tuple[str, bytes]:
    """Construit le fichier Excel d'une catégorie et retourne (nom de fichier, bytes)."""
    selected_codes = [
        c for c in df_selected["Attribute Code"].tolist() if c.lower() != "sku"
    ]
    df_filtered = proc.filter_export_columns(df_group, selected_codes)

    code_to_label = dict(zip(df_selected["Attribute Code"], df_selected["Label"]))

    df_template = proc.format_final_template(
        df_filtered, df_selected, catalog_id,
        category_path or "", code_to_label, obl_codes
    )

    df_list_of_values = api.build_dropdown_dataframe(client, catalog_id, df_selected)
    df_template = proc.normalize_export_data(df_template, df_list_of_values)
    df_template = proc.map_codes_to_labels(df_template, df_list_of_values)

    output = io.BytesIO()
    excel.build_and_export_excel(df_template, df_selected, df_list_of_values, output)

    leaf = (category_path or f"Categorie_{code}").split(" > ")[-1]
    clean_name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", leaf)
    filename = f"{st.session_state.store_name}_{clean_name} [{len(df_template)} produits].xlsx"

    return filename, output.getvalue()


def _display_results(results: dict):
    """Affiche le résumé de la génération et le bouton de téléchargement du ZIP."""
    st.space("small")

    if results["missing_skus"]:
        with st.expander(
                f"⚠️ {len(results['missing_skus'])} SKU(s) absent(s) de l'export BeezUP",
                expanded=False
        ):
            st.code("\n".join(results["missing_skus"]))

    if results["uncategorized_skus"]:
        with st.expander(
                f"⚠️ {len(results['uncategorized_skus'])} SKU(s) sans catégorie dans l'export",
                expanded=False
        ):
            st.code("\n".join(str(s) for s in results["uncategorized_skus"]))

    st.dataframe(pd.DataFrame(results["summary"]), hide_index=True, width="stretch")

    st.download_button(
        label="Télécharger les templates (ZIP)",
        data=results["zip_bytes"],
        file_name=f"{st.session_state.store_name}_templates.zip",
        mime="application/zip",
        type="primary",
        width="content",
        icon=":material/folder_zip:"
    )

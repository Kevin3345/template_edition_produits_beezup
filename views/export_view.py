import io
import re
import time

import streamlit as st

import api_services as api
import data_processing as proc
import excel_utils as excel
from logger_utils import get_log_context


def render(selected_path: str, skus_list: list, df_selected_attributes):
    """
    Orchestre la génération du template Excel et affiche l'aperçu + bouton de téléchargement.
    """
    logger = get_log_context()

    st.html("<style>.st-key-export_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, key="export_container", gap="medium"):
        st.subheader("🔎 Création du template")

        # Clé d'état unique pour détecter tout changement de paramètres.
        # On hashe les SKUs (pas leur longueur) pour éviter les faux négatifs
        # quand l'utilisateur change un SKU sans modifier le nombre total.
        skus_hash = hash(tuple(sorted(skus_list)))
        current_state_key = f"{selected_path}_{skus_hash}_{len(df_selected_attributes)}"

        if st.session_state.get("last_export_key") != current_state_key:
            st.session_state.final_excel_data = None
            st.session_state.df_preview_head = None

        if st.session_state.get("final_excel_data") is None:
            progress_bar = st.progress(0)
            status_text = st.empty()
            client = st.session_state.client
            catalog_id = st.session_state.catalog_id

            try:
                with st.spinner("Construction du template..."):

                    # Étape 1 — Product IDs
                    status_text.text("Extraction des produits...")
                    df_ids = api.get_product_ids(client, catalog_id, selected_path, skus_list=skus_list)
                    progress_bar.progress(10)

                    # Étape 2 — Export BeezUP
                    status_text.text("Téléchargement des valeurs d'attributs...")
                    df_values = api.download_export_file(catalog_id)
                    progress_bar.progress(20)

                    # Étape 3 — Fusion
                    status_text.text("Traitement des données...")
                    df_merged = proc.merge_export_data(df_ids, df_values)
                    progress_bar.progress(30)

                    # Étape 4 — Filtrage des attributs sélectionnés
                    status_text.text("Filtrage des attributs sélectionnés...")
                    selected_codes = [
                        c for c in df_selected_attributes["Attribute Code"].tolist()
                        if c.lower() != "sku"
                    ]
                    df_merged_filtered = proc.filter_export_columns(df_merged, selected_codes)
                    progress_bar.progress(40)

                    # Étape 5 — Formatage
                    status_text.text("Formatage final du fichier...")
                    code_to_label = dict(zip(
                        df_selected_attributes["Attribute Code"],
                        df_selected_attributes["Label"]
                    ))
                    obl_codes = st.session_state.get("required_attributes", [])
                    df_template = proc.format_final_template(
                        df_merged_filtered,
                        df_selected_attributes,
                        catalog_id,
                        selected_path,
                        code_to_label,
                        obl_codes
                    )
                    progress_bar.progress(50)

                    # Étape 6 — Listes de valeurs bornées
                    status_text.text("Récupération des listes déroulantes...")
                    df_list_of_values = api.build_dropdown_dataframe(client, catalog_id, df_selected_attributes)
                    progress_bar.progress(60)

                    # Étape 7 — Normalisation des types
                    status_text.text("Normalisation des données...")
                    df_template = proc.normalize_export_data(df_template, df_list_of_values)
                    progress_bar.progress(70)

                    # Étape 8 — Mapping code → label pour les listes bornées
                    status_text.text("Mapping des valeurs bornées...")
                    df_template = proc.map_codes_to_labels(df_template, df_list_of_values)
                    progress_bar.progress(80)

                    # Étape 9 — Génération du fichier Excel
                    status_text.text("Création du fichier Excel...")
                    output = io.BytesIO()
                    # df_selected_attributes joue le rôle de DataInfo dans le fichier Excel :
                    # il contient le mapping Label → Attribute Code utilisé à la réintégration.
                    excel.build_and_export_excel(df_template, df_selected_attributes, df_list_of_values, output)
                    progress_bar.progress(90)

                    # Sauvegarde en session
                    st.session_state.final_excel_data = output.getvalue()
                    st.session_state.df_preview_head = df_template.iloc[:, 3:].head(20)
                    st.session_state.last_export_key = current_state_key
                    st.session_state.total_count = len(df_template)

                    progress_bar.progress(100)
                    status_text.text("Template généré avec succès !")

                    logger.success(
                        f"Template généré : {len(df_template)} produit(s), "
                        f"{len(df_template.columns)} colonne(s) — '{selected_path}'."
                    )

                    time.sleep(1)

            except ValueError as e:
                logger.warning(f"Données invalides lors de la génération du template : {e}")
                st.warning(f"Problème avec les données : {e}")

            except Exception as e:
                logger.error(
                    f"Erreur lors de la génération du template "
                    f"(catalog_id={catalog_id}) : {type(e).__name__}: {e}"
                )
                st.error(f"Une erreur technique est survenue : {e}")

            finally:
                progress_bar.empty()
                status_text.empty()

        if st.session_state.get("df_preview_head") is not None:
            st.dataframe(st.session_state.df_preview_head, width="stretch", hide_index=False)

            last_level = selected_path.split(" > ")[-1]
            clean_name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", last_level)
            count = st.session_state.get("total_count", 0)

            st.download_button(
                label="Télécharger le template",
                data=st.session_state.final_excel_data,
                file_name=f"{st.session_state.store_name}_{clean_name} [{count} produits].xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                width="content",
                icon=":material/download:"
            )

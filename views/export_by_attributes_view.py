import io
import re

import pandas as pd
import streamlit as st

import api_services as api
import data_processing as proc
import excel_utils as excel
from logger_utils import get_log_context


def render():
    """
    Génération par attributs : l'utilisateur choisit des attributs un par un
    (sans notion de catégorie) et colle une liste de SKUs. Un template unique
    est généré, prérempli avec les valeurs de l'export BeezUP (valeurs finales
    envoyées à la marketplace, overrides inclus), au format standard v3 —
    donc réintégrable tel quel par l'onglet ÉDITER DES PRODUITS.
    """
    logger = get_log_context()

    st.html("<style>.st-key-attrs_gen_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, gap="medium", key="attrs_gen_container"):
        st.subheader("🎯 Génération par attributs")

        if not st.session_state.get("catalog_id"):
            st.info("Sélectionnez d'abord une boutique dans la barre latérale.")
            return

        catalog_id = st.session_state.catalog_id

        # Invalidation du référentiel si la boutique a changé
        if st.session_state.get("attrs_referential_catalog") != catalog_id:
            st.session_state.attrs_referential = None

        # Chargement du référentiel d'attributs à la demande (pas au rendu de
        # l'onglet : st.tabs rend tous les onglets, on évite des appels API inutiles)
        if st.session_state.get("attrs_referential") is None:
            if st.button(
                    label="Charger les attributs disponibles",
                    type="primary",
                    key="attrs_gen_load",
                    icon=":material/cloud_download:"
            ):
                with st.spinner("Chargement du référentiel d'attributs..."):
                    try:
                        _load_referential(logger, catalog_id)
                        st.rerun()
                    except Exception as e:
                        logger.error(
                            f"Échec du chargement du référentiel d'attributs "
                            f"(catalog_id={catalog_id}) : {type(e).__name__}: {e}"
                        )
                        st.error(f"Erreur lors du chargement des attributs : {e}")
            return

        df_ref = st.session_state.attrs_referential

        col_attrs, col_skus = st.columns([1.2, 1])

        with col_attrs:
            st.markdown(f"**Attributs à éditer** — {len(df_ref)} disponibles :")
            options = df_ref.to_dict(orient="records")
            selected_attrs = st.multiselect(
                "Attributs",
                label_visibility="collapsed",
                options=options,
                format_func=lambda r: f"{r['Attribute Name']} — {r['Attribute Code']}",
                key=f"attrs_gen_select_{catalog_id}"
            )

        with col_skus:
            st.markdown("**SKUs à traiter :**")
            raw_skus = st.text_area(
                "SKUs (un par ligne)",
                label_visibility="collapsed",
                key=f"attrs_gen_skus_{catalog_id}",
                height=180
            )
            skus_list = list(dict.fromkeys(s.strip() for s in raw_skus.splitlines() if s.strip()))
            st.caption(f"{len(skus_list)} SKU(s) saisi(s).")

        # Invalidation des résultats si les paramètres ont changé
        selection_hash = hash((
            tuple(skus_list),
            tuple(sorted(r["Label"] for r in selected_attrs)),
        ))
        current_key = f"{catalog_id}_{selection_hash}"

        if st.session_state.get("last_attrs_generation_key") not in (None, current_key):
            st.session_state.attrs_generation_results = None
            st.session_state.last_attrs_generation_key = None

        if st.button(
                label="Générer le template",
                type="primary",
                width=250,
                key="attrs_gen_generate",
                icon=":material/edit_document:",
                disabled=not (skus_list and selected_attrs)
        ):
            results = _generate_template(logger, catalog_id, skus_list, selected_attrs)
            st.session_state.attrs_generation_results = results
            st.session_state.last_attrs_generation_key = current_key if results else None

        if st.session_state.get("attrs_generation_results"):
            _display_results(st.session_state.attrs_generation_results)


def _load_referential(logger, catalog_id):
    """
    Construit le référentiel d'attributs : canal + toutes catégories, normalisé
    et dédoublonné. Les appels API sous-jacents sont cachés 30 minutes.
    """
    client = st.session_state.client

    df_chan = api.get_channel_attributes(client, st.session_state.channel_id)
    df_all_cat = api.get_all_channel_category_attributes(client, catalog_id)

    df_ref = proc.normalize_attributes_dataframe(
        pd.concat([df_chan, df_all_cat], ignore_index=True)
    )
    df_ref = proc.dedupe_keep_most_restrictive(df_ref)
    df_ref = df_ref.sort_values("Attribute Name").reset_index(drop=True)

    st.session_state.attrs_referential = df_ref
    st.session_state.attrs_referential_catalog = catalog_id

    logger.info(
        f"Référentiel d'attributs chargé : {len(df_ref)} attributs uniques "
        f"(catalog_id={catalog_id})."
    )


def _generate_template(logger, catalog_id, skus_list, selected_attrs):
    """
    Génère le template unique : valeurs préremplies depuis l'export BeezUP,
    format standard v3 (3 onglets, dropdowns, EAN en chaîne 13 caractères).
    Retourne { file_bytes, filename, missing_skus, nb_products } ou None.
    """
    client = st.session_state.client

    try:
        with st.status("Génération du template...", expanded=True) as status:

            # --- Export BeezUP et vérification des SKUs ---
            st.write("⬇️ Téléchargement de l'export BeezUP...")
            df_values = api.download_export_file(catalog_id)

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

            # --- Construction du template ---
            st.write("⚙️ Construction du template...")
            df_selected = pd.DataFrame(selected_attrs)

            selected_codes = [
                c for c in df_selected["Attribute Code"].tolist() if c.lower() != "sku"
            ]
            df_filtered = proc.filter_export_columns(df_merged, selected_codes)

            code_to_label = dict(zip(df_selected["Attribute Code"], df_selected["Label"]))

            df_template = proc.format_final_template(
                df_filtered, df_selected, catalog_id,
                "Multi-catégories", code_to_label, obl_codes=[]
            )

            st.write("📋 Récupération des listes de valeurs bornées...")
            df_list_of_values = api.build_dropdown_dataframe(client, catalog_id, df_selected)
            df_template = proc.normalize_export_data(df_template, df_list_of_values)
            df_template = proc.map_codes_to_labels(df_template, df_list_of_values)

            output = io.BytesIO()
            excel.build_and_export_excel(df_template, df_selected, df_list_of_values, output)

            store_name = st.session_state.store_name
            clean_store = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", store_name)
            filename = (
                f"{clean_store}_Par attributs "
                f"[{len(df_template)} produits - {len(selected_codes)} attributs].xlsx"
            )

            status.update(label="Template généré.", state="complete")

            logger.success(
                f"Génération par attributs : {len(df_template)} produit(s), "
                f"{len(selected_codes)} attribut(s), {len(missing_skus)} SKU(s) absent(s) "
                f"(catalog_id={catalog_id})."
            )

            return {
                "file_bytes": output.getvalue(),
                "filename": filename,
                "missing_skus": missing_skus,
                "nb_products": len(df_template),
            }

    except ValueError as e:
        logger.warning(f"Génération par attributs — données invalides : {e}")
        st.warning(f"Problème avec les données : {e}")
        return None

    except Exception as e:
        logger.error(
            f"Génération par attributs — erreur (catalog_id={catalog_id}) : "
            f"{type(e).__name__}: {e}"
        )
        st.error(f"Une erreur technique est survenue : {e}")
        return None


def _display_results(results: dict):
    """Affiche les SKUs absents et le bouton de téléchargement du template."""
    st.space("small")

    if results["missing_skus"]:
        with st.expander(
                f"⚠️ {len(results['missing_skus'])} SKU(s) absent(s) de l'export BeezUP",
                expanded=False
        ):
            st.code("\n".join(results["missing_skus"]))

    st.download_button(
        label=f"Télécharger le template ({results['nb_products']} produits)",
        data=results["file_bytes"],
        file_name=results["filename"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="content",
        icon=":material/download:"
    )

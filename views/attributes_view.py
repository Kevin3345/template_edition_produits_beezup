import pandas as pd
import streamlit as st

import api_services as api
import data_processing as proc
from logger_utils import get_log_context
from marketplace_config import get_channel_config


def render(selected_category_path: str):
    """
    Affiche la sélection des attributs pour la catégorie donnée.
    Recharge automatiquement si la catégorie change.
    """
    logger = get_log_context()

    # Invalidation du cache si la catégorie a changé
    if st.session_state.get("last_category") != selected_category_path:
        st.session_state.df_all_attributes = None
        st.session_state.df_selected_attributes = None
        st.session_state.last_category = selected_category_path

    st.html("<style>.st-key-attributes_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, gap="medium", key="attributes_container"):
        st.subheader("📝 Sélection des attributs")

        # 1. Chargement des attributs si nécessaire
        if st.session_state.df_all_attributes is None:
            with st.spinner(f"Extraction des attributs pour : {selected_category_path}..."):
                try:
                    df_clean = _load_attributes(
                        logger,
                        selected_category_path,
                        st.session_state.client,
                        st.session_state.catalog_id,
                        st.session_state.channel_id,
                        st.session_state.store_name
                    )
                    st.session_state.df_all_attributes = df_clean
                    logger.info(
                        f"Attributs chargés : {len(df_clean)} attributs "
                        f"pour '{selected_category_path}'."
                    )

                except Exception as e:
                    logger.error(
                        f"Échec du chargement des attributs pour '{selected_category_path}' : "
                        f"{type(e).__name__}: {e}"
                    )
                    st.error(f"Erreur lors de l'extraction des attributs : {e}")
                    return None

        # 2. Interface de filtrage
        df_attr = st.session_state.df_all_attributes
        col_source, col_status, col_select = st.columns([1, 1, 1.5])

        with col_source:
            st.markdown("**Sources :**")
            available_sources = [s for s in df_attr["Source"].unique() if s != "Obligatory"]
            selected_sources = st.pills(
                "Sources",
                label_visibility="collapsed",
                options=available_sources,
                selection_mode="multi",
                key=f"pills_src_{selected_category_path}"
            )

        with col_status:
            st.markdown("**Statuts :**")
            status_order = {"Required": 0, "Recommended": 1, "Optional": 2}
            available_statuses = sorted(
                df_attr["Status"].dropna().unique(),
                key=lambda s: status_order.get(s, 99)
            )
            selected_statuses = st.pills(
                "Statuts",
                label_visibility="collapsed",
                options=available_statuses,
                selection_mode="multi",
                default=["Required"],
                key=f"pills_stat_{selected_category_path}"
            )

        # 3. Calcul de la sélection finale
        df_obligatory = df_attr[df_attr["Source"] == "Obligatory"]

        mask = (
                df_attr["Source"].isin(selected_sources or []) &
                df_attr["Status"].isin(selected_statuses or [])
        )
        df_filtered = df_attr[mask]
        current_selection = pd.concat([df_obligatory, df_filtered]).drop_duplicates(subset=["Label"])

        remaining_attr = df_attr[~df_attr["Label"].isin(current_selection["Label"])]

        with col_select:
            st.markdown("**Sélection manuelle :**")
            extra_options = remaining_attr.to_dict(orient="records")
            selected_extra = st.multiselect(
                "Attributs non sélectionnés",
                label_visibility="collapsed",
                options=extra_options,
                format_func=lambda r: f"{r['Attribute Name']} | {r['Source']}",
                key=f"extra_{selected_category_path}"
            )

        if selected_extra:
            df_extra = pd.DataFrame(selected_extra)
            final_selection = pd.concat([current_selection, df_extra]).drop_duplicates(subset=["Label"])
        else:
            final_selection = current_selection

        # 4. Résumé de la sélection
        with st.expander(f"Voir le détail des **{len(final_selection)} attributs sélectionnés**"):
            st.dataframe(
                final_selection[["Attribute Name", "Status", "Source"]].sort_values("Attribute Name"),
                hide_index=True,
                width="stretch"
            )

        if st.button(
                label="Valider la sélection",
                type="primary",
                width=200,
                key="attributes_selection",
                icon=":material/check:"
        ):
            st.session_state.df_selected_attributes = final_selection
            nb_obl = len(final_selection[final_selection["Source"] == "Obligatory"])
            nb_other = len(final_selection) - nb_obl
            logger.info(
                f"Sélection d'attributs validée : {len(final_selection)} attributs "
                f"({nb_obl} obligatoires, {nb_other} additionnels)."
            )

        if st.session_state.df_selected_attributes is not None:
            return st.session_state.df_selected_attributes

        return None


def _load_attributes(
        logger,
        selected_category_path: str,
        client,
        catalog_id: str,
        channel_id: str,
        store_name: str
) -> pd.DataFrame:
    """
    Charge et prépare le DataFrame complet des attributs disponibles pour une catégorie.
    Extrait séparément : attributs canal, attributs catégorie, mapping colonnes, attributs obligatoires.
    """
    # Attributs canal et catégorie
    df_chan = api.get_channel_attributes(client, channel_id)
    df_cat = api.get_channel_category_attributes(client, catalog_id, selected_category_path)
    df_concat = pd.concat([df_chan, df_cat], ignore_index=True)
    df_concat = proc.normalize_attributes_dataframe(df_concat)

    # Dédoublonnage + colonne Label
    df_clean = proc.dedupe_keep_most_restrictive(df_concat)

    # Colonne Is Mapped
    mapping_dict = api.get_column_mapping_dict(client, catalog_id)
    df_clean["Is Mapped"] = df_clean["Channel Attribute Id"].apply(
        lambda x: x in mapping_dict and mapping_dict[x] is not None
    )

    # Attributs obligatoires depuis la configuration marketplace
    channel_config = get_channel_config(store_name)
    required_attributes_clean = channel_config["required_attributes"]

    if not required_attributes_clean:
        logger.warning(
            f"Aucun attribut obligatoire trouvé pour le canal '{channel_config['sales_channel']}' "
            f"(store_name='{store_name}'). Vérifiez marketplace_config.json."
        )

    st.session_state.required_attributes = required_attributes_clean

    mask_obl = df_clean["Attribute Code"].isin(required_attributes_clean)
    df_clean.loc[mask_obl, "Source"] = "Obligatory"

    # Ordre des colonnes
    desired_order = [
        "Source", "Channel Category Path", "Label", "Attribute Name",
        "Attribute Code", "Channel Attribute Id", "Status", "Type Value",
        "Default Value", "Attribute Value List Code", "Attribute Description", "Is Mapped"
    ]
    return df_clean[desired_order]

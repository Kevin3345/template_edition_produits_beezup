import streamlit as st

import api_services as api
import data_processing as proc
from logger_utils import get_log_context


def render():
    """Affiche la sélection des catégories avec cascade dynamique et filtrage SKU optionnel."""

    logger = get_log_context()

    st.html("<style>.st-key-category_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, gap="medium", key="category_container"):
        st.subheader("🔖 Sélection de la catégorie")

        # 1. Chargement des catégories si nécessaire (premier rendu ou changement de catalogue)
        current_catalog = st.session_state.get("catalog_id")
        last_catalog = st.session_state.get("last_catalog_id")

        if st.session_state.get("df_available_categories") is None or current_catalog != last_catalog:
            with st.spinner("Chargement des catégories mappées..."):
                try:
                    df_cat = api.get_catalog_categories(
                        st.session_state.client,
                        st.session_state.store_id
                    )
                    df_map = api.get_category_mapping(
                        st.session_state.client,
                        st.session_state.catalog_id
                    )

                    df_available = proc.get_available_categories(df_cat, df_map)
                    st.session_state.df_available_categories = df_available
                    st.session_state.last_catalog_id = current_catalog

                    logger.info(
                        f"Catégories chargées : {len(df_available)} catégorie(s) mappée(s) "
                        f"(catalog_id={current_catalog})."
                    )

                except Exception as e:
                    logger.error(
                        f"Échec du chargement des catégories "
                        f"(catalog_id={current_catalog}) : {type(e).__name__}: {e}"
                    )
                    st.error(f"Erreur lors du chargement des catégories : {e}")
                    return

        # 2. Vérification que le DataFrame est disponible et non vide
        df_categories = st.session_state.df_available_categories

        if df_categories is None or df_categories.empty:
            st.warning("Aucune catégorie disponible ou mappée pour ce catalogue.")
            return

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Hiérarchie de la catégorie :**")

            # Navigation en cascade : chaque niveau filtre les options du suivant
            paths_list = df_categories["Channel Category Path"].str.split(" > ").tolist()
            selected_path = []
            level = 0

            while True:
                candidates = [
                    path[level]
                    for path in paths_list
                    if len(path) > level and path[:level] == selected_path
                ]
                options = sorted(set(candidates))
                if not options:
                    break

                choice = st.selectbox(
                    f"Niveau {level + 1}",
                    options=options,
                    index=0,
                    key=f"cat_lvl_{level}_{st.session_state.catalog_id}",
                    disabled=(len(options) == 1)
                )
                selected_path.append(choice)
                level += 1

        with col2:
            st.markdown("**Filtrage par SKUs (optionnel) :**")
            raw_skus = st.text_area(
                "Saisissez une liste de SKUs (un par ligne)",
                key=f"skus_list_{st.session_state.catalog_id}"
            )
            skus_list = [sku.strip() for sku in raw_skus.splitlines() if sku.strip()]

        # 3. Résumé et validation
        full_path_str = " > ".join(selected_path)
        row = df_categories[df_categories["Channel Category Path"] == full_path_str]

        validate_disabled = True

        if not row.empty:
            count = int(row.iloc[0]["Total Product Count"])
            is_limit_exceeded = count > 1000
            badge_color = "green" if not is_limit_exceeded else "red"

            st.markdown(f"**Sélection :** {full_path_str} :{badge_color}-badge[{count} produits]")

            if is_limit_exceeded and not skus_list:
                st.warning(
                    "⚠️ Vous vous apprêtez à traiter un grand volume de produits. "
                    "Afin de réduire l'impact sur BeezUP, indiquez une liste de SKUs à traiter."
                )
            else:
                validate_disabled = False
        else:
            st.info("Veuillez sélectionner une catégorie valide.")

        if st.button(
                "Valider la catégorie",
                disabled=validate_disabled,
                type="primary",
                width=200,
                key="category_selection",
                icon=":material/check:"
        ):
            st.session_state.selected_category = full_path_str
            st.session_state.selected_skus = skus_list

            logger.info(
                f"Catégorie validée : '{full_path_str}' "
                f"({'tous les produits' if not skus_list else f'{len(skus_list)} SKU(s) filtrés'})."
            )

        if st.session_state.get("selected_category"):
            return st.session_state.selected_category, st.session_state.get("selected_skus", [])

        return None

import streamlit as st

import api_services as api
from logger_utils import get_log_context


def render():
    """
    Gère la saisie du Catalog ID et affiche la boutique sélectionnée.
    Rendu dans la sidebar : la boutique est un état de session, partagé
    par les onglets de génération.
    """

    logger = get_log_context()

    st.html("<style>.st-key-settings_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, gap="medium", key="settings_container"):
        st.markdown("**🛍️ Boutique**")

        # Cas 1 : Catalog ID pas encore renseigné → formulaire de saisie
        if not st.session_state.get("catalog_id"):
            catalog_input = st.text_input("Saisissez le Channel Catalog ID :", key="input_catalog_id")

            if st.button("Valider", type="primary", width="stretch", key="store_selection", icon=":material/check:"):
                catalog_id = catalog_input.strip()

                if not catalog_id:
                    st.warning("Veuillez entrer un ID.")
                    return

                logger.info(f"Chargement du catalogue : {catalog_id}")

                with st.spinner("Chargement du catalogue vendeur..."):
                    try:
                        infos = api.get_catalog_infos(st.session_state.client, catalog_id)

                        st.session_state.catalog_id = catalog_id
                        st.session_state.store_id = infos["storeId"]
                        st.session_state.channel_id = infos["channelId"]
                        st.session_state.store_name = api.get_store_name(
                            st.session_state.client, infos["storeId"]
                        )

                        # Rechargement du contexte pour inclure le nom de boutique dans le log
                        logger = get_log_context()
                        logger.success(f"Boutique chargée : {st.session_state.store_name} (catalog_id={catalog_id})")

                        st.rerun()

                    except ValueError as e:
                        logger.warning(f"Catalog ID invalide : {catalog_id} — {e}")
                        st.warning(f"Channel Catalog ID invalide : {e}")

                    except Exception as e:
                        logger.error(f"Erreur lors du chargement du catalogue {catalog_id} : {type(e).__name__}: {e}")
                        st.error(f"Erreur lors du chargement du catalogue. Vérifiez l'ID et votre connexion.")

        # Cas 2 : Catalogue déjà chargé → affichage du résumé
        else:
            st.write(f":orange[{st.session_state.store_name}]")
            st.caption(st.session_state.catalog_id)

# Valide les certificats via le magasin de l'OS (proxy d'entreprise avec inspection
# SSL sur Windows) au lieu du bundle certifi. À faire avant tout import réseau.
try:
    import truststore
except ModuleNotFoundError:
    import sys

    raise ModuleNotFoundError(
        f"truststore est introuvable dans l'environnement Python utilisé :\n"
        f"  {sys.executable}\n"
        f"L'app a probablement été lancée avec un streamlit hors du venv du projet.\n"
        f"Lancez-la avec : uv run streamlit run app.py (ou run.bat)"
    ) from None

truststore.inject_into_ssl()

import streamlit as st

import session_manager as sm
from logger_utils import get_log_context, setup_logging
from views import (
    attributes_view,
    category_view,
    export_by_skus_view,
    export_view,
    import_view,
    login_view,
    settings_view,
)

# Configuration de la page
st.set_page_config(
    page_title="ShadBeez \u22EE \u0190dition \u00FEroduits v\u01B7\u00B9",
    layout="wide",
    page_icon="🐝"
)

# Initialisation du logger — une seule fois par session, pas à chaque re-render
if "logger_initialized" not in st.session_state:
    setup_logging()
    st.session_state.logger_initialized = True
    get_log_context().info("Application démarrée.")


def main():
    sm.init_session_state()

    if not st.session_state.authenticated:
        login_view.render()
        return

    logger = get_log_context()

    # --- Sidebar ---
    with st.sidebar:
        st.title("🌚 Gestion de session")

        name = st.session_state.user_info.get("firstName") or "l'ami"
        st.markdown(
            f"*\u201FHey :orange[{name}] \u203C \nComment y va çui-ci \u2047\u201D*",
            unsafe_allow_html=True
        )

        st.space("xxsmall")

        st.html("<style>.st-key-reset { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")
        if st.button("Réinitialiser", type="secondary", width="stretch", key="reset",
                     icon=":material/refresh:"):
            sm.reset_to_new_template()
            st.rerun()

        st.space("xxsmall")

        st.html("<style>.st-key-switch { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")
        if st.button("Changer de boutique", type="secondary", width="stretch", key="switch",
                     icon=":material/swap_horiz:", disabled=not st.session_state.get("catalog_id")):
            sm.reset_to_new_catalog()
            st.rerun()

        st.space("xxsmall")

        st.html("<style>.st-key-logout { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")
        if st.button("Déconnexion", type="secondary", width="stretch", key="logout",
                     icon=":material/logout:"):
            # Capturer le nom avant clear() pour pouvoir le logger
            user_name = st.session_state.user_info.get("firstName") or "inconnu"
            logger.info(f"Déconnexion : {user_name}")
            st.session_state.clear()
            st.rerun()

        st.space("small")
        st.caption("Fait avec 💕 par ShadBeez")

    # --- S\u00E9lection de la boutique (commune aux deux workflows de g\u00E9n\u00E9ration) ---
    settings_view.render()

    st.space("small")

    # --- Onglets principaux ---
    tab1, tab2, tab3 = st.tabs([
        "G\u00C9N\u00C9RER PAR CAT\u00C9GORIE",
        "G\u00C9N\u00C9RER PAR SKUS",
        "\u00C9DITER DES PRODUITS"
    ])

    with tab1:
        st.space("small")

        if st.session_state.catalog_id:
            category_data = category_view.render()

            if category_data:
                full_path_str, sku_list = category_data
                st.space("small")

                df_attr = attributes_view.render(full_path_str)

                if df_attr is not None:
                    st.space("small")
                    export_view.render(full_path_str, sku_list, df_attr)
        else:
            st.info("S\u00E9lectionnez une boutique pour commencer.")

    with tab2:
        st.space("small")
        export_by_skus_view.render()

    with tab3:
        st.space("small")
        import_view.render()


if __name__ == "__main__":
    main()

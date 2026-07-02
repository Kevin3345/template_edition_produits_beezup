import streamlit as st
from loguru import logger


def get_defaults() -> dict:
    """
    Centralise les valeurs par défaut de toutes les clés du session_state.
    Toute nouvelle clé persistante doit être déclarée ici pour être correctement
    initialisée et réinitialisée par les fonctions de reset.
    """
    return {
        # Auth
        "logger_initialized": False,
        "authenticated": False,
        "client": None,
        "user_info": {},

        # Boutique
        "catalog_id": None,
        "last_catalog_id": None,
        "store_name": "NoStore",
        "store_id": None,
        "channel_id": None,

        # Catégorie & attributs
        "df_available_categories": None,
        "selected_category": None,
        "selected_skus": [],
        "required_attributes": [],
        "df_all_attributes": None,
        "df_selected_attributes": None,
        "last_category": None,

        # Export (génération de template)
        "final_excel_data": None,
        "df_preview_head": None,
        "last_export_key": None,
        "total_count": None,

        # Export par SKUs (génération multi-catégories)
        "skus_generation_results": None,
        "last_skus_generation_key": None,

        # Import (réintégration)
        "import_results": None,
        "import_fingerprint": None,
    }


def init_session_state():
    """Initialise les clés manquantes au démarrage de l'application."""
    defaults = get_defaults()
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_to_new_template():
    """
    Réinitialise les sélections liées au template en cours
    tout en conservant la boutique sélectionnée et la session.
    """
    defaults = get_defaults()
    keep_keys = {
        "logger_initialized", "authenticated", "client", "user_info",
        "catalog_id", "last_catalog_id", "store_name", "store_id", "channel_id"
    }

    reset_keys = [k for k in defaults if k not in keep_keys]
    for key in reset_keys:
        st.session_state[key] = defaults[key]

    logger.info(f"Session réinitialisée (nouveau template) : {len(reset_keys)} clé(s) remises à zéro.")


def reset_to_new_catalog():
    """
    Réinitialise tous les paramètres liés à la boutique et au template
    tout en conservant la session utilisateur.
    Vide également le cache Streamlit.
    """
    st.cache_data.clear()

    defaults = get_defaults()
    keep_keys = {"logger_initialized", "authenticated", "client", "user_info"}

    reset_keys = [k for k in defaults if k not in keep_keys]
    for key in reset_keys:
        st.session_state[key] = defaults[key]

    logger.info(f"Session réinitialisée (nouveau catalogue) : {len(reset_keys)} clé(s) remises à zéro.")

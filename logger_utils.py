import sys

import streamlit as st
from loguru import logger


def setup_logging():
    """
    Initialise le logger global de l'application.
    - Console (stdout) : niveau INFO
    - Fichier log.txt  : niveau DEBUG, rotation 10 MB, 5 fichiers max conservés

    Doit être appelé une seule fois au démarrage (gardé par 'logger_initialized'
    dans le session_state de app.py).
    """
    # Suppression du handler par défaut de loguru pour éviter les doublons
    logger.remove()

    # Valeurs par défaut des champs contextuels utilisés dans le format
    # (évite une KeyError si un module logue sans avoir appelé get_log_context)
    logger.configure(extra={"user": "System", "store": "App"})

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[user]}</cyan> @ <magenta>{extra[store]}</magenta> | "
        "<level>{message}</level>"
    )

    logger.add(sys.stdout, format=log_format, level="INFO")
    logger.add(
        "log.txt",
        format=log_format,
        level="DEBUG",
        rotation="10 MB",
        retention=5,
        compression="zip",
        encoding="utf-8",
    )

    logger.info("Logger initialisé — console : INFO | fichier : DEBUG (rotation 10 MB, 5 fichiers max).")


def get_log_context():
    """
    Retourne un logger enrichi avec le contexte utilisateur actuel (prénom + boutique).
    Lit les valeurs depuis st.session_state.

    ⚠️ Cette fonction nécessite un contexte d'exécution Streamlit actif.
    Ne pas appeler au niveau module ou hors d'une fonction déclenchée par Streamlit.
    """
    user_info = st.session_state.get("user_info", {})
    user_name = (user_info.get("firstName") or "Guest") if user_info else "Guest"

    store_name = st.session_state.get("store_name") or "NoStore"

    return logger.bind(user=user_name, store=store_name)

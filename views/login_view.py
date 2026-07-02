import streamlit as st

import api_services as api
from beezup_client import AuthenticationError, BeezUPClient
from logger_utils import get_log_context


def render():
    """Affiche l'interface de connexion BeezUP."""

    logger = get_log_context()

    st.html("<style>.st-key-login_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); } </style>")

    _, col2, _ = st.columns([1, 2, 1])

    with col2:
        st.space("medium")

        with st.container(key="login_container"):
            with st.form(key="login_form"):
                st.subheader(":honeybee: :grey[ShadBeez \u22EE] :orange[\u0190dition \u00FEroduits v\u0427]")
                st.space("xxsmall")

                email = st.text_input("Email")
                password = st.text_input("Mot de passe", type="password")
                st.space("xxsmall")

                submit_button = st.form_submit_button(
                    "Connexion",
                    type="primary",
                    width=200,
                    icon=":material/login:"
                )

                if submit_button:
                    if not email or not password:
                        st.warning("Veuillez remplir tous les champs.")
                        return

                    logger.info(f"Tentative de connexion : {email}")

                    with st.spinner("Authentification en cours..."):
                        try:
                            client = BeezUPClient(email, password)
                            client.authenticate()

                            st.session_state.client = client
                            st.session_state.user_info = api.get_user_identity(client)
                            st.session_state.authenticated = True

                            # On recharge le contexte pour inclure le prénom dans le log
                            logger = get_log_context()
                            first_name = st.session_state.user_info.get("firstName", "?")
                            logger.success(f"Connexion réussie : {first_name} ({email})")

                            st.rerun()

                        except AuthenticationError:
                            logger.warning(f"Échec d'authentification : identifiants invalides pour {email}.")
                            st.error("Identifiants incorrects. Vérifiez votre email et mot de passe.")

                        except Exception as e:
                            logger.error(
                                f"Erreur inattendue lors de la connexion pour {email} : {type(e).__name__}: {e}")
                            st.error(f"Erreur de connexion : {e}")

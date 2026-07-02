from typing import Any
from urllib.parse import urljoin

import requests
from loguru import logger
from requests.exceptions import HTTPError, Timeout
from tenacity import retry, retry_if_exception, stop_after_attempt

# Le client ne connaît pas le contexte Streamlit (session_state),
# il logue toujours avec ces valeurs fixes.
logger = logger.bind(user="System", store="BeezUP API")

# Statuts transitoires : le serveur n'a rien appliqué, la requête peut être retentée
_RETRYABLE_STATUS = {429, 503}
_MAX_ATTEMPTS = 5


def _is_retryable_error(exc: BaseException) -> bool:
    return (
        isinstance(exc, HTTPError)
        and exc.response is not None
        and exc.response.status_code in _RETRYABLE_STATUS
    )


def _wait_before_retry(retry_state) -> float:
    """Respecte le header Retry-After si présent, sinon backoff exponentiel (2, 4, 8... max 30 s)."""
    exc = retry_state.outcome.exception()
    retry_after = exc.response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(float(retry_after), 60.0)
    return min(2.0 ** retry_state.attempt_number, 30.0)


def _log_before_retry(retry_state):
    exc = retry_state.outcome.exception()
    wait_s = retry_state.next_action.sleep
    logger.warning(
        f"HTTP {exc.response.status_code} reçu — tentative {retry_state.attempt_number}/{_MAX_ATTEMPTS} "
        f"échouée, nouvel essai dans {wait_s:.1f}s."
    )


class AuthenticationError(Exception):
    """Levée quand l'authentification BeezUP échoue."""
    pass


class BeezUPClient:
    def __init__(self, login, password):
        self.base_url = "https://api.beezup.com"
        self.session = requests.Session()
        self.login = login
        self.password = password
        self.token = None

    def _build_url(self, endpoint: str) -> str:
        """Fusionne proprement la base URL et l'endpoint."""
        path = endpoint.lstrip("/")
        return urljoin(self.base_url + "/", path)

    def authenticate(self) -> bool:
        """Récupère le token et l'injecte dans la session."""
        logger.info(f"Tentative d'authentification : {self.login}")

        url = self._build_url("v2/public/security/login")
        payload = {
            "login": self.login,
            "password": self.password
        }

        data = self._request("POST", url, json=payload)

        if data and "credentials" in data:
            credentials = data["credentials"]
            self.token = next(
                (c.get("primaryToken") for c in credentials if c.get("primaryToken")),
                None
            )

            if self.token:
                self.session.headers.update({
                    "Content-Type": "application/json",
                    "X-BeezUP-Decrypted-Expression": "true",
                    "Ocp-Apim-Subscription-Key": self.token
                })

                # Le mot de passe ne sert qu'au login : on ne le garde pas en mémoire de session
                self.password = None

                logger.success(f"Authentification réussie : {self.login}")
                return True

        msg = f"Impossible de récupérer le Primary Token pour : {self.login}"
        logger.error(msg)
        raise AuthenticationError(msg)

    @retry(
        retry=retry_if_exception(_is_retryable_error),
        wait=_wait_before_retry,
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        before_sleep=_log_before_retry,
        reraise=True,
    )
    def _send(self, method: str, url: str, **kwargs) -> requests.Response:
        """Envoie la requête ; les statuts transitoires (429, 503) sont retentés automatiquement."""
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def _request(self, method: str, url: str, **kwargs) -> Any:
        """Méthode privée qui centralise les appels HTTP et la gestion d'erreurs."""
        try:
            kwargs.setdefault("timeout", 30)

            response = self._send(method, url, **kwargs)

            # 204 No Content → succès sans corps de réponse
            if response.status_code == 204:
                logger.debug(f"[{method}] {url} → 204 No Content.")
                return True

            # Succès avec corps de réponse
            if response.content:
                logger.debug(
                    f"[{method}] {url} → {response.status_code} OK "
                    f"({len(response.content)} bytes)."
                )
                return response.json()

            # Succès, corps vide (hors 204)
            logger.debug(f"[{method}] {url} → {response.status_code} OK (body vide).")
            return True

        except Timeout:
            timeout = kwargs.get("timeout", 30)
            logger.error(f"[{method}] {url} → Timeout après {timeout}s.")
            raise

        except requests.exceptions.ConnectionError:
            logger.error(f"[{method}] {url} → Impossible de joindre le serveur.")
            raise

        except requests.exceptions.TooManyRedirects:
            logger.error(f"[{method}] {url} → Trop de redirections.")
            raise

        except HTTPError as e:
            status = e.response.status_code
            # On tronque le body de la réponse pour ne pas polluer les logs,
            # mais on le conserve car BeezUP y glisse souvent un message d'erreur utile.
            body = e.response.text[:300].strip() if e.response.text else "vide"

            if 300 <= status < 400:
                location = e.response.headers.get("Location", "inconnue")
                logger.warning(f"[{method}] {url} → Redirection {status} vers {location}.")
                raise

            if 400 <= status < 500:
                match status:
                    case 400:
                        logger.error(f"[{method}] {url} → Requête invalide (400). Body : {body}")
                    case 401:
                        logger.error(f"[{method}] {url} → Non autorisé (401). Token expiré ?")
                    case 403:
                        logger.error(f"[{method}] {url} → Accès refusé (403). Body : {body}")
                    case 404:
                        logger.error(f"[{method}] {url} → Ressource introuvable (404).")
                    case 429:
                        logger.warning(
                            f"[{method}] {url} → Rate limit persistant (429) "
                            f"après {_MAX_ATTEMPTS} tentatives."
                        )
                    case _:
                        logger.error(f"[{method}] {url} → Erreur client {status}. Body : {body}")
                raise

            if 500 <= status < 600:
                match status:
                    case 500:
                        logger.error(f"[{method}] {url} → Erreur interne serveur (500). Body : {body}")
                    case 503:
                        logger.error(
                            f"[{method}] {url} → Service indisponible (503) "
                            f"après {_MAX_ATTEMPTS} tentatives."
                        )
                    case _:
                        logger.error(f"[{method}] {url} → Erreur serveur {status}. Body : {body}")
                raise

            raise

        except requests.exceptions.JSONDecodeError:
            logger.error(f"[{method}] {url} → Réponse non parseable en JSON.")
            raise

        except Exception as e:
            logger.critical(f"[{method}] {url} → Erreur inattendue | {type(e).__name__}: {e}.")
            raise

    # -------------------------------------------------------------------------
    # Méthodes HTTP publiques
    # -------------------------------------------------------------------------

    def get(self, endpoint: str, params: dict = None) -> Any:
        url = self._build_url(endpoint)
        return self._request("GET", url, params=params)

    def post(self, endpoint: str, data=None, json=None, params=None) -> Any:
        url = self._build_url(endpoint)
        return self._request("POST", url, data=data, json=json, params=params)

    def put(self, endpoint: str, data=None, json=None) -> Any:
        url = self._build_url(endpoint)
        return self._request("PUT", url, data=data, json=json)

    def delete(self, endpoint: str) -> Any:
        url = self._build_url(endpoint)
        return self._request("DELETE", url)

    # -------------------------------------------------------------------------
    # Appels API BeezUP
    # -------------------------------------------------------------------------

    def get_user_account_information(self) -> Any:
        return self.get("v2/user/customer/account")

    def get_channel_catalog_information(self, catalog_id: str) -> Any:
        return self.get(f"v2/user/channelCatalogs/{catalog_id}")

    def get_marketplace_channel_catalog_list(self, store_id: str) -> Any:
        return self.get("v2/user/marketplaces/channelcatalogs/", params={"storeId": store_id})

    def get_channel_columns(self, channel_id: str, columns_list: list = None) -> Any:
        payload = columns_list if columns_list is not None else []
        return self.post(f"v2/user/channels/{channel_id}/columns", json=payload)

    def get_concerned_channel_catalog_attributes(self, catalog_id: str) -> Any:
        return self.get(f"v2/user/channelCatalogs/{catalog_id}/attributes")

    def get_channel_catalog_product_information_list(self, catalog_id: str, payload: dict) -> Any:
        return self.post(f"v2/user/channelCatalogs/{catalog_id}/products", json=payload)

    def get_channel_catalog_attribute_value_mapping(self, catalog_id: str, attribute_id: str) -> Any:
        return self.get(f"v2/user/channelCatalogs/{catalog_id}/attributes/{attribute_id}/mapping")

    def get_custom_column_list(self, store_id: str) -> Any:
        return self.get(f"v2/user/catalogs/{store_id}/customColumns")

    def create_or_replace_decrypted_custom_column(self, store_id: str, column_id: str, body: dict) -> Any:
        return self.put(f"v2/user/catalogs/{store_id}/customColumns/{column_id}/decrypted", json=body)

    def configure_channel_catalog_column_mappings(self, catalog_id: str, mapping_list: list) -> Any:
        return self.put(f"v2/user/channelCatalogs/{catalog_id}/columnMappings", json=mapping_list)

    def override_channel_catalog_product_values(self, catalog_id: str, product_id: str, overrides: dict) -> Any:
        return self.put(
            f"v2/user/channelCatalogs/{catalog_id}/products/{product_id}/overrides",
            json=overrides
        )

    def delete_channel_catalog_product_override(self, catalog_id: str, product_id: str, attribute_id: str) -> Any:
        return self.delete(
            f"v2/user/channelCatalogs/{catalog_id}/products/{product_id}/overrides/{attribute_id}"
        )

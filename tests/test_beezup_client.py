from unittest.mock import MagicMock

import pytest
import requests
from requests.exceptions import HTTPError

from beezup_client import _MAX_ATTEMPTS, AuthenticationError, BeezUPClient


def fake_response(status: int, headers: dict = None, body: bytes = b'{"ok": true}'):
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    r._content = body
    r.url = "https://api.beezup.com/test"
    return r


def make_client(responses: list) -> BeezUPClient:
    client = BeezUPClient("test@test", "pwd")
    client.session = MagicMock()
    client.session.request = MagicMock(side_effect=responses)
    return client


# Retry-After: 0 permet de tester le retry sans attendre le backoff exponentiel
RETRY_NOW = {"Retry-After": "0"}


# ---------------------------------------------------------------------------
# Retry sur statuts transitoires
# ---------------------------------------------------------------------------

def test_429_is_retried_until_success():
    client = make_client([fake_response(429, RETRY_NOW),
                          fake_response(429, RETRY_NOW),
                          fake_response(200)])
    result = client.get("v2/test")

    assert result == {"ok": True}
    assert client.session.request.call_count == 3


def test_503_is_retried():
    client = make_client([fake_response(503, RETRY_NOW), fake_response(200)])

    assert client.get("v2/test") == {"ok": True}
    assert client.session.request.call_count == 2


def test_429_gives_up_after_max_attempts():
    client = make_client([fake_response(429, RETRY_NOW)] * _MAX_ATTEMPTS)

    with pytest.raises(HTTPError):
        client.get("v2/test")
    assert client.session.request.call_count == _MAX_ATTEMPTS


def test_400_is_not_retried():
    client = make_client([fake_response(400, body=b'{"error": "bad"}')])

    with pytest.raises(HTTPError):
        client.get("v2/test")
    assert client.session.request.call_count == 1


def test_404_is_not_retried():
    client = make_client([fake_response(404)])

    with pytest.raises(HTTPError):
        client.get("v2/test")
    assert client.session.request.call_count == 1


# ---------------------------------------------------------------------------
# Réponses sans corps
# ---------------------------------------------------------------------------

def test_204_returns_true():
    client = make_client([fake_response(204, body=b"")])
    assert client.get("v2/test") is True


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------

def test_authenticate_sets_token_and_clears_password():
    body = b'{"credentials": [{"primaryToken": "tok-123"}]}'
    client = make_client([fake_response(200, body=body)])

    assert client.authenticate() is True
    assert client.token == "tok-123"
    client.session.headers.update.assert_called_once()
    headers = client.session.headers.update.call_args[0][0]
    assert headers["Ocp-Apim-Subscription-Key"] == "tok-123"
    # Le mot de passe ne doit plus être en mémoire après le login
    assert client.password is None


def test_authenticate_without_token_raises():
    body = b'{"credentials": []}'
    client = make_client([fake_response(200, body=body)])

    with pytest.raises(AuthenticationError):
        client.authenticate()
    assert client.password == "pwd"  # pas de nettoyage si l'auth échoue

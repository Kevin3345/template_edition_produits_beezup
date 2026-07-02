import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


class FakeClient:
    """Client factice : retourne des réponses préenregistrées par suffixe d'endpoint."""

    def __init__(self, responses: dict):
        # responses : { suffixe d'endpoint : payload }
        self._responses = responses
        self.calls = []

    def get(self, endpoint: str, params: dict = None):
        self.calls.append(("GET", endpoint))
        for suffix, payload in self._responses.items():
            if endpoint.endswith(suffix):
                return payload
        raise AssertionError(f"Endpoint inattendu dans le test : {endpoint}")


@pytest.fixture
def fake_client():
    return FakeClient


@pytest.fixture(scope="session")
def cultura_attributes():
    return load_fixture("cultura_attributes.json")


@pytest.fixture(scope="session")
def cultura_mapping_paths():
    mapping = load_fixture("cultura_categories.json")
    return sorted({
        " > ".join(m["channelCategoryPath"])
        for m in mapping["channelCatalogCategoryConfigurations"]
        if m.get("channelCategoryPath")
    })


@pytest.fixture(scope="session")
def bricodepot_attributes():
    return load_fixture("bricodepot_attributes.json")


@pytest.fixture(scope="session")
def bricodepot_mapping_paths():
    mapping = load_fixture("bricodepot_categories.json")
    return sorted({
        " > ".join(m["channelCategoryPath"])
        for m in mapping["channelCatalogCategoryConfigurations"]
        if m.get("channelCategoryPath")
    })

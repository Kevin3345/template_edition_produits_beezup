import pytest

import api_services as api
from api_services import _detect_csv_separator, _is_selected_category, _parse_json_export


# ---------------------------------------------------------------------------
# Détection du séparateur CSV
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first_line, expected", [
    ("sku;ean;color", ";"),
    ("sku,ean,color", ","),
    ("sku\tean\tcolor", "\t"),
    ("sku;ean;color,avec,virgules;autre", ";"),
])
def test_detect_csv_separator(first_line, expected):
    assert _detect_csv_separator(first_line) == expected


# ---------------------------------------------------------------------------
# Parsing de l'export JSON déguisé en CSV
# ---------------------------------------------------------------------------

def test_parse_json_export_flattens_properties():
    text = """[
      {"sku": "S1", "messageType": "publish",
       "properties": [{"key": "sku", "value": "S1"}, {"key": "ean", "value": "0657419692298"}]},
      {"sku": "S2", "messageType": "publish",
       "properties": [{"key": "sku", "value": "S2"}, {"key": "color", "value": "rouge"}]}
    ]"""
    df = _parse_json_export(text, "cat-test")

    assert len(df) == 2
    assert set(df.columns) == {"sku", "ean", "color"}
    assert df.iloc[0]["ean"] == "0657419692298"  # zéro de tête préservé (str, pas float)

def test_parse_json_export_invalid_json_raises():
    with pytest.raises(ValueError, match="parseable"):
        _parse_json_export("[{invalide", "cat-test")


def test_parse_json_export_non_list_raises():
    with pytest.raises(ValueError, match="liste"):
        _parse_json_export('{"pas": "une liste"}', "cat-test")


# ---------------------------------------------------------------------------
# _is_selected_category — correctif Cultura (channelFullCategoryPath tronqué)
# ---------------------------------------------------------------------------

class TestIsSelectedCategory:
    def test_exact_match(self):
        assert _is_selected_category("A > B > C", "A > B > C", "C")

    def test_one_missing_level(self):
        assert _is_selected_category("A > B > C", "A > B", "C")

    def test_two_missing_levels(self):
        assert _is_selected_category("A > B > C > D", "A > B", "D")

    def test_leaf_mismatch_is_rejected(self):
        assert not _is_selected_category("A > B > C", "A > B", "AUTRE")

    def test_prefix_mismatch_is_rejected(self):
        assert not _is_selected_category("A > B > C", "X > Y", "C")

    def test_partial_segment_prefix_is_rejected(self):
        # "A > Bxxx" ne doit pas matcher un chemin tronqué "A > B"
        assert not _is_selected_category("A > Bxxx > C", "A > B", "C")

    def test_none_inputs_are_rejected(self):
        assert not _is_selected_category("A > B", None, "B")
        assert not _is_selected_category("A > B", "A", None)


# ---------------------------------------------------------------------------
# Régression Cultura / Brico Dépôt sur données réelles (fixtures allégées)
# ---------------------------------------------------------------------------

def _match_counts(mapping_paths, attributes):
    """Pour chaque chemin sélectionnable, compte les entrées /attributes qui matchent."""
    cats = [c for c in attributes if c.get("channelFullCategoryPath") != "Cross Categories"]
    counts = {}
    for selected in mapping_paths:
        counts[selected] = sum(
            1 for c in cats
            if _is_selected_category(
                selected,
                c.get("channelFullCategoryPath"),
                c.get("channelOriginCategoryName"),
            )
        )
    return counts


def test_cultura_truncated_paths_all_match(cultura_mapping_paths, cultura_attributes):
    counts = _match_counts(cultura_mapping_paths, cultura_attributes)

    # 265 des 269 catégories ont des attributs ; les 4 restantes (CD, vinyle, Bluray,
    # DVD) n'ont réellement aucune entrée dans /attributes.
    assert sum(1 for c in counts.values() if c == 1) == 265
    assert sum(1 for c in counts.values() if c == 0) == 4
    # Jamais d'ambiguïté : aucun chemin ne doit matcher plusieurs entrées
    assert not [p for p, c in counts.items() if c > 1]


def test_bricodepot_exact_paths_still_match(bricodepot_mapping_paths, bricodepot_attributes):
    counts = _match_counts(bricodepot_mapping_paths, bricodepot_attributes)

    assert sum(1 for c in counts.values() if c == 1) == 22
    assert not [p for p, c in counts.items() if c > 1]


def test_get_channel_category_attributes_on_truncated_path(fake_client, cultura_attributes):
    # Chemin réel Cultura dont l'entrée /attributes est tronquée de deux niveaux
    selected = "Jeu et jouet > Accessoire de jeu > Accessoire de machine de jeu > Accessoire air hockey"
    client = fake_client({"/attributes": cultura_attributes})

    df = api.get_channel_category_attributes(client, "cat-test", selected)

    sources = set(df["Source"])
    assert sources == {"Cross Categories", "Category"}
    # Le chemin stocké est le chemin sélectionné complet, pas la version tronquée
    category_paths = df.loc[df["Source"] == "Category", "Channel Category Path"].unique()
    assert list(category_paths) == [selected]

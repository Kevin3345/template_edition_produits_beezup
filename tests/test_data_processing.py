import io

import numpy as np
import pandas as pd
import pytest

import data_processing as proc
from data_processing import _ean_converter


# ---------------------------------------------------------------------------
# normalize_value
# ---------------------------------------------------------------------------

class TestNormalizeValue:
    @pytest.mark.parametrize("val, expected", [
        (None, ""),
        (float("nan"), ""),
        (1.0, "1"),
        (1.5, "1.5"),
        (5, "5"),
        (True, "True"),
        (False, "False"),
        ("  x  ", "x"),
        ("C123 | Libellé lisible", "C123"),
        ("", ""),
    ])
    def test_cases(self, val, expected):
        assert proc.normalize_value(val) == expected

    def test_bool_is_not_treated_as_int(self):
        # isinstance(True, int) est vrai en Python : le bool doit être intercepté avant
        assert proc.normalize_value(True) == "True"
        assert proc.normalize_value(True) != "1"


# ---------------------------------------------------------------------------
# _ean_converter (lecture Excel de la colonne EAN)
# ---------------------------------------------------------------------------

class TestEanConverter:
    @pytest.mark.parametrize("val, expected", [
        ("123", "0000000000123"),
        (9782123456789.0, "9782123456789"),          # pandas a inféré un float
        ("0657419692298", "0657419692298"),          # déjà 13 caractères, zéro de tête conservé
        ("", ""),
        (float("nan"), ""),
        ("None", ""),
    ])
    def test_cases(self, val, expected):
        assert _ean_converter(val) == expected


# ---------------------------------------------------------------------------
# extract_attr_id
# ---------------------------------------------------------------------------

class TestExtractAttrId:
    def test_nominal(self):
        assert proc.extract_attr_id("Couleur | 58b8a1b4-aaaa-bbbb") == "58b8a1b4-aaaa-bbbb"

    def test_column_without_pipe_is_not_an_attribute(self):
        assert proc.extract_attr_id("SKU") is None

    def test_empty_id_returns_none(self):
        assert proc.extract_attr_id("Nom |") is None


# ---------------------------------------------------------------------------
# dedupe_keep_most_restrictive
# ---------------------------------------------------------------------------

def test_dedupe_keeps_required_over_optional():
    df = pd.DataFrame([
        {"Attribute Name": "Couleur", "Channel Attribute Id": "id-1", "Status": "Optional"},
        {"Attribute Name": "Couleur", "Channel Attribute Id": "id-1", "Status": "Required"},
        {"Attribute Name": "Taille", "Channel Attribute Id": "id-2", "Status": "Recommended"},
    ])
    result = proc.dedupe_keep_most_restrictive(df)

    assert len(result) == 2
    couleur = result[result["Label"] == "Couleur | id-1"]
    assert couleur.iloc[0]["Status"] == "Required"


# ---------------------------------------------------------------------------
# get_available_categories
# ---------------------------------------------------------------------------

def test_get_available_categories_merges_and_sums():
    df_cat = pd.DataFrame([
        {"Catalog Category": "Jouets", "Total Product Count": 10},
        {"Catalog Category": "Jeux", "Total Product Count": 5},
        {"Catalog Category": "Non mappée", "Total Product Count": 99},
    ])
    df_map = pd.DataFrame([
        {"Catalog Category": "Jouets", "Channel Category Path": "A > B"},
        {"Catalog Category": "Jeux", "Channel Category Path": "A > B"},
    ])
    result = proc.get_available_categories(df_cat, df_map)

    assert len(result) == 1
    assert result.iloc[0]["Channel Category Path"] == "A > B"
    assert result.iloc[0]["Total Product Count"] == 15


# ---------------------------------------------------------------------------
# merge_export_data / filter_export_columns
# ---------------------------------------------------------------------------

def test_merge_export_data_joins_on_sku():
    df_ids = pd.DataFrame([{"Product Id": "p1", "sku": "S1"}, {"Product Id": "p2", "sku": "S2"}])
    df_values = pd.DataFrame([{"sku": "S1", "color": "rouge"}])

    result = proc.merge_export_data(df_ids, df_values)

    assert len(result) == 1
    assert result.iloc[0]["Product Id"] == "p1"
    assert result.iloc[0]["color"] == "rouge"


def test_merge_export_data_raises_when_no_match():
    df_ids = pd.DataFrame([{"Product Id": "p1", "sku": "S1"}])
    df_values = pd.DataFrame([{"sku": "AUTRE", "color": "x"}])

    with pytest.raises(ValueError):
        proc.merge_export_data(df_ids, df_values)


def test_filter_export_columns_ignores_missing_attributes():
    df = pd.DataFrame([{"Product Id": "p1", "sku": "S1", "color": "rouge", "extra": "x"}])
    result = proc.filter_export_columns(df, ["color", "absent_de_lexport"])

    assert list(result.columns) == ["Product Id", "sku", "color"]


# ---------------------------------------------------------------------------
# compute_diff — le cœur de la réintégration
# ---------------------------------------------------------------------------

def _template(rows):
    """Construit un template minimal : Product Id, SKU + colonnes attributs."""
    return pd.DataFrame(rows)


ATTR_COL = "Couleur | uuid-color"
ATTR_MAPPING = {ATTR_COL: "color"}


class TestComputeDiff:
    def test_changed_value_produces_update(self):
        df_api = pd.DataFrame([{"sku": "S1", "color": "bleu"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: "rouge"}])

        updates, to_map = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)

        assert to_map == []
        assert len(updates) == 1
        assert updates[0] == {
            "sku": "S1", "product_id": "p1", "attribute_id": "uuid-color",
            "label": "Couleur", "old_value": "bleu", "new_value": "rouge",
        }

    def test_identical_value_is_ignored(self):
        df_api = pd.DataFrame([{"sku": "S1", "color": "rouge"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: "rouge"}])

        updates, _ = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)
        assert updates == []

    def test_numeric_representations_are_equal(self):
        # 1.0 (float pandas) et "1" (texte Excel) doivent être considérés identiques
        df_api = pd.DataFrame([{"sku": "S1", "color": 1.0}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: "1"}])

        updates, _ = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)
        assert updates == []

    def test_code_pipe_label_compares_on_code(self):
        # Le template contient "Code | Label" (dropdown), l'export contient le code brut
        df_api = pd.DataFrame([{"sku": "S1", "color": "C12"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: "C12 | Rouge vif"}])

        updates, _ = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)
        assert updates == []

    def test_emptied_value_produces_deletion_update(self):
        # Export rempli + template vidé → update avec "" (suppression volontaire)
        df_api = pd.DataFrame([{"sku": "S1", "color": "bleu"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: ""}])

        updates, _ = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)

        assert len(updates) == 1
        assert updates[0]["new_value"] == ""

    def test_attribute_missing_from_export_goes_to_map(self):
        df_api = pd.DataFrame([{"sku": "S1", "autre": "x"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: "rouge"}])

        updates, to_map = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)

        assert to_map == ["uuid-color"]
        assert len(updates) == 1
        assert updates[0]["old_value"] == ""
        assert updates[0]["new_value"] == "rouge"

    def test_attribute_missing_from_export_with_empty_value_no_update(self):
        df_api = pd.DataFrame([{"sku": "S1", "autre": "x"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: ""}])

        updates, to_map = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)

        assert to_map == ["uuid-color"]
        assert updates == []

    def test_column_absent_from_datainfo_is_ignored(self):
        df_api = pd.DataFrame([{"sku": "S1", "color": "bleu"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", "Inconnu | uuid-x": "val"}])

        updates, to_map = proc.compute_diff(df_api, df_excel, {})

        assert updates == []
        assert to_map == []

    def test_sku_absent_from_export_is_skipped(self):
        df_api = pd.DataFrame([{"sku": "AUTRE", "color": "bleu"}])
        df_excel = _template([{"Product Id": "p1", "SKU": "S1", ATTR_COL: "rouge"}])

        updates, _ = proc.compute_diff(df_api, df_excel, ATTR_MAPPING)
        assert updates == []

    def test_missing_sku_column_raises(self):
        df_api = pd.DataFrame([{"sku": "S1"}])
        df_excel = pd.DataFrame([{"Product Id": "p1", ATTR_COL: "x"}])

        with pytest.raises(ValueError, match="SKU"):
            proc.compute_diff(df_api, df_excel, ATTR_MAPPING)


# ---------------------------------------------------------------------------
# normalize_export_data (colonne EAN à l'index 4)
# ---------------------------------------------------------------------------

def test_normalize_export_data_pads_ean_and_keeps_empty():
    df = pd.DataFrame([{
        "Product Id": "p1", "Catalog Id": "c1", "Channel Category Path": "A > B",
        "SKU": "S1", "EAN": "657419692298",
    }, {
        "Product Id": "p2", "Catalog Id": "c1", "Channel Category Path": "A > B",
        "SKU": "S2", "EAN": np.nan,
    }])
    result = proc.normalize_export_data(df, pd.DataFrame())

    assert result.iloc[0, 4] == "0657419692298"
    assert result.iloc[1, 4] == ""  # un EAN vide ne doit PAS devenir "0000000000000"


# ---------------------------------------------------------------------------
# get_excel_attribute_mapping (onglet DataInfo)
# ---------------------------------------------------------------------------

def _excel_bytes(sheets: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    buf.seek(0)
    return buf


def test_get_excel_attribute_mapping_reads_datainfo():
    buf = _excel_bytes({"DataInfo": pd.DataFrame([
        {"Label": "Couleur | uuid-1", "Attribute Code": "color"},
        {"Label": "Taille | uuid-2", "Attribute Code": "size"},
    ])})
    mapping = proc.get_excel_attribute_mapping(buf)

    assert mapping == {"Couleur | uuid-1": "color", "Taille | uuid-2": "size"}


def test_get_excel_attribute_mapping_missing_columns_raises():
    buf = _excel_bytes({"DataInfo": pd.DataFrame([{"Autre": "x"}])})

    with pytest.raises(ValueError, match="DataInfo"):
        proc.get_excel_attribute_mapping(buf)

import pandas as pd
import pytest

import data_processing as proc
from marketplace_config import get_channel_config


# ---------------------------------------------------------------------------
# marketplace_config
# ---------------------------------------------------------------------------

class TestGetChannelConfig:
    def test_known_channel_with_category_column(self):
        config = get_channel_config("Maxeda_BRCOBE")

        assert config["sales_channel"] == "BRCOBE"
        assert config["category_column"] == "online_hybris_category"
        assert "core_gtin" in config["required_attributes"]
        assert "product-id" in config["excluded_attributes"]

    def test_known_channel_without_category_column(self):
        config = get_channel_config("Boutique_CULTFR")

        # Canal inconnu ou sans category_column → génération par SKUs indisponible
        assert config["category_column"] is None

    def test_unknown_channel_returns_empty_lists(self):
        config = get_channel_config("Boutique_INCONNU")

        assert config["sales_channel"] == "INCONNU"
        assert config["required_attributes"] == []
        assert config["excluded_attributes"] == []
        assert config["category_column"] is None


# ---------------------------------------------------------------------------
# normalize_attributes_dataframe
# ---------------------------------------------------------------------------

def test_normalize_attributes_dataframe():
    df = pd.DataFrame([{
        "Channel Attribute Id": " ABC-DEF ",
        "Attribute Name": "Couleur",
        "Attribute Code": None,
        "Status": "required",
        "Type Value": "string",
    }])
    result = proc.normalize_attributes_dataframe(df)

    assert result.iloc[0]["Channel Attribute Id"] == "abc-def"
    assert result.iloc[0]["Status"] == "Required"
    assert result.iloc[0]["Type Value"] == "String"
    assert result.iloc[0]["Attribute Code"] == "Couleur"  # complété par Attribute Name


# ---------------------------------------------------------------------------
# select_attributes_for_category — jointure par code catégorie
# ---------------------------------------------------------------------------

def _attrs_df():
    """Attributs de test : 1 Channel (sans code), 1 Cross, 2 catégories distinctes."""
    return pd.DataFrame([
        {"Source": "Channel", "Category Code": None, "Channel Category Path": None,
         "Channel Attribute Id": "id-chan", "Attribute Name": "EAN",
         "Attribute Code": "ean", "Status": "Required", "Type Value": "String",
         "Attribute Value List Code": None, "Default Value": None},
        {"Source": "Cross Categories", "Category Code": None,
         "Channel Category Path": "Cross Categories",
         "Channel Attribute Id": "id-cross", "Attribute Name": "Marque",
         "Attribute Code": "brand", "Status": "Optional", "Type Value": "String",
         "Attribute Value List Code": None, "Default Value": None},
        {"Source": "Category", "Category Code": "1000", "Channel Category Path": "A > B",
         "Channel Attribute Id": "id-cat1", "Attribute Name": "Couleur",
         "Attribute Code": "color", "Status": "Recommended", "Type Value": "String",
         "Attribute Value List Code": None, "Default Value": None},
        {"Source": "Category", "Category Code": "2000", "Channel Category Path": "A > C",
         "Channel Attribute Id": "id-cat2", "Attribute Name": "Taille",
         "Attribute Code": "size", "Status": "Required", "Type Value": "String",
         "Attribute Value List Code": None, "Default Value": None},
        {"Source": "Channel", "Category Code": None, "Channel Category Path": None,
         "Channel Attribute Id": "id-tech", "Attribute Name": "product-id",
         "Attribute Code": "product-id", "Status": "Required", "Type Value": "String",
         "Attribute Value List Code": None, "Default Value": None},
    ])


class TestSelectAttributesForCategory:
    def test_keeps_channel_cross_and_matching_category(self):
        result = proc.select_attributes_for_category(
            _attrs_df(), "1000",
            selected_statuses=["Required", "Recommended", "Optional"],
            required_codes=[], excluded_codes=[]
        )
        codes = set(result["Attribute Code"])

        assert "color" in codes        # catégorie 1000
        assert "size" not in codes     # catégorie 2000 exclue
        assert "ean" in codes          # Channel : valable partout
        assert "brand" in codes        # Cross Categories : valable partout

    def test_status_filter_and_obligatory_override(self):
        # brand est Optional : exclu par le filtre de statuts, sauf s'il est obligatoire
        result = proc.select_attributes_for_category(
            _attrs_df(), "1000",
            selected_statuses=["Required"],
            required_codes=["brand"], excluded_codes=[]
        )
        codes = set(result["Attribute Code"])

        assert "brand" in codes
        assert result.loc[result["Attribute Code"] == "brand", "Source"].iloc[0] == "Obligatory"
        assert "color" not in codes    # Recommended non sélectionné

    def test_excluded_wins(self):
        result = proc.select_attributes_for_category(
            _attrs_df(), "1000",
            selected_statuses=["Required"],
            required_codes=[], excluded_codes=["product-id"]
        )

        assert "product-id" not in set(result["Attribute Code"])

    def test_numeric_category_code_from_export_matches(self):
        # L'export peut livrer un code numérique lu 1000.0 par pandas
        result = proc.select_attributes_for_category(
            _attrs_df(), 1000.0,
            selected_statuses=["Recommended"],
            required_codes=[], excluded_codes=[]
        )

        assert "color" in set(result["Attribute Code"])

    def test_labels_are_present_for_downstream_renaming(self):
        result = proc.select_attributes_for_category(
            _attrs_df(), "1000",
            selected_statuses=["Required"],
            required_codes=[], excluded_codes=[]
        )

        assert "Label" in result.columns
        assert result["Label"].str.contains(r" \| ").all()


# ---------------------------------------------------------------------------
# get_category_path_by_code
# ---------------------------------------------------------------------------

class TestGetCategoryPathByCode:
    def test_known_code(self):
        assert proc.get_category_path_by_code(_attrs_df(), "2000") == "A > C"

    def test_numeric_code(self):
        assert proc.get_category_path_by_code(_attrs_df(), 2000.0) == "A > C"

    def test_unknown_code_returns_none(self):
        assert proc.get_category_path_by_code(_attrs_df(), "9999") is None

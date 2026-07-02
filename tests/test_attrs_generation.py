import io

import numpy as np
import pandas as pd
import pytest

import data_processing as proc
from data_processing import _is_ean_column


# ---------------------------------------------------------------------------
# _is_ean_column — remplace la règle positionnelle "EAN = index 4"
# ---------------------------------------------------------------------------

class TestIsEanColumn:
    @pytest.mark.parametrize("column_name", [
        "EAN | 58b8a1b4-uuid",
        "core_gtin | uuid",
        "identifier_ean | uuid",
        "EANs/EAN | uuid",
        "gtin",
        "GTIN-13 | uuid",
        "codebarres | uuid",
        "Code barre | uuid",
        "Code-barres | uuid",
        "barcode | uuid",
        "upc | uuid",
    ])
    def test_ean_columns_detected(self, column_name):
        assert _is_ean_column(column_name)

    @pytest.mark.parametrize("column_name", [
        "Jeanne | uuid",            # contient "ean" au milieu d'un mot
        "Océane | uuid",
        "Barre de son | uuid",      # "barre" sans "code"
        "hero_image | uuid",
        "price",
        "Description NL | uuid",
        "sku",
    ])
    def test_other_columns_ignored(self, column_name):
        assert not _is_ean_column(column_name)


def test_normalize_export_data_handles_ean_at_any_position():
    # Template "par attributs" : l'EAN n'est pas à l'index 4
    df = pd.DataFrame([{
        "Product Id": "p1", "Catalog Id": "c1", "Channel Category Path": "Multi-catégories",
        "SKU": "S1", "hero_image | uuid-img": "http://x.jpg",
        "core_gtin | uuid-ean": "657419692298",
    }])
    result = proc.normalize_export_data(df, pd.DataFrame())

    assert result["core_gtin | uuid-ean"].iloc[0] == "0657419692298"
    # La colonne à l'index 4 (une image) ne doit PAS être zfillée
    assert result["hero_image | uuid-img"].iloc[0] == "http://x.jpg"


def test_normalize_export_data_empty_ean_stays_empty():
    df = pd.DataFrame([{
        "Product Id": "p1", "Catalog Id": "c1", "Channel Category Path": "A",
        "SKU": "S1", "EAN | uuid": np.nan,
    }])
    result = proc.normalize_export_data(df, pd.DataFrame())

    assert result["EAN | uuid"].iloc[0] == ""


# ---------------------------------------------------------------------------
# Compatibilité du template "par attributs" avec la réintégration v3
# ---------------------------------------------------------------------------

def _attrs_template_excel() -> io.BytesIO:
    """Simule un template généré par attributs : EAN hors index 4, DataInfo standard."""
    df_template = pd.DataFrame([{
        "Product Id": "p1", "Catalog Id": "cat-1", "Channel Category Path": "Multi-catégories",
        "SKU": "S1", "hero_image | uuid-img": "http://x.jpg",
        "EAN | uuid-ean": "0657419692298",
    }])
    df_datainfo = pd.DataFrame([
        {"Label": "hero_image | uuid-img", "Attribute Code": "hero_image"},
        {"Label": "EAN | uuid-ean", "Attribute Code": "ean"},
    ])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df_template.to_excel(writer, sheet_name="Template", index=False)
        df_datainfo.to_excel(writer, sheet_name="DataInfo", index=False)
    buf.seek(0)
    buf.name = "template_test.xlsx"
    return buf


def test_run_comparison_reads_attrs_template(monkeypatch):
    """La réintégration lit un template par attributs : converter EAN appliqué à la
    bonne colonne (pas à l'index 4) et diff calculé normalement."""
    monkeypatch.setattr(
        proc.api, "get_catalog_infos",
        lambda client, cid: {"storeId": "store-1", "channelId": "chan-1"},
    )
    monkeypatch.setattr(
        proc.api, "download_export_file",
        lambda cid: pd.DataFrame([{
            "sku": "S1", "hero_image": "http://x.jpg", "ean": "657419692298",
        }]),
    )

    result = proc.run_comparison_for_file(client=None, excel_file=_attrs_template_excel())

    assert result["catalog_id"] == "cat-1"
    assert result["to_map"] == []
    # hero_image identique → pas d'update ; EAN : le template porte le zéro de tête,
    # l'export non → l'update reflète la vraie valeur normalisée du template
    updates = {u["label"]: u for u in result["updates"]}
    assert "hero_image" not in updates
    assert updates["EAN"]["new_value"] == "0657419692298"

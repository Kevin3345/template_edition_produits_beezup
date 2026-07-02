import json
from pathlib import Path

# Chemin absolu vers marketplace_config.json, indépendant du répertoire de lancement
_CONFIG_PATH = Path(__file__).resolve().parent / "marketplace_config.json"


def get_channel_config(store_name: str) -> dict:
    """
    Retourne la configuration marketplace déduite du suffixe du store_name.

    Structure retournée (valeurs vides si le canal est inconnu) :
    - sales_channel        : suffixe du store_name (ex. "BRCOBE")
    - required_attributes  : codes des attributs toujours inclus dans le template
    - excluded_attributes  : codes à exclure (colonnes techniques Mirakl...)
    - category_column      : nom de la colonne catégorie dans l'export BeezUP,
                             ou None si la marketplace n'en expose pas (la
                             génération par SKUs n'est alors pas disponible)
    """
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    sales_channel = store_name.split("_")[-1]
    conf = data.get(sales_channel) or {}

    return {
        "sales_channel": sales_channel,
        "required_attributes": [str(a).strip() for a in conf.get("required_attributes", [])],
        "excluded_attributes": [str(a).strip() for a in conf.get("excluded_attributes", [])],
        "category_column": conf.get("category_column"),
    }

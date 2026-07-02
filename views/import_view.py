import time
from collections import defaultdict

import pandas as pd
import streamlit as st

from api_services import (
    create_custom_column,
    get_existing_overrides_by_product_id,
    map_attributes_group,
)
from data_processing import run_comparison_for_file
from logger_utils import get_log_context


def _get_files_fingerprint(files: list) -> str:
    """
    Crée une clé unique basée sur les noms et tailles des fichiers uploadés.
    Permet de détecter si la sélection a changé sans relancer l'analyse.
    """
    return "|".join(f"{f.name}:{f.size}" for f in sorted(files, key=lambda x: x.name))


def render():
    """Rendu principal de l'onglet d'intégration des données."""
    logger = get_log_context()

    st.html("<style>.st-key-edition_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, gap="medium", key="edition_container"):
        st.subheader("✏️️ Intégration des données")

        # accept_multiple_files=True retourne toujours une liste (vide si rien sélectionné)
        uploaded_files = st.file_uploader(
            "Sélectionnez le(s) template(s) .xlsx",
            accept_multiple_files=True,
            type="xlsx",
            key="uploader_import"
        )

        if not uploaded_files:
            st.session_state.pop("import_results", None)
            st.session_state.pop("import_fingerprint", None)
            return

        current_fingerprint = _get_files_fingerprint(uploaded_files)
        results_are_fresh = (
                "import_results" in st.session_state
                and st.session_state.get("import_fingerprint") == current_fingerprint
        )

        analyze_clicked = st.button(
            label="Analyser les modifications",
            type="primary" if not results_are_fresh else "secondary",
            icon=":material/search:"
        )

        if results_are_fresh:
            st.info("Les résultats sont à jour. Modifiez la sélection ou re-cliquez pour forcer.")

        st.write("")

        if analyze_clicked:
            client = st.session_state.get("client")
            if not client:
                st.error("Session expirée. Veuillez vous reconnecter.")
                return

            results = {}
            error_count = 0

            with st.status(f"Analyse de {len(uploaded_files)} fichier(s)...", expanded=True) as status:
                for f in uploaded_files:
                    st.write(f"📄 Analyse de **{f.name}**...")
                    try:
                        result = run_comparison_for_file(client, f)
                        results[f.name] = result

                        nb_updates = len(result["updates"])
                        nb_to_map = len(result["to_map"])
                        details = f"{nb_updates} modification(s)"
                        if nb_to_map:
                            details += f", {nb_to_map} attribut(s) à mapper"
                        st.write(f"✅ **{f.name}** — {details}")

                    except Exception as e:
                        error_count += 1
                        logger.error(
                            f"Échec de l'analyse pour '{f.name}' : {type(e).__name__}: {e}"
                        )
                        results[f.name] = {
                            "error": str(e),
                            "catalog_id": None,
                            "store_id": None,
                            "updates": [],
                            "to_map": [],
                            "nb_products": 0
                        }
                        st.write(f"❌ **{f.name}** — Erreur : {e}")

                # État du bandeau selon le nombre d'erreurs
                if error_count == len(uploaded_files):
                    status.update(label="Analyse échouée — aucun fichier traité.", state="error")
                elif error_count:
                    status.update(
                        label=f"Analyse terminée avec {error_count} erreur(s) sur {len(uploaded_files)} fichier(s).",
                        state="error"
                    )
                else:
                    status.update(
                        label=f"Analyse terminée — {len(uploaded_files)} fichier(s) traité(s).",
                        state="complete"
                    )

            st.session_state["import_results"] = results
            st.session_state["import_fingerprint"] = current_fingerprint
            st.rerun()

    if st.session_state.get("import_results"):
        _display_results(st.session_state["import_results"])


def _display_results(results: dict):
    """Affiche les résultats de l'analyse fichier par fichier."""
    logger = get_log_context()

    client = st.session_state.get("client")
    if not client:
        st.error("Session expirée. Veuillez vous reconnecter.")
        return

    st.space("small")
    st.html("<style>.st-key-results_container { box-shadow: 0px 2px 20px rgba(0, 0, 0, 0.5); }</style>")

    with st.container(border=True, gap="medium", key="results_container"):
        st.subheader("📑 Résumé de l'analyse")

        for filename, data in results.items():
            nb_products = data.get("nb_products", 0)
            catalog_id = data.get("catalog_id")
            applied = data.get("applied", False)

            icon = "✅" if applied else "📦"
            catalog_label = catalog_id or "inconnu"
            label = f"{icon} {filename} — Catalogue : {catalog_label} ({nb_products} produits)"

            with st.expander(label, expanded=not applied):

                if data.get("error"):
                    st.error(data["error"])
                    continue

                if applied:
                    st.success("Les modifications ont été appliquées avec succès.")
                    continue

                store_id = data.get("store_id")

                to_map = data.get("to_map", [])
                updates = data.get("updates", [])
                mapping_done = data.get("mapping_done", False)

                # --- SECTION MAPPING ---
                if to_map:
                    st.warning(f"⚠️ {len(to_map)} attribut(s) non mappé(s) dans BeezUP.")
                    st.write("Attributs concernés : " + ", ".join(f"`{a}`" for a in to_map))

                    if not store_id or not catalog_id:
                        st.error("Impossible de créer le mapping : store_id ou catalog_id manquant.")
                        logger.error(
                            f"Mapping impossible pour '{filename}' : "
                            f"store_id={store_id}, catalog_id={catalog_id}."
                        )
                    else:
                        if st.button(
                                label="Créer le mapping",
                                type="primary",
                                key=f"btn_map_{filename}",
                                icon=":material/link:",
                                disabled=mapping_done
                        ):
                            with st.spinner("Création du champ personnalisé et mapping des attributs..."):
                                try:
                                    new_col_id = create_custom_column(client, store_id)
                                    map_attributes_group(client, catalog_id, to_map, new_col_id)
                                    st.cache_data.clear()
                                    st.session_state["import_results"][filename]["mapping_done"] = True
                                    st.success("Mapping créé. Vous pouvez maintenant appliquer les modifications.")
                                    st.rerun()
                                except Exception as e:
                                    logger.error(
                                        f"Échec de la création du mapping pour '{filename}' "
                                        f"(catalog_id={catalog_id}) : {type(e).__name__}: {e}"
                                    )
                                    st.error(f"Erreur lors de la création du mapping : {e}")

                # --- SECTION UPDATES ---
                if updates:
                    st.success(f"✅ {len(updates)} valeur(s) à mettre à jour.")

                    df_diff = pd.DataFrame(updates)
                    st.dataframe(
                        df_diff[["sku", "label", "old_value", "new_value"]],
                        width="content",
                        hide_index=True
                    )

                    apply_blocked = (bool(to_map) and not mapping_done) or applied

                    if to_map and not mapping_done:
                        st.info(
                            "Des attributs doivent encore être mappés avant l'application. "
                            "Créez le mapping ci-dessus."
                        )

                    if st.button(
                            label="Appliquer les modifications",
                            type="primary",
                            key=f"btn_upd_{filename}",
                            disabled=apply_blocked,
                            icon=":material/check:"
                    ):
                        success = _execute_overrides_with_progress(client, catalog_id, updates)
                        if success:
                            st.session_state["import_results"][filename]["applied"] = True
                            st.rerun()

                else:
                    st.info("Aucune modification détectée pour ce fichier.")


def _group_updates_by_product(updates: list) -> list:
    """
    Regroupe les modifications par product_id pour minimiser les appels API.

    Entrée  : [{ "sku", "product_id", "attribute_id", "new_value", ... }, ...]
    Sortie  : [{ "sku", "product_id", "overrides": { attr_id: value, ... } }, ...]
    """
    grouped = defaultdict(lambda: {"sku": None, "product_id": None, "overrides": {}})

    for upd in updates:
        pid = upd["product_id"]
        grouped[pid]["sku"] = upd["sku"]
        grouped[pid]["product_id"] = pid
        grouped[pid]["overrides"][upd["attribute_id"]] = upd["new_value"]

    return list(grouped.values())


def _execute_overrides_with_progress(client, catalog_id: str, updates: list) -> bool:
    """
    Envoie les modifications à l'API BeezUP en préservant les overrides existants.

    Le PUT /overrides remplace l'intégralité des overrides du produit.
    On récupère les overrides existants en amont et on fusionne :
    les valeurs du template gagnent sur les anciennes (y compris les suppressions via "").

    :return: True si tous les produits ont été mis à jour sans erreur, False sinon.
    """
    logger = get_log_context()

    grouped_updates = _group_updates_by_product(updates)

    if not grouped_updates:
        st.info("Aucune modification à appliquer.")
        return True

    skus = [item["sku"] for item in grouped_updates if item.get("sku")]

    with st.spinner("Récupération des overrides existants..."):
        try:
            existing_overrides = get_existing_overrides_by_product_id(
                client=client,
                catalog_id=catalog_id,
                skus=skus
            )
        except Exception as e:
            logger.error(
                f"Impossible de récupérer les overrides existants "
                f"(catalog_id={catalog_id}) : {type(e).__name__}: {e}"
            )
            st.error(f"Impossible de récupérer les overrides existants. Mise à jour annulée : {e}")
            return False

    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(grouped_updates)
    success_count = 0
    error_count = 0

    logger.info(f"Début de la réintégration : {total} produit(s) à mettre à jour (catalog_id={catalog_id}).")

    try:
        for i, item in enumerate(grouped_updates):
            try:
                product_id = item["product_id"]
                merged_overrides = {
                    **existing_overrides.get(product_id, {}),
                    **item["overrides"]
                }
                client.override_channel_catalog_product_values(
                    catalog_id=catalog_id,
                    product_id=product_id,
                    overrides=merged_overrides
                )
                success_count += 1

            except Exception as e:
                error_count += 1
                sku = item.get("sku", "inconnu")
                logger.error(
                    f"Échec de la mise à jour — SKU={sku}, "
                    f"product_id={item.get('product_id')} : {type(e).__name__}: {e}"
                )
                st.error(f"Erreur sur SKU {sku} : {e}")

            progress_bar.progress(int((i + 1) / total * 100))
            status_text.text(f"Mise à jour : {i + 1}/{total}")

            # Throttle proactif : 100 req/min (comptes les plus limités) = 1 req/0,6 s.
            # Le retry 429 du client (beezup_client._send) sert de filet de sécurité.
            time.sleep(0.65)

    finally:
        progress_bar.empty()
        status_text.empty()

    if error_count:
        logger.warning(
            f"Réintégration terminée avec erreurs : {success_count}/{total} produit(s) mis à jour "
            f"(catalog_id={catalog_id})."
        )
        st.warning(f"Terminé avec erreurs : {success_count}/{total} produits mis à jour.")
        return False

    logger.success(
        f"Réintégration réussie : {success_count}/{total} produit(s) mis à jour "
        f"(catalog_id={catalog_id})."
    )
    st.success(f"✅ {success_count}/{total} produits mis à jour avec succès.")
    st.cache_data.clear()
    return True

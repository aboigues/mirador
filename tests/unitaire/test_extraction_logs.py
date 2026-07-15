"""Tests unitaires : extraction du texte depuis l'archive ZIP des logs GitHub.

L'API GitHub `/actions/runs/{id}/logs` renvoie un ZIP (un fichier .txt par
étape). Décoder ces octets directement en UTF-8 produit du charabia et casse
le matching des règles (`pattern_log`). `extraire_texte_logs` dézippe d'abord.
"""
import io
import zipfile

from src.infrastructure.github.actions_client import extraire_texte_logs


def _zip(entrees: dict[str, str]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for nom, contenu in entrees.items():
            z.writestr(nom, contenu)
    return tampon.getvalue()


def test_dezippe_et_concatene_les_entrees_texte():
    contenu = _zip(
        {
            "build/1_Set up job.txt": "Preparing environment\n",
            "build/2_Run tests.txt": "FAILED: ModuleNotFoundError: No module named 'foo'\n",
        }
    )
    texte = extraire_texte_logs(contenu)
    assert "ModuleNotFoundError" in texte
    assert "Preparing environment" in texte


def test_pattern_de_regle_trouvable_apres_extraction():
    # Reproduit le bug réel : sans dézippage, "npm ERR!" est introuvable.
    contenu = _zip({"job/step.txt": "npm ERR! code ELIFECYCLE\n"})
    texte = extraire_texte_logs(contenu)
    assert "npm ERR!" in texte


def test_octets_textuels_non_zip_retournes_tels_quels():
    # Robustesse : si la source renvoie déjà du texte (doubles de test,
    # logs non archivés), on ne doit pas échouer.
    texte = extraire_texte_logs(b"aucun pattern")
    assert texte == "aucun pattern"


def test_octets_vides_ne_plantent_pas():
    assert extraire_texte_logs(b"") == ""

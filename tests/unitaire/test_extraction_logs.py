"""Tests unitaires : extraction du texte depuis l'archive ZIP des logs GitHub.

L'API GitHub `/actions/runs/{id}/logs` renvoie un ZIP (un fichier .txt par
étape). Décoder ces octets directement en UTF-8 produit du charabia et casse
le matching des règles (`pattern_log`). `extraire_texte_logs` dézippe d'abord.
"""
import io
import zipfile

from src.infrastructure.github.actions_client import (
    assembler_contexte_depot,
    assembler_extrait_jobs,
    est_fichier_manifeste,
    extraire_texte_logs,
)


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


# --- assemblage des logs de jobs en échec (régression #129) ----------------


def test_un_job_verbeux_n_evince_pas_les_autres():
    # Le cœur du bug : sans partage du budget, le job bavard occupe toute la
    # fenêtre et l'erreur des autres jobs n'atteint jamais le correcteur.
    jobs = [
        ("Scan A", "bruit\n" * 5000),
        ("Scan B", "CVE-2024-45491 dans cassandra:4.1\n"),
    ]
    texte = assembler_extrait_jobs(jobs, budget=2000)
    assert "CVE-2024-45491" in texte
    assert "Scan A" in texte and "Scan B" in texte


def test_budget_respecte():
    jobs = [(f"Job {i}", "x" * 10000) for i in range(3)]
    assert len(assembler_extrait_jobs(jobs, budget=3000)) <= 3000


def test_garde_la_fin_du_log_ou_est_l_erreur():
    jobs = [("Job", "PREMIERE_LIGNE\n" + "milieu\n" * 2000 + "Error: exit code 1\n")]
    texte = assembler_extrait_jobs(jobs, budget=500)
    assert "Error: exit code 1" in texte
    assert "PREMIERE_LIGNE" not in texte


def test_au_dela_de_cinq_jobs_les_noms_sont_cites():
    jobs = [(f"Scan image-{i}", f"Error dans {i}\n") for i in range(8)]
    texte = assembler_extrait_jobs(jobs, budget=4000)
    # Les 8 sont nommés (l'humain voit l'ampleur), 5 sont détaillés.
    assert "Scan image-7" in texte
    assert "8 jobs en échec" in texte
    assert texte.count("===== Job en échec") == 5


def test_aucun_job_en_echec():
    assert assembler_extrait_jobs([], budget=1000) == ""


# --- filtrage des fichiers de manifeste (contexte dépôt du correcteur) -----


def test_reconnait_yaml_et_yml():
    assert est_fichier_manifeste("tp08/compose.yaml")
    assert est_fichier_manifeste("k8s/deployment.yml")
    assert est_fichier_manifeste(".github/workflows/scan-images.yml")


def test_reconnait_dockerfile_et_variantes():
    assert est_fichier_manifeste("Dockerfile")
    assert est_fichier_manifeste("app/Dockerfile")
    assert est_fichier_manifeste("Dockerfile.prod")
    assert est_fichier_manifeste("hardened/Containerfile")


def test_rejette_le_bruit():
    assert not est_fichier_manifeste("README.md")
    assert not est_fichier_manifeste("src/agents/correcteur.py")
    assert not est_fichier_manifeste("assets/logo.png")


# --- assemblage du contexte dépôt (jamais de fichier tronqué) --------------


def test_assemble_les_fichiers_qui_tiennent_dans_le_budget():
    fichiers = [("a.yaml", "contenu A"), ("b.yaml", "contenu B")]
    texte = assembler_contexte_depot(fichiers, budget=1000)
    assert "a.yaml" in texte and "contenu A" in texte
    assert "b.yaml" in texte and "contenu B" in texte


def test_omet_un_fichier_entier_plutot_que_de_le_tronquer():
    # Le fichier qui ne tient pas doit être ABSENT, jamais coupé en morceau :
    # un fichier partiel serait pris pour un contenu complet par le correcteur,
    # qui produirait alors un correctif corrompu.
    fichiers = [("gros.yaml", "X" * 500), ("petit.yaml", "ok")]
    texte = assembler_contexte_depot(fichiers, budget=100)
    assert "X" * 500 not in texte  # jamais de contenu partiel de gros.yaml
    assert "petit.yaml" in texte and "ok" in texte
    assert "gros.yaml" in texte  # cité dans le récapitulatif des omis
    assert "omis" in texte


def test_budget_respecte_sans_troncature():
    fichiers = [(f"f{i}.yaml", "x" * 500) for i in range(10)]
    texte = assembler_contexte_depot(fichiers, budget=1200)
    assert len(texte) <= 1200 + 200  # marge pour la ligne récapitulative
    # Chaque fichier inclus doit apparaître avec son contenu ENTIER (500 x).
    for ligne in texte.splitlines():
        if ligne == "x" * 500:
            assert len(ligne) == 500


def test_aucun_fichier():
    assert assembler_contexte_depot([], budget=1000) == ""

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
    extraire_images_durcies,
    extraire_references_images,
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


# --- extraction des références d'image (ciblage de la recherche dépôt) -----


def test_extrait_reference_namespace_repo_tag():
    refs = extraire_references_images("Scan amazon/aws-cli:2.36.8 : CVE-2026-44605 (rpm) HIGH")
    assert "amazon/aws-cli:2.36.8" in refs


def test_extrait_reference_avec_tag_compose():
    refs = extraire_references_images(
        "telemachlearning/wordpress:6.8-php8.3-apache a des CVE non corrigées"
    )
    assert "telemachlearning/wordpress:6.8-php8.3-apache" in refs


def test_extrait_reference_sans_namespace():
    assert "postgres:18-alpine" in extraire_references_images("image: postgres:18-alpine")


def test_ignore_les_horodatages():
    # « 05:13:53 » ne doit pas être pris pour une image taguée « 13 » ou « 53 ».
    refs = extraire_references_images("2026-08-10T05:13:53.7558443Z Trivy démarre")
    assert not any(r.endswith(":13") or r.endswith(":53") for r in refs)


def test_ignore_les_couples_cle_valeur_yaml():
    # « clé: valeur » (espacé) n'est pas une référence d'image (non espacée).
    assert extraire_references_images("shell: /usr/bin/bash\nFAIL_ON_SEVERITY: CRITICAL,HIGH") == []


def test_ignore_les_urls():
    assert extraire_references_images("https://avd.aquasec.com/nvd/cve-2026-44605") == []


def test_deduplique_et_plafonne():
    texte = " ".join(f"repo{i}/image{i}:1.{i}" for i in range(10))
    refs = extraire_references_images(texte + " repo0/image0:1.0")  # doublon
    assert len(refs) <= 5
    assert len(refs) == len(set(refs))


# --- détection des images durcies du dépôt (rebuild plutôt qu'abstention) --


def test_detecte_une_image_durcie_referencee():
    # kubernetes-formation#149 : CVE de paquet OS dans une image que le dépôt
    # construit lui-même (docker/hardened/wordpress) — pas de fichier à
    # éditer, seule une reconstruction republie le paquet corrigé.
    chemins = ["docker/hardened/wordpress/Dockerfile", "docker/hardened/README.md"]
    images = extraire_images_durcies(
        "Scan telemachlearning/wordpress:7.0-php8.5-apache CVE-2026-33164 HIGH", chemins
    )
    assert images == ["wordpress"]


def test_ignore_une_image_officielle_non_construite_par_le_depot():
    chemins = ["docker/hardened/wordpress/Dockerfile"]
    assert extraire_images_durcies("Scan postgres:17-alpine CVE-2026-14456 HIGH", chemins) == []


def test_aucun_dossier_docker_hardened_dans_le_depot():
    assert extraire_images_durcies(
        "Scan telemachlearning/wordpress:7.0-php8.5-apache HIGH", ["README.md"]
    ) == []


def test_deduplique_les_images_durcies():
    chemins = ["docker/hardened/nginx/Dockerfile"]
    texte = "Scan telemachlearning/nginx:1.29-alpine HIGH\nScan telemachlearning/nginx:1.29-alpine HIGH"
    assert extraire_images_durcies(texte, chemins) == ["nginx"]


def test_detecte_une_image_durcie_a_la_racine_hardened():
    # docker-formation range ses images durcies sous hardened/ (racine), pas
    # docker/hardened/ comme kubernetes-formation — les deux conventions
    # doivent être reconnues.
    chemins = ["hardened/grafana/Dockerfile", "hardened/README.md"]
    images = extraire_images_durcies(
        "Scan telemachlearning/grafana:13.0.2 CVE-2026-1234 HIGH", chemins
    )
    assert images == ["grafana"]

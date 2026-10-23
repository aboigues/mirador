"""Tests unitaires : tags d'image (comparaison, candidats, changements d'un diff).

Le cas réel kubernetes-formation#216 sert de fixture, tel quel : Mirador y a
rétrogradé `grafana/grafana:13.2.1 → 11.4.0` et `alpine/helm:3.22.0 → 3.14.0`,
des tags plausibles tirés des données d'entraînement, vérifiés par personne.
"""
from pathlib import Path

import pytest

from src.domaine.tag_image import (
    ChangementImage,
    candidats,
    changements_images,
    est_retrogradation,
    nom_docker_hub,
)

FIXTURE_216 = Path(__file__).parent.parent / "fixtures" / "kubernetes-formation-216"
FICHIERS_216 = [
    "tp04/05-grafana-deployment.yaml",
    "tp06/tekton/tasks/helm-deploy-task.yaml",
    "tp10/20b-grafana-deployment.yaml",
]


def _lire_216(etat: str, chemin: str) -> str:
    return (FIXTURE_216 / etat / chemin).read_text(encoding="utf-8")


class TestRetrogradation:
    @pytest.mark.parametrize("ancien,nouveau", [
        ("13.2.1", "11.4.0"),  # kubernetes-formation#216
        ("3.22.0", "3.14.0"),  # kubernetes-formation#216
        ("1.30.2-alpine", "1.29.8-alpine"),
        ("v2.5.0", "v2.4.9"),
    ])
    def test_version_inferieure_est_une_retrogradation(self, ancien, nouveau):
        assert est_retrogradation(ancien, nouveau)

    @pytest.mark.parametrize("ancien,nouveau", [
        ("13.2.1", "13.2.4"),
        ("3.22.0", "3.22.1"),
        ("1.30-alpine", "1.31-alpine"),
        ("13.2.1", "13.2.1"),
    ])
    def test_version_superieure_ou_egale_n_en_est_pas_une(self, ancien, nouveau):
        assert not est_retrogradation(ancien, nouveau)

    @pytest.mark.parametrize("ancien,nouveau", [
        ("latest", "13.2.4"),
        ("13.2.1", "13.2.1-ubuntu"),  # variante différente : incomparable
    ])
    def test_tags_incomparables_ne_sont_pas_declares_retrogrades(self, ancien, nouveau):
        assert not est_retrogradation(ancien, nouveau)


class TestCandidats:
    # Tags réellement publiés sur Docker Hub le 2026-10-23 (extrait).
    GRAFANA = ["13.2.4-ubuntu", "13.2.4", "13.2.3", "13.2.2", "13.2.1", "13.2.0",
               "13.1.9", "12.4.0", "11.4.0", "latest"]
    HELM = ["3.22", "3.22.1", "3.22.0", "3.21.4", "3.13.2", "3.3.2", "3.2.0-rc.1"]

    def test_dernier_correctif_de_la_meme_mineure(self):
        assert candidats("13.2.1", self.GRAFANA) == ["13.2.4"]
        assert candidats("3.22.0", self.HELM) == ["3.22.1"]

    def test_puis_la_mineure_suivante(self):
        tags = self.GRAFANA + ["13.3.0", "13.3.2", "13.4.0", "14.0.0"]
        assert candidats("13.2.1", tags) == ["13.2.4", "13.3.2"]

    def test_jamais_en_arriere(self):
        assert candidats("13.2.4", self.GRAFANA) == []
        assert "11.4.0" not in candidats("13.2.1", self.GRAFANA)

    def test_meme_variante_seulement(self):
        tags = ["1.30.6-alpine", "1.30.6", "1.30.6-alpine-perl", "1.31.0-alpine"]
        assert candidats("1.30.2-alpine", tags) == ["1.30.6-alpine", "1.31.0-alpine"]

    def test_tag_a_deux_composantes_monte_d_une_mineure(self):
        tags = ["1.30-alpine", "1.31-alpine", "1.32-alpine", "1.31.0-alpine"]
        assert candidats("1.30-alpine", tags) == ["1.31-alpine"]

    def test_tag_sans_version_n_a_pas_de_candidat(self):
        assert candidats("latest", self.GRAFANA) == []
        assert candidats("18", ["18", "19", "20"]) == []


class TestNomDockerHub:
    @pytest.mark.parametrize("image,attendu", [
        ("grafana/grafana", "grafana/grafana"),
        ("nginx", "library/nginx"),
        ("docker.io/alpine/helm", "alpine/helm"),
        ("docker.io/library/nginx", "library/nginx"),
        ("ghcr.io/org/outil", None),
        ("registry.k8s.io/pause", None),
        ("localhost:5000/x", None),
    ])
    def test_seules_les_images_docker_hub_ont_un_nom(self, image, attendu):
        assert nom_docker_hub(image) == attendu


class TestChangementsImages:
    def test_cas_reel_216(self):
        changements = []
        for chemin in FICHIERS_216:
            changements += changements_images(_lire_216("avant", chemin), _lire_216("apres", chemin))
        assert changements == [
            ChangementImage(image="grafana/grafana", ancien="13.2.1", nouveau="11.4.0"),
            ChangementImage(image="alpine/helm", ancien="3.22.0", nouveau="3.14.0"),
            ChangementImage(image="grafana/grafana", ancien="13.2.1", nouveau="11.4.0"),
        ]
        assert all(est_retrogradation(c.ancien, c.nouveau) for c in changements)

    def test_dockerfile_from(self):
        avant = "FROM --platform=linux/amd64 nginx:1.30-alpine AS web\nRUN true\n"
        apres = "FROM --platform=linux/amd64 nginx:1.31-alpine AS web\nRUN true\n"
        assert changements_images(avant, apres) == [
            ChangementImage(image="nginx", ancien="1.30-alpine", nouveau="1.31-alpine"),
        ]

    def test_image_entre_guillemets(self):
        avant = 'image: "postgres:18.1"\n'
        apres = 'image: "postgres:18.3"\n'
        assert changements_images(avant, apres) == [
            ChangementImage(image="postgres", ancien="18.1", nouveau="18.3"),
        ]

    def test_fichier_sans_changement_d_image(self):
        avant = "image: grafana/grafana:13.2.1\nreplicas: 1\n"
        apres = "image: grafana/grafana:13.2.1\nreplicas: 2\n"
        assert changements_images(avant, apres) == []

    def test_fichier_nouveau(self):
        assert changements_images(None, "image: grafana/grafana:13.2.4\n") == []

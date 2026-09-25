"""Tests unitaires : lecture du journal depuis un répertoire local, sans écriture possible."""
import pytest

from src.infrastructure.stockage.lecture_fichier import (
    ClientBucketLectureFichier,
    EcritureInterdite,
)


def test_lit_une_cle_presente(tmp_path):
    (tmp_path / "mirador.db").write_bytes(b"contenu")
    assert ClientBucketLectureFichier(tmp_path).telecharger("mirador.db") == b"contenu"


def test_cle_absente_renvoie_none(tmp_path):
    assert ClientBucketLectureFichier(tmp_path).telecharger("mirador.db") is None


def test_televerser_est_toujours_refuse(tmp_path):
    with pytest.raises(EcritureInterdite):
        ClientBucketLectureFichier(tmp_path).televerser("mirador.db", b"x")
    assert not (tmp_path / "mirador.db").exists()


@pytest.mark.parametrize("cle", ["../secret", "/etc/passwd", "sous/../../secret"])
def test_refuse_une_cle_qui_sort_du_repertoire(tmp_path, cle):
    with pytest.raises(ValueError):
        ClientBucketLectureFichier(tmp_path).telecharger(cle)


def test_repertoire_inexistant_refuse_a_la_construction(tmp_path):
    with pytest.raises(ValueError):
        ClientBucketLectureFichier(tmp_path / "absent")

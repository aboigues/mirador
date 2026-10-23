"""Tags d'image : comparaison de versions, candidats à mesurer, changements d'un diff.

Mirador ne laisse plus Claude choisir le tag d'une image (kubernetes-formation#216 :
grafana/grafana 13.2.1 → 11.4.0, tag plausible tiré des données d'entraînement,
vérifié par personne). Ce module fournit la partie déterministe : repérer qu'un
correctif change une référence d'image, refuser tout retour en arrière, et
retenir parmi les tags réellement publiés ceux de la même ligne à mesurer.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

# `v1.2.3-alpine` → préfixe « v », composantes (1, 2, 3), variante « -alpine ».
# La variante ne commence ni par un chiffre ni par un point : `13.2.4-ubuntu`
# et `13.2.4` sont deux lignes distinctes, jamais comparées entre elles.
_PATTERN_VERSION = re.compile(r"^(v?)(\d+(?:\.\d+)*)((?:[^\d.].*)?)$")

# Référence d'image dans un manifeste YAML (`image: x:y`, élément de liste ou
# non, avec ou sans guillemets) ou un Dockerfile (`FROM [--platform=…] x:y`).
_PATTERN_IMAGE_YAML = re.compile(r"""^\s*(?:-\s*)?image:\s*["']?([^\s"'#]+)""", re.MULTILINE)
_PATTERN_IMAGE_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class ChangementImage:
    image: str  # telle qu'écrite dans le fichier, sans le tag
    ancien: str
    nouveau: str


@dataclass(frozen=True)
class _Version:
    prefixe: str
    composantes: tuple[int, ...]
    variante: str

    def meme_ligne_que(self, autre: "_Version") -> bool:
        return (self.prefixe, self.variante, len(self.composantes)) == (
            autre.prefixe, autre.variante, len(autre.composantes))


def _version(tag: str) -> Optional[_Version]:
    correspondance = _PATTERN_VERSION.match(tag)
    if correspondance is None:
        return None
    prefixe, nombres, variante = correspondance.groups()
    return _Version(prefixe, tuple(int(n) for n in nombres.split(".")), variante)


def est_retrogradation(ancien: str, nouveau: str) -> bool:
    """Vrai si `nouveau` est une version strictement inférieure à `ancien`.

    Deux tags de variantes différentes (`13.2.1` / `13.2.1-ubuntu`) ou sans
    numéro de version (`latest`) sont incomparables : pas une rétrogradation.
    """
    v_ancien, v_nouveau = _version(ancien), _version(nouveau)
    if v_ancien is None or v_nouveau is None or not v_ancien.meme_ligne_que(v_nouveau):
        return False
    return v_nouveau.composantes < v_ancien.composantes


def candidats(actuel: str, tags_publies: list[str]) -> list[str]:
    """Tags à mesurer pour remplacer `actuel`, du moins risqué au plus risqué.

    Le dernier correctif de la même mineure, puis la dernière version de la
    mineure suivante. Jamais en arrière, jamais de changement de majeure ni de
    variante. Un tag sans mineure (`18`, `latest`) n'a pas de ligne : aucun
    candidat.
    """
    v_actuel = _version(actuel)
    if v_actuel is None or len(v_actuel.composantes) < 2:
        return []
    majeure, mineure = v_actuel.composantes[:2]
    meilleurs: dict[int, tuple[tuple[int, ...], str]] = {}
    for tag in tags_publies:
        v_tag = _version(tag)
        if (
            v_tag is None
            or not v_tag.meme_ligne_que(v_actuel)
            or v_tag.composantes <= v_actuel.composantes
            or v_tag.composantes[0] != majeure
            or v_tag.composantes[1] not in (mineure, mineure + 1)
        ):
            continue
        ligne = v_tag.composantes[1]
        if ligne not in meilleurs or v_tag.composantes > meilleurs[ligne][0]:
            meilleurs[ligne] = (v_tag.composantes, tag)
    return [meilleurs[ligne][1] for ligne in sorted(meilleurs)]


def prefixe_recherche(tag: str) -> Optional[str]:
    """Filtre à passer au registre (`13.2.1` → `13.`) : borne la liste des tags
    à la majeure en place, la seule dont `candidats` retient des tags."""
    v_tag = _version(tag)
    if v_tag is None or len(v_tag.composantes) < 2:
        return None
    return f"{v_tag.prefixe}{v_tag.composantes[0]}."


def nom_docker_hub(image: str) -> Optional[str]:
    """Nom du dépôt Docker Hub (`nginx` → `library/nginx`), ou None si l'image
    vient d'un autre registre (ghcr.io, registry.k8s.io…), non interrogé."""
    if image.startswith("docker.io/"):
        image = image[len("docker.io/"):]
    premier, _, reste = image.partition("/")
    if not reste:
        return f"library/{premier}"
    if "." in premier or ":" in premier or premier == "localhost":
        return None
    return image


def _decouper(reference: str) -> Optional[tuple[str, str]]:
    if "@" in reference:
        return None  # épinglée par digest : pas de tag à comparer
    image, separateur, tag = reference.rpartition(":")
    if not separateur or "/" in tag:
        return None  # pas de tag (ou « : » d'un port de registre)
    return image, tag


def _references(contenu: str) -> list[tuple[str, str]]:
    trouvees = []
    for motif in (_PATTERN_IMAGE_YAML, _PATTERN_IMAGE_FROM):
        for correspondance in motif.finditer(contenu):
            decoupee = _decouper(correspondance.group(1))
            if decoupee is not None:
                trouvees.append((correspondance.start(), decoupee))
    return [reference for _, reference in sorted(trouvees)]


def changements_images(avant: Optional[str], apres: str) -> list[ChangementImage]:
    """Références d'image dont le tag change entre deux versions d'un fichier.

    `avant` vaut None pour un fichier créé par le correctif : rien à comparer.
    """
    if avant is None:
        return []
    refs_avant, refs_apres = _references(avant), _references(apres)
    disparues = list((Counter(refs_avant) - Counter(refs_apres)).elements())
    apparues = list((Counter(refs_apres) - Counter(refs_avant)).elements())
    changements = []
    for image, ancien in sorted(disparues, key=refs_avant.index):
        remplacante = next((r for r in apparues if r[0] == image), None)
        if remplacante is not None:
            apparues.remove(remplacante)
            changements.append(ChangementImage(image, ancien, remplacante[1]))
    return changements

# Post-mortem — « une relance devrait résoudre le problème » : une cause inventée à partir d'un job qui avait réussi

**Date** : 2026-07-17 (incident du 2026-07-16)
**Gravité** : aucun impact production. Impact décisionnel : une proposition erronée, soumise à un humain, rejetée par lui.
**Auteur** : Claude (Mirador), à la demande d'Alexandre.

## Résumé

Le 16 juillet, le workflow « Weekly Image Vulnerability Scan » de
`aboigues/kubernetes-formation` échoue (run 29509874217). Mirador ouvre
[l'issue #129](https://github.com/aboigues/kubernetes-formation/issues/129) et propose :

> **Relance du workflow** — Le workflow a complété avec succès l'extraction des images
> (33 images détectées, 21 à scanner après filtrage). […] Le problème rapporté comme
> « ÉCHEC » pourrait être un problème transitoire réseau lors de la communication avec les
> registries de conteneurs pendant la phase de scan de vulnérabilité (**qui n'est pas visible
> ici**), typique pour un workflow de scan hebdomadaire. Une relance devrait résoudre le problème.

Alexandre a rejeté : « modifications rejeter tu ne sais comment corriger ».

**Il avait raison.** Six jobs sur 35 avaient échoué sur de vraies vulnérabilités OS
(`CVE-2023-45853` CRITICAL sur `zlib1g`, entre autres), à l'étape « Fail on OS package
vulnerabilities ». La relance aurait re-échoué à l'identique, et consommé un run pour rien.

Le plus révélateur est entre parenthèses dans la proposition elle-même : *« qui n'est pas
visible ici »*. Mirador **savait** que la phase de scan était absente de son extrait. Il a
conclu quand même.

## Cause racine

**Mirador n'a jamais eu les lignes d'erreur sous les yeux. Il a décrit ce qu'il voyait — un
job qui avait réussi — et inventé une cause pour le reste.**

Le mécanisme, en deux fichiers :

1. `actions_client.telecharger_logs` téléchargeait `/actions/runs/{id}/logs` : une archive ZIP
   contenant **tous** les jobs du run, concaténés sans distinction (942 842 caractères ici).
2. `correcteur._analyser_via_claude` en gardait les 8 000 derniers caractères, sur cette
   hypothèse écrite noir sur blanc à la ligne 30 :

   > `# On ne garde que la fin du log (les erreurs de CI y sont quasi toujours), plafonnée.`

Cette hypothèse est vraie pour un run à un seul job. Sur une matrice de 35 jobs, « la fin du
log » n'est pas l'erreur : c'est le dernier job de l'archive, choisi par l'ordre de
`zipfile.infolist()`. En l'occurrence, « List Images to Scan » — qui avait réussi.

Le correcteur n'a pas mal raisonné sur ses données. Il a bien raisonné sur les mauvaises.

## Facteurs aggravants

1. **Le prompt n'offrait aucune issue honnête.** Le système imposait « UNE intervention :
   RELANCE ou PULL_REQUEST ». Face à un log sans erreur visible, aucune des deux réponses
   n'était correcte — mais « je ne sais pas » n'était pas au menu. Le modèle a produit la
   plus plausible : un échec réseau transitoire, « typique pour un workflow de scan
   hebdomadaire ». Une hypothèse formulée comme un diagnostic.
2. **La pagination masquait un tiers des jobs.** L'API `/jobs` renvoie 30 entrées par défaut ;
   le run en comptait 35. En rédigeant ce post-mortem, ma première requête a affiché « 33
   images / 3 jobs en échec ». La vérité est 35 jobs et **6** échecs : deux des jobs fautifs
   étaient sur la page 2. Le même piège, une couche plus bas — et je suis tombé dedans en
   analysant l'incident causé par lui.
3. **La proposition était plausible et bien écrite.** Elle citait des faits exacts (33 images,
   21 à scanner, avertissements Node 20) tirés du seul job qu'elle avait lu. C'est ce vernis
   de précision qui rend ce type d'erreur coûteux : rien n'y sonne faux.

## Ce que seul le test réel a révélé

Le premier correctif — cibler les jobs en échec, garder la fin de chacun — passait tous les
tests à doubles. Rejoué sur les **vrais** logs du run, il ramenait ceci :

```
===== Job en échec : Scan cassandra:4.1 =====
Post job cleanup.
Post job cleanup.
Node 20 is being deprecated. […]
```

La fin du log d'un job en échec n'est pas son erreur : c'est le **nettoyage exécuté après**.
L'annotation `##[error]` était 27 lignes plus haut, hors budget — et la table Trivy des CVE
juste avant elle. J'aurais livré un correctif vert en CI qui ratait toujours la cause, en
remplaçant « la fin du run » par « la fin du job » : la même hypothèse, d'un cran plus fine.

L'extrait se cale désormais sur la dernière annotation `##[error]` et remonte à partir d'elle.
Les horodatages ISO de chaque ligne (~29 caractères) sont retirés : c'est un quart du budget
rendu au contexte utile.

## Corrections

| Correction | Fichier |
|---|---|
| Cibler les jobs dont la conclusion est un échec, plutôt que le run entier | `actions_client.lister_jobs_en_echec` |
| Paginer la liste des jobs (`per_page=100`) — 30 par défaut cachait les jobs fautifs | `actions_client.lister_jobs_en_echec` |
| Répartir le budget entre les jobs en échec : un job verbeux n'évince plus les autres | `actions_client.assembler_extrait_jobs` |
| Ancrer l'extrait sur `##[error]`, pas sur la fin du log | `actions_client._fenetre_utile` |
| Retirer les horodatages (~25 % du budget) | `actions_client._HORODATAGE` |
| Ajouter `ABSTENTION` : Mirador peut répondre « je ne sais pas » | `correcteur.TypeCorrection` |
| Exiger que toute proposition cite une erreur **lue** dans l'extrait ; l'absence d'erreur visible n'est pas une preuve de transitoire | `correcteur._SYSTEME` |
| Une abstention escalade vers un humain au lieu de relancer ; `/approuver` sur une abstention ne déclenche rien | `traitement` |
| Rejouer #129 sur les logs réels contre le vrai modèle | `tests/replay_129.py`, `.github/workflows/replay-129.yml` |

## Enseignements

1. **Un agent doit pouvoir dire « je ne sais pas ».** Un menu de réponses qui n'offre que des
   actions produit une action, même quand la bonne réponse est l'abstention. La proposition
   signalait elle-même son angle mort (« qui n'est pas visible ici ») sans pouvoir en tirer la
   conséquence : le format l'obligeait à conclure. **Le coût d'un « je ne sais pas » est une
   escalade ; celui d'une cause inventée est une confiance perdue.**
2. **Une fenêtre de contexte est un choix de diagnostic, pas un réglage de coût.** `[-8000:]`
   a été introduit pour réduire la facture en tokens ([`7ac314e`](https://github.com/aboigues/mirador/commit/7ac314e)). Personne n'a
   demandé *quelles* 8 000 caractères. La question n'est pas « combien de log » mais « quel
   log » — et la réponse tient dans un repère explicite (`##[error]`), pas dans une position.
3. **Ce que le modèle voit compte plus que le modèle.** Le débat naturel après #129 est
   « Haiku est-il assez bon ? ». Il ne l'était pas en cause : aucun modèle ne diagnostique un
   échec dont l'erreur ne lui est pas montrée. Avant de changer de modèle, vérifier ce qu'on
   lui donne à lire.
4. **Les doubles valident le câblage, pas le jugement.** Mes tests passaient sur un correctif
   qui ramenait « Post job cleanup ». Ce sont les 942 Ko de logs authentiques qui l'ont dit.
   Un test à doubles ne peut pas révéler une hypothèse fausse sur le réel : il la reproduit.
5. **Le même piège se tend à deux niveaux.** L'incident vient d'une troncature silencieuse
   (8 000 caractères) ; en l'analysant, je suis tombé sur une autre (30 jobs par page) et j'ai
   d'abord écrit « 3 jobs en échec ». Une limite par défaut ne s'annonce jamais.

## Lien avec le post-mortem précédent

Le [post-mortem du 16 juillet](post-mortem-2026-07-16-analyse-scans.md) se terminait sur une
décision : **attendre un cas réel** avant de construire un auto-fix par registre.

Ce cas réel est arrivé le jour même. La barrière « Fail on OS package vulnerabilities » de
`kubernetes-formation` — celle dont ce post-mortem démontrait, contre mon analyse, qu'elle
existait bel et bien — s'est déclenchée sur six images. La question ouverte (« vers quel tag
aller ? ») n'a toujours pas été posée à Mirador : il n'est pas allé jusque-là.

Les deux incidents ont la même forme, à un jour d'intervalle : **une conclusion tirée d'une
fenêtre de données qui ne contenait pas la réponse.** La première fois, un `grep` dont les
premières correspondances confortaient l'hypothèse. La seconde, une troncature qui ne
contenait que des étapes réussies. Dans les deux cas, les données manquantes n'ont pas produit
de doute : elles ont produit une réponse plausible.

La leçon du 16 juillet — « un grep localise, il ne conclut pas » — visait ma façon de
travailler. Celle du 17 juillet est la même règle, appliquée à Mirador lui-même : **on ne
conclut pas sur ce qu'on n'a pas lu.** Restait à la lui apprendre.

## Actions

| Action | État |
|---|---|
| Cibler les jobs en échec + ancrage sur `##[error]` | Fait |
| `ABSTENTION` dans le correcteur, câblée jusqu'à l'issue et à `/approuver` | Fait |
| Fixture réelle du run 29509874217 + workflow de replay | Fait |
| Rejouer #129 contre le vrai modèle et constater le verdict | **À lancer** (`replay-129.yml`) |
| Rejouer un scan réel de bout en bout sur `kubernetes-formation` après déploiement | En attente |
| Vérifier si la question du tag cible se pose une fois la cause correctement lue | En attente |

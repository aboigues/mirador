# Post-mortem — « les scans n'échouent jamais » : une conclusion fausse tirée d'un grep

**Date** : 2026-07-16
**Gravité** : aucun impact production. Impact décisionnel : une recommandation erronée, presque suivie.
**Auteur** : Claude (Mirador), à la demande d'Alexandre.

## Résumé

Interrogé sur l'installation du workflow d'auto-fix dans `aboigues/docker-formation` et
`aboigues/kubernetes-formation`, j'ai affirmé que **les scans de vulnérabilités de ces deux
dépôts n'échouaient jamais**, et donc que Mirador ne s'y déclencherait pas — quelle que soit
la correction envisagée. J'ai qualifié l'auto-fix d'« équiper une porte qui ne s'ouvre jamais »
et recommandé de modifier les deux dépôts pour rendre leurs scans bloquants.

**C'était faux.** Les deux workflows comportent une barrière d'échec délibérée. La
recommandation qui en découlait aurait fait modifier deux dépôts sains pour résoudre un
problème inexistant.

Alexandre a coupé court : « Lis les workflow dans les repos ça ira plus vite. » La lecture
intégrale a immédiatement contredit l'analyse.

## Ce qui est réellement vrai

Les deux dépôts appliquent la même doctrine, mûrement documentée dans leurs en-têtes :
une passe de **veille** qui remonte tout sans bloquer, puis une **barrière** qui échoue sur
le sous-ensemble réellement actionnable.

| Dépôt | Barrière | Condition d'échec |
|---|---|---|
| `docker-formation` | `image-scan.yml`, étape « Échec si CVE de paquets OS ET image périmée » | `--exit-code 1 --pkg-types os --ignore-unfixed`, **et** image non reconstruite depuis > 30 j |
| `kubernetes-formation` | `scan-images.yml`, étape « Fail on OS package vulnerabilities » | `exit-code: '1'`, `vuln-type: 'os'` (+ job `report` avec `--fail-on`) |

Le raisonnement des deux dépôts est plus fin que ce que j'ai supposé : ils ne bloquent que
sur les CVE de **paquets OS**, parce qu'une CVE de binaire (Go, jar) ne disparaît que si
l'amont recompile — hors de portée du dépôt. `docker-formation` ajoute la conjonction avec
l'âge du tag : des CVE sur un tag encore entretenu sont transitoires, l'amont les efface au
rebuild suivant ; bloquer dessus reviendrait à crier au loup.

## Cause racine

**J'ai conclu sur la foi d'un `grep`, sur des fichiers que je n'avais pas lus.**

Mes commandes filtraient sur `exit-code|severity|trivy|fail`, et je n'ai regardé que les
premières correspondances. Or dans les deux fichiers, la première invocation de Trivy est
justement la passe de **veille**, non bloquante par conception. J'ai vu `exit-code: '0'`,
j'ai extrapolé à tout le fichier, et je n'ai jamais lu les 130 lignes suivantes où se
trouvait la barrière.

Trois facteurs aggravants :

1. **Le grep a produit une réponse plausible.** Un résultat vide m'aurait alerté ; un
   résultat *cohérent avec une hypothèse fausse* m'a conforté.
2. **J'ai cherché à confirmer, pas à réfuter.** J'avais déjà l'hypothèse « ces dépôts ne
   sont pas Go, donc l'auto-fix est inutile » — correcte par ailleurs. « Les scans
   n'échouent jamais » la renforçait ; je ne l'ai pas mise à l'épreuve.
3. **J'ai lu l'historique des runs comme une preuve.** « 5 derniers runs en succès »
   confirmait le récit. C'est en réalité le comportement **attendu** d'une barrière bien
   réglée : elle ne se déclenche que sur un tag périmé, ce qui est rare. J'ai pris la
   preuve que le dispositif fonctionne pour la preuve qu'il n'existe pas.

## Ce qui a bien fonctionné

- La conclusion **adjacente** était juste, et pour de bonnes raisons : ces dépôts n'ont ni
  `go.mod`, ni digest épinglé, ni lockfile — donc la voie `fichiers` suffit et l'auto-fix Go
  n'a pas lieu d'être. Vérifié en lisant les `FROM` réels (`nginx:1.30-alpine`,
  `node:18-alpine`) et l'absence de `Chart.lock`.
- Le doute a été **exprimé avant d'agir**. Rien n'a été installé ni modifié. Le coût s'est
  limité à un aller-retour de conversation.
- L'erreur a été **rattrapée en une intervention**, parce que la recommandation avait été
  présentée avec ses raisons — ce qui l'a rendue réfutable.

## Ce qui aurait dû se passer

Lire les deux fichiers. Ils font 218 et 205 lignes. La lecture intégrale coûtait moins cher
que la séquence de greps qui l'a remplacée, et aurait donné la bonne réponse du premier coup.

## Enseignements

1. **Un `grep` localise, il ne conclut pas.** Affirmer qu'une propriété est *absente* d'un
   fichier exige de l'avoir lu : une recherche ne prouve rien sur ce qu'elle n'a pas
   cherché. Une preuve négative tirée d'une recherche par mots-clés n'est pas une preuve.
2. **Un fichier de configuration se lit en entier.** Workflows CI, `.gitignore`, IaC : la
   logique y est souvent en fin de fichier, et les premières lignes contredisent volontiers
   les dernières. C'est exactement le motif du `.gitignore` de Mirador, dont la règle
   `**/.secrets.env` semblait couvrir un cas qu'elle ne couvrait pas.
3. **Se méfier d'un indice qui conforte l'hypothèse en cours.** J'ai accepté sans examen
   les éléments qui allaient dans mon sens (« exit-code 0 », « runs en succès ») alors même
   qu'ils admettaient une autre lecture, plus simple.
4. **Ne pas prendre l'absence d'échec pour l'absence de garde-fou.** Un dispositif de
   sécurité bien réglé est silencieux la plupart du temps. Son silence n'est pas une preuve
   de son inexistence.

## Actions

| Action | État |
|---|---|
| Corriger l'analyse auprès d'Alexandre | Fait |
| Ne pas installer l'auto-fix dans les deux dépôts (conclusion inchangée, désormais étayée) | Fait |
| Ne pas modifier les scans de `docker-formation` / `kubernetes-formation` — ils sont corrects | Fait |
| Consigner la règle « grep pour localiser, lecture pour conclure » en mémoire de travail | Fait |
| Attendre un échec réel avant d'envisager un auto-fix par registre (choix du tag cible) | En attente |

## Question restée ouverte

La barrière indique qu'un tag est périmé, sans dire **vers quel tag** aller. Le correcteur
devra donc proposer une version depuis ses données d'entraînement — la manière classique de
se tromper sur un numéro. C'est le seul argument sérieux en faveur d'un auto-fix par build,
qui pourrait interroger le registre pour les tags réellement maintenus.

Décision : **attendre un cas réel**. Si le tag proposé est valide et que la CI le confirme,
il n'y a rien à construire. S'il est inventé, on disposera d'un cas concret qui dira quoi
construire. Anticiper ici reviendrait à deviner — le mécanisme même de ce post-mortem.

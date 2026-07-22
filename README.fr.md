# spaghetti-detector

Détecteur de code spaghetti et de "défaillances" architecturales.

Scanne les paquets de l'espace de travail à la recherche d'anti-patterns, de violations architecturales et de problèmes structurels du code — des problèmes au niveau d'une seule fonction (fonctions longues, imbrication profonde, complexité cyclomatique élevée) jusqu'aux problèmes qui n'apparaissent qu'en observant l'ensemble des fichiers : imports circulaires réels (pas seulement une heuristique parent/enfant), corps de fonctions copiés-collés, et le modèle de duplication gemelle synchrone/asynchrone (`load`/`aload`, `foo`/`foo_async`) où un correctif appliqué à une gemelle ne parvient silencieusement jamais à l'autre.

Chaque paquet demandé est examiné de manière concurrente — un agent par paquet — puis consolidé dans un rapport unique.

Le registre de paquets est générique et configurable via un fichier YAML, des paramètres ad-hoc en ligne de commande, ou les deux — voir [Configurer les Paquets](#configurer-les-paquets).

## Pourquoi Ce Projet Existe

Le code spaghetti généré par l'IA — souvent appelé « code bas-de-gamme » — est extrêmement courant car les grands modèles de langage (LLM) priorisent l'achèvement fonctionnel immédiat (la « voie heureuse ») sur l'architecture logicielle à long terme. Bien qu'il semble syntaxiquement parfait et très commenté, il souffre souvent de problèmes structurels :

- **Structures monolithiques:** L'IA a tendance à déverser de grandes quantités de logique dans des fichiers uniques et gigantesques plutôt que de séparer les responsabilités.
- **Duplication par copier-coller:** Au lieu de refactoriser le code en fonctions réutilisables, les LLM répètent souvent le même bloc de code avec des variations mineures.
- **Complexité accidentelle:** Car l'IA manque de perspective globale du système, elle connecte les fonctionnalités de manière très couplée.
- **Dépendances hallucinées:** Un risque significatif où l'IA suggère l'utilisation de bibliothèques ou paquets qui n'existent pas.

Cette prévalence découle de la nature fondamentale de l'entraînement de l'IA : les LLM sont optimisés pour prédire le prochain token logique basé sur la probabilité, pas pour concevoir des logiciels maintenables. Bien qu'une IA puisse produire un script fonctionnel rapidement, elle manque de la prévoyance intuitive que les développeurs expérimentés utilisent pour construire des applications modulaires et évolutives.

Le code spaghetti écrit par des humains est extrêmement courant et existe depuis les débuts de la programmation. Tandis que l'IA crée du code désordonné par manque de conscience situationnelle, les humains le créent généralement par manque de temps, des exigences changeantes ou un manque d'expérience.

### Pourquoi les Humains Écrivent du Code Spaghetti

- **Délais serrés:** Les développeurs se précipitent pour livrer des fonctionnalités, priorisant la vitesse sur une architecture propre.
- **Dérive de périmètre:** Ajouter constamment de nouvelles fonctionnalités à un ancien système sans réécrire la structure de base.
- **Lacunes de compétences:** Les développeurs juniors ne comprennent peut-être pas encore les patterns de conception ou comment séparer les responsabilités.
- **L'habitude du « copier-coller »:** Réutiliser des blocs de code fonctionnels dans un projet au lieu de créer des fonctions réutilisables.
- **Absence de revues de code:** Les équipes sautent les revues par les pairs, laissant passer une logique brouillonne en production.

### IA vs. Code Spaghetti Humain

- **Style humain:** Présente souvent des boucles imbriquées massives, des noms de variables confus (comme `x` ou `data1`), et des notes `TODO` oubliées.
- **Style de l'IA:** A généralement une apparence très professionnelle, une indentation parfaite, et de beaux commentaires, mais la logique sous-jacente est profondément emmêlée et redondante.

## Comment spaghetti-detector Aide

Chaque problème décrit ci-dessus correspond à une ou plusieurs règles appliquées mécaniquement. Le détecteur ne devine pas — il mesure des seuils concrets et signale des violations exactes.

### Correspondance Problèmes → Règles

| Problème | Règles du Détecteur | Ce Qu'Il Détecte |
|----------|-------------------|-----------------|
| **Structures monolithiques** | `god-class`, `god-module`, `long-function`, `long-file`, `deep-nesting` | Classes avec 25+ méthodes, fichiers de plus de 400 lignes, fonctions dépassant 50 lignes, imbrication au-delà de 5 niveaux |
| **Duplication par copier-coller** | `duplicate-function-body`, `sync-async-duplication` | Corps de fonctions identiques (5+ lignes), paires gemelles synchrone/asynchrone avec ≥60% de similarité textuelle |
| **Complexité accidentelle** | `high-complexity`, `excessive-returns`, `message-chain`, `deep-inheritance`, `excessive-decorators` | Complexité cyclomatique supérieure à 10, fonctions avec 4+ chemins de retour, appels chaînés plus profonds que 3 niveaux, héritage dépassant 4 niveaux |
| **Violations de couches** | `layer-violation`, `transport-in-library`, `import-cycle`, `encapsulation-violation` | Code de bibliothèque important des frameworks de transport, chaînes d'imports circulaires, accès à des attributs privés entre objets |
| **Lacunes de sécurité des types** | `missing-return-type`, `missing-param-type`, `untyped-dict`, `bare-except` | Fonctions publiques sans annotations, `dict` sans type dans les annotations, clauses `except:` sans type |
| **Code mort et encombrement** | `dead-code`, `unused-import`, `star-import`, `todo-marker`, `magic-number` | Instructions inaccessibles après `return`/`raise`/`break`, `from x import *`, littéraux numériques inexpliqués |

### De la Détection à la Remédiation

Le détecteur produit un rapport consolidé avec un score de santé et une note par paquet :

```
  Package          Grade   Score   Files   KLOC   Issues
  ──────────────── ───── ───────  ────── ────── ───────
  boti-data           B    78.3       18   3.2       12
  etl-core            A    92.1       14   2.8        5
  OVERALL             B    82.5       32   6.0       17
```

Utilisez `--plan` pour obtenir une feuille de route de remédiation priorisée scorée par `poids_sévérité × effort_correction` :

```bash
uv run spaghetti --plan --top 10
```

```
  #   Pri  Rule                           Sev  Effort     Issues  Score
  ─── ──── ────────────────────────────── ──── ───────── ──────  ─────
  1   P0   import-cycle                   ✖    major        3   30.0
  2   P0   god-class                      ✖    major        2   30.0
  3   P1   high-complexity                ⚠    moderate     5   15.0
  4   P1   long-function                  ⚠    moderate     4   12.0
```

Cela garantit que les problèmes structurels (imports circulaires, god-classes) sont corrigés avant les problèmes cosmétiques (annotations de types manquantes, nombres magiques) — maximisant l'impact par unité d'effort.

## Utilisation

```bash
uv run spaghetti
uv run spaghetti --packages boti-data boti-dask
uv run spaghetti --severity error
uv run spaghetti --top 10 --exclude tests/ examples/
uv run spaghetti --json > report.json
uv run spaghetti --plan --top 10
uv run spaghetti --config spaghetti.yaml
uv run spaghetti --package my-lib=my-lib/src/my_lib
```

Codes de sortie : `0` (propre), `1` (avertissements présents), `2` (erreurs présentes) — sûr à intégrer dans un CI comme verrou.

### Options

| Paramètre | Valeur par Défaut | Description |
| --- | --- | --- |
| `--config` | aucun | Fichier YAML avec un mappage `packages: {name: path}` (voir ci-dessous) ; remplace les valeurs par défaut intégrées |
| `--package` | aucun | Ajoute ou écrase un paquet sous la forme `NAME=PATH` (répétable) ; appliqué par-dessus `--config` ou les valeurs par défaut |
| `--packages` | tous les paquets résolus | Noms à scanner dans le registre résolu |
| `--severity` | `info` | Sévérité minimale à afficher (`info` / `warning` / `error`) |
| `--json` | désactivé | Sortie en JSON au lieu du rapport en console |
| `--top` | `5` | Nombre de pires fichiers à lister par paquet |
| `--exclude` | aucun | Sous-chaînes de chemin à exclure du scan |
| `--min-duplicate-lines` | `5` | Longueur minimale de fonction à considérer pour la détection de corps dupliqués |
| `--twin-similarity` | `0.6` | Ratio minimum de similarité textuelle (0–1) pour signaler une paire gemelle synchrone/asynchrone |
| `--plan` | désactivé | Sortie d'un plan de remédiation priorisé au lieu du rapport standard

Exécutez `uv run spaghetti --help` pour la liste complète.

## Suppression en Ligne

Supprimez des résultats spécifiques sur une ligne avec `# spaghetti-ignore[règle]` :

```python
# Supprimer une règle spécifique
def f():  # spaghetti-ignore[long-function]: intentionnellement longue
    ...

# Supprimer toutes les règles sur une ligne
x: dict = {}  # spaghetti-ignore : revu, aucun problème
```

Le marqueur s'applique à la ligne où il apparaît et à la ligne directement au-dessus (de sorte qu'un marqueur peut se situer au-dessus d'une ligne `def` trop longue pour un commentaire en fin de ligne). Les résultats supprimés sont comptabilisés dans le rapport (`suppressed: N` dans l'en-tête) plutôt que supprimés silencieusement — ils restent visibles.

## Sortie JSON

Avec `--json`, le rapport est un objet JSON unique sur stdout :

```json
{
  "issues": [
    {
      "file": "src/my_module.py",
      "line": 42,
      "severity": "warning",
      "rule": "long-function",
      "message": "my_func() is 65 lines (max 50)",
      "package": "my-lib"
    }
  ],
  "suppressed": 3
}
```

## Plan de Remédiation

Avec `--plan`, le détecteur produit un ordre de correction priorisé au lieu du rapport standard. Chaque règle est scorée par `poids_sévérité × effort_correction` et regroupée en niveaux de priorité (P0–P3) :

```bash
uv run spaghetti --plan --top 10
```

**Niveaux de priorité :**
- **P0** (score ≥ 12) : CRITIQUE — corriger immédiatement (p. ex., imports circulaires, god-classes)
- **P1** (score ≥ 7) : ÉLEVÉ — corriger ce sprint
- **P2** (score ≥ 3) : MOYEN — planifier pour le prochain cycle
- **P3** (score < 3) : FAIBLE — suivre dans le backlog

Le plan regroupe les problèmes par règle, compte les fichiers affectés et liste un ordre de correction recommandé. Cela facilite le démarrage d'un cycle d'amélioration de la qualité du code en commençant par les corrections à plus fort impact.

## Règles

Le détecteur vérifie **36 règles** sur quatre niveaux :

**Vérifications AST par fichier (30 règles) :** `long-function`, `high-complexity`, `missing-return-type`, `missing-param-type`, `too-many-params`, `excessive-returns`, `boolean-flag-params`, `deep-nesting`, `untyped-dict`, `unused-import`, `swallowed-exception`, `duplicate-branch`, `encapsulation-violation`, `god-class`, `layer-violation`, `transport-in-library`, `potential-circular-import`, `god-module`, `mutable-default`, `bare-except`, `star-import`, `global-mutable`, `scope-mutation`, `dead-code`, `message-chain`, `excessive-decorators`, `magic-number`, `missing-else`, `lazy-class`, `deep-inheritance`.

**Vérifications de texte source par fichier (2 règles) :** `long-file`, `todo-marker`.

**Vérifications d'infrastructure (1 règle) :** `syntax-error` (fichiers échouant `ast.parse()`).

**Vérifications inter-fichiers par paquete (3 règles) :** `import-cycle`, `duplicate-function-body`, `sync-async-duplication`.

Voir [SDD.md](SDD.md) pour le catalogue complet des règles, seuils et formule de scoring.

## Configurer les Paquets

Sans paramètres, `spaghetti` découvre automatiquement les paquets du répertoire courant : chaque sous-répertoire immédiat contenant au moins un fichier `.py` devient son propre paquet nommé (en ignorant `.venv`, `.git`, `__pycache__`, `node_modules` et les répertoires de bruit similaires), et tout fichier `.py` isolé directement dans le répertoire courant est regroupé dans un paquet supplémentaire nommé d'après le répertoire lui-même. Pour cibler d'autres paquets — dans cet espace de travail, un autre espace de travail, ou n'importe quel répertoire sur le disque — utilisez `--config` et/ou `--package`.

**Prépondérance :**
1. Aucun paramètre donné → la découverte automatique du répertoire courant est utilisée, comme décrit ci-dessus.
2. `--config` donné → son mappage `packages:` est utilisé comme jeu complet, explicitement plutôt que découvert automatiquement.
3. Les entrées `--package NAME=PATH` sont alors superposées sur le jeu produit par (1) ou (2) — ajoutant de nouveaux noms ou écrasant ceux déjà définis, de sorte qu'un fichier de configuration et un ajout ad-hoc rapide fonctionnent ensemble.

### `--config` : Fichier YAML

```yaml
# spaghetti.yaml
packages:
  my-lib: my-lib/src/my_lib
  my-service: services/my-service/src/my_service
```

Les chemins sont résolus **par rapport au répertoire du fichier de configuration lui-même**, pas au répertoire de travail de l'appelant, de sorte que la même configuration fonctionne où que vous invoquiez `spaghetti`.

```bash
uv run spaghetti --config spaghetti.yaml
```

### `--package` : Entrées Ad-hoc en Ligne de Commande

```bash
uv run spaghetti --package my-lib=my-lib/src/my_lib --package other=../other/src/other
```

Répétable ; les chemins sont résolus par rapport au répertoire courant. Combiné avec `--config` pour écraser ou étendre un fichier de configuration pour une exécution sans le modifier.

## Développement

```bash
uv run pytest spaghetti/tests/
```

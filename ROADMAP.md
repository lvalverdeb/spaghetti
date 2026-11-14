# Roadmap: spaghetti at the big boys' table

> Goal: a credible peer to SonarQube, CodeClimate/Qlty, CodeScene and Ruff/Pylint for
> Python structural-quality analysis. Not a clone of any of them. "Not in the same room,
> but at the same table": a team evaluating those tools should be able to put spaghetti
> on the shortlist without embarrassment, and pick it for the things it does that they
> do not.

Drafted 2026-11-13 against spaghetti 1.4.0 / spaghetti-rs 1.4.1. Companion to
[SDD.md](SDD.md) (what the rules are) and
[RUST_PORT_PROPOSAL.md](RUST_PORT_PROPOSAL.md) (why there are two implementations).

---

## 1. Where we stand

What is genuinely competitive today:

- 40 rules, cross-file analysis (real import cycles with relative-import resolution and
  `TYPE_CHECKING` exclusion, duplicate bodies, high-coupling, orphan interfaces).
- `sync-async-duplication`. No mainstream tool has this.
- Remediation plan scored by severity × effort, with fix-pattern hints written for both
  humans and LLM agents.
- Inline suppressions that carry a reason and stay visible in the report.
- Two conformant implementations, 265 + 135 tests, exit codes, JSON output.

What keeps it off the shortlist:

| Capability | Sonar | Qlty | CodeScene | Ruff / Pylint | spaghetti today |
|---|---|---|---|---|---|
| Adopt on a legacy repo (baseline / new-code only) | yes | yes | yes | partial | **no** |
| Configurable thresholds | yes | yes | yes | yes | **no** (constants in `config.py`) |
| SARIF / GitHub code-scanning | yes | yes | — | yes | **no** |
| Cognitive complexity | yes | — | yes | yes (ruff C901 is cyclomatic; `complexipy` is cognitive) | **no** |
| Cyclomatic complexity that matches McCabe | yes | yes | yes | yes | **undercounts** (`match`, ternary, comprehension `if`) |
| Clone detection beyond exact AST match (type-2) | yes | yes | yes | — | **no** |
| Trend over time, hotspots by churn | yes | yes | **best** | — | **no** |
| Editor / PR integration | yes | yes | yes | yes | **no** |
| Runs on Python 3.9–3.12 projects | yes | yes | yes | yes | **no** (requires 3.13 runtime) |
| No private dependencies | yes | yes | yes | yes | **no** (`boti`, monorepo package names) |
| Agent-native interface (MCP, structured fix hints) | — | — | — | — | partial, and nobody else has it |

The pattern: the analysis core is at the table already. The **adoption surface** is not.
Phases are ordered to fix that first, because nothing else matters until someone can run
it on a real repo without drowning or crashing.

## 2. Principles

1. **Adoption before breadth.** A team must be able to turn it on today with zero new
   findings and ratchet from there. Every new rule after Phase 1 ships behind that gate.
2. **Measure the way the industry measures.** Where a metric has a published definition
   (McCabe, Sonar's cognitive complexity, PMD CPD tokens), match it exactly so numbers
   are comparable across tools. Differentiate on rules and remediation, not on
   private definitions of "complexity".
3. **One implementation ships; the other is the oracle.** Two ports at parity is a tax
   only worth paying if one is the reference spec and the other is the product. See
   Decision D1.
4. **Precision over recall.** Every rule gets a measured false-positive rate on a
   public corpus before it is on by default. Noisy rules are what get linters uninstalled.
5. **Agents are first-class users.** The LLM-oriented fix hints are the one asset the
   incumbents lack. Keep structured output and remediation guidance a step ahead of the
   console report.

## 3. Phases

Effort is a rough single-developer estimate. Acceptance criteria are what "done" means;
nothing moves to the next phase until the current one's criteria hold on CI.

### Phase 0 — Credibility hygiene (≈ 1–2 weeks)

Make the published artifact something a stranger would install.

- **Drop private coupling.** Remove the `boti` dependency (`detector.py` uses one base
  class for a process-pool wrapper; replace with `concurrent.futures` directly). Remove
  `etl-core`, `boti-data` and friends from `ALLOWED_IMPORT_PREFIXES` and the layer rules
  in `config.py`; those become user configuration (Phase 1) with no defaults.
- **Lower the runtime floor.** Python ≥ 3.11 for the Python port. Audit for 3.12/3.13-only
  syntax and stdlib. The Rust binary already runs anywhere; make that the headline
  install path in the README.
- **Issue model.** Add `column`, `end_line`, `end_column` and a stable `fingerprint`
  (hash of rule + normalized location + normalized message) to `Issue`. SARIF, baselines
  and PR annotations all need these. Bump JSON schema to v1 and freeze it with a
  `schema_version` field.
- **Dogfood gate.** CI fails if spaghetti's own grade drops below the current B, and
  the three `error`-level findings on `ast_per_file.py` are fixed or justified. Split
  `tests/test_detector.py` (2979 lines) by module.
- **Translated READMEs** either regenerated from the English one in CI or deleted. Stale
  translations read as abandonment.

Acceptance: `pip install spaghetti-detector` on a clean Python 3.11 venv with no private
index works; `uv run spaghetti` on itself passes the CI gate; JSON output validates
against a committed schema.

### Phase 1 — Adoptable on legacy code (≈ 3–4 weeks)

This is the phase that gets spaghetti onto the shortlist.

- **Baseline.** `spaghetti --baseline .spaghetti-baseline.json` records fingerprints of
  every current finding; subsequent runs report only findings not in the baseline.
  `--update-baseline` rewrites it. Baseline entries that no longer match are reported as
  "fixed" so the ratchet is visible.
- **Changed-lines mode.** `--diff-base origin/main` restricts reporting to findings whose
  line range intersects lines added or modified in `git diff`. This is Sonar's "new code"
  concept and the single most requested feature of any gate tool.
- **Configurable thresholds.** `[tool.spaghetti]` in `pyproject.toml`, plus the existing
  `spaghetti.yaml`, covering every constant in `config.py`, per-rule severity overrides
  and per-rule enable/disable. Layer rules and allowed-import prefixes move here from
  code. Rust reads the same files (serde already has `toml` and YAML).
- **SARIF 2.1.0 output.** `--format sarif`. Includes rule metadata (help text is the
  existing fix hint), locations with columns, fingerprints as `partialFingerprints`.
  This alone gets findings into GitHub code scanning, VS Code's SARIF viewer, and
  every enterprise aggregator.
- **Exit-code policy.** `--fail-on error|warning|info|never`. Today's fixed
  0/1/2 mapping stays the default.
- **GitHub Action and pre-commit hook.** Thin wrappers around the Rust binary. The Action
  uploads SARIF and posts a summary comment on PRs.
- **Noise audit.** Scan five public repos (suggested: `httpx`, `rich`, `fastapi`,
  `pydantic`, `black`) and classify every finding of every rule as true/false positive.
  Publish the table in `docs/precision.md`. Fix or demote any rule under 80 % precision.
  Known candidates: `magic-number` on `match`/`case` pattern literals, `missing-else`,
  `boolean-flag-params` on well-known APIs.
  *Done 2026-11-13 (Python):* strict precision was 12 % before calibration; three detector
  bugs fixed, nine rules narrowed, seven demoted to off-by-default; corpus findings
  2727 → 940. See `docs/precision.md`. Rust mirror pending.

Acceptance: on each of the five corpus repos, `spaghetti --baseline` then a second run
reports zero findings; `--diff-base` on a synthetic PR reports only in-diff findings;
SARIF validates against the OASIS schema and renders in GitHub code scanning; all rules
≥ 80 % precision or off by default.

### Phase 2 — Measure like the big boys (≈ 4–6 weeks)

Bring the metrics to published definitions and close the clone-detection gap.

- **Cyclomatic complexity parity with Radon/McCabe.** Count `match` arms, `IfExp`,
  comprehension `if` clauses, `while/else`, and `BoolOp` the way `radon cc` does.
  Conformance test: identical numbers to Radon on the corpus. Expose `--complexity-style
  mccabe|legacy` for one release so existing baselines do not shift silently.
- **Cognitive complexity** per Sonar's 2018 specification (nesting increments, no
  increment for shorthand structures, recursion counts). New rule
  `high-cognitive-complexity`, default threshold 15 as Sonar uses. This is the metric
  teams actually argue about in reviews; cyclomatic is what CI gates on.
- **Token-based clone detection.** Replace the exact `ast.dump` hash with a normalized
  token stream (identifiers → `ID`, literals → `LIT`, keep structure) and a suffix-array
  or rolling-hash window, PMD CPD style. Detects type-2 clones (renamed variables),
  partial-function clones, and clones that span function boundaries. Configurable
  `min_tokens` (default 50) alongside the existing `min_duplicate_lines`. Report clone
  *groups* with every location, not pairs.
- **Cross-package analysis.** Import cycles, coupling and clones across all registered
  packages, not just within one. Today each package is scanned in isolation, which hides
  exactly the workspace-level tangles the README promises to find.
- **Maintainability Index** (SEI/Radon formula) per file as an optional secondary score,
  because that is the number Qlty and Sonar users already know how to read.

Acceptance: `radon cc` and spaghetti agree on every function in the corpus; cognitive
complexity matches SonarCloud on a 50-function hand-checked sample; the clone detector
finds a variable-renamed copy of a 20-line function that the current detector misses;
a two-package import cycle in the test fixtures is reported.

*Status 2026-11-13 (Python):* all five items landed; version 2.0.0 (D3 break, no legacy
flag). `radon cc` agreement 3211/3211 corpus functions; MI within 2 points of `radon mi`
on 250/255 files (Halstead and complexity exact, logical-line counting approximated);
cognitive complexity implemented from the Sonar specification with spec-example tests —
the SonarCloud sample check is not possible offline and remains open; `duplicate-code`
finds renamed copies (tests/test_clones.py) and ships at `info` (see docs/precision.md);
two-package cycle covered by tests/test_workspace.py. Rust mirror pending.

### Phase 3 — Trend, history and hotspots (≈ 3–4 weeks)

This is where CodeScene lives, and it is the most valuable thing the pure linters do
not do.

- **Snapshot store.** `spaghetti --snapshot` appends a run (git SHA, timestamp, per-file
  and per-package scores, finding fingerprints) to a local SQLite or JSONL store.
- **Compare.** `spaghetti --compare <sha|snapshot>` prints a delta: score change,
  findings introduced/fixed, files that got worse. Usable as a PR status.
- **Hotspots.** Combine per-file health with `git log` churn (commits touching the file
  in the last N months, optionally weighted by author count). Report `health × churn`
  ranking: the files that are both tangled and actively edited are where the plan's
  effort should go. Feed this into `--plan` so P0 is "worst *and* hottest", not just
  worst.
- **HTML report.** Single self-contained file: scorecard, hotspot list, per-file
  findings, trend sparklines from the snapshot store. No server, no account.

Acceptance: a repo with 20 synthetic commits produces a trend chart; the hotspot ranking
on `fastapi` puts its routing core above its rarely-touched utility modules; `--compare`
between two SHAs reports the exact findings a known commit introduced.

*Status 2026-11-13 (Python, version 2.1.0):* all four items landed — JSONL snapshot store
(`--snapshot`, `--snapshot-store`), `--compare REF` with fingerprint matching, `--hotspots`
with a churn-weighted `--plan`, and `--format html`. On a 400-commit `fastapi` clone the
hotspot ranking is `dependencies/utils.py`, `routing.py`, `openapi/utils.py` — the routing
core — above every utility module. The trend chart is covered by a CLI test with a
snapshot series rather than 20 commits. Rust mirror pending.

### Phase 4 — Ecosystem and agent interface (≈ 3–4 weeks)

- **Editor integration.** Either an LSP server (Rust, `tower-lsp`) publishing
  diagnostics, or document that SARIF output plus the standard SARIF viewer extensions
  is the supported path. Recommend starting with the SARIF path and adding LSP only if
  users ask.
- **MCP server.** Expose `scan`, `plan`, `explain_rule`, `compare` and `baseline` as
  tools so coding agents can ask "what should I fix in this package and how" and get
  the existing fix hints as structured data. This is the feature none of the incumbents
  has and it leans on the asset spaghetti already built: remediation text written for
  LLMs. Ship as a subcommand of the same binary (`spaghetti mcp`).
- **Rule documentation site.** One page per rule: what it catches, why it matters, the
  pattern that fixes it, examples, precision figure from Phase 1, the Pylint/Sonar
  equivalent if any. Generated from rule metadata so it cannot drift.
- **Plugin-free custom rules.** Not a plugin API. A small YAML/TOML "architecture
  contract" format (allowed/forbidden import edges between named layers), because that
  is the custom rule 90 % of teams actually want and it is what `import-linter` and
  `Tach` sell.

Acceptance: a Claude Code or Cursor session can run `spaghetti mcp`, call `plan`, and
receive JSON with rule, location, severity and fix pattern; the docs site lists all
rules with precision numbers; an architecture contract forbidding `domain → transport`
produces a finding in the test fixtures.

*Status 2026-11-13 (Python, version 2.2.0):* all four items landed. Editor integration is
the documented SARIF path (README "Editor integration"). `spaghetti mcp` serves `scan`,
`plan`, `explain_rule`, `compare`, `baseline` over stdio with a dependency-free JSON-RPC
layer; `plan` returns rule, files, severity and the fix pattern per step and is covered by
an end-to-end stdio test. `docs/rules/` (47 pages) is generated from `rules.py` +
`rule_docs.py` + `docs/precision.md` with a staleness test. `[[tool.spaghetti.contracts]]`
(`forbidden` and `layers`) is evaluated on the workspace import graph as
`contract-violation`; the fixture test forbids `shop.domain → shop.transport`. Rust mirror
pending (Phases 3 and 4 together).

### Phase 5 — Scale and hardening (≈ 2–3 weeks, then ongoing)

- **Benchmark corpus and performance budget.** Scan `django`, `pandas`, `airflow`
  (≈ 1–2 M lines combined) in CI. Budget: Rust under 15 s, Python under 120 s, best of two runs on the
  4-core GitHub runner (first set at 10 s / 90 s from laptop timings; the runner measured
  11.9 s for Rust and, for the same Python commit, 61 s then 100 s). Track in a committed benchmark table.
- **Incremental cache.** Per-file results keyed by content hash, invalidated by
  config hash. Cross-file checks re-run over the cached per-file summaries. Makes
  pre-commit and editor use viable on large repos.
- **Robustness suite.** Fuzz the scanner with `hypothesis` (Python) and `cargo-fuzz`
  (Rust) on generated ASTs; property: never raises, every input yields a report.
  Hard limits on file size and recursion depth with an `info` finding when hit.
- **Conformance in CI.** The Python↔Rust byte-identical comparison from the port
  proposal runs on every PR over the corpus, not just by hand.

Acceptance: benchmark table committed and CI-enforced; fuzzers run 10 minutes per PR
with no crash; conformance diff is empty on the corpus.

*Status 2026-11-13 (Python, version 2.3.0):* benchmark table in `docs/benchmark.md` from
`scripts/bench.py` over the pinned corpus (`scripts/corpus.lock`: django, pandas, airflow,
1.07 M lines, 3144 files): Python 81.6 s cold / 20.1 s with a warm `--cache`, Rust 8.2 s, on an
11-core laptop. pandas alone was 79 s in one process and 130 s on the 4-core GitHub runner, so
the first CI run failed the budget; packages of 64 files or more are now scanned as file chunks
across the process pool and reassembled in file order (`detector._scan_files`), which brings the
combined run to 38 s with 4 workers on the laptop with byte-identical output. CI enforces the
budget with `--runs 2 --budget 120`. Memoizing the whole-tree walk shared by the per-file checks had
already cut Python time by a third. The sequential tail was then cut (2.5.0): the package-level
checks consume per-file *facts* the worker records while it holds the tree (`package_level.collect_facts`),
each file's findings are finalized in the worker, and the parent never re-parses; clone detection
indexes only recurring window hashes. pandas with 4 workers: 32.7 s → 19.7 s cold, 17.9 s → 3.9 s
warm. Incremental cache (`spaghetti.cache`, `--cache`/`--cache-dir`, `cache`/`cache-dir`
settings) keyed by content hash under a settings+version key; entries hold the facts, so a hit
needs no parse. Robustness: `tests/test_fuzz.py`
(hypothesmith programs + arbitrary text, never raises; CI fuzz job ≈ 10 min), `max-file-bytes`
(1 MiB) and a recursion guard emit `scan-limit` instead of stalling or crashing; the sweep
found and fixed a crash on a lone-surrogate string annotation (pandas). Conformance:
`scripts/conformance.py --rust BIN` over the whole corpus; it is clean on the five audit repos
and this tree, and on django/airflow it exposed five Rust deviations (twin-pair naming and
ratio, a long-function flat-nesting case, untyped-dict order and three misses, one
deep-nesting case) now queued for the Rust port, whose CI will run the script per PR.

## 4. Decisions (resolved 2026-11-13: yes to all four)

All four accepted as recommended. Recorded here so the phases above can assume them.

- **D1. Which port is the product? → Rust.** The Rust binary is what users
  install (`cargo install`, `pip install` via a `maturin`-built wheel, prebuilt
  binaries in GitHub releases). The Python port stays as the readable reference
  implementation and the conformance oracle, and is where new rules are prototyped.
  Every phase above assumes this. The alternative, keeping both at full parity as
  products, roughly doubles Phases 1–4.
- **D2. Python runtime floor → 3.11.** 3.10 adds `match` already; 3.9 would
  mean giving up `|` union syntax throughout. If D1 is accepted, this only affects the
  reference implementation and contributors.
- **D3. Metric definitions → clean break at 2.0.** Accept that adopting McCabe-exact cyclomatic complexity
  will change existing scores. Ship the one-release `--complexity-style legacy` escape
  hatch or just take the break at a 2.0 version bump. Decision: take the break at 2.0,
  no legacy flag.
- **D4. Scope lock → Python only** through Phase 5. The per-file checks are AST-shaped
  and a second language is a second product. Revisit after Phase 5 if there is demand.

## 5. Non-goals

- Hosted SaaS, accounts, dashboards with login. The HTML report and snapshot store
  cover the single-team case; aggregators ingest SARIF for the rest.
- Security analysis. That is [fenceline](../fenceline)'s job. Do not duplicate.
- Auto-fix. The fix hints describe a refactor pattern; applying it is an agent's or a
  human's job. Generating refactors mechanically is a different product with a
  different failure mode.
- Replacing Ruff or Pylint. spaghetti should run *alongside* them and avoid their
  territory (style, naming, unused variables). Deprecate `unused-import` and
  `star-import` once Phase 1 lands; Ruff owns those and does them better.

## 6. Sequencing summary

| Phase | Theme | Gate it unlocks | Effort |
|---|---|---|---|
| 0 | Credibility hygiene | A stranger can install it | 1–2 wk |
| 1 | Adoptable on legacy code | A team can turn it on today | 3–4 wk |
| 2 | Measure like the big boys | Numbers comparable to Sonar/Radon | 4–6 wk |
| 3 | Trend and hotspots | Answers "what should we fix first, really" | 3–4 wk |
| 4 | Ecosystem and agents | Shows up where developers work | 3–4 wk |
| 5 | Scale and hardening | Runs on million-line repos, never crashes | 2–3 wk |

Roughly five to six months of focused single-developer work to the end of Phase 4.
Phase 5 is where the project then lives.

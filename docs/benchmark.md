# Benchmark

Generated 2026-11-14 by `scripts/bench.py` on arm64 (Darwin), Python 3.13.6, 11 CPUs, best of 2. Corpus pinned in `scripts/corpus.lock`: django@f24f38c, pandas@ff36f37, airflow@de88871.

| Package | Files | Lines | Findings | Python (s) | Python, warm cache (s) | Rust (s) | Rust findings |
|---|---|---|---|---|---|---|---|
| django | 909 | 166,424 | 19041 | 2.2 | 0.8 | 0.4 | 19041 |
| pandas | 1446 | 733,823 | 18183 | 15.8 | 3.7 | 1.4 | 18183 |
| airflow | 789 | 165,702 | 4014 | 1.8 | 0.7 | 0.3 | 4014 |
| all three | 3144 | 1,065,949 | 41247 | 19.3 | 5.4 | 2.0 | 41247 |

Budget (ROADMAP Phase 5): the *all three* row under 120 s for Python and 15 s for Rust on the 4-core GitHub runner, best of two runs. The same commit measured 61 s and 100 s there on consecutive days, so the budget is a regression guard with headroom for runner noise, not a target. CI enforces it with `--runs 2 --budget 120` and `--rust-budget 15`.

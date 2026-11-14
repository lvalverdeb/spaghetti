# Benchmark

Generated 2026-11-14 by `scripts/bench.py` on arm64 (Darwin), Python 3.13.6, 11 CPUs, best of 2. Corpus pinned in `scripts/corpus.lock`: django@f24f38c, pandas@ff36f37, airflow@de88871.

| Package | Files | Lines | Findings | Python (s) | Python, warm cache (s) | Rust (s) | Rust findings |
|---|---|---|---|---|---|---|---|
| django | 909 | 166,424 | 19163 | 4.3 | 3.1 | 0.6 | 19163 |
| pandas | 1446 | 733,823 | 18610 | 28.8 | 17.4 | 2.6 | 18610 |
| airflow | 789 | 165,702 | 4100 | 3.6 | 2.6 | 0.5 | 4100 |
| all three | 3144 | 1,065,949 | 41882 | 33.9 | 24.6 | 3.4 | 41882 |

Budget (ROADMAP Phase 5): the *all three* row under 90 s for Python and 15 s for Rust on the 4-core GitHub runner, which is about 1.7× slower per core than the laptop above. CI enforces the budgets with `--budget 90` and `--rust-budget 15`.

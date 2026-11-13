# Benchmark

Generated 2026-11-13 by `scripts/bench.py` on arm64 (Darwin), Python 3.13.6, 11 CPUs, best of 2. Corpus pinned in `scripts/corpus.lock`: django@f24f38c, pandas@ff36f37, airflow@de88871.

| Package | Files | Lines | Findings | Python (s) | Python, warm cache (s) | Rust (s) | Rust findings |
|---|---|---|---|---|---|---|---|
| django | 909 | 166,424 | 19537 | 9.2 | 2.9 | 1.6 | 19538 |
| pandas | 1446 | 733,823 | 24284 | 79.1 | 17.6 | 7.6 | 24283 |
| airflow | 789 | 165,702 | 4861 | 8.0 | 2.4 | 1.5 | 4856 |
| all three | 3144 | 1,065,949 | 48791 | 81.6 | 20.1 | 8.2 | 48786 |

Budget (ROADMAP Phase 5): the *all three* row under 90 s for Python and 10 s for Rust on a 4-core CI runner. CI enforces the Python budget with `--budget 90`.

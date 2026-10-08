# data/

Student answer sheets contain personal data (names, register numbers, signatures).
This repository is **public**, so the following directories are gitignored and must stay local:

| Path | Contents |
|---|---|
| `data/samples/` | Raw owner-supplied scans (Phase 0 spike input) |
| `data/golden/` | Real human-graded sheets + manifests (benchmark golden set, spec §19) |
| `data/transcriptions/` | Owner-verified ground-truth transcriptions |

`data/synthetic/` (not yet created) is for SYNTHETIC smoke-test sheets only and may be committed;
it is never counted in reported metrics.

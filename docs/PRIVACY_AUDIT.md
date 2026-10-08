# Privacy history audit (D14), 2026-10-08, before any data commit

Repository: `bsrikumar855-dot/GradeMIND-Reloded`, visibility **PUBLIC**. Branches: `main` only (no other refs, no stashes,
no LFS objects yet). Audited at HEAD `f78ddb7`.

## 1. Every path ever committed (all refs)

```text
$ git log --all --format= --name-only | sort -u | wc -l
49
$ git log --all --format= --name-only | sort -u | grep -iE "\.(jpe?g|png|pdf|tiff?|webp)$|^data/(samples|originals|transcriptions|fixtures|golden)/|^spike/runs/|^spike/pages/|page_[0-9]+\.(txt|json)$"
grep exit=1 (1 = no matches)
```

The 49 paths are code, docs, config, and 4 `SYNTHETIC` test fixtures. **No page image, PDF, transcription, OCR output or run
output has ever been committed.**

## 2. Identifiers in any historical content or commit message

The pattern list (student names, register numbers, hall superintendents' names; 9 patterns) is kept local, because it contains the identifiers.

```text
$ git grep -i -E -f <patterns> $(git rev-list --all) | wc -l
0
$ git log --all --format=%B | grep -i -E -f <patterns> | wc -l
0
```

## 3. Context that could narrow identification

```text
$ git grep -n -i -E "shakthi|coimbatore|641 ?062|DSCH|21/8/26|21-8-26|anna university|CIAT|B\.?Tech" $(git rev-list --all)
```

Real hits: "B.Tech, Anna University-affiliated" (docs/PHASE0_DISCOVERY.md) and "CIAT-I" (data/README.md). Both are generic (one
affiliating university, many colleges). Other hits are substring false positives ("Depre**ciat**ion", "asso**ciat**ed").
**No institution name, course code or exam date has been committed.**

## 4. Student answer text in committed docs (disclosed)

`docs/OCR_SPIKE.md`, `docs/ARCHITECTURE.md`, `PHASE_0_REPORT.md` and `docs/TRANSCRIPTION_GUIDE.md` quote short fragments of answer text as
OCR error examples (e.g. `has rear to his extinction`, `segrade`, `happend`, `enviromental`). They contain no identifiers.
Some were pushed **before** owner decision D6 (2026-10-08) approved committing student content with identifiers redacted.

## Verdict

No unredacted student identifiers were ever pushed. STOP condition (D14) **not** triggered. No history rewrite is needed.

# OCR Spike (Phase 0): Results Log

Status: **in progress**. Nothing here is an approved metric yet. CER/WER need owner-verified transcriptions
(`docs/TRANSCRIPTION_GUIDE.md`); everything below is a raw observation, each with the command that produced it.
Raw outputs live in `spike/runs/` and the scratchpad (gitignored, because they contain student text).

Host: RTX 5070 12 GiB (sm_120), driver 595.84, **shared**. About 4.65 GiB is held by other processes before we start
(see `docs/PHASE0_DISCOVERY.md` §4). Owner decision: design for a ~7 GiB budget.

## Engine A: Unlimited-OCR (rev `07dea832`), transformers path, `gundam` mode

### A1. bf16 does not fit the shared GPU (2026-10-08)

```text
$ spike/engine_a/.venv/bin/python spike/engine_a/run_engine_a.py --model ~/models/Unlimited-OCR \
    --revision 07dea832e22aefee32ad281d4b80551282e1c168 --pages data/samples/sheet_001/pages/page_02.jpg --out <scratch>
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 114.00 MiB. GPU 0 has a total capacity of 11.49 GiB
of which 141.25 MiB is free. Process 2024 has 3.81 GiB memory in use. ... this process has 6.82 GiB memory in use.
Of the allocated memory 6.54 GiB is allocated by PyTorch
```

The architecture is a DeepSeek-OCR-style MoE: 12 decoder layers (layer 0 dense), 64 routed experts with 6 active per token, plus 2 shared experts,
hidden size 1280, and a SAM-B + CLIP-L vision encoder. Most of the 6.67 GB is routed experts (about 440 MB per layer).

### A2. int8 (bitsandbytes 0.50.2) fits, but the smoke output was degenerate

```text
$ PYTORCH_ALLOC_CONF=expandable_segments:True spike/engine_a/.venv/bin/python spike/engine_a/run_engine_a.py \
    ... --quant int8 --pages data/samples/sheet_001/pages/page_02.jpg --out <scratch>
page_02.jpg: 60.0s tokens=1309 regions=5 peak_alloc=6153MiB flags=[]
```

- `expandable_segments` was required. Without it there was an OOM from fragmentation (1.46 GiB reserved but unallocated, 1.38 GiB request in the SAM attention).
- **The output was degenerate.** It parsed the ruled page as a `<table>` and repeated it 5 times, emitted a 109-character run of `0`,
  and **invented digits** that are not on the page (`2017/6/1`, `0.1+0.01`, `6.0/kg`, `a + 2019`). Real answers were partly read (`non-biotic compounds`, `c3 plants`).
- Token probability: mean 0.902, min 0.057, 6.95% of tokens below 0.5. The mean is high despite garbage output, so **mean token
  probability is not a usable confidence signal on its own**.
- The original degenerate checks returned `[]`. The detector now flags this output as
  `CHAR_RUN_'0'_x109, NEAR_DUPLICATE_REGION_PAIRS_x6, REPEATED_BBOX_x3` (commit `a8ffe19`).
- **Open question:** is this caused by int8, or is it model behaviour on ruled handwritten pages? See A3.

### A3. The exact-bf16 reference also degenerates, so int8 is not the cause (on this page)

`--quant offload` keeps the vision encoder, attention and shared experts on the GPU and streams the routed experts of layers 5–11 from RAM.

```text
$ PYTORCH_ALLOC_CONF=expandable_segments:True spike/engine_a/.venv/bin/python spike/engine_a/run_engine_a.py \
    ... --quant offload --pages data/samples/sheet_001/pages/page_02.jpg --out <scratch>
page_02.jpg: 1210.3s tokens=29436 regions=5 peak_alloc=5686MiB flags=['NEAR_DUPLICATE_REGION_PAIRS_x1']
```

- The output starts identically to int8 (`part - A`, `2017/6/1`), then falls into **counting loops**: `1. 2. 3. … 99.` and
  `9. Carbon monoxide 2010.2. 2011. 2012. … 2187.`. It ran 29,436 tokens, close to the 32,768 cap.
- Mean token probability was **0.9967**: near-certain on garbage. This confirms that token probability cannot gate degenerate output.
- A counting loop never repeats an n-gram, so it **evades the model's own `no_repeat_ngram_size=35` ban** and the duplicate checks.
  The detector now adds `COUNTING_SEQUENCE_x4110` and `RUNAWAY_LENGTH_29436_tokens` (commit `d5f5ea2`).
- Offload is about 20× slower than int8 (1210 s vs 60 s on this page, although the bf16 run generated about 22× more tokens). It is not usable in production,
  but it is a valid reference.
- **Conclusion so far (one page):** the degeneration is model behaviour on this sparse, ruled, handwritten page in `gundam` mode
  with the model card's `document parsing.` prompt. The full 27-page int8 run (max_length 4096) will show how often it happens.

### A4. Full run: 27 pages, int8, max_length 4096 (run `20261008T074243Z_gundam_int8`)

```text
$ UNLIMITED_OCR_REV=07dea832e22aefee32ad281d4b80551282e1c168 spike/run_spike.sh --engines a --quant int8 \
    --max-length 4096 data/samples/sheet_001 data/samples/sheet_002
$ spike/engine_a/.venv/bin/python spike/summarize.py --a spike/runs/20261008T074243Z_gundam_int8 \
    --b spike/runs/20261008T073416Z_gundam
sheet      page        A s  A tok  A chr  B chr   A/B  flags
sheet_001  page_01    67.1   1359    263   1277  0.21  COUNTING_SEQUENCE_x32,EMPTY_CELL_SPAM_x541
sheet_001  page_02    61.6   1309   1829    108 16.94  CHAR_RUN_'0'_x109,NEAR_DUPLICATE_REGION_PAIRS_x6,REPEATED_BBOX_x3
sheet_001  page_03     6.9    138    239    226  1.06
sheet_001  page_04     6.8    132    257    192  1.34
sheet_001  page_05     8.3    174    371    330  1.12
sheet_001  page_06     3.8     65    292    398  0.73  WORD_REPEAT_x35
sheet_001  page_07    16.8    360    387    332  1.17
sheet_001  page_08     8.5    146    392    258  1.52
sheet_001  page_09     9.4    189    397    293  1.35
sheet_001  page_10     7.5    148    387    304  1.27
sheet_001  page_11    60.6   1359    362    287  1.26  EMPTY_CELL_SPAM_x428
sheet_001  page_12     3.0     48     70     81  0.86
sheet_002  page_01    53.9   1129   1238    986  1.26  EMPTY_CELL_SPAM_x137
sheet_002  page_02    19.3    402    315    221  1.43  REPEATED_REGION_TEXT_x8,UNEXPECTED_SCRIPT_CJK_x24
sheet_002  page_03    15.6    311    323    142  2.27
sheet_002  page_04    14.7    296    353    349  1.01
sheet_002  page_05    22.8    470    569    609  0.93
sheet_002  page_06     9.1    183    588    558  1.05
sheet_002  page_07     8.0    154    383    357  1.07
sheet_002  page_08     8.2    159    521    561  0.93
sheet_002  page_09    10.7    202    604    561  1.08
sheet_002  page_10     9.4    189    589    489   1.2
sheet_002  page_11     6.8    128    347    334  1.04
sheet_002  page_12    13.2    267    385    189  2.04
sheet_002  page_13     9.2    181    615    561   1.1
sheet_002  page_14     9.3    187    599    518  1.16
sheet_002  page_15     4.9     91    324    223  1.45

answer pages: 25 | A degenerate-flagged: 4 | unflagged with A/B coverage < 0.6: 0
```

(The flags above come from the detector at commit `4e9a95e`, re-applied to the stored outputs. A/B is Engine A's text length over the length of
Engine B's lines with score ≥ 0.7. It is a coarse omission signal, and Engine B's bleed-through inflates it.)

- **Peak GPU allocation was 6648 MiB** (with `expandable_segments`), alongside the other host processes, so it fits the shared budget.
- **Latency on non-degenerate answer pages was about 3–23 s** (int8, bitsandbytes). Degenerate pages run until their own stop condition (about 60 s at 1300+ tokens).
  These timings ran concurrently with Engine B's CPU run for part of the time.
- **4 of 25 answer pages were degenerate.** The shapes were: a repeated table with a run of `0`, `species species …` ×35, empty-`<td>` spam,
  and a **hallucinated Chinese date `2017年1月1日` ×8** on smudged lines of an English sheet (sheet_002 p2), with the answer lines lost.
  Each new shape escaped the previous detector. The detector is a growing list, not a guarantee.
- **Silent omission and normalisation on non-flagged pages** (sheet_001 p3, against the draft transcription):
  `has rear to his extinction` → `has to its extinction`; `Endemic species are found only in some specific region` →
  `Endemic species only in some region`. Nothing on the Engine A side flags this.
- **Autocorrection of student misspellings** (sheet_002 p12, against the draft transcription): `segrade` → `separate`, `happend` → `happened`.
  This is the §5.2 risk, observed directly.
- **Real-word substitutions** (sheet_002 p2): `Deforestation` → `Depreciation`, `large numbers` → `sludge numbers`, `Forest` → `Food`.
- Engine A frequently wraps ruled handwritten pages in a single `table` region, so its region bboxes are often page-sized and not useful for
  evidence highlighting on those pages. Engine B line boxes are the usable highlight geometry.

### A5. Draft-scored CER/WER: NOT REPORTABLE

These are scored against `DRAFT_UNVERIFIED` transcriptions, so they are directional only and must not be quoted as metrics.
Files: `spike/runs/20261008T074243Z_gundam_int8/sheet_00{1,2}/scores_DRAFT.json`.
Directionally: on clean prose pages (sheet_001 p3 and p8) Engine A CER is about 0.19–0.20 and Engine B about 0.14–0.39. Degenerate pages dominate Engine A's
aggregate. Neither engine reads **question labels** reliably (critical-token recall per page ranges from 0/1 to 3/3), and
question labels are the main alignment signal (spec §7.3).

## Engine B: PaddleOCR 3.7.0 (paddlepaddle-gpu 3.4.0 cu129), PP-OCRv5, CPU

### B1. Two CPU-path failures fixed

1. `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`
   (oneDNN under the PIR executor). Fix: `enable_mkldnn=False`.
2. `ResourceExhaustedError: Fail to alloc memory of 46549499904 size`. The default detection limit (`min`/64) never downscales.
   Fix: `text_det_limit_type="max", text_det_limit_side_len=1920`.

`lang="en"` selects `en_PP-OCRv5_mobile_rec` (the mobile recogniser, not the server one). A server recogniser has not been evaluated yet.

### B3. Full run: 27 pages, CPU (run `20261008T073416Z_gundam`)

Command: `spike/run_spike.sh --engines b data/samples/sheet_001 data/samples/sheet_002`. All 27 pages completed.
The driver exited 2 after the last page only because the script was edited mid-run (bash `unexpected EOF`). The outputs are unaffected, and this is noted in the run dir.

| Sheet | Answer pages | Lines | Median s/page (CPU) | Median line score | Lines with score < 0.6 |
|---|---|---|---|---|---|
| sheet_001 | 11 | 447 | 21.5 | 0.791 | 27.7% |
| sheet_002 | 14 | 368 | 24.0 | 0.777 | 20.9% |

Bleed-through digits are invented across sheet_001 (draft-scored "extra" critical tokens include `89103`, `108`, `810`, `812`, `330`).

### B2. Smoke result (sheet_001 pages 2–3)

```text
$ /usr/bin/time -f "maxrss=%MkB wall=%es" spike/engine_b/.venv/bin/python spike/engine_b/run_engine_b.py \
    --pages data/samples/sheet_001/pages/page_02.jpg data/samples/sheet_001/pages/page_03.jpg --out <scratch>
page_02.jpg: 21.09s lines=31
page_03.jpg: 21.53s lines=34
maxrss=14520424kB wall=44.78s
```

- About 21 s per page on CPU, with **14.5 GB peak RSS**. That is heavy for a 31 GiB host and must be bounded in production.
- **Critical-token error:** the written question label `10` was read as `14` (`14 deforestaton`).
- **Bleed-through was read as content.** Mirrored writing from the reverse side produced lines such as `noihihe`, `Bad on- ige A`,
  and **invented digits** `89103`, `&0i92`, `108`. Their recognition scores were 0.1–0.6, against 0.75–0.96 for most real lines. That helps,
  but it does not separate them cleanly: one line merged real text and bleed-through at score 0.55.
- Real answers were read with spelling-level errors: `non-bbtic pomponont`, `3.cJplant`, `s. a) ciradar`, `9. 0 carbonMonoide`.

## Implications so far (preliminary, 2 writers, 1 course, 25 answer pages)

1. **Both engines can emit digits that are not on the page.** Engine A did it through degeneration, Engine B through bleed-through. Reconciliation
   must treat a numeral found by only one engine as `OCR_CRITICAL_TOKEN_MISMATCH`. That is the spec's rule, and these pages show it is needed.
2. Bleed-through suppression belongs in preprocessing (spec §6), and it must be validated against these sheets.
3. Engine A's per-token probability alone does not detect degenerate output: the mean was 0.997 on a counting loop. Structural checks
   are required, and they will keep missing new shapes. Cross-engine disagreement is the more general safety net.
4. Engine A's literal fidelity on handwriting is weak. It omits words, normalises misspellings, and substitutes real words. On these
   sheets it is not trustworthy as the *primary* reader for grading evidence without Engine B agreement.
5. Throughput on this host: Engine A int8 takes about 3–23 s per normal page on the GPU, and Engine B about 21–24 s per page on CPU (14.5 GB RSS).
   A 12-page booklet takes about 5 min for Engine B and 1–3 min for Engine A, if run sequentially. vLLM/FP8 throughput has not been measured yet (the image pull is in progress).

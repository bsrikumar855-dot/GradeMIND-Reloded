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

### A3. Exact-bf16 reference via routed-expert CPU offload: running

`--quant offload` keeps the vision encoder, attention and shared experts on the GPU, and streams the routed experts of layers 5–11 from RAM.
It uses about 3.9 GiB of GPU memory, but it is very slow (more than 13 minutes on one page so far). The result will be recorded here.

## Engine B: PaddleOCR 3.7.0 (paddlepaddle-gpu 3.4.0 cu129), PP-OCRv5, CPU

### B1. Two CPU-path failures fixed

1. `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`
   (oneDNN under the PIR executor). Fix: `enable_mkldnn=False`.
2. `ResourceExhaustedError: Fail to alloc memory of 46549499904 size`. The default detection limit (`min`/64) never downscales.
   Fix: `text_det_limit_type="max", text_det_limit_side_len=1920`.

`lang="en"` selects `en_PP-OCRv5_mobile_rec` (the mobile recogniser, not the server one). A server recogniser has not been evaluated yet.

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

## Implications so far (preliminary)

1. **Both engines can emit digits that are not on the page.** Engine A did it through degeneration, Engine B through bleed-through. Reconciliation
   must treat a numeral found by only one engine as `OCR_CRITICAL_TOKEN_MISMATCH`. That is the spec's rule, and these pages show it is needed.
2. Bleed-through suppression belongs in preprocessing (spec §6), and it must be validated against these sheets.
3. Engine A's per-token probability alone does not detect degenerate output. Structural checks (runs, duplicates, repeated boxes) are required.

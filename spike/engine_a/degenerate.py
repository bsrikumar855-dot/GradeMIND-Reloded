"""Engine A degenerate-output detector (spike). Stdlib only, so tests need no GPU stack.
Each rule was added after a real miss; see docs/OCR_SPIKE.md and spike/tests/test_degenerate_checks.py."""

from __future__ import annotations

import difflib
import json
import re

MAX_LENGTH = 32768


def degenerate_checks(raw: str, n_tokens: int, regions: list[dict], max_length: int = MAX_LENGTH,
                      finish_reason: str | None = None) -> list[str]:
    """Backstop detector for degenerate Engine A output (process rule 10: NOT a gate; cross-engine disagreement is).
    Every rule has a real-page regression fixture or is listed as fixture-less in spike/tests."""
    flags = []
    if finish_reason == "length":
        flags.append("FINISH_REASON_LENGTH")
    if n_tokens >= max_length - 8:
        flags.append("TRUNCATED_AT_MAX_LENGTH")
    elif n_tokens >= 0.5 * MAX_LENGTH:
        flags.append(f"RUNAWAY_LENGTH_{n_tokens}_tokens")
    # Counting loops ("1. 2. 3. ..." / "2010. 2011. ...") evade n-gram bans and duplicate checks.
    nums = [int(x) for x in re.findall(r"\d+", raw)]
    longest = cur = 1
    for a, b in zip(nums, nums[1:]):
        cur = cur + 1 if b == a + 1 else 1
        longest = max(longest, cur)
    if longest >= 12:
        flags.append(f"COUNTING_SEQUENCE_x{longest}")
    content = re.sub(r"<\|det\|>.*?<\|/det\|>|<\|ref\|>.*?<\|/ref\|>", " ", raw, flags=re.DOTALL)
    content = re.sub(r"<[^>]*>", " ", content)
    words = re.findall(r"[A-Za-z]+", content)
    longest = cur = 1
    for a, b in zip(words, words[1:]):
        cur = cur + 1 if a.lower() == b.lower() else 1
        longest = max(longest, cur)
    if longest >= 5:
        flags.append(f"WORD_REPEAT_x{longest}")
    region_texts = [re.sub(r"<[^>]*>", " ", r["text"]).strip() for r in regions]
    counts = {t: region_texts.count(t) for t in set(region_texts) if t}
    if counts and max(counts.values()) >= 3:
        flags.append(f"REPEATED_REGION_TEXT_x{max(counts.values())}")
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", content))
    if cjk:
        flags.append(f"UNEXPECTED_SCRIPT_CJK_x{cjk}")
    cells = [c.strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", raw, flags=re.DOTALL) if c.strip()]
    cell_counts = {c: cells.count(c) for c in set(cells) if len(c) >= 4}
    if cell_counts and max(cell_counts.values()) >= 5:
        flags.append(f"REPEATED_CELL_TEXT_x{max(cell_counts.values())}")
    empty_cells = len(re.findall(r"<td[^>]*>\s*</td>", raw))
    if empty_cells >= 20:
        flags.append(f"EMPTY_CELL_SPAM_x{empty_cells}")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if lines:
        most = max(lines.count(x) for x in set(lines))
        if most >= 5:
            flags.append(f"REPEATED_LINE_x{most}")
    run = re.search(r"(.)\1{19,}", raw)
    if run:
        flags.append(f"CHAR_RUN_{run.group(1)!r}_x{len(run.group(0))}")
    texts = [r["text"] for r in regions if len(r["text"]) > 20]
    dupes = sum(1 for i in range(len(texts)) for j in range(i + 1, len(texts))
                if difflib.SequenceMatcher(None, texts[i], texts[j], autojunk=False).ratio() > 0.8)
    if dupes:
        flags.append(f"NEAR_DUPLICATE_REGION_PAIRS_x{dupes}")
    boxes = [json.dumps(r["bbox_raw"]) for r in regions if r["bbox_raw"]]
    if len(boxes) - len(set(boxes)) >= 2:
        flags.append(f"REPEATED_BBOX_x{len(boxes) - len(set(boxes))}")
    if not raw.strip():
        flags.append("EMPTY_OUTPUT")
    elif regions and all(not r["text"] for r in regions):
        flags.append("ALL_REGIONS_EMPTY")
    return flags

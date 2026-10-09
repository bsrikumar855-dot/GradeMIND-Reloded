"""Phase 0c cloud ceiling runner (owner decision D22). BENCHMARK ONLY: outputs are CEILING_REFERENCE and never product path.

Pre-registered plan: docs/PHASE_0C_PLAN.md (committed before any request). Gates, all enforced here:
  1. both data/transcriptions/sheet_00{1,2}/manifest.json are OWNER_VERIFIED (D21);
  2. --owner-confirmed-in-chat (the agent passes it only after the owner confirms in chat);
  3. ANTHROPIC_API_KEY and GEMINI_API_KEY (or GOOGLE_API_KEY) present; --gemini-paid-tier-attested (no-training tier).
--dry-run builds and logs every request with NO network call and ignores gates 1-3 (nothing is sent).

Usage: spike/ceiling/.venv/bin/python spike/ceiling/run_ceiling.py --out spike/runs/<run_id> [--dry-run]
       [--owner-confirmed-in-chat --gemini-paid-tier-attested] [--providers anthropic gemini] [--runs 2]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "prompts" / "ocr_ceiling" / "v1.md"
PROMPT_ID, PROMPT_VERSION = "ocr_ceiling", "v1"
SHEETS = {"sheet_001": range(2, 13), "sheet_002": range(2, 16)}  # answer pages only; page_01 (cover) never sent
ANTHROPIC_MODEL = "claude-fable-5-1"  # D22 "top Claude vision model"; no silent substitution
MAX_TOKENS = 16000
GEMINI_SEED = 20261009
LABEL = "CEILING_REFERENCE"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_prompt() -> tuple[str, str]:
    t = PROMPT_FILE.read_text()
    m = re.search(r"<!-- BEGIN PROMPT -->\n(.*?)\n<!-- END PROMPT -->", t, re.S)
    if not m:
        raise SystemExit("prompt markers not found in " + str(PROMPT_FILE))
    return m.group(1), sha256_bytes(m.group(1).encode())


def pages() -> list[tuple[str, str, Path]]:
    out = []
    for sheet, rng in SHEETS.items():
        for n in rng:
            p = ROOT / "data" / "redacted" / sheet / "pages" / f"page_{n:02d}.jpg"
            if not p.exists():
                raise SystemExit(f"missing redacted page {p}")
            out.append((sheet, p.stem, p))
    assert len(out) == 25, len(out)
    return out


def check_gates(args) -> None:
    problems = []
    for s in SHEETS:
        st = json.loads((ROOT / "data" / "transcriptions" / s / "manifest.json").read_text())["status"]
        if st != "OWNER_VERIFIED":
            problems.append(f"{s} manifest status is {st}, need OWNER_VERIFIED (D21)")
    if not args.owner_confirmed_in_chat:
        problems.append("--owner-confirmed-in-chat not given")
    if "anthropic" in args.providers and not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY not set")
    if "gemini" in args.providers:
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            problems.append("GEMINI_API_KEY / GOOGLE_API_KEY not set")
        if not args.gemini_paid_tier_attested:
            problems.append("--gemini-paid-tier-attested not given (paid tier = no training on API data)")
    if problems:
        print("REFUSING TO SEND ANYTHING:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        raise SystemExit(2)


# ---------- model resolution (rule 12) ----------

def select_gemini(models: list[dict]) -> tuple[str | None, list[dict]]:
    """Pure function (unit-tested): pick the highest gemini-<maj>.<min>-pro* with generateContent, excluding aliases/previews."""
    cands = []
    for m in models:
        name = m["name"].split("/")[-1]
        mm = re.fullmatch(r"gemini-(\d+)\.(\d+)-pro(?:-[a-z0-9-]+)?", name)
        if not mm or re.search(r"(preview|exp|latest)", name):
            continue
        if "generateContent" not in (m.get("supported_actions") or []):
            continue
        cands.append({"name": name, "version": (int(mm.group(1)), int(mm.group(2)))})
    cands.sort(key=lambda c: (c["version"], -len(c["name"])), reverse=True)
    return (cands[0]["name"] if cands else None), cands


def resolve_anthropic(client) -> dict:
    ids = {m.id: m for m in client.models.list()}
    if ANTHROPIC_MODEL not in ids:
        raise SystemExit(f"{ANTHROPIC_MODEL} not available to this key (no silent substitution, D22/rule 12)")
    m = ids[ANTHROPIC_MODEL]
    if not m.capabilities["image_input"]["supported"]:
        raise SystemExit(f"{ANTHROPIC_MODEL} reports image_input unsupported")
    return {"provider": "anthropic", "model": m.id, "display_name": m.display_name,
            "max_input_tokens": m.max_input_tokens, "max_tokens": m.max_tokens, "available_models": sorted(ids)}


def resolve_gemini(client) -> dict:
    listed = [{"name": m.name, "supported_actions": list(m.supported_actions or []), "version": m.version}
              for m in client.models.list()]
    name, cands = select_gemini(listed)
    if not name:
        raise SystemExit("no Gemini model matched the pre-registered selection rule")
    return {"provider": "gemini", "model": name, "candidates": cands, "listed": [m["name"] for m in listed]}


# ---------- calls ----------

def call_anthropic(client, model: str, prompt: str, img: bytes) -> dict:
    resp = client.messages.create(
        model=model, max_tokens=MAX_TOKENS, output_config={"effort": "high"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(img).decode("utf-8")}},
            {"type": "text", "text": prompt}]}])
    text = "".join(b.text for b in resp.content if b.type == "text") if resp.stop_reason != "refusal" else ""
    iterations = [getattr(i, "type", None) for i in (getattr(resp.usage, "iterations", None) or [])]
    return {"text": text, "response_id": resp.id, "response_model": resp.model, "stop_reason": resp.stop_reason,
            "stop_details": resp.stop_details.model_dump() if resp.stop_details else None,
            "usage": resp.usage.model_dump(), "fallback_ran": "fallback_message" in iterations}


def call_gemini(client, model: str, prompt: str, img: bytes) -> dict:
    from google.genai import types
    resp = client.models.generate_content(
        model=model, contents=[prompt, types.Part.from_bytes(data=img, mime_type="image/jpeg")],
        config=types.GenerateContentConfig(temperature=0, seed=GEMINI_SEED, max_output_tokens=MAX_TOKENS))
    cand = resp.candidates[0] if resp.candidates else None
    text = ""
    if cand and cand.content and cand.content.parts:
        text = "".join(p.text for p in cand.content.parts if getattr(p, "text", None) and not getattr(p, "thought", False))
    return {"text": text, "response_id": resp.response_id, "response_model": resp.model_version,
            "stop_reason": str(cand.finish_reason) if cand else None,
            "prompt_feedback": resp.prompt_feedback.model_dump() if resp.prompt_feedback else None,
            "usage": resp.usage_metadata.model_dump() if resp.usage_metadata else None, "fallback_ran": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--owner-confirmed-in-chat", action="store_true")
    ap.add_argument("--gemini-paid-tier-attested", action="store_true")
    ap.add_argument("--providers", nargs="+", default=["anthropic", "gemini"], choices=["anthropic", "gemini"])
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    prompt, prompt_sha = load_prompt()
    pg = pages()
    out = Path(args.out) / "ceiling"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(__file__, out / "run_ceiling.py.snapshot")  # rule 9: the code that ran, next to its outputs
    log = open(out / "request_log.jsonl", "a")

    if args.dry_run:
        resolved = {"anthropic": {"provider": "anthropic", "model": ANTHROPIC_MODEL, "resolution": "DRY_RUN (not resolved)"},
                    "gemini": {"provider": "gemini", "model": "<resolved at runtime>", "resolution": "DRY_RUN (not resolved)"}}
    else:
        check_gates(args)
        resolved, clients = {}, {}
        if "anthropic" in args.providers:
            import anthropic
            clients["anthropic"] = anthropic.Anthropic()
            resolved["anthropic"] = resolve_anthropic(clients["anthropic"])
        if "gemini" in args.providers:
            from google import genai
            clients["gemini"] = genai.Client()
            resolved["gemini"] = resolve_gemini(clients["gemini"])
    (out / "resolved_models.json").write_text(json.dumps({"at": now(), "label": LABEL, "dry_run": args.dry_run,
                                                          "prompt": {"id": PROMPT_ID, "version": PROMPT_VERSION, "sha256": prompt_sha},
                                                          "models": {k: v for k, v in resolved.items() if k in args.providers}}, indent=1))

    for prov in args.providers:
        model = resolved[prov]["model"]
        for run in range(1, args.runs + 1):
            for sheet, page, path in pg:
                img = path.read_bytes()
                entry = {"at": now(), "label": LABEL, "provider": prov, "requested_model": model, "run": run,
                         "sheet": sheet, "page": page, "image": str(path.relative_to(ROOT)), "image_sha256": sha256_bytes(img),
                         "image_bytes": len(img), "prompt_id": PROMPT_ID, "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_sha,
                         "params": ({"max_tokens": MAX_TOKENS, "output_config": {"effort": "high"}, "temperature": "NOT SETTABLE (model removes sampling params)",
                                     "fallbacks": "none (rule 12)"} if prov == "anthropic" else
                                    {"temperature": 0, "seed": GEMINI_SEED, "max_output_tokens": MAX_TOKENS}),
                         "sent": not args.dry_run}
                log.write(json.dumps(entry) + "\n")
                log.flush()
                if args.dry_run:
                    continue
                t = time.perf_counter()
                try:
                    res = (call_anthropic if prov == "anthropic" else call_gemini)(clients[prov], model, prompt, img)
                    err = None
                except Exception as e:  # recorded per page, never silently retried beyond the SDK default
                    res, err = {"text": "", "response_model": None, "stop_reason": None, "fallback_ran": False}, f"{type(e).__name__}: {e}"
                lat = time.perf_counter() - t
                mismatch = (not err) and (res.get("fallback_ran") or not str(res.get("response_model") or "").startswith(model))
                rec = {"label": LABEL, "provider": prov, "requested_model": model, "run": run, "sheet": sheet, "page": page,
                       "latency_s": round(lat, 2), "error": err, "rule12": "RULE12_MISMATCH" if mismatch else ("ERROR" if err else "OK"),
                       "lines_text": res["text"].splitlines(), **{k: v for k, v in res.items()}}
                d = out / f"{prov}" / f"run{run}" / sheet
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{page}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str))
                print(f"{prov} run{run} {sheet}/{page}: {lat:.1f}s stop={res.get('stop_reason')} rule12={rec['rule12']}"
                      + (f" ERROR {err}" if err else ""), flush=True)
    print(f"{'DRY RUN: nothing sent. ' if args.dry_run else ''}log -> {out / 'request_log.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

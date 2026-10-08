"""Local-only transcription verifier server (owner request, Phase 0b step 1). Stdlib only.

Why not plain `python -m http.server`: it cannot accept saves. This is a stdlib http.server subclass bound to
127.0.0.1 with an explicit route whitelist (no directory listing, no path traversal).

Start:  python3 spike/verify_ui/serve.py            -> http://127.0.0.1:8765/
Routes: GET /                         index.html
        GET /api/pages                sheets, pages, draft/current lines, line boxes for crops
        GET /img/<sheet>/<page>.jpg   ORIGINAL page image (local only; never committed)
        POST /api/save                {sheet, page, verified_by, lines: [{text, confirmed}]}

On save: writes data/transcriptions/<sheet>/<page>.txt (line texts), per-page state in verification_state.json,
per-page verified_by/verified_at in manifest.json, and verification_diff.json (draft -> verified, line level).
The manifest status becomes OWNER_VERIFIED only when every line of every page in the manifest is confirmed.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = Path(__file__).resolve().parent / "index.html"
import os
GT = Path(os.environ.get("VERIFIER_GT_DIR", ROOT / "data" / "transcriptions"))  # override for self-tests only
IMG = ROOT / "data" / "samples"  # originals (gitignored); runs read these too
BOX_RUN = ROOT / "spike" / "runs" / "20261008T091415Z_phase0b2" / "raw"
SHEETS = ["sheet_001", "sheet_002"]
NAME_RE = re.compile(r"^(sheet_\d{3})$")
PAGE_RE = re.compile(r"^(page_\d{2})$")


def rows_with_boxes(rec: dict) -> list[tuple[str, list[int]]]:
    lines = sorted(rec["lines"], key=lambda l: (l["box"][1], l["box"][0]))
    rows: list[list[dict]] = []
    for ln in lines:
        h = max(1, ln["box"][3] - ln["box"][1])
        if rows and abs(ln["box"][1] - rows[-1][0]["box"][1]) < h * 0.5:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    out = []
    for r in rows:
        r = sorted(r, key=lambda l: l["box"][0])
        box = [min(l["box"][0] for l in r), min(l["box"][1] for l in r), max(l["box"][2] for l in r), max(l["box"][3] for l in r)]
        out.append((" ".join(l["text"] or "" for l in r), box))
    return out


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()


def line_boxes(sheet: str, page: str, gt_lines: list[str]) -> list[dict]:
    """Best-effort crop box per GT line: match against PP-OCRv5 rows (both its own and TrOCR's reading of them)."""
    cands = []
    for eng in ("b_v5s", "c_v5det"):
        f = BOX_RUN / sheet / eng / f"{page}.json"
        if f.exists():
            cands += rows_with_boxes(json.loads(f.read_text()))
    out, prev_y = [], 0
    for g in gt_lines:
        scored = sorted(((sim(g.replace("[?]", ""), t), box) for t, box in cands if t), key=lambda x: -x[0])
        # prefer matches below the previous line (reading order), fall back to best overall
        pick = next(((s, b) for s, b in scored if b[1] >= prev_y - 40 and s >= 0.35), scored[0] if scored else (0, None))
        s, b = pick
        if b is not None and s >= 0.35:
            out.append({"box": b, "match": round(s, 2)})
            prev_y = b[1]
        else:
            out.append({"box": None, "match": round(s, 2)})
    return out


def load_state(sheet: str) -> dict:
    f = GT / sheet / "verification_state.json"
    return json.loads(f.read_text()) if f.exists() else {}


def snapshot_drafts(sheet: str, man: dict) -> None:
    d = GT / sheet / "drafts"
    d.mkdir(exist_ok=True)
    for pf in man["pages"]:
        if not (d / pf).exists():
            shutil.copy(GT / sheet / pf, d / pf)


def pages_payload() -> dict:
    out = {"sheets": []}
    for sheet in SHEETS:
        man = json.loads((GT / sheet / "manifest.json").read_text())
        snapshot_drafts(sheet, man)
        st = load_state(sheet)
        pages = []
        for pf in man["pages"]:
            page = Path(pf).stem
            draft = [l for l in (GT / sheet / "drafts" / pf).read_text().splitlines()]
            cur = st.get(page, {}).get("lines") or [{"text": l, "confirmed": False} for l in draft]
            pages.append({"page": page, "draft": draft, "lines": cur,
                          "boxes": line_boxes(sheet, page, [l["text"] for l in cur]),
                          "verified_by": man.get("page_verification", {}).get(page, {}).get("verified_by")})
        out["sheets"].append({"sheet": sheet, "status": man["status"], "notes": man.get("notes", []), "pages": pages})
    return out


def diff_log(sheet: str, man: dict, st: dict) -> dict:
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pages": {}, "totals": {}}
    tot = {"draft_lines": 0, "unchanged": 0, "changed": 0, "deleted": 0, "inserted": 0}
    for pf in man["pages"]:
        page = Path(pf).stem
        draft = (GT / sheet / "drafts" / pf).read_text().splitlines()
        final = [l["text"] for l in st.get(page, {}).get("lines", [])] or draft
        sm = difflib.SequenceMatcher(None, draft, final, autojunk=False)
        c = {"draft_lines": len(draft), "unchanged": 0, "changed": 0, "deleted": 0, "inserted": 0, "edits": []}
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                c["unchanged"] += i2 - i1
            elif op == "replace":
                k = min(i2 - i1, j2 - j1)
                c["changed"] += k
                c["deleted"] += (i2 - i1) - k
                c["inserted"] += (j2 - j1) - k
                c["edits"].append({"op": "replace", "draft": draft[i1:i2], "verified": final[j1:j2]})
            elif op == "delete":
                c["deleted"] += i2 - i1
                c["edits"].append({"op": "delete", "draft": draft[i1:i2]})
            else:
                c["inserted"] += j2 - j1
                c["edits"].append({"op": "insert", "verified": final[j1:j2]})
        res["pages"][page] = c
        for k in tot:
            tot[k] += c[k]
    tot["draft_line_error_rate"] = round((tot["changed"] + tot["deleted"]) / tot["draft_lines"], 4) if tot["draft_lines"] else None
    res["totals"] = tot
    return res


def save(payload: dict) -> dict:
    sheet, page = payload["sheet"], payload["page"]
    if not NAME_RE.match(sheet) or not PAGE_RE.match(page) or sheet not in SHEETS:
        raise ValueError("bad sheet/page")
    man_f = GT / sheet / "manifest.json"
    man = json.loads(man_f.read_text())
    if f"{page}.txt" not in man["pages"]:
        raise ValueError("page not in manifest")
    lines = [{"text": str(l["text"]).rstrip("\n"), "confirmed": bool(l["confirmed"])} for l in payload["lines"]]
    lines = [l for l in lines if l["text"].strip() or l["confirmed"]]
    by = str(payload.get("verified_by") or "").strip()[:80]
    st = load_state(sheet)
    st[page] = {"lines": lines}
    (GT / sheet / "verification_state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))
    (GT / sheet / f"{page}.txt").write_text("\n".join(l["text"] for l in lines if l["text"].strip()) + "\n")
    all_conf = bool(lines) and all(l["confirmed"] for l in lines)
    pv = man.setdefault("page_verification", {})
    pv[page] = {"verified_by": by or None, "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if all_conf else None,
                "all_lines_confirmed": all_conf, "n_lines": len(lines)}
    every = all(pv.get(Path(pf).stem, {}).get("all_lines_confirmed") for pf in man["pages"])
    if every:
        man["status"] = "OWNER_VERIFIED"
        man["verified_by"] = by or man.get("verified_by")
        man["verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        man["status"] = "DRAFT_UNVERIFIED"
        man["verified_by"] = man["verified_at"] = None
    man_f.write_text(json.dumps(man, ensure_ascii=False, indent=2))
    dl = diff_log(sheet, man, st)
    (GT / sheet / "verification_diff.json").write_text(json.dumps(dl, ensure_ascii=False, indent=1))
    return {"ok": True, "page_all_confirmed": all_conf, "manifest_status": man["status"], "diff_totals": dl["totals"]}


class H(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/pages":
            return self._send(200, json.dumps(pages_payload()).encode(), "application/json")
        m = re.fullmatch(r"/img/(sheet_\d{3})/(page_\d{2})\.jpg", self.path)
        if m and m.group(1) in SHEETS:
            pages = json.loads((GT / m.group(1) / "manifest.json").read_text())["pages"]
            f = IMG / m.group(1) / "pages" / f"{m.group(2)}.jpg"
            if f"{m.group(2)}.txt" in pages and f.exists():  # only transcribed pages (never covers)
                return self._send(200, f.read_bytes(), "image/jpeg")
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/save":
            return self._send(404, b"not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            res = save(json.loads(self.rfile.read(min(n, 2_000_000))))
            self._send(200, json.dumps(res).encode(), "application/json")
        except Exception as e:  # report to the UI; never crash the server
            self._send(400, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s %s\n" % (self.command, self.path))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Transcription verifier: http://127.0.0.1:{port}/  (local only; Ctrl+C to stop)", flush=True)
    srv.serve_forever()

"""The ONLY module that imports Paddle/PaddleOCR (spec rule 7; enforced by import-linter in CI).

Builds the pipeline from `expected_models.json` (models always named explicitly: PaddleOCR 3.7 silently substituted the
detector when only the recogniser was named) and reports what it actually resolved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import paddle
import paddleocr
from paddleocr import PaddleOCR

from grademind_ocr.rule12 import fingerprint

MAX_PIXELS = 40_000_000  # a 300-dpi A3 scan is ~17.4 MP


class ImageDecodeError(ValueError):
    pass


class PaddleEngine:
    def __init__(self, expected: dict[str, Any]) -> None:
        kw = dict(expected["pipeline_args"])
        kw["text_detection_model_name"] = expected["models"]["det"]["name"]
        kw["text_recognition_model_name"] = expected["models"]["rec"]["name"]
        self._ocr = PaddleOCR(**kw)
        inner = getattr(self._ocr.paddlex_pipeline, "_pipeline", self._ocr.paddlex_pipeline)
        det, rec = inner.text_det_model, inner.text_rec_model
        files = {role: list(spec["sha256"]) for role, spec in expected["models"].items()}
        self._resolved = {
            "libraries": {"paddlepaddle": paddle.__version__, "paddleocr": paddleocr.__version__},
            "models": {
                "det": {"name": det.model_name, "sha256": fingerprint(Path(det.model_dir), files["det"])},
                "rec": {"name": rec.model_name, "sha256": fingerprint(Path(rec.model_dir), files["rec"])},
            },
            # read back from the instantiated pipeline, never echoed from our arguments
            "pipeline": {k: getattr(inner, k, None) for k in expected["pipeline_resolved"]},
        }

    def resolved(self) -> dict[str, Any]:
        return self._resolved

    def read(self, data: bytes) -> dict[str, Any]:
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ImageDecodeError("The image could not be decoded.")
        h, w = img.shape[:2]
        if h * w > MAX_PIXELS:
            raise ImageDecodeError(f"The image is too large ({w}x{h}).")
        res = self._ocr.predict(img)[0].json["res"]
        lines = [
            {
                "text": txt,
                "score": round(float(sc), 4),
                "poly": [[int(x), int(y)] for x, y in poly],
                "box": [int(v) for v in box],
            }
            for txt, sc, poly, box in zip(res["rec_texts"], res["rec_scores"], res["rec_polys"], res["rec_boxes"], strict=True)
        ]
        return {"width": int(w), "height": int(h), "lines": lines}

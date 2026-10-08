Detector regression fixtures (gitignored: real student OCR output). Used by spike/tests/test_degenerate_checks.py.
degenerate/: real Engine A outputs that MUST be flagged (shape in filename).
clean/: real Engine A answer-page outputs judged non-degenerate by manual review (2026-10-08); must NOT be flagged.
  (Non-degenerate != correct: e.g. int8 sheet_001 page_03 has silent omissions; that is not the detector's job.)

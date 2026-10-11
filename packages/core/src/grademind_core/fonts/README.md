# Fonts for the Tamil and Devanagari parts of the PDF reports (D30.8)

| File | What | Source (pinned) | Licence |
|---|---|---|---|
| `NotoSansTamil-Regular.ttf` | Noto Sans Tamil, Regular, static TrueType (`full/ttf`) | https://github.com/notofonts/notofonts.github.io/blob/75ea68885b02b9255046c9d630f7c02c0984a776/fonts/NotoSansTamil/full/ttf/NotoSansTamil-Regular.ttf | SIL Open Font License 1.1 (`OFL-NotoSansTamil.txt`) |
| `NotoSansDevanagari-Regular.ttf` | Noto Sans Devanagari, Regular, static TrueType (`full/ttf`) | https://github.com/notofonts/notofonts.github.io/blob/75ea68885b02b9255046c9d630f7c02c0984a776/fonts/NotoSansDevanagari/full/ttf/NotoSansDevanagari-Regular.ttf | SIL Open Font License 1.1 (`OFL-NotoSansDevanagari.txt`) |

The licence texts come from the font projects (https://github.com/notofonts/tamil, https://github.com/notofonts/devanagari, `OFL.txt` on `main` on 2026-10-11).
The files are unchanged copies. The OFL allows embedding the fonts in documents and redistributing them with the software, as long as the licence text travels with
them (it does: this folder is shipped in the image) and the fonts are not sold on their own.

`SHA256SUMS` is checked by a test (`packages/core/tests/test_fonts.py`) and by the loader at run time: a file that does not match is refused, never used.
To update a font: replace the file from the new pinned source, regenerate `SHA256SUMS`, and change the table above and `docs/DEPENDENCIES.md` in the same commit.

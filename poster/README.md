# 1959 — A3 poster (LPM 2026 Rome)

    make            # -> 1959_poster_A3.pdf  (xelatex, one pass)

- `poster.tex` — source. XeLaTeX + Work Sans (the LPM 2026 site typeface),
  A3 portrait (297 × 420 mm), margins 20–22 mm. The TTFs live in `fonts/` so
  the build does not depend on system fontconfig.
- `fig/cat_1959.jpg` — still of Hubel & Wiesel's cat in its stereotaxic frame,
  from the film *Hubel & Wiesel 2: Cat Experiment* (1959 footage).
- `fig/die.jpg` — annotated die micrograph, copied from
  `paper/RISCV_NeuronArray_2026/figures/skywater_2024_avlsi_chip_SNN_RISCV.jpg`.
- `preview.png` — low-res render of the current PDF, for a quick look.

Body text = the accepted LPM program description (#1959), lightly copy-edited.
Layout: kicker + bold red "1959" title, red hairline rules, two columns
(description + pull quote / cat still, chip photo + listen note), affiliation
footer. Palette and typeface follow the LPM 2026 Rome site (red #9a1915,
black, Work Sans).

Print at 100 % on A3. All fonts embedded; no transparency.

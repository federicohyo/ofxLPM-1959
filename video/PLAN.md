# 1959 — the explainer video (replaces the poster)

~2 minutes, 16:9 master + seamless loop for the venue. AI voice reads a
narration adapted from the poster text. The only music is the tape: clicks
from `bin/data/audio.s16`. Look = the poster: white/black/LPM red #9a1915,
Work Sans (TTFs in `poster/fonts/`).

Built like the poster: a Makefile in this directory, reproducible from
assets, nothing hand-assembled in a GUI.

## Storyboard (~2:00, narration ~280 words)

| # | Beat | Time | Visual | Audio |
|---|---|---|---|---|
| 1 | Cold open | 0:00–0:08 | Black. Red "1959" (Work Sans bold, poster title). | Tape clicks, no voice. |
| 2 | The experiment | 0:08–0:40 | H&W film: cat, electrode, bar of light. | Narration ¶1 + clicks. |
| 3 | The chip | 0:40–1:10 | Die micrograph → zoom into neuron layout still → `soma_synapse.mp4` (membrane, leak, threshold). | Narration ¶2. |
| 4 | The synapse | 1:10–1:25 | Synapse circuit clip/still; one line: a click on the tape = one spike into neuron 14, weight = charge per spike. | Narration ¶2b. |
| 5 | The array | 1:25–1:40 | `array_loop160_v0.mp4` excerpt — sixteen neurons, routing. "Sixteen neurons is not a brain…" | Narration ¶3. |
| 6 | The piece | 1:40–2:00 | Screen recording of the app: spiral left, film right. One ear the cat, one ear the silicon. | Real binaural capture. |
| 7 | Coda | 2:00–2:10 | Black, red rule: "What they copied was the wiring. What they left behind was the body." Credits + film credit. | Clicks fade out. |

Loop version: same cut without the cold-open hold and the credits; starts
and ends on a click so it tiles.

## Assets

| Asset | Source | Status |
|---|---|---|
| Asset | Source | Status |
|---|---|---|
| H&W film | `yt-dlp` from IOHayh06LJ4 — best available is 854×480 | **fetched** → `assets/HubelWiesel_1959.webm` |
| Tape clicks | `bin/data/audio.s16` (raw PCM) | in repo |
| Neuron+synapse circuit anim | `~/tue/NeuromorphicEdgeComputingSystemsLab/slides/animations/aVLSI-Skywater2024/soma_synapse.mp4` (960×1080, 60 s) | contact sheet in `assets/` — **pick ranges** |
| Array anim | same dir, `array_loop160_v0.mp4` (160 s) | contact sheet in `assets/` — **pick ranges** |
| Neuron layout still | `paper/RISCV_NeuronArray_2026/figures/silicon_neuron_layout_avlsi2024.png` | in repo |
| Synapse schematic | `…/figures/syn_dpi_exc_aVLSI.png` + tikz `synapse_circuit.tikz` (render big) | in repo |
| Die micrograph | `…/figures/skywater_2024_avlsi_chip_SNN_RISCV.jpg` (poster copy: `poster/fig/die.jpg`) | in repo |
| System overview | tikz `system_overview.tikz`, `netarch.png` | optional |
| App capture | screen-record the running piece (real chip, both ears) — user | to record |
| Fonts | `poster/fonts/WorkSans-*.ttf` | in repo |

## Pipeline (video/Makefile)

```
video/
  PLAN.md            this file
  narration.md       the words, per beat, with timings
  assets/            fetched/copied clips and stills (not in git if large)
  voice/             piper-generated narration WAVs, one per beat
  titles/            title/coda cards (PNG, Work Sans, LPM red)
  Makefile           everything below
```

Stages:

1. `make assets` — yt-dlp the film, copy stills, render tikz, trim
   circuit clips to chosen ranges. Every clip normalised: 1920×1080,
   h264, yuv420p, 25 fps, letterboxed on black.
2. `make narration` — piper (`en_US-ryan-high`, chosen by ear), one WAV
   per beat so timing can be nudged per beat.
3. `make cards` — cold-open "1959" card, coda quote card, credits card,
   same treatment as the poster (kicker letterspacing, red hairline).
4. `make master` — concat beats with xfade, mix narration over the click
   bed (tape in the open and coda, ducked under narration elsewhere),
   burn subtitles for the silent loop, mux. → `1959_master_1080p.mp4`
   + `.srt` for the online copy.
5. `make loop` — re-cut without cold open and credits → `1959_loop.mp4`,
   first and last frame both mid-click.

## Division of labour

- Me: narration draft, Makefile, titles, piper setup, clip normalisation,
  contact sheets of the two circuit videos so ranges can be picked from
  stills, subtitles, final renders.
- Federico: approve narration wording, pick AI voice, pick circuit-clip
  ranges from contact sheets, record the app screen (~30 s, headphones
  out to line-in or OBS desktop audio), final review.

## Notes

- Credit the film in the end card: "Footage: Hubel & Wiesel 2 — Cat
  Experiment (1959)". We do not upload the film itself anywhere; only
  excerpts inside the piece's documentation, as the poster already does.
- Subtitles burned only in the loop version; online gets soft subs.
- `assets/` stays out of git (films, big clips); everything else in.

## Status 2026-10-23 — first full render

- `1959_master_1080p.mp4` — 2:33, 1920x1080/30, narration (en_US-amy-medium) + burned subtitles.
- `1959_loop.mp4` — 2:27, credits dropped, subtitles burned, for silent looping.
- Beat 6 captured with a 106 s warm-up so the app's level envelope is converged
  (otherwise the spiral pins at max radius) and the film sits in its densest
  click window; 465 tape clicks -> 465 silicon spikes, 1:1.
- Beat 6 (the piece) was captured here with `replay_chip_server.py`: the app
  ran against **real recorded neuron-14 membrane** (paper bench scope CSVs,
  4 kHz), not the live die. The self-healing loop cannot act in a replay;
  it only exists live at the venue. To swap in real footage later: record
  ~30 s of the running piece, drop it at `assets/app_capture.mp4`,
  `make master loop`.
- Circuit clip ranges picked by motion analysis: soma_synapse 0-16 s,
  array_loop 29-41 s.

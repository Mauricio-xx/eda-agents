# Ep 00 shot-list -- Series intro, what is LibreLane (target ~10 min, slide-only)

The conceptual opener. No terminal, no panes, no flow. The viewer sees a 6-slide deck that frames the four episodes ahead and answers the single question "what does LibreLane do?". Everything is the master deck rendered in the browser; voiceover does the work.

## Hook narrative

> *"Five episodes. Forty-five minutes of slides plus terminal across the whole series, plus one ninety-minute chip flow you watch through a time-lapse. We open with the conceptual frame so the rest of the series feels like instructions, not magic."*

This is the only episode without a 3-pane terminal. The recording is the master deck rendered full-screen + voiceover.

## Pane layout (single-pane, browser only)

| Pane | Contents |
|---|---|
| Pane 1 (full-screen) | `mini_decks/ep00_intro.html` rendered in the recording browser |

No bootstrap, no `docker exec`, no `start_x.sh`. The only thing the viewer sees is the deck.

## Episode timeline

| Time | Slide (in the Ep 00 mini-deck) | Action |
|---|---|---|
| 00:00 | Slide 1 — Cover ("RTL-to-GDS on GF180MCU — series intro") | title card + author |
| 01:00 | Slide 2 — RTL vs GDS (master 5) | the input/output pair the series solves |
| 02:30 | Slide 3 — Pipeline overview (master 6) | yosys → openroad → magic → klayout → netgen, in order |
| 04:30 | Slide 4 — LibreLane is the conductor (master 7) | one tool that orchestrates the rest |
| 06:00 | Slide 5 — Deliverables (master 8) | metrics.csv + GDS + LEF + lib are what you ship |
| 08:00 | Slide 6 — Five episodes ahead (series-overview, unique to Ep 00) | ep01 counter / ep02 slots / ep03 workshop slot / ep04 multi-macro |
| 09:30 | Out — quick "see you in episode 1" | -- |

## Cuts / time-lapses

None. The whole episode is voiceover over static slides.

## Honest scope (lines on camera)

- Min 00:00: *"This is a five-episode series. Episode zero — this one — is conceptual. The next four episodes are terminal-driven; everything happens inside one Docker container. Zero local installs."*
- Min 04:30: *"LibreLane is the only tool you invoke. It orchestrates yosys, openroad, magic, klayout, netgen — five separate engines — and hands you back a single CSV with the signoff verdict and a single GDS to ship."*
- Min 08:00: *"Episode 1 is a four-bit counter — runs in two minutes, fits in your head. Episode 2 explains the chipathon padring slot. Episode 3 drops your RTL into the chipathon workshop slot end to end. Episode 4 wraps two macros into one chip-top — the pattern that lets you compose tape-outs from independently-validated blocks."*

## Pre-recorded fallback

None. The deck is static; if rendering fails on recording day, fall back to the stand-alone PDF export of the master deck and skip the per-episode mini-deck for this opener.

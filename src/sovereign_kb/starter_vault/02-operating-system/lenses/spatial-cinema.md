---
title: "Lens L3: Spatial Audio & Cinema Director"
type: operating-system
updated: 2026-10-29
tags: [lens, spatial-cinema, audio, ebu-r128, youtube, cinematography, webgl, syneidesis, verga]
lens_id: spatial-cinema
pole: verga
applies_to: ["**/*.mp4", "**/*.webm", "**/*.wav", "**/*.mp3", "**/*.srt", "**/*.vtt", "**/audio/**", "**/cinema/**", "**/walkthrough*"]
applies_when_deps: ["tone", "howler"]
related:
  - "[[02-operating-system/lenses/README-lenses]]"
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[00-identity/philosophy-and-worldview]]"
---

# Lens L3: Spatial Audio & Cinema Director (`spatial-cinema`)

Evaluates visual essays, 1080p/4K broadcast masters, automated platform walkthrough videos, and WebXR spatial audio landscapes across **EBU R128 broadcast loudness, narration-over-music ducking, camera shot grammar, and caption synchronization**.

---

## 1. Deterministic Telemetry Probes & Hard Vetoes

| Probe ID | Metric Inspected | Sovereign Target | Hard Veto Threshold |
| :--- | :--- | :--- | :--- |
| `sc.ebu_r128_lufs` | Integrated loudness (`I`), True Peak (`TP`) & Loudness Range (`LRA`) via `ffmpeg -af ebur128` | `I` $\in [-16.0, -13.0]\text{ LUFS}$, `TP` $\le -1.0\text{ dBTP}$, `LRA` $\ge 2.0\text{ LU}$ | Silent stream, `I` $\notin [-24.0, -11.0]\text{ LUFS}$, `TP` $> -0.5\text{ dBTP}$, or flat sine drone (`LRA` $< 0.2\text{ LU}$) |
| `sc.srt_ergonomics` | Broadcast `.srt` reading speed ($\text{CPS} = \frac{\text{chars}}{\Delta t}$), line length & overlaps | $\text{CPS} \le 20.0\text{ chars/s}$, $\le 42\text{ chars/line}$, $0$ overlaps | Any cue overlap or reading speed $\text{CPS} > 28.0\text{ chars/s}$ |
| `sc.shot_progression` | 9-Frame Act Sequence Sheet pairwise tile dissimilarity ($\text{MSE}$) | Pairwise tile $\text{MSE} \ge 25.0$; RGBA thumbnail $< 2.0\text{ MB}$ | Frozen/blank video capture ($\text{MSE} < 2.0$) or missing 9-Frame Sheet under `--strict-lenses` |
| `sc.dead_air_gaps` | Unintentional silence segments inside master video stream | $0$ gaps $> 2.5\text{ s}$ (outside chapter transitions) | Any dead-air gap $> 3.5\text{ s}$ or audio ending $> 2.0\text{ s}$ before video ends |
| `sc.video_encoding` | Container stream geometry, pixel format, and frame pacing | `1920x1080` or `3840x2160`, `yuv420p`, $\ge 30\text{ fps}$ CFR | Odd dimensions, `yuv444p` (breaks Safari/mobile hardware decode), or truncated stream |
| `sc.camera_rig_bounds` | Camera FOV & roll stability across keyframe timeline | FOV $\in [35^\circ, 75^\circ]$ (cinema) / $\le 90^\circ$ (XR); roll $\le 3^\circ$ | Unintentional camera roll $> 3^\circ$, FOV $> 95^\circ$ (fish-eye distortion), or static hold $> 25\text{ s}$ |
| `sc.captions_thumbnail` | Timed `.srt`/`.vtt` subtitle cues & `< 2 MB` `100%`-opaque thumbnail | Zero overlapping `.srt` timestamps; RGBA alpha $= 255$ | Missing `.srt` captions on narrative video or transparent/missing thumbnail |

---

## 2. Builder Pre-Flight Constraints (Injected into `fable` / `sonnet`)

When a builder agent is dispatched on a repository where `spatial-cinema` is active:
1. **Deterministic Render Mode**: Support `?render=1&seed=42` frame-stepped capture so complex WebGL2/Three.js scenes render at locked 30/60 FPS without dropped frames.
2. **Broadcast Audio Mastering**: Normalize final audio with `loudnorm=I=-14:TP=-1.2:LRA=11` and duck background beds by $\ge 6\text{ dB}$ beneath voiceover or key telemetry cues.
3. **Cinematic Camera Pacing**: Use smooth cubic/quintic easing (`smoothstep`) between camera keyframes; never hold a completely static frame for $>15\text{ seconds}$ without camera drift or scene motion.

---

## 3. Astra Stage-5 Contact Sheet Rubric (`9-Frame Shot Sequence + Waveform Strip`)

Astra inspects the **9-Frame Act Sequence Contact Sheet** (with burned-in timecodes and bottom waveform/spectrogram strip) and must cite `(frame_index, timecode)` for every finding:
- **Visual Story Arc**: Do the 9 sampled keyframes show distinct narrative progression across acts rather than repetitive identical angles?
- **Subject Framing & Lower-Third Safe Area**: Do captions or lower-third titles avoid colliding with primary visual subjects in the bottom $15\%$ of the frame?
- **Acoustic-Visual Sync**: Do major visual act transitions align with waveform phrasing on the spectrogram strip?

---
title: Multi-Disciplinary Craft Lenses (Chrysos x Verga Specialist Verification)
type: operating-system
updated: 2026-10-29
tags: [lenses, verga, chrysos, goldwood-index, gwi, technical-artist, ux-ergonomics, spatial-cinema, typography-editor]
related:
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[02-operating-system/subtractive-design-laws]]"
  - "[[02-operating-system/autonomous-engineering-framework]]"
  - "[[02-operating-system/lenses/technical-artist]]"
  - "[[02-operating-system/lenses/ux-ergonomics]]"
  - "[[02-operating-system/lenses/spatial-cinema]]"
  - "[[02-operating-system/lenses/typography-editor]]"
---

# Multi-Disciplinary Craft Lenses ($\chi\rho\upsilon\sigma\acute{o}\varsigma \times \beta\acute{\epsilon}\rho\gamma\alpha$)

While traditional autonomous coding pipelines evaluate only **Chrysos** ($\chi\rho\upsilon\sigma\acute{o}\varsigma$ — compiler determinism, test exit codes, and zero console/network errors), sovereign engineering spans 3D WebXR worlds, WebGL cinema, B2B spatial SaaS, and camera-ready scientific/philosophical manuscripts. Evaluating those artifacts by exit code alone leaves the **Wooden Pole** ($\beta\acute{\epsilon}\rho\gamma\alpha$ / *Verga* — embodied sensorimotor poise, visual/acoustic warmth, spatial ergonomics, and typographic craft) unmeasured.

To prevent unmeasured *Verga* from silently defaulting to `100%`, **`skb gwi` (`sovereign-kb`)** enforces **4 Probe-Backed Specialist Craft Lenses**.

---

## 1. The Anti-Cosplay Admission Law (`v1.3.0`–`v1.4.0` Fable 5.1 $\times$ GPT-6 Astra Mandate)

A specialist perspective is **banned** if it consists only of a persona prompt (*"You are a world-class UX designer, rate this 1–10"*) or a source-code vocabulary grep (`grep 'InstancedMesh'`). Every Craft Lens must satisfy three engineering invariants:
1. **No `PASS` Without a Measured Artifact Number**: Every lens probe inspects the **rendered artifact itself** (decoded `rgb24` contact-sheet pixels, Playwright DOM bounding-box JSON, `ffmpeg ebur128` loudness + LRA dynamics, `.srt` CPS/line-length timing, or Poppler `pdffonts`/`pdfimages` streams) and emits `(value, target, status)`.
2. **Coordinate-Bound Contact Sheet Critique (`artifacts`)**: When GPT-6 Astra or Claude Fable 5.1 critiques a 1080p visual/acoustic contact sheet, every finding must cite an exact cell/frame/page coordinate and probe metric (`sheet TA-1, cell 4: camera frustum inside mesh AABB; luminance stddev 11.2`).
3. **Honest Unmeasured Governance Ceiling ($\mathcal{G}_{\text{Verga}} \le 7.80$)**: Under `--strict-lenses`, any required Craft Lens whose contact sheet or runtime telemetry is `UNMEASURED` is capped at **`7.80 / 10` (`VERGA_UNMEASURED_CAP`)**, keeping $\mathcal{G}_{\text{total}} = \sqrt{\mathcal{G}_{\text{Chrysos}} \cdot \mathcal{G}_{\text{Verga}}} < 9.0$ and blocking premature 9+ certification.

---

## 2. The 4 Canonical Craft Lenses

| Lens ID | Vault Specification | Auto-Activation Signals (Touch-Set / Repo Deps) | Primary Deterministic Probes & Vetoes | Contact Sheet Artifact |
| :--- | :--- | :--- | :--- | :--- |
| **`technical-artist`** | [[02-operating-system/lenses/technical-artist]] | `three`, `@react-three/*`, `.glsl`, `.wgsl`, `.glb/.gltf`, `.godot`, `.unity` | `renderer.info` (`draws <= 300`, VRAM), frame `p95 <= 16.6ms`, KTX2/Draco compression, camera $\notin$ mesh AABB, $1.6\text{ m}$ XR eye height, luminance histogram (`stddev >= 18`, clipped `< 15%`) | 6-Pose Canonical Orbit Sheet (`1920x1080`) + Luminance Histogram |
| **`ux-ergonomics`** | [[02-operating-system/lenses/ux-ergonomics]] | `routes/**`, `pages/**`, `components/**`, `.tsx`, `.css`, `metatopia-chameleon` | **2D HUD $\cap$ 3D Canvas Occlusion $\le 25\%$**, WCAG AA contrast, tap targets $\ge 44\text{ px}$, $1$ primary CTA above fold, $\le 5$ steps to first value, and Subtractive Design Laws (`L0`–`L6`) | 3-Viewport Responsive Sheet (`390` / `768` / `1440px`) |
| **`spatial-cinema`** | [[02-operating-system/lenses/spatial-cinema]] | `AudioContext`, `PannerNode`, `.mp4`, `.wav`, camera rigs, `syneidesis-visual-essay` | `ffmpeg ebur128` (`[-24, -14] LUFS`, true peak $\le -1.0\text{ dBTP}$, dead-air $< 3\text{ s}$), narration ducking $\ge 6\text{ dB}$, XR FOV $\le 90^\circ$, horizon roll $\le 3^\circ$ | 9-Frame Shot Sequence Sheet + Waveform / Spectrogram Strip |
| **`typography-editor`** | [[02-operating-system/lenses/typography-editor]] | `.tex`, `.bib`, `figures/*.pdf`, `book/**`, `paper*/**` | LaTeX `.log` `Overfull \hbox <= 10pt`, `0` undefined refs/citations, widow/orphan detection, float drift $\le 1$ page, figure text $\ge 7\text{ pt}$, Type-42 vector fonts | 12-Page-per-Sheet Thumbnail Grid (`1920x1080`) |

---

## 3. Coupled Goldwood Scoring ($\mathcal{G}_{\text{total}} = \sqrt{\mathcal{G}_{\text{Chrysos}} \cdot \mathcal{G}_{\text{Verga}}}$)

Let $\mathcal{L}_{\text{req}}(R)$ denote the set of Craft Lenses required by repository $R$'s domain and touch-set:

$$\mathcal{G}_{\text{Verga}}(R) = \min_{\ell \in \mathcal{L}_{\text{req}}(R)} \mathcal{S}_{\ell}(R), \qquad \mathcal{G}_{\text{total}}(R) = \sqrt{\mathcal{G}_{\text{Chrysos}}(R) \cdot \mathcal{G}_{\text{Verga}}(R)}$$

- **Honest Coverage Gate**: If any required lens $\ell \in \mathcal{L}_{\text{req}}(R)$ is unmeasured during a fast pass, the disposition is explicitly stamped `APPROVED [CHRYSOS-ONLY — VERGA UNMEASURED]` rather than claiming an unqualified `99/100`.
- **CLI Inspection**:
  ```bash
  skb gwi lenses                        # List all 4 Craft Lenses and their deterministic probes
  skb gwi audit <path> --strict-lenses  # Run full Goldwood + Multi-Lens Craft Audit on any repo
  ```

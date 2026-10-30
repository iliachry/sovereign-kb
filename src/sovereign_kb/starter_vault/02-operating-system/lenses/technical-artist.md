---
title: "Lens L1: Technical Artist & 3D Pipeline Specialist"
type: operating-system
updated: 2026-10-29
tags: [lens, technical-artist, 3d, webgl, webxr, shaders, threejs, godot, bevy, unity, verga]
lens_id: technical-artist
pole: verga
applies_to: ["**/*.glsl", "**/*.wgsl", "**/*.frag", "**/*.vert", "**/*.glb", "**/*.gltf", "**/*.godot", "**/*.tscn", "**/*.unity", "**/scenes/**", "**/three/**"]
applies_when_deps: ["three", "@react-three/fiber", "@react-three/drei", "@react-three/xr", "cesium", "bevy"]
related:
  - "[[02-operating-system/lenses/README-lenses]]"
  - "[[02-operating-system/goldwood-index-gwi-standard]]"
  - "[[03-life-domains/ventures-and-engineering]]"
---

# Lens L1: Technical Artist & 3D Pipeline Specialist (`technical-artist`)

Evaluates real-time 3D scenes, WebGL2/WebGPU shaders, WebXR immersive environments, 3D GIS digital twins, and native spatial simulations (Godot 4, Bevy ECS, Unity 6) for **GPU/shader budget discipline, spatial scale & camera ergonomics, and lighting/material legibility**.

---

## 1. Deterministic Telemetry Probes & Hard Vetoes

| Probe ID | Metric Inspected | Sovereign Target | Hard Veto Threshold |
| :--- | :--- | :--- | :--- |
| `ta.draw_calls` | `renderer.info.render.calls` (or engine draw calls per frame) | $\le 180$ calls | $> 300$ draw calls without instancing (`InstancedMesh` / `MultiMesh`) |
| `ta.frame_p95_ms` | 95th-percentile frame time across 120-frame orbit @ 1080p | $\le 16.6\text{ ms}$ ($60\text{ FPS}$) | $> 33.3\text{ ms}$ ($< 30\text{ FPS}$) |
| `ta.asset_compression` | Binary geometry/texture compression (`.glb`, KTX2, Draco, Meshopt) | $100\%$ compressed $>1\text{ MB}$ assets | Uncompressed `.gltf`/`.obj`/`.png` texture $> 2.0\text{ MB}$ in production bundle |
| `ta.camera_aabb_clearance` | Camera frustum origin & near-plane clearance vs. scene mesh AABBs | $\ge 0.25\text{ m}$ clearance; XR eye height $1.55\text{–}1.70\text{ m}$ | Camera spawned inside geometry or clipping interactive manipulatives at eye level |
| `ta.luminance_balance` | Rendered frame Rec.709 luminance ($Y = 0.2126R + 0.7152G + 0.0722B$) & clipping % | $\mu_Y \in [18, 215]$, $\sigma_Y \ge 14.0$, clipped ($Y < 2$ or $Y > 253$) $\le 15\%$ | Black/dead canvas ($\mu_Y < 6$), blown-out exposure ($>60\%$ clipped), or flat unlit ($\sigma_Y < 8$) |
| `ta.silhouette_and_pose_mse` | Sobel gradient edge density ($|\nabla Y| > 18$) & 6-tile pairwise $\text{MSE} = \frac{1}{N}\sum(Y_i - Y_j)^2$ | Edge density $\ge 0.03$; pairwise tile $\text{MSE} \ge 25.0$ | Duplicate static poses ($\text{MSE} < 3.0$) or featureless canvas |
| `ta.gpu_disposal` | WebGL/WGSL program link logs, `.dispose()` teardown & deterministic PRNG seeding | `0` shader warnings; explicit `.dispose()` on `Geometry`/`Material` | Unhandled `THREE.WebGLProgram` error, GPU VRAM leak, or unseeded `Math.random()` |

---

## 2. Builder Pre-Flight Constraints (Injected into `fable` / `sonnet`)

When a builder agent is dispatched on a repository where `technical-artist` is active, the following craft rules are enforced:
1. **Batching & Instancing**: Use `THREE.InstancedMesh` / Godot `MultiMeshInstance3D` whenever rendering $>25$ repeated meshes; keep total draw calls $\le 300$.
2. **Three-Point Lighting & Tonemapping**: Never rely on flat `AmbientLight` alone. Configure ACESFilmic or AgX tonemapping with distinct key, fill, and rim/backlight separation so 3D silhouettes read clearly against dark backgrounds.
3. **Human-Scale & XR Eye-Level Discipline**: Maintain $1.0\text{ unit} = 1.0\text{ meter}$; place XR standing cameras at $y = 1.60\text{ m}$ and keep floating UI/manipulatives between $0.85\text{ m}$ and $1.45\text{ m}$ (below eye line to prevent neck fatigue and near-plane clipping).
4. **Explicit Disposal**: Dispose geometries, materials, and render targets on component unmount to prevent GPU VRAM leaks.

---

## 3. Astra Stage-5 Contact Sheet Rubric (`6-Pose Canonical Orbit Sheet`)

Astra inspects the **6-Pose 1080p Orbit Sheet** (`front`, `3/4-key`, `top-down`, `eye-level-xr`, `wide-context`, `material-close-up`) and must cite `(cell_index, metric)` for every finding:
- **Silhouette & Depth Separation**: Can the primary subject be distinguished from the background in $<1\text{ second}$ at thumbnail scale?
- **Material Plausibility**: Do roughness/metalness values avoid plastic Phong-style specular blowouts?
- **Spatial Framing**: Are interactive 3D elements centered within the comfortable $60^\circ$ central field of view without intersecting the camera near plane?

"""Self-contained shell + HTML5 polyglot preamble generator for `.evr` capsules."""

from __future__ import annotations

import html
from typing import Any

from .codec import _escape_json_for_html_script, _pad64_bytes


def build_polyglot_preamble(title: str, preview_summary: dict[str, Any]) -> bytes:
    """
    Build a triple-polyglot preamble:
      1. Executable POSIX shell script (`chmod +x file.evr && ./file.evr` runs `evr inspect`)
      2. Self-contained HTML5 + 3D Spatial + Tensor + Search + Merkle Studio with strict XSS escaping
      3. Sealed with `<div hidden style="display:none!important"><plaintext>` and padded with
         ASCII spaces (`0x20`) to a strict 64-byte boundary so `EvrSuperblock` and every
         `RAW` chunk (`TENSOR`, `SQLITE`, `SPATIAL`, `MEDIA`) sit at `offset % 64 == 0`.
    """
    safe_title = html.escape(title, quote=True)
    embedded_json = _escape_json_for_html_script(preview_summary)
    html_doc = f""": '<!--'
if command -v evr >/dev/null 2>&1; then exec evr inspect "$0" "$@"; else exec python3 -m sovereign_kb.evr inspect "$0" "$@"; fi
-->
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} — .evr Everything Capsule</title>
<style>
:root {{
  --bg: #F8FAFC;
  --surface: #FFFFFF;
  --border: #E2E8F0;
  --text: #0F172A;
  --muted: #475569;
  --accent: #1E40AF;
  --accent-soft: #EFF6FF;
  --ok: #15803D;
  --mono: "JetBrains Mono", "SFMono-Regular", Menlo, monospace;
  --sans: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}}
header.topbar {{
  height: 52px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 10;
}}
.brand {{ display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 15px; }}
.badge {{
  font-family: var(--mono);
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid #BFDBFE;
}}
.badge-ok {{ background: #F0FDF4; color: var(--ok); border-color: #BBF7D0; }}
.layout {{
  display: grid;
  grid-template-columns: 2fr 7fr 3fr;
  gap: 16px;
  max-width: 1440px;
  margin: 16px auto;
  padding: 0 20px;
}}
.panel {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px;
}}
h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); margin-bottom: 10px; }}
.chunk-item {{
  padding: 7px 10px;
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--mono);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border: 1px solid transparent;
}}
.chunk-item:hover, .chunk-item.active {{
  background: var(--accent-soft);
  border-color: #BFDBFE;
  color: var(--accent);
}}
input.search-box {{
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 13px;
  margin-bottom: 12px;
}}
pre.code-view {{
  background: #0F172A;
  color: #F8FAFC;
  font-family: var(--mono);
  font-size: 12px;
  padding: 14px;
  border-radius: 6px;
  overflow: auto;
  max-height: 440px;
}}
canvas.viewport-3d {{
  width: 100%;
  height: 320px;
  background: #0F172A;
  border-radius: 6px;
  display: block;
}}
table.data-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-top: 8px;
}}
table.data-table th, table.data-table td {{
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
}}
table.data-table th {{ background: var(--bg); font-weight: 600; }}
.kv-row {{
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}}
.kv-row span:last-child {{ font-family: var(--mono); font-weight: 600; }}
.heatmap-grid {{
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 3px;
  margin-top: 10px;
}}
.heat-cell {{
  padding: 6px 4px;
  text-align: center;
  font-family: var(--mono);
  font-size: 11px;
  border-radius: 3px;
}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
<span class="badge">EVR v1.0</span>
<span id="cap-title">{safe_title}</span>
  </div>
  <div style="display:flex;gap:8px;align-items:center;">
<span class="badge badge-ok" id="align-pill">64B ALIGNED MMAP</span>
<span class="badge badge-ok" id="merkle-pill">MERKLE ENVELOPE</span>
  </div>
</header>
<main class="layout">
  <section class="panel" aria-label="Chunk Navigator">
<h2>Streams (<span id="chunk-count">0</span>)</h2>
<input type="search" id="search-input" class="search-box" placeholder="Filter streams...">
<div id="chunk-list"></div>
  </section>
  <section class="panel" aria-label="Primary Inspector">
<h2 id="inspector-heading">Capsule Overview</h2>
<div id="inspector-stage"></div>
  </section>
  <aside class="panel" aria-label="Cryptographic Provenance & Telemetry">
<h2>Provenance & Layout</h2>
<div id="provenance-list"></div>
  </aside>
</main>
<script id="evr-embedded-manifest" type="application/json">{embedded_json}</script>
<script>
(function() {{
  if (document.body && document.body.firstChild && document.body.firstChild.nodeType === 3) {{
document.body.firstChild.nodeValue = "";
  }}
  function esc(val) {{
return String(val ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#39;");
  }}
  const data = JSON.parse(document.getElementById("evr-embedded-manifest").textContent);
  document.getElementById("chunk-count").textContent = String(data.chunks.length);
  const provEl = document.getElementById("provenance-list");
  const rows = [
["Format", "EVR v" + data.version],
["Alignment", data.alignment + " bytes (SIMD/mmap)"],
["Total Chunks", String(data.chunks.length)],
["Modalities", (data.modalities || []).join(", ")],
["Merkle Root", (data.merkle_root || "").slice(0, 16) + "..."],
["Author", data.author || "Sovereign Organon"]
  ];
  provEl.innerHTML = rows.map(r => `<div class="kv-row"><span>${{esc(r[0])}}</span><span>${{esc(r[1])}}</span></div>`).join("");

  const listEl = document.getElementById("chunk-list");
  const stageEl = document.getElementById("inspector-stage");
  const headEl = document.getElementById("inspector-heading");

  function renderSpatialCanvas(meta) {{
const cvs = document.createElement("canvas");
cvs.className = "viewport-3d";
cvs.width = 640;
cvs.height = 320;
stageEl.appendChild(cvs);
const ctx = cvs.getContext("2d");
const verts = meta.preview_vertices || [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
const faces = meta.preview_faces || [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6]];
let angle = 0.4;
function draw() {{
  ctx.fillStyle = "#0F172A";
  ctx.fillRect(0, 0, cvs.width, cvs.height);
  const ca = Math.cos(angle), sa = Math.sin(angle);
  const proj = verts.map(v => {{
    const x = v[0]*ca - v[2]*sa;
    const z = v[0]*sa + v[2]*ca + 4.0;
    const y = v[1];
    const s = 220 / Math.max(z, 0.5);
    return [cvs.width/2 + x*s, cvs.height/2 - y*s];
  }});
  ctx.strokeStyle = "#38BDF8";
  ctx.lineWidth = 1.5;
  faces.forEach(f => {{
    ctx.beginPath();
    f.forEach((idx, i) => {{
      const p = proj[idx % proj.length];
      if (!p) return;
      if (i === 0) ctx.moveTo(p[0], p[1]);
      else ctx.lineTo(p[0], p[1]);
    }});
    ctx.closePath();
    ctx.stroke();
  }});
  angle += 0.015;
  requestAnimationFrame(draw);
}}
draw();
  }}

  function selectChunk(idx) {{
const ch = data.chunks[idx];
if (!ch) return;
document.querySelectorAll(".chunk-item").forEach((el, i) => el.classList.toggle("active", i === idx));
headEl.textContent = `${{ch.path}} [${{ch.kind}} · ${{ch.raw_len}} B]`;
stageEl.innerHTML = "";
const meta = ch.metadata || {{}};
if (ch.kind === "TENSOR") {{
  const vals = meta.preview_values || [];
  const minV = Number(meta.min ?? 0), maxV = Number(meta.max ?? 1);
  const span = Math.max(maxV - minV, 1e-6);
  stageEl.innerHTML = `<div class="kv-row"><span>Shape</span><span>[${{esc((meta.shape||[]).join(", "))}}] (${{esc(meta.dtype)}})</span></div>
    <div class="kv-row"><span>Range / L2 Norm</span><span>[${{esc(minV)}}, ${{esc(maxV)}}] · L2=${{esc(meta.l2_norm)}}</span></div>
    <div class="heatmap-grid">${{vals.slice(0, 32).map(v => {{
      const numV = Number(v) || 0;
      const t = Math.max(0, Math.min(1, (numV - minV) / span));
      const bg = `rgba(30, 64, 175, ${{0.12 + 0.75 * t}})`;
      const fg = t > 0.55 ? "#FFFFFF" : "#0F172A";
      return `<div class="heat-cell" style="background:${{bg}};color:${{fg}}">${{esc(numV)}}</div>`;
    }}).join("")}}</div>`;
}} else if (ch.kind === "SPATIAL") {{
  stageEl.innerHTML = `<div class="kv-row"><span>Geometry</span><span>${{esc(meta.vertex_count || 0)}} vertices · ${{esc(meta.face_count || 0)}} faces</span></div>`;
  renderSpatialCanvas(meta);
}} else if (ch.kind === "TABLE" && meta.preview_rows && meta.columns) {{
  const cols = meta.columns.slice(0, 4);
  const trs = meta.preview_rows.slice(0, 8).map(r => `<tr>${{cols.map(c => `<td>${{esc(r[c] ?? "")}}</td>`).join("")}}</tr>`).join("");
  stageEl.innerHTML = `<table class="data-table"><thead><tr>${{cols.map(c => `<th>${{esc(c)}}</th>`).join("")}}</tr></thead><tbody>${{trs}}</tbody></table>`;
}} else {{
  const pre = document.createElement("pre");
  pre.className = "code-view";
  pre.textContent = ch.preview_text || JSON.stringify(meta, null, 2);
  stageEl.appendChild(pre);
}}
  }}

  function renderList(filterText) {{
const q = (filterText || "").toLowerCase();
listEl.innerHTML = "";
data.chunks.forEach((ch, idx) => {{
  const hay = `${{ch.path}} ${{ch.kind}} ${{ch.preview_text || ""}}`.toLowerCase();
  if (q && !hay.includes(q)) return;
  const div = document.createElement("div");
  div.className = "chunk-item";
  const spPath = document.createElement("span");
  spPath.textContent = ch.path;
  const spKind = document.createElement("span");
  spKind.className = "badge";
  spKind.textContent = ch.kind;
  div.appendChild(spPath);
  div.appendChild(spKind);
  div.onclick = () => selectChunk(idx);
  listEl.appendChild(div);
}});
  }}

  document.getElementById("search-input").addEventListener("input", e => renderList(e.target.value));
  renderList("");
  if (data.chunks.length > 0) selectChunk(0);
}})();
</script>
<div hidden style="display:none!important"><plaintext>"""
    raw = html_doc.encode("utf-8")
    padding = _pad64_bytes(len(raw), fill=b" ")
    return raw + padding

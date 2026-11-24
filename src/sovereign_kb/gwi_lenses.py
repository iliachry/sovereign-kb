"""Compatibility import path for the craft-lens evaluators (`sovereign_kb.gwi_lenses`).

The evaluators live in the `sovereign_kb.gwi` package: one `_lens_<id>(ctx)` function per lens in
`gwi/lenses_verga_craft.py`, `gwi/lenses_verga_ux.py` and `gwi/lenses_chrysos.py`. `LENS_EVALUATORS`
is the same dict object that `gwi.evaluate_craft_lenses` dispatches through (`gwi/lenses_eval.py`),
so replacing an entry here changes what an audit runs.
"""

from __future__ import annotations

from sovereign_kb.gwi.lenses_chrysos import (
    _lens_ai_ml_science,
    _lens_data_api_architect,
    _lens_principal_verification,
    _lens_science_integrity,
    _lens_web_perf_edge,
)
from sovereign_kb.gwi.lenses_eval import LENS_EVALUATORS
from sovereign_kb.gwi.lenses_verga_craft import _lens_spatial_cinema, _lens_technical_artist, _lens_typography_editor
from sovereign_kb.gwi.lenses_verga_ux import _lens_ux_ergonomics

__all__ = [
    "LENS_EVALUATORS",
    "_lens_ai_ml_science",
    "_lens_data_api_architect",
    "_lens_principal_verification",
    "_lens_science_integrity",
    "_lens_spatial_cinema",
    "_lens_technical_artist",
    "_lens_typography_editor",
    "_lens_ux_ergonomics",
    "_lens_web_perf_edge",
]

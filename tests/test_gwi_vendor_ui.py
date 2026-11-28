"""Law L7 must judge authored UI only: vendored third-party CSS is excluded from UI discovery."""
from pathlib import Path

from sovereign_kb.gwi.lenses_laws import _discover_ui_files, audit_directory_regularity

_MANY_SIZES = "\n".join(f".size{i}{{font-size:{0.5 + i * 0.07:.2f}em}}" for i in range(40))


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_vendored_css_is_not_authored_ui(tmp_path):
    _write(tmp_path, "src/app/app.css", ".a{font-size:12px}.b{font-size:14px;border-radius:4px}")
    _write(tmp_path, "src/app/assets/vendor/katex/katex.min.css", _MANY_SIZES)
    _write(tmp_path, "third_party/lib/lib.css", _MANY_SIZES)
    files, _ = _discover_ui_files(tmp_path)
    assert [f.relative_to(tmp_path).as_posix() for f in files] == ["src/app/app.css"]
    res = audit_directory_regularity(tmp_path)
    assert res["distinct_font_sizes"] == 2 and res["passed"] is True


def test_same_css_outside_vendor_dir_still_counts(tmp_path):
    _write(tmp_path, "src/app/app.css", ".a{font-size:12px}")
    _write(tmp_path, "src/app/assets/katex/katex.min.css", _MANY_SIZES)
    res = audit_directory_regularity(tmp_path)
    assert res["distinct_font_sizes"] > 12 and res["status"] == "VETO"


def test_app_profile_v_dimension_ignores_vendored_css(tmp_path):
    from sovereign_kb.gwi.profile_app import evaluate_app_profile

    _write(tmp_path, "src/app/app.css", ".a{font-size:12px}:root{--color-x:#000}")
    _write(tmp_path, "src/app/index.html", "<!doctype html><html><body><dialog aria-label='x'></dialog></body></html>")
    _write(tmp_path, "src/app/assets/vendor/katex/katex.min.css", _MANY_SIZES)
    blockers = {b["id"] for b in evaluate_app_profile(tmp_path)["blockers"]}
    assert "GWI-APP-V02" not in blockers
    (tmp_path / "src/app/assets/vendor").rename(tmp_path / "src/app/assets/bundled")
    blockers = {b["id"] for b in evaluate_app_profile(tmp_path)["blockers"]}
    assert "GWI-APP-V02" in blockers

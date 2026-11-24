"""F-22: README drift gate (`readme_check.py`): test counts.

Regression for the way the gate failed on 2026-11-24: the test-count check never ran in a project whose own
pytest options include `-q`, so a README went on claiming a count that pytest no longer collected, and every
push printed "collection was unavailable".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sovereign_kb import readme_check
from sovereign_kb.readme_check import Collection, check, collected_tests, parse_collection

THIS_PYTEST = [sys.executable, "-m", "pytest"]  # stands in for `uv run pytest` / `uvx pytest`
VIA = " ".join(THIS_PYTEST)  # how the gate names the runner in its output
FAILS_TO_IMPORT = "import a_module_that_is_not_installed_anywhere\n\n\ndef test_never_collected():\n    assert True\n"
PRINTS_AN_ERROR_LINE_THEN_FAILS_TO_IMPORT = 'print("ERROR could not reach the fixture server")\n' + FAILS_TO_IMPORT
EXITS_WHILE_IMPORTED = "import sys\n\nsys.exit(3)\n\n\ndef test_never_collected():\n    assert True\n"
STOPS_COLLECTING_AT_THE_SECOND_MODULE = (
    "import pytest\n\n\ndef pytest_collectstart(collector):\n"
    '    if collector.nodeid.endswith("test_beta.py"):\n        pytest.exit("enough for today", returncode=0)\n'
)
PRINTS_ITS_OWN_EXCLAMATION_LINE = (
    "def pytest_terminal_summary(terminalreporter):\n"
    '    terminalreporter.write_sep("!", "remember to rebuild the fixtures")\n'
)
FAILS_THE_SESSION_AFTER_COLLECTING = (
    "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtestloop(session):\n"
    "    yield\n    session.testsfailed += 1\n"
)


@pytest.fixture(autouse=True)
def _no_machine_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commits in the throwaway repositories must not run this machine's global hooks or signing setup."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    for key, value in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(root, "config", key, value)
    return root


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _suite(root: Path, addopts: str = "") -> Path:
    """A pytest project with six tests in two files; three of them carry the `slow` marker."""
    _write(
        root / "pyproject.toml",
        f'[tool.pytest.ini_options]\naddopts = {json.dumps(addopts)}\ntestpaths = ["tests"]\nmarkers = ["slow"]\n',
    )
    _write(
        root / "tests" / "test_alpha.py",
        "import pytest\n\n\ndef test_one():\n    assert True\n\n\n"
        '@pytest.mark.slow\n@pytest.mark.parametrize("n", [1, 2, 3])\ndef test_each(n):\n    assert n\n',
    )
    _write(
        root / "tests" / "test_beta.py", "def test_two():\n    assert True\n\n\ndef test_three():\n    assert True\n"
    )
    return root


def _claiming(repo: Path, count: int) -> str:
    """Commit a README that states `count` tests; returns the commit."""
    _write(repo / "README.md", f"# proj\n\nThe suite has {count} tests.\n")
    return _commit(repo, f"readme says {count}")


def _run_with(monkeypatch: pytest.MonkeyPatch, cmd: list[str], own_env: bool = True) -> None:
    monkeypatch.setattr(readme_check, "_pytest_cmd", lambda repo: (cmd, own_env))


@pytest.fixture
def own_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collect with the pytest that runs this suite, as the project's own environment: same flags, no uv."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    _run_with(monkeypatch, THIS_PYTEST)


@pytest.fixture
def throwaway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same pytest, standing in for `uvx pytest`: an environment without the project's dependencies."""
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    _run_with(monkeypatch, THIS_PYTEST, own_env=False)


# --------------------------------------------------------------------------- test counts
@pytest.mark.parametrize("addopts", ["", "-q", "-qq", "-v", "-ra -q --strict-markers"])
def test_count_does_not_depend_on_the_projects_own_verbosity(tmp_path: Path, own_env: None, addopts: str) -> None:
    """F-22: A project's `addopts` cannot stack with the gate's own flags and take the summary line away."""
    repo = _suite(tmp_path / "proj", addopts)

    got = collected_tests(repo)

    assert (got.tests, got.failed) == (6, False)


def test_count_ignores_quiet_flags_from_the_environment(
    tmp_path: Path, own_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-22: `PYTEST_ADDOPTS` is read before the command line too, and stacks no better than `addopts`."""
    repo = _suite(tmp_path / "proj", "-q")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-q -q")

    assert collected_tests(repo).tests == 6


def test_wrong_readme_count_is_refused_in_a_project_that_sets_quiet(tmp_path: Path, own_env: None) -> None:
    """F-22: The incident: with `addopts = "-q"` the README count was never compared and the push passed."""
    repo = _suite(_repo(tmp_path / "proj"), "-q")
    stale = _claiming(repo, 9)

    issues, notes = check(repo, None, stale)

    assert issues == ["[count] README.md claims 9 tests but pytest collects 6"]
    assert not any("unavailable" in note for note in notes)

    issues, _ = check(repo, None, _claiming(repo, 6))

    assert issues == []


def test_deselected_tests_still_count_towards_the_total(tmp_path: Path, own_env: None) -> None:
    """F-22: With a deselecting `-m` the summary reads "3/6 tests collected"; the README is held to the six."""
    repo = _suite(tmp_path / "proj", '-q -m "not slow"')

    assert collected_tests(repo).tests == 6


@pytest.mark.parametrize(
    ("addopts", "extra_file", "content", "collected"),
    [
        ("-q", "tests/test_gamma.py", FAILS_TO_IMPORT, 6),
        ("-v --tb=short --color=yes", "tests/test_gamma.py", FAILS_TO_IMPORT, 6),
        ("-q --continue-on-collection-errors", "tests/test_gamma.py", FAILS_TO_IMPORT, 6),
        ("-q", "tests/test_gamma.py", EXITS_WHILE_IMPORTED, 6),
        ("-q", "conftest.py", STOPS_COLLECTING_AT_THE_SECOND_MODULE, 4),
    ],
    ids=[
        "module-fails-to-import",
        "same-with-forced-colour",
        "same-with-exit-code-1",
        "module-exits-while-imported",
        "hook-stops-collecting-and-exits-0",
    ],
)
def test_count_from_a_collection_that_did_not_end_cleanly_is_refused_not_compared(
    tmp_path: Path, own_env: None, addopts: str, extra_file: str, content: str, collected: int
) -> None:
    """F-22: The tests pytest got to are not the suite's count, even when the README states exactly that number.

    Each case is told by a different sign. An import failure puts an error on the summary line and exits 2;
    with `--continue-on-collection-errors` it exits 1, as a plugin's own verdict does. On pytest 9 a module that
    exits while imported leaves no "error" on the summary line: only the exit code, 3. A hook that stops the
    collection with `pytest.exit(returncode=0)` leaves neither: only pytest's banner.
    """
    repo = _suite(_repo(tmp_path / "proj"), addopts)
    _write(repo / extra_file, content)
    matches_what_was_collected = _claiming(repo, collected)

    issues, _ = check(repo, None, matches_what_was_collected)

    assert len(issues) == 1
    assert re.fullmatch(
        rf"\[count\] pytest --collect-only did not end cleanly \(exit [0-3] via `{re.escape(VIA)}`: [ -~]+\), "
        rf"so the README\.md test counts \[{collected}\] cannot be checked",
        issues[0],
    )


def test_refusal_names_the_module_that_did_not_import(tmp_path: Path, own_env: None) -> None:
    """F-22: The refusal carries pytest's summary line and the first module it lists as an error.

    A line that merely starts with "ERROR" in the module's captured output is not that list.
    """
    repo = _suite(_repo(tmp_path / "proj"), "-q")
    _write(repo / "tests" / "test_gamma.py", PRINTS_AN_ERROR_LINE_THEN_FAILS_TO_IMPORT)

    issues, _ = check(repo, None, _claiming(repo, 9))

    assert len(issues) == 1
    assert re.search(r"\(exit 2 via `.+`: 6 tests collected, 1 error in [\d.]+s; ERROR tests/test_gamma\.py", issues[0])


def test_refusal_quotes_pytests_crash_line_when_it_lists_no_module(tmp_path: Path, own_env: None) -> None:
    """F-22: pytest 9 turns a module that exits while imported into an internal error and lists nothing.

    Older releases list the module, as for any import failure. Either way the reader gets more than a count.
    """
    repo = _suite(_repo(tmp_path / "proj"), "-q")
    _write(repo / "tests" / "test_gamma.py", EXITS_WHILE_IMPORTED)

    issues, _ = check(repo, None, _claiming(repo, 9))

    assert len(issues) == 1
    assert re.search(r"; (INTERNALERROR> SystemExit: 3|ERROR tests/test_gamma\.py)", issues[0])


def test_unfinished_collection_in_a_throwaway_environment_is_a_note(tmp_path: Path, throwaway_env: None) -> None:
    """F-22: Under `uvx pytest` a project's dependencies are missing by construction.

    A module that cannot be imported there says nothing about the project, and no README text could satisfy a
    refusal, so the count is reported as unavailable instead.
    """
    repo = _suite(_repo(tmp_path / "proj"), "-q")
    _write(repo / "tests" / "test_gamma.py", FAILS_TO_IMPORT)

    issues, notes = check(repo, None, _claiming(repo, 9))

    assert issues == []
    assert any(f"unavailable (exit 2 via `{VIA}`: 6 tests collected, 1 error in " in note for note in notes)


def test_plugin_that_fails_the_session_after_a_full_collection_does_not_void_the_count(
    tmp_path: Path, own_env: None
) -> None:
    """F-22: pytest exits 1 when a plugin fails the session (a coverage threshold, say); every test was collected."""
    repo = _suite(_repo(tmp_path / "proj"), "-q")
    _write(repo / "conftest.py", FAILS_THE_SESSION_AFTER_COLLECTING)

    assert check(repo, None, _claiming(repo, 6))[0] == []
    assert check(repo, None, _claiming(repo, 9))[0] == ["[count] README.md claims 9 tests but pytest collects 6"]


def test_plugins_own_exclamation_line_does_not_void_the_count(tmp_path: Path, own_env: None) -> None:
    """F-22: Only pytest's own stop banners count; any plugin can print a `!!! ... !!!` line after a full collection."""
    repo = _suite(_repo(tmp_path / "proj"), "-q")
    _write(repo / "conftest.py", PRINTS_ITS_OWN_EXCLAMATION_LINE)

    assert check(repo, None, _claiming(repo, 6))[0] == []
    assert check(repo, None, _claiming(repo, 9))[0] == ["[count] README.md claims 9 tests but pytest collects 6"]


def test_folder_pytest_collects_nothing_from_gives_a_note_not_a_zero(tmp_path: Path, own_env: None) -> None:
    """F-22: A `tests/` folder that belongs to another runner must not turn a README count into a mismatch."""
    repo = _repo(tmp_path / "proj")
    _write(repo / "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    _write(repo / "tests" / "app.spec.ts", "test('renders', () => {});\n")

    issues, notes = check(repo, None, _claiming(repo, 4))

    assert issues == []
    assert any(f"collection was unavailable (exit 5 via `{VIA}`: no tests collected in " in note for note in notes)


def test_no_number_at_all_stays_a_note_even_when_every_module_fails_to_import(tmp_path: Path, own_env: None) -> None:
    """F-22: With no count printed there is nothing to hold against the README, in either kind of environment.

    It is also what an environment looks like that lacks the project's test dependencies or pytest plugins,
    which the gate cannot tell from a broken suite.
    """
    repo = _repo(tmp_path / "proj")
    _write(repo / "pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q"\ntestpaths = ["tests"]\n')
    _write(repo / "tests" / "test_only.py", FAILS_TO_IMPORT)

    issues, notes = check(repo, None, _claiming(repo, 4))

    assert issues == []
    assert any(f"unavailable (exit 2 via `{VIA}`: no tests collected, 1 error in " in note for note in notes)


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("tests/test_a.py::test_x\n\n1 test collected in 0.00s\n", (1, False)),
        ("tests/test_a.py::test_x\n\n3/6 tests collected (3 deselected) in 0.01s\n", (6, False)),
        ("tests/test_a.py::test_x\n\n6 tests collected, 1 error in 0.03s\n", (6, True)),
        ("tests/test_a.py::test_x\n\n3/6 tests collected (3 deselected), 2 errors in 0.03s\n", (6, True)),
        ("E   assert '5 tests collected, 1 error' in out\n\n6 tests collected in 0.03s\n", (6, False)),
        ("\n2 tests collected in 0.29s\n!!!!!!!! _pytest.outcomes.Exit: enough !!!!!!!!\n", (2, True)),
        (
            "!!!!!!! KeyboardInterrupt !!!!!!!\ntests/test_z.py:1: KeyboardInterrupt\n3 tests collected in 0.35s\n",
            (3, True),
        ),
        ("tests/test_c.py::test_p[!!! boom !!!]\n\n4 tests collected in 0.25s\n", (4, False)),
        ("\n3 tests collected in 0.30s\n!!!!!! remember to rebuild the fixtures !!!!!!\n", (3, False)),
        ("\nno tests collected in 0.00s\n", (None, False)),
        ("\nno tests collected, 1 error in 0.03s\n", (None, False)),
        ("\nno tests collected (6 deselected) in 0.00s\n", (None, False)),
        ("tests/test_a.py: 4\ntests/test_b.py: 2\n\n", (None, False)),
        ("", (None, False)),
    ],
    ids=[
        "one-test",
        "deselected-counts-the-total",
        "count-next-to-an-error",
        "deselected-and-errors",
        "summary-is-the-last-match",
        "stopped-by-a-hook",
        "interrupted-without-an-error-count",
        "exclamation-marks-in-a-test-id",
        "a-plugins-own-exclamation-line",
        "nothing-collected",
        "nothing-collected-and-error",
        "everything-deselected",
        "per-file-lines-are-not-summed",
        "no-output",
    ],
)
def test_summary_line_gives_the_count_and_whether_pytest_says_it_is_partial(
    stdout: str, expected: tuple[int | None, bool]
) -> None:
    """F-22: The count is pytest's own total; per-file lines (verbosity -2) would leave deselected tests out."""
    assert parse_collection(stdout) == expected


def test_usage_error_is_reported_by_its_own_line(tmp_path: Path, own_env: None) -> None:
    """F-22: An option the environment's pytest does not know ends in an `inifile:`/`rootdir:` trailer.

    The note has to carry the line that names the option, as when `addopts` asks for a plugin that is not
    installed where the gate runs.
    """
    repo = _suite(tmp_path / "proj", "--an-option-no-plugin-provides")

    got = collected_tests(repo)

    assert (got.tests, got.failed) == (None, False)
    assert got.outcome.startswith(f"exit 4 via `{VIA}`: ")
    assert got.outcome.endswith("error: unrecognized arguments: --an-option-no-plugin-provides")


def test_unavailable_count_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-22: The push note names the reason, so a check that never runs is not mistaken for a missing tool."""
    assert collected_tests(tmp_path) == Collection(None, False, "no tests/ directory")

    (tmp_path / "tests").mkdir()
    _run_with(monkeypatch, ["no-such-runner", "--quiet", "pytest"])
    assert collected_tests(tmp_path) == Collection(
        None, False, "could not start `no-such-runner pytest`: FileNotFoundError"
    )

    python = sys.executable
    spawn_failure = (
        "import sys; sys.stderr.write('error: Failed to spawn: pytest\\n  cause: No such file\\n'); sys.exit(2)"
    )
    _run_with(monkeypatch, [python, "-c", spawn_failure])
    assert (
        collected_tests(tmp_path).outcome == f"exit 2 via `{python} -c {spawn_failure}`: error: Failed to spawn: pytest"
    )

    _run_with(monkeypatch, [python, "-c", "pass"])
    assert collected_tests(tmp_path) == Collection(None, False, f"exit 0 via `{python} -c pass`: no output")

    monkeypatch.setattr(readme_check, "COLLECT_TIMEOUT", 0.5)
    _run_with(monkeypatch, [python, "-c", "import time; time.sleep(60)"])
    assert collected_tests(tmp_path).outcome.startswith("timed out after 0.5 s via `")


def test_output_that_is_not_valid_text_does_not_crash_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-22: A byte this process cannot decode is replaced; an exception here would refuse the push with a traceback."""
    (tmp_path / "tests").mkdir()
    emits_latin_1 = "import sys; sys.stdout.buffer.write(b'caf\\xe9 \\x81\\n\\n3 tests collected in 0.01s\\n')"
    _run_with(monkeypatch, [sys.executable, "-c", emits_latin_1])

    assert collected_tests(tmp_path).tests == 3

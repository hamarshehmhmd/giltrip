"""End-to-end against a real free-threaded CPython and real compiled extension modules."""
import json
import os
import subprocess
import sys

import pytest

from conftest import run_cli


def _scan(ftpy, site, *extra):
    return run_cli("scan", "--python", ftpy, "--path", site, "--format", "json", *extra)


def test_scan_finds_every_gil_needing_module(ftpy, site):
    r = _scan(ftpy, site)
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert rep["python"]["free_threaded"] is True
    mods = {c["module"]: c for c in rep["culprits"]}
    # both culprits are found, not just the first one that flipped the GIL on
    assert set(mods) == {"fixpkg.gilneed", "fixpkg.legacy"}
    assert mods["fixpkg.gilneed"]["dist"] == "fixpkg"
    assert mods["fixpkg.gilneed"]["version"] == "1.0"
    assert mods["fixpkg.gilneed"]["source"].startswith("built from source")
    assert "fixpkg.gilfree" in rep["ok"]
    assert "fixpkg.libhelper" in rep["skipped"]
    assert {"module": "crashpkg.boom", "error": rep["errors"][0]["error"]} == rep["errors"][0]
    assert rep["errors"][0]["error"].startswith("crash")


def test_scan_text_output(ftpy, site):
    r = run_cli("scan", "--python", ftpy, "--path", site, "-v")
    assert r.returncode == 1
    assert "GIL re-enabled by 2 modules from 1 distribution" in r.stdout
    assert "fixpkg 1.0" in r.stdout
    assert "crashpkg.boom: crash" in r.stdout
    assert "repro: uv run --no-project --python 3." in r.stdout


def test_scan_allow_flag_and_pyproject(ftpy, site, tmp_path):
    r = _scan(ftpy, site, "--allow", "fixpkg", "--exclude", "crashpkg.*")
    assert r.returncode == 0, r.stdout
    rep = json.loads(r.stdout)
    assert rep["culprits"] == [] and len(rep["allowed"]) == 2 and rep["errors"] == []
    (tmp_path / "pyproject.toml").write_text('[tool.giltrip]\nallow = ["fixpkg.legacy"]\nexclude = ["crashpkg.*"]\n')
    r = run_cli("scan", "--python", ftpy, "--path", site, "--format", "json", cwd=tmp_path)
    rep = json.loads(r.stdout)
    assert [c["module"] for c in rep["culprits"]] == ["fixpkg.gilneed"]
    assert [c["module"] for c in rep["allowed"]] == ["fixpkg.legacy"]


def test_scan_only_and_exit_zero(ftpy, site):
    r = _scan(ftpy, site, "--only", "fixpkg", "--exit-zero")
    assert r.returncode == 0
    rep = json.loads(r.stdout)
    assert rep["checked"] == 4 and len(rep["culprits"]) == 2
    r = _scan(ftpy, site, "--only", "nosuchdist")
    assert r.returncode == 2 and "no extension modules found" in r.stderr


def test_scan_ignores_python_gil_env(ftpy, site, monkeypatch, capsys):
    # with PYTHON_GIL=0 CPython stays quiet; giltrip must still find the culprits.
    # (In-process: a regular-build host Python refuses to even start with PYTHON_GIL=0.)
    from giltrip import cli
    monkeypatch.setenv("PYTHON_GIL", "0")
    monkeypatch.setenv("NO_COLOR", "1")
    rc = cli.main(["scan", "--python", ftpy, "--path", str(site), "--exclude", "crashpkg.*"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "GIL re-enabled by 2 modules" in out
    assert "PYTHON_GIL=0 is set in your shell; giltrip ignored it" in out


def test_check_your_own_module_reports_what_it_pulls_in(ftpy, site, app):
    a = app("from fixpkg import wrapper\n", name="myapp.py")
    r = run_cli("check", "--python", ftpy, "--path", site, "--path", a.parent, "--format", "json", "myapp")
    assert r.returncode == 1, r.stderr
    rep = json.loads(r.stdout)
    assert [(c["module"], c["triggered_by"]) for c in rep["culprits"]] == [("fixpkg.gilneed", "myapp")]


def test_check_clean_and_missing(ftpy, site):
    r = run_cli("check", "--python", ftpy, "--path", site, "fixpkg.gilfree", "json")
    assert r.returncode == 0 and "OK: nothing here turns the GIL back on." in r.stdout
    r = run_cli("check", "--python", ftpy, "nosuchmodule_xyz")
    assert r.returncode == 2 and "ModuleNotFoundError" in r.stderr


def test_check_timeout(ftpy, app):
    a = app("import time\ntime.sleep(30)\n", name="sleepy.py")
    r = run_cli("check", "--python", ftpy, "--path", a.parent, "--timeout", "3", "sleepy")
    assert r.returncode == 2 and "sleepy: timeout" in r.stderr


def test_regular_python_is_rejected(tmp_path):
    import sysconfig
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        pytest.skip("test runner itself is free-threaded")
    r = run_cli("scan", "--python", sys.executable, cwd=tmp_path)
    assert r.returncode == 2
    assert "needs a free-threaded Python" in r.stderr and "regular GIL build" in r.stderr


def test_run_reports_the_line_that_imported_it(ftpy, site, app):
    a = app("""
        import sys
        import fixpkg.gilfree

        def later():
            import fixpkg.legacy  # after the GIL is already on

        import fixpkg.wrapper
        later()
        print("gil", sys._is_gil_enabled())
    """)
    rep_file = a.parent / "rep.json"
    r = run_cli("run", "--report", rep_file, "--", ftpy, a, env={"PYTHONPATH": str(site)})
    assert r.returncode == 0, r.stderr
    assert "gil True" in r.stdout
    rep = json.loads(rep_file.read_text())
    mods = {c["module"]: c for c in rep["culprits"]}
    assert set(mods) == {"fixpkg.gilneed", "fixpkg.legacy"}
    assert mods["fixpkg.gilneed"]["first"] is True
    assert mods["fixpkg.gilneed"]["imported_at"] == "%s:8" % a
    # loaded after the GIL was already on: still caught, verified in a fresh interpreter
    assert mods["fixpkg.legacy"]["first"] is False
    assert mods["fixpkg.legacy"]["imported_at"] == "%s:6" % a
    assert "fixpkg.gilfree" in rep["ok"]


def test_run_fail_flag_and_exit_code_passthrough(ftpy, site, app):
    a = app("import fixpkg.gilneed\n")
    env = {"PYTHONPATH": str(site)}
    assert run_cli("run", "--", ftpy, a, env=env).returncode == 0
    assert run_cli("run", "--fail", "--", ftpy, a, env=env).returncode == 1
    assert run_cli("run", "--fail", "--allow", "fixpkg", "--", ftpy, a, env=env).returncode == 0
    b = app("import sys, fixpkg.gilneed\nsys.exit(7)\n", name="b.py")
    assert run_cli("run", "--fail", "--", ftpy, b, env=env).returncode == 7


def test_run_clean_program(ftpy, site, app):
    a = app("import fixpkg.gilfree, json, _decimal\n")
    r = run_cli("run", "--fail", "--", ftpy, a, env={"PYTHONPATH": str(site)})
    assert r.returncode == 0 and "OK: nothing here turns the GIL back on." in r.stdout


def test_run_follows_child_processes_and_keeps_existing_sitecustomize(ftpy, site, app, tmp_path):
    sc = tmp_path / "sc"
    sc.mkdir()
    marker = tmp_path / "marker"
    (sc / "sitecustomize.py").write_text("open(%r, 'a').write('x')\n" % str(marker))
    child = app("import fixpkg.legacy\n", name="child.py")
    parent = app("import subprocess, sys\nsubprocess.check_call([sys.executable, %r])\n" % str(child), name="parent.py")
    rep_file = tmp_path / "r.json"
    env = {"PYTHONPATH": os.pathsep.join([str(site), str(sc)])}
    r = run_cli("run", "--report", rep_file, "--", ftpy, parent, env=env)
    assert r.returncode == 0, r.stderr
    rep = json.loads(rep_file.read_text())
    assert rep["processes"] == 2
    assert [c["module"] for c in rep["culprits"]] == ["fixpkg.legacy"]
    assert len(marker.read_text()) >= 2  # the user's own sitecustomize still ran in both watched processes


def test_run_under_pytest(ftpy, site, tmp_path):
    has = subprocess.run([ftpy, "-c", "import pytest"], capture_output=True).returncode == 0
    if not has:
        pytest.skip("pytest not installed in the free-threaded interpreter")
    t = tmp_path / "test_x.py"
    t.write_text("def test_a():\n    import fixpkg.gilneed\n")
    r = run_cli("run", "--fail", "--format", "markdown", "--", ftpy, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                t, env={"PYTHONPATH": str(site)}, cwd=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "| fixpkg | 1.0 | `fixpkg.gilneed` | test_x.py:2 |" in r.stdout  # relative to the working dir

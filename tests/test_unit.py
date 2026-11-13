import io
import json
import os
import sys

from giltrip import config, engine, pypi, render, report
from giltrip import run as runmod

sys.path.insert(0, os.path.join(os.path.dirname(engine.__file__)))
import probe  # noqa: E402


def test_parse_log_attributes_each_gil_line_to_the_import_that_caused_it():
    log = "\n".join([
        "import 'json' # something",
        "@@giltrip@@ BEGIN pkg.a",
        "# loading module 'pkg._core', which requires the GIL",
        "# loading module 'pkg.a', which requires the GIL",
        "@@giltrip@@ OK pkg.a",
        "@@giltrip@@ BEGIN pkg.b",
        "# loading module 'pkg._core', which requires the GIL",
        "@@giltrip@@ ERR pkg.b ImportError: dynamic module does not define module export function (PyInit_b)",
        "@@giltrip@@ BEGIN pkg.c",
        "@@giltrip@@ ERR pkg.c ValueError: nope",
        '@@giltrip@@ FILES {"pkg.a": "/x/a.so"}',
        "@@giltrip@@ END gil=1",
    ])
    r = engine.parse_log(log)
    assert r["culprits"] == {"pkg._core": "pkg.a", "pkg.a": "pkg.a"}
    assert r["status"] == {"pkg.a": "ok", "pkg.b": "skip", "pkg.c": "error: ValueError: nope"}
    assert r["files"] == {"pkg.a": "/x/a.so"}
    assert r["finished"] and r["gil_end"] is True and r["current"] is None


def test_parse_log_unfinished_run_reports_current_module():
    r = engine.parse_log("@@giltrip@@ BEGIN a\n@@giltrip@@ OK a\n@@giltrip@@ BEGIN b\n")
    assert not r["finished"] and r["current"] == "b"


def test_check_group_isolates_crash_and_continues(monkeypatch):
    calls = []

    def fake(python, names, paths, timeout):
        calls.append(list(names))
        if "b" in names:
            log = "".join("@@giltrip@@ BEGIN %s\n@@giltrip@@ OK %s\n" % (n, n) for n in names[:names.index("b")])
            return engine.parse_log(log + "@@giltrip@@ BEGIN b\n"), -6, False
        log = "".join("@@giltrip@@ BEGIN %s\n# loading module '%s', which requires the GIL\n@@giltrip@@ OK %s\n"
                      % (n, n, n) for n in names)
        return engine.parse_log(log + "@@giltrip@@ END gil=1\n"), 0, False

    monkeypatch.setattr(engine, "_run_batch", fake)
    res = engine.check_group("py", ["a", "b", "c", "d"])
    assert calls == [["a", "b", "c", "d"], ["c", "d"]]
    assert res["status"] == {"a": "ok", "b": "crash (exit code -6)", "c": "ok", "d": "ok"}
    assert set(res["culprits"]) == {"c", "d"}


def test_check_group_timeout(monkeypatch):
    seq = iter([(engine.parse_log("@@giltrip@@ BEGIN slow\n"), None, True),
                (engine.parse_log("@@giltrip@@ BEGIN fast\n@@giltrip@@ OK fast\n@@giltrip@@ END gil=0\n"), 0, False)])
    monkeypatch.setattr(engine, "_run_batch", lambda *a: next(seq))
    res = engine.check_group("py", ["slow", "fast"])
    assert res["status"] == {"slow": "timeout", "fast": "ok"}


def test_module_name_from_path():
    sfx = [".cpython-314t-x86_64-linux-gnu.so", ".abi3.so", ".so"]
    assert probe._module_name("lxml/etree.cpython-314t-x86_64-linux-gnu.so", sfx) == "lxml.etree"
    assert probe._module_name("_cffi_backend.cpython-314t-x86_64-linux-gnu.so", sfx) == "_cffi_backend"
    assert probe._module_name("pkg/_x.abi3.so", sfx) == "pkg._x"
    assert probe._module_name("numpy.libs/libopenblas.so", sfx) is None
    assert probe._module_name("pkg/not-valid.so", sfx) is None
    assert probe._module_name("pkg/readme.txt", sfx) is None


def test_source_of():
    assert report.source_of(None) == "not from an installed distribution"
    assert report.source_of({"tags": ["cp314-cp314t-manylinux_2_28_x86_64"]}).startswith("wheel ")
    assert report.source_of({"tags": ["cp314-cp314t-linux_x86_64"]}).startswith("built from source")
    assert report.source_of({"tags": ["cp314-cp314t-macosx_11_0_arm64"]}).startswith("wheel ")
    assert report.source_of({"tags": [], "direct_url": "file:///src/pkg"}).startswith("local install")
    assert report.source_of({"tags": []}).startswith("unknown")


def test_is_allowed():
    assert report.is_allowed("lxml.etree", "lxml", ["lxml"])
    assert report.is_allowed("scout_apm.core._objtrace", "scout-apm", ["Scout_APM"])
    assert report.is_allowed("pkg.sub.mod", None, ["pkg.sub"])
    assert report.is_allowed("pkg.speedups", None, ["*.speedups"])
    assert not report.is_allowed("pkgx.mod", "pkgx", ["pkg"])


def test_pick_repro_prefers_your_import_then_shallow_public():
    items = [{"module": "a._b", "repro": 1}, {"module": "a.c.d", "repro": 2}, {"module": "a.e", "repro": 3}]
    assert report.pick_repro(items)["repro"] == 3
    items.append({"module": "a.c.d.e", "repro": 4, "imported_at": "app.py:3"})
    assert report.pick_repro(items)["repro"] == 4


def test_repro_command():
    info = {"version": "3.14.10"}
    cmd = report.repro(info, "lxml.etree", {"name": "lxml", "version": "6.1.3"})
    assert cmd == ('uv run --no-project --python 3.14t --with lxml==6.1.3 python -c '
                   '"import sys, lxml.etree; assert not sys._is_gil_enabled()"')


def test_ft_wheels():
    files = ["x-1-cp314-cp314t-manylinux_2_28_x86_64.whl", "x-1-cp314-cp314-manylinux_2_28_x86_64.whl",
             "x-1.tar.gz", "x-1-cp315-abi3t-manylinux_2_28_x86_64.whl"]
    assert pypi.ft_wheels(files, "cp314t") == ["x-1-cp314-cp314t-manylinux_2_28_x86_64.whl"]
    assert pypi.ft_wheels(files, "cp315t") == ["x-1-cp315-abi3t-manylinux_2_28_x86_64.whl"]
    assert pypi.ft_wheels(files, "cp313t") == []


def test_pypi_latest_with_fake_opener():
    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = {"info": {"version": "2.0"}, "urls": [{"filename": "x-2.0-cp314-cp314t-win_amd64.whl"}]}
    got = pypi.latest("x", "cp314t", opener=lambda req, timeout: R(json.dumps(payload).encode()))
    assert got == {"latest": "2.0", "latest_ft_wheels": 1}

    def boom(req, timeout):
        raise OSError("offline")
    assert "offline" in pypi.latest("x", "cp314t", opener=boom)["pypi_error"]


def test_config_load(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.giltrip]\nallow = ["lxml"]\nexclude = ["*.tests.*"]\n')
    assert config.load(str(tmp_path)) == {"allow": ["lxml"], "exclude": ["*.tests.*"]}
    assert config.load(str(tmp_path / "missing")) == {}


def test_user_frame_skips_site_packages(tmp_path):
    site = str(tmp_path / "site")
    chain = ["/app/main.py:3", "/app/db.py:10", site + "/sqlalchemy/__init__.py:5"]
    assert runmod._user_frame(chain, [site]) == "/app/db.py:10"


def test_root_for():
    assert runmod._root_for("a.b.c", "/s/a/b/c.so") == "/s"
    assert runmod._root_for("mod", "/s/mod.so") == "/s"


def _rep(culprits):
    return {"python": {"version": "3.14.10", "abi_tag": "cp314t", "executable": "/py"}, "checked": 3,
            "culprits": culprits, "allowed": [], "errors": [], "ok": [], "skipped": []}


def test_render_text_and_markdown():
    c = {"module": "lxml.etree", "dist": "lxml", "version": "6.1.3", "source": "wheel t", "triggered_by": None,
         "repro": "uv run ...", "imported_at": "app.py:6", "latest": "6.2.0", "latest_ft_wheels": 4}
    buf = io.StringIO()
    render.text(_rep([c]), out=buf)
    out = buf.getvalue()
    assert "GIL re-enabled by 1 module from 1 distribution" in out
    assert "imported at app.py:6" in out and "newer release on PyPI: 6.2.0 (you have 6.1.3, it has 4 cp314t wheels): try upgrading" in out
    md = render.markdown(_rep([c]))
    assert "| lxml | 6.1.3 | `lxml.etree` | app.py:6 | wheel t |" in md
    buf = io.StringIO()
    render.text(_rep([]), out=buf)
    assert "OK: nothing here turns the GIL back on." in buf.getvalue()


def test_github_summary_and_annotations(tmp_path, monkeypatch, capsys):
    summ = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summ))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    c = {"module": "m", "dist": "d", "version": "1", "source": "s", "repro": "r"}
    render.github(_rep([c]))
    assert "`m`" in summ.read_text()
    assert "::warning title=GIL re-enabled::m (d 1) re-enables the GIL on cp314t" in capsys.readouterr().out


def test_display_relative_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runmod._display(str(tmp_path / "pkg" / "a.py") + ":3") == os.path.join("pkg", "a.py") + ":3"
    assert runmod._display("/elsewhere/b.py:9") == "/elsewhere/b.py:9"
    assert runmod._display(None) is None


def test_pypi_note_same_version():
    c = {"version": "1.0", "latest": "1.0", "latest_ft_wheels": 0}
    assert render._pypi_note(c, "cp314t") == ("already the latest release on PyPI (no cp314t wheels), "
                                              "so upgrading won't fix it: tell upstream")

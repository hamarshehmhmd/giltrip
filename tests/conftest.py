import json
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CSRC = os.path.join(HERE, "fixtures", "c")


def _is_ft(py):
    try:
        out = subprocess.run([py, "-c", "import sysconfig;print(sysconfig.get_config_var('Py_GIL_DISABLED'))"],
                             capture_output=True, text=True, timeout=60)
        return out.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def find_ft_python():
    env = os.environ.get("GILTRIP_TEST_PYTHON")
    if env:
        return env if _is_ft(env) else None
    for v in ("3.14t", "3.15t", "3.13t"):
        p = shutil.which("python" + v)
        if p and _is_ft(p):
            return p
    if shutil.which("uv"):
        for v in ("3.14t", "3.15t", "3.13t"):
            r = subprocess.run(["uv", "python", "find", v], capture_output=True, text=True)
            if r.returncode == 0 and _is_ft(r.stdout.strip()):
                return r.stdout.strip()
    return None


@pytest.fixture(scope="session")
def ftpy():
    py = find_ft_python()
    if not py:
        if os.environ.get("GILTRIP_REQUIRE_FT"):
            pytest.fail("no free-threaded Python found but GILTRIP_REQUIRE_FT is set")
        pytest.skip("no free-threaded Python available (set GILTRIP_TEST_PYTHON)")
    return py


def _cfg(py):
    code = "import sysconfig,json;print(json.dumps([sysconfig.get_paths()['include'],sysconfig.get_config_var('EXT_SUFFIX')]))"
    return json.loads(subprocess.check_output([py, "-c", code], text=True))


def _cc(py, src, out, include):
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc or sys.platform == "win32":
        pytest.skip("needs a C compiler (Linux/macOS)")
    flags = ["-shared", "-fPIC", "-O0"]
    if sys.platform == "darwin":
        flags += ["-undefined", "dynamic_lookup"]
    subprocess.check_call([cc] + flags + ["-I", include, "-o", out, src])


@pytest.fixture(scope="session")
def site(ftpy, tmp_path_factory):
    """A fake site-packages dir with one distribution (fixpkg) and a crashing package."""
    include, ext = _cfg(ftpy)
    root = tmp_path_factory.mktemp("site")
    pkg = root / "fixpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "wrapper.py").write_text("from . import gilneed\n")
    files = ["fixpkg/__init__.py", "fixpkg/wrapper.py"]
    for name in ("gilfree", "gilneed", "legacy"):
        _cc(ftpy, os.path.join(CSRC, name + ".c"), str(pkg / (name + ext)), include)
        files.append("fixpkg/" + name + ext)
    _cc(ftpy, os.path.join(CSRC, "libhelper.c"), str(pkg / "libhelper.so"), include)
    files.append("fixpkg/libhelper.so")
    crash = root / "crashpkg"
    crash.mkdir()
    (crash / "__init__.py").write_text("")
    _cc(ftpy, os.path.join(CSRC, "boom.c"), str(crash / ("boom" + ext)), include)
    di = root / "fixpkg-1.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: fixpkg\nVersion: 1.0\n")
    (di / "WHEEL").write_text("Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp314-cp314t-linux_x86_64\n")
    (di / "INSTALLER").write_text("pip\n")
    files += ["fixpkg-1.0.dist-info/METADATA", "fixpkg-1.0.dist-info/WHEEL", "fixpkg-1.0.dist-info/RECORD"]
    (di / "RECORD").write_text("".join(f + ",,\n" for f in files))
    return root


@pytest.fixture
def app(tmp_path):
    def make(src, name="app.py"):
        p = tmp_path / name
        p.write_text(textwrap.dedent(src))
        return p
    return make


def run_cli(*args, env=None, cwd=None):
    cmd = [sys.executable, "-m", "giltrip"] + [str(a) for a in args]
    e = dict(os.environ)
    e.pop("GITHUB_STEP_SUMMARY", None)
    e.pop("GITHUB_ACTIONS", None)
    e.pop("VIRTUAL_ENV", None)
    e["NO_COLOR"] = "1"
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=cwd, timeout=600)

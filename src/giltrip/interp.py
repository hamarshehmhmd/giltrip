"""Find the free-threaded interpreter to check and ask it about itself."""
import json
import os
import re
import shutil
import subprocess
import sys

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.py")


class GiltripError(Exception):
    """A problem the user has to fix (exit code 2)."""


def child_env(extra=None, keep_gil=False):
    env = dict(os.environ)
    env.pop("PYTHONVERBOSE", None)
    env.pop("PYTHONWARNINGS", None)
    if not keep_gil:
        env.pop("PYTHON_GIL", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def _venv_python(root):
    for rel in ("bin/python", "Scripts/python.exe"):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    return None


def _from_spec(spec):
    """'3.14t' / 'python3.14t' / a path -> an interpreter path, or None."""
    if os.path.sep in spec or (os.altsep and os.altsep in spec) or os.path.exists(spec):
        if os.path.isdir(spec):
            return _venv_python(spec)
        return spec if os.path.exists(spec) else None
    if re.fullmatch(r"\d+\.\d+t?", spec):
        found = shutil.which("python" + spec)
        if found:
            return found
        if shutil.which("uv"):
            try:
                out = subprocess.run(["uv", "python", "find", spec], capture_output=True, text=True, timeout=60)
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.strip().splitlines()[-1]
            except (OSError, subprocess.SubprocessError):
                pass
        return None
    return shutil.which(spec)


def candidates(spec=None):
    """Interpreters to try, most specific first."""
    if spec:
        p = _from_spec(spec)
        if not p:
            raise GiltripError("can't find a Python interpreter for %r (try a path, or install it: uv python install %s)"
                               % (spec, spec if re.fullmatch(r"\d+\.\d+t?", spec) else "3.14t"))
        return [p]
    out = []
    venv = os.environ.get("VIRTUAL_ENV")
    if venv and _venv_python(venv):
        out.append(_venv_python(venv))
    local = _venv_python(os.path.join(os.getcwd(), ".venv"))
    if local:
        out.append(local)
    out.append(sys.executable)
    seen, uniq = set(), []
    for p in out:
        key = os.path.realpath(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def inventory(python, paths=(), also=()):
    cmd = [python, PROBE, "inventory"]
    for p in paths:
        cmd += ["--path", os.path.abspath(p)]
    for p in also:
        cmd += ["--also", os.path.abspath(p)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=child_env(),
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        raise GiltripError("could not run %s: %s" % (python, e))
    if r.returncode != 0:
        raise GiltripError("%s failed to describe itself:\n%s" % (python, r.stderr.strip()[-2000:]))
    info = json.loads(r.stdout)
    info["python_gil_env"] = os.environ.get("PYTHON_GIL")  # the child ran without it on purpose
    return info


def resolve(spec=None, paths=()):
    """Return the inventory of the first free-threaded candidate interpreter."""
    tried = []
    for py in candidates(spec):
        info = inventory(py, paths)
        if info["free_threaded"]:
            return info
        tried.append("%s (Python %s, regular GIL build)" % (py, info["version"]))
    raise GiltripError(
        "giltrip needs a free-threaded Python (3.13t or newer) to check, and none of these is one:\n  "
        + "\n  ".join(tried)
        + "\nPoint it at one with --python, e.g. --python .venv/bin/python or --python 3.14t "
          "(install with: uv python install 3.14t)"
    )

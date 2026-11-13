"""Import extension modules in a fresh free-threaded interpreter with -v and read what CPython says.

CPython prints "# loading module 'X', which requires the GIL" (with -v) for *every* extension
module that hasn't declared Py_mod_gil = Py_MOD_GIL_NOT_USED, not only the first one that flips
the GIL on. That is what lets one process find all of them.
"""
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .interp import PROBE, child_env

MARK = "@@giltrip@@"
GIL_LINE = re.compile(r"^# loading module '([^']+)', which requires the GIL")
NOT_EXT = ("does not define module export function", "dynamic module does not define")


def parse_log(text):
    """Parse the probe's stderr. Returns dict with culprits, status, files, finished, current."""
    culprits = {}
    status = {}
    files = {}
    current = None
    finished = False
    gil_end = None
    for line in text.splitlines():
        m = GIL_LINE.match(line)
        if m:
            culprits.setdefault(m.group(1), current)
            continue
        if not line.startswith(MARK):
            continue
        rest = line[len(MARK) + 1:]
        kind, _, arg = rest.partition(" ")
        if kind == "BEGIN":
            current = arg
        elif kind == "OK":
            status[arg] = "ok"
            current = None
        elif kind == "ERR":
            name, _, msg = arg.partition(" ")
            status[name] = ("skip" if any(s in msg for s in NOT_EXT) else "error: " + msg)
            current = None
        elif kind == "FILES":
            try:
                files = json.loads(arg)
            except ValueError:
                files = {}
        elif kind == "END":
            finished = True
            gil_end = arg.strip() == "gil=1"
    return {"culprits": culprits, "status": status, "files": files, "finished": finished,
            "current": current, "gil_end": gil_end}


def _run_batch(python, names, paths, timeout):
    req = json.dumps({"names": names, "paths": list(paths)})
    cmd = [python, "-P", "-v", PROBE, "import"]
    try:
        r = subprocess.run(cmd, input=req, capture_output=True, text=True, timeout=timeout,
                           env=child_env(), encoding="utf-8", errors="replace")
        return parse_log(r.stderr), r.returncode, False
    except subprocess.TimeoutExpired as e:
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return parse_log(err), None, True


def check_group(python, names, paths=(), timeout=120):
    """Import names in as few processes as possible; isolate crashes/hangs to the module that caused them."""
    res = {"culprits": {}, "status": {}, "files": {}}
    todo = list(names)
    while todo:
        log, rc, timed_out = _run_batch(python, todo, paths, timeout)
        for k, v in log["culprits"].items():
            res["culprits"].setdefault(k, v)
        res["status"].update(log["status"])
        res["files"].update(log["files"])
        if log["finished"] and not timed_out:
            break
        bad = log["current"]
        if bad is None:
            # died before the first BEGIN or between modules; blame the first unfinished one
            pending = [n for n in todo if n not in log["status"]]
            bad = pending[0] if pending else None
        if bad is None:
            break
        res["status"][bad] = "timeout" if timed_out else "crash (exit code %s)" % rc
        idx = todo.index(bad) if bad in todo else -1
        todo = [n for n in todo[idx + 1:] if n not in res["status"]] if idx >= 0 else \
            [n for n in todo if n not in res["status"]]
    return res


def check_modules(python, names, paths=(), jobs=4, timeout=120):
    """Group by top-level package and run groups in parallel."""
    groups = {}
    for n in names:
        groups.setdefault(n.split(".")[0], []).append(n)
    out = {"culprits": {}, "status": {}, "files": {}}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        for res in ex.map(lambda g: check_group(python, g, paths, timeout), list(groups.values())):
            for k, v in res["culprits"].items():
                out["culprits"].setdefault(k, v)
            out["status"].update(res["status"])
            out["files"].update(res["files"])
    return out

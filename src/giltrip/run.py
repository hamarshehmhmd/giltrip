"""`giltrip run -- CMD`: watch a real command (your tests, your app) and report every extension module
it loaded that re-enables the GIL, with the line of your code that imported it."""
import glob
import json
import os
import subprocess
import tempfile

from . import engine, interp, report

SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shim")


def read_records(outdir):
    procs = {}
    for fn in sorted(glob.glob(os.path.join(outdir, "*.jsonl"))):
        with open(fn, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                procs.setdefault(rec.get("pid"), []).append(rec)
    return procs


def _root_for(module, file):
    """Directory that makes `module` importable from `file` (site-packages, or a source tree)."""
    d = os.path.dirname(os.path.abspath(file))
    for _ in range(module.count(".")):
        d = os.path.dirname(d)
    return d


def _user_frame(chain, skip_dirs):
    skip = [os.path.realpath(s) for s in skip_dirs if s]
    for fr in reversed(chain or []):
        fn = fr.rsplit(":", 1)[0]
        rp = os.path.realpath(fn)
        if not any(rp.startswith(s + os.sep) for s in skip):
            return fr
    return None


def _display(frame):
    """Show a frame relative to the current directory when it is inside it."""
    if not frame:
        return frame
    fn, _, line = frame.rpartition(":")
    try:
        rel = os.path.relpath(fn)
    except ValueError:  # other drive on Windows
        return frame
    return frame if rel.startswith("..") else "%s:%s" % (rel, line)


def analyse(procs, python_paths=None, jobs=4, timeout=120):
    """Return per-interpreter results: direct culprits plus verified ones loaded after the GIL was already on."""
    by_exe = {}
    for pid, recs in procs.items():
        start = next((r for r in recs if r["kind"] == "start"), {})
        exe = start.get("executable") or "python"
        slot = by_exe.setdefault(exe, {"pids": [], "exts": {}, "forced": set(), "argv": {}})
        slot["pids"].append(pid)
        if start.get("gil") and start.get("python_gil_env") == "1":
            slot["forced"].add(pid)
        end = next((r for r in recs if r["kind"] == "end"), None)
        if end:
            slot["argv"][pid] = end.get("argv")
        for r in recs:
            if r["kind"] != "ext":
                continue
            e = slot["exts"].setdefault(r["module"], {"file": r["file"], "chain": r["chain"], "direct": False,
                                                      "needs_check": False, "pids": set()})
            e["pids"].add(pid)
            if r.get("gil_before") is False and r.get("gil_after") is True:
                e["direct"] = True
                e["chain"] = r["chain"]
            elif r.get("gil_before"):
                e["needs_check"] = True
    out = []
    for exe, slot in by_exe.items():
        roots = sorted({_root_for(m, e["file"]) for m, e in slot["exts"].items()})
        try:
            info = interp.inventory(exe, also=roots)
        except interp.GiltripError:
            info = {"executable": exe, "version": "?", "modules": [], "dists": {}, "site_dirs": []}
        todo = [m for m, e in slot["exts"].items() if e["needs_check"] and not e["direct"]]
        paths = sorted({_root_for(m, slot["exts"][m]["file"]) for m in todo})
        checked = engine.check_modules(exe, todo, paths=paths, jobs=jobs, timeout=timeout) if todo else \
            {"culprits": {}, "status": {}, "files": {}}
        culprit_mods = {m for m, e in slot["exts"].items() if e["direct"]} | \
            {m for m in checked["culprits"] if m in slot["exts"]}
        skip = list(info.get("base_site_dirs", info.get("site_dirs", []))) + [info.get("stdlib"), os.path.dirname(engine.PROBE)]
        extra = {}
        for m in culprit_mods:
            e = slot["exts"][m]
            own = os.path.join(_root_for(m, e["file"]), m.split(".")[0])  # the culprit's own package
            extra[m] = {"first": e["direct"], "imported_at": _display(_user_frame(e["chain"], skip + [own])),
                        "chain": e["chain"], "processes": len(e["pids"])}
        result = {"culprits": {m: None for m in culprit_mods},
                  "status": {m: "ok" for m in slot["exts"]},
                  "files": {m: e["file"] for m, e in slot["exts"].items()}}
        result["status"].update({m: s for m, s in checked["status"].items() if s not in ("ok",)})
        out.append((info, slot, result, extra))
    return out


def run(cmd, jobs=4, timeout=120, keep_gil=True):
    outdir = tempfile.mkdtemp(prefix="giltrip-")
    env = interp.child_env({"GILTRIP_OUT": outdir}, keep_gil=keep_gil)
    env["PYTHONPATH"] = SHIM + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    if "PYTHONDONTWRITEBYTECODE" in os.environ:
        env["PYTHONDONTWRITEBYTECODE"] = os.environ["PYTHONDONTWRITEBYTECODE"]
    try:
        rc = subprocess.call(cmd, env=env)
    except OSError as e:
        raise interp.GiltripError("could not run %s: %s" % (cmd[0], e))
    except KeyboardInterrupt:
        rc = 130
    procs = read_records(outdir)
    return rc, procs, analyse(procs, jobs=jobs, timeout=timeout), outdir


def reports(analysed, allow=()):
    reps = []
    for info, slot, result, extra in analysed:
        rep = report.build(info, sorted(slot["exts"]), result, allow=allow, extra=extra)
        rep["processes"] = len(slot["pids"])
        rep["gil_forced_on"] = bool(slot["forced"])
        rep["free_threaded"] = info.get("free_threaded")
        reps.append(rep)
    return reps

"""Turn raw check results into a report: who re-enables the GIL, from which distribution, how it got there."""
import fnmatch
import os
import re


def norm(name):
    return re.sub(r"[-_.]+", "-", (name or "").lower())


def source_of(dist):
    """Where an installed distribution's binaries came from, as far as the metadata says."""
    if not dist:
        return "not from an installed distribution"
    url = dist.get("direct_url") or ""
    tags = dist.get("tags") or []
    if url.startswith("file:"):
        return "local install (%s)" % url
    if not tags:
        return "unknown (no WHEEL metadata)"
    plats = {t.rsplit("-", 1)[-1] for t in tags}
    if any(p.startswith(("manylinux", "musllinux", "macosx", "win", "android", "ios")) for p in plats):
        return "wheel " + tags[0]
    if any(re.fullmatch(r"linux_\w+", p) for p in plats):
        return "built from source on this machine (" + tags[0] + ")"
    return "wheel " + tags[0]


def is_allowed(module, dist_name, allow):
    for pat in allow or ():
        if dist_name and norm(pat) == norm(dist_name):
            return True
        if fnmatch.fnmatchcase(module, pat) or module == pat or module.startswith(pat + "."):
            return True
    return False


def repro(info, module, dist):
    ver = ".".join(info["version"].split(".")[:2]) + "t"
    code = '"import sys, %s; assert not sys._is_gil_enabled()"' % module
    if dist:
        return "uv run --no-project --python %s --with %s==%s python -c %s" % (
            ver, dist["name"], dist["version"], code)
    return "python%s -c %s" % (ver, code)


def module_index(info):
    by_mod = {m["module"]: m for m in info.get("modules", [])}
    by_file = {os.path.realpath(m["file"]): m for m in info.get("modules", [])}
    return by_mod, by_file


def build(info, checked, result, allow=(), extra=None):
    """info: interpreter inventory, checked: names we imported, result: engine.check_modules() output."""
    by_mod, by_file = module_index(info)
    dists = info.get("dists", {})
    culprits, allowed = [], []
    for mod, trig in sorted(result["culprits"].items()):
        f = result["files"].get(mod) or (by_mod.get(mod) or {}).get("file")
        entry = by_mod.get(mod) or (by_file.get(os.path.realpath(f)) if f else None) or {}
        dname = entry.get("dist")
        d = dists.get(dname) if dname else None
        c = {
            "module": mod,
            "file": f,
            "dist": dname,
            "version": d["version"] if d else None,
            "source": source_of(d),
            "triggered_by": trig if trig and trig != mod else None,
            "repro": repro(info, mod, d),
        }
        if extra and mod in extra:
            c.update(extra[mod])
        (allowed if is_allowed(mod, dname, allow) else culprits).append(c)
    status = result["status"]
    errors = [{"module": m, "error": s} for m, s in sorted(status.items())
              if s not in ("ok", "skip") and m in checked]
    ok = [m for m in checked if status.get(m) == "ok" and m not in result["culprits"]]
    py = {k: info[k] for k in ("executable", "version", "version_full", "abi_tag", "free_threaded",
                               "python_gil_env", "platform") if k in info}
    return {
        "python": py,
        "checked": len(checked),
        "culprits": culprits,
        "allowed": allowed,
        "errors": errors,
        "ok": ok,
        "skipped": sorted(m for m in checked if status.get(m) == "skip"),
    }


def by_dist(culprits):
    groups = {}
    for c in culprits:
        groups.setdefault((c["dist"] or "(no distribution)", c["version"] or ""), []).append(c)
    return sorted(groups.items(), key=lambda kv: norm(kv[0][0]))


def pick_repro(items):
    """The culprit to show a repro for: one your code imported, else the shallowest public one."""
    def key(c):
        last = c["module"].rsplit(".", 1)[-1]
        return (not c.get("imported_at"), c["module"].count("."), last.startswith("_"), c["module"])
    return sorted(items, key=key)[0]

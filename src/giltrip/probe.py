"""giltrip probe: runs *inside the interpreter being checked*. Standard library only.

Modes (argv[1]):
  inventory [--path DIR ...]   print JSON describing the interpreter and every extension module
  import                       read {"names": [...], "paths": [...]} on stdin and import them one by one.
                               Run with -v: CPython then prints
                               "# loading module 'X', which requires the GIL" for every extension
                               module that has not declared Py_mod_gil = Py_MOD_GIL_NOT_USED.
                               Progress markers go to stderr so their order matches those lines.
"""
import json
import os
import sys

MARK = "@@giltrip@@"


def _site_dirs(extra, also=()):
    import site
    import sysconfig

    dirs = []
    defaults = [] if extra else [sysconfig.get_paths().get("platlib"), sysconfig.get_paths().get("purelib")]
    for d in list(extra) + defaults + list(also):
        if d and os.path.isdir(d) and os.path.realpath(d) not in [os.path.realpath(x) for x in dirs]:
            dirs.append(d)
    if not extra:
        try:
            for d in site.getsitepackages() + [site.getusersitepackages()]:
                if os.path.isdir(d) and os.path.realpath(d) not in [os.path.realpath(x) for x in dirs]:
                    dirs.append(d)
        except Exception:
            pass
    return dirs


def _module_name(rel, suffixes):
    base = None
    for suf in sorted(suffixes, key=len, reverse=True):
        if rel.endswith(suf):
            base = rel[: -len(suf)]
            break
    if base is None:
        return None
    parts = base.replace(os.sep, "/").split("/")
    if not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def _dist_index(dirs):
    import importlib.metadata as md

    index = {}
    dists = {}
    for dist in md.distributions(path=dirs):
        try:
            name = dist.metadata["Name"]
        except Exception:
            continue
        if not name or name in dists:
            continue
        info = {"name": name, "version": dist.version, "tags": [], "installer": None, "direct_url": None}
        try:
            wheel = dist.read_text("WHEEL") or ""
            info["tags"] = [ln.split(":", 1)[1].strip() for ln in wheel.splitlines() if ln.startswith("Tag:")]
            info["installer"] = (dist.read_text("INSTALLER") or "").strip() or None
            du = dist.read_text("direct_url.json")
            if du:
                info["direct_url"] = json.loads(du).get("url")
        except Exception:
            pass
        dists[name] = info
        for f in dist.files or []:
            try:
                index[os.path.realpath(str(dist.locate_file(f)))] = name
            except Exception:
                pass
    return index, dists


def inventory(extra, also=()):
    import importlib.machinery as m
    import sysconfig

    dirs = _site_dirs(extra, also)
    suffixes = list(m.EXTENSION_SUFFIXES)
    index, dists = _dist_index(dirs)
    mods = {}
    for root in dirs:
        for dp, dn, fn in os.walk(root):
            dn[:] = sorted(d for d in dn if d != "__pycache__" and not d.endswith((".dist-info", ".egg-info")))
            for f in sorted(fn):
                if not any(f.endswith(s) for s in suffixes):
                    continue
                full = os.path.join(dp, f)
                name = _module_name(os.path.relpath(full, root), suffixes)
                if not name or name in mods:
                    continue
                mods[name] = {"module": name, "file": full, "dist": index.get(os.path.realpath(full))}
    gil = getattr(sys, "_is_gil_enabled", None)
    out = {
        "executable": sys.executable,
        "version": sys.version.split()[0],
        "version_full": sys.version,
        "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "gil_enabled_at_start": bool(gil()) if gil else True,
        "python_gil_env": os.environ.get("PYTHON_GIL"),
        "abi_tag": "cp%d%d%s" % (sys.version_info[0], sys.version_info[1],
                                 "t" if sysconfig.get_config_var("Py_GIL_DISABLED") else ""),
        "platform": sys.platform,
        "site_dirs": dirs,
        "base_site_dirs": _site_dirs([]),
        "stdlib": sysconfig.get_paths().get("stdlib"),
        "modules": sorted(mods.values(), key=lambda x: x["module"]),
        "dists": dists,
    }
    json.dump(out, sys.stdout)


def do_import(names):
    import importlib

    err = sys.stderr
    for name in names:
        err.write("%s BEGIN %s\n" % (MARK, name))
        err.flush()
        try:
            importlib.import_module(name)
        except BaseException as e:  # noqa: BLE001 - report everything, keep going
            msg = ("%s: %s" % (type(e).__name__, e)).replace("\n", " ")[:300]
            err.write("%s ERR %s %s\n" % (MARK, name, msg))
        else:
            err.write("%s OK %s\n" % (MARK, name))
        err.flush()
    files = {}
    for k, v in list(sys.modules.items()):
        f = getattr(v, "__file__", None)
        if f and not f.endswith((".py", ".pyc")):
            files[k] = f
    gil = getattr(sys, "_is_gil_enabled", None)
    err.write("%s FILES %s\n" % (MARK, json.dumps(files)))
    err.write("%s END gil=%s\n" % (MARK, int(bool(gil())) if gil else 1))
    err.flush()


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: probe.py inventory|import")
    if argv[1] == "inventory":
        extra, also = [], []
        it = iter(argv[2:])
        for a in it:
            if a == "--path":
                extra.append(next(it))
            elif a == "--also":
                also.append(next(it))
        inventory(extra, also)
    elif argv[1] == "import":
        req = json.load(sys.stdin)
        for p in reversed(req.get("paths", [])):
            if p not in sys.path:
                sys.path.insert(0, p)
        do_import(req["names"])
    else:
        sys.exit("unknown mode %r" % argv[1])


if __name__ == "__main__":
    main(sys.argv)

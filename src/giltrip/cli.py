"""giltrip command line."""
import argparse
import fnmatch
import os
import sys
import time

from . import __version__, config, engine, interp, pypi, render, report, run as runmod

EPILOG = """exit codes: 0 nothing re-enables the GIL, 1 something does (scan/check, or run --fail),
2 usage or environment error. `run` otherwise passes through the command's own exit code."""


def _common(p):
    p.add_argument("--allow", action="append", default=[], metavar="NAME",
                   help="distribution or module (glob) that may keep the GIL on; repeatable. "
                        "Also read from [tool.giltrip] allow in pyproject.toml")
    p.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    p.add_argument("--online", action="store_true", help="ask PyPI whether newer releases ship free-threaded wheels")
    p.add_argument("-j", "--jobs", type=int, default=min(8, os.cpu_count() or 2))
    p.add_argument("--timeout", type=float, default=120, help="seconds per import batch (default 120)")
    p.add_argument("-v", "--verbose", action="store_true", help="also list failed imports, clean modules, import chains")
    p.add_argument("--no-color", action="store_true")


def parser():
    ap = argparse.ArgumentParser(prog="giltrip", epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description="Find every extension module that turns the GIL back on in "
                                             "free-threaded Python, which package it came from, and why.")
    ap.add_argument("--version", action="version", version="giltrip " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("scan", help="import every extension module in an environment and list the ones that need the GIL")
    s.add_argument("--python", metavar="PY", help="interpreter, venv dir, or version like 3.14t "
                                                  "(default: $VIRTUAL_ENV, ./.venv, then this Python)")
    s.add_argument("--path", action="append", default=[], metavar="DIR", help="scan DIR instead of site-packages")
    s.add_argument("--only", action="append", default=[], metavar="DIST", help="only modules from this distribution")
    s.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip modules matching GLOB")
    s.add_argument("--exit-zero", action="store_true", help="exit 0 even if something re-enables the GIL")
    _common(s)

    c = sub.add_parser("check", help="import specific modules (yours, too) and list every GIL-needing extension they load")
    c.add_argument("modules", nargs="+")
    c.add_argument("--python", metavar="PY")
    c.add_argument("--path", action="append", default=[], metavar="DIR", help="extra sys.path entry")
    c.add_argument("--exit-zero", action="store_true")
    _common(c)

    r = sub.add_parser("run", help="run a command (tests, app) and report what re-enabled the GIL, with the line that imported it")
    r.add_argument("--fail", action="store_true", help="exit 1 if something re-enabled the GIL and the command passed")
    r.add_argument("--report", metavar="FILE", help="also write the JSON report to FILE")
    r.add_argument("command", nargs=argparse.REMAINDER)
    _common(r)
    return ap


def _allow(args):
    return list(args.allow) + config.load().get("allow", [])


def _online(rep, info):
    seen = {}
    for c in rep["culprits"] + rep["allowed"]:
        if c["dist"]:
            if c["dist"] not in seen:
                seen[c["dist"]] = pypi.latest(c["dist"], info.get("abi_tag", ""))
            c.update(seen[c["dist"]])


def _emit(rep, args, mode):
    color = (not args.no_color and args.format == "text" and sys.stdout.isatty()
             and "NO_COLOR" not in os.environ)
    if args.format == "json":
        render.as_json(rep)
    elif args.format == "markdown":
        sys.stdout.write(render.markdown(rep, mode))
    else:
        render.text(rep, color=color, verbose=args.verbose, mode=mode)
    render.github(rep, mode)


def cmd_scan(args):
    info = interp.resolve(args.python, args.path)
    excl = list(args.exclude) + config.load().get("exclude", [])
    only = {report.norm(d) for d in args.only}
    names = []
    for m in info["modules"]:
        if any(fnmatch.fnmatchcase(m["module"], g) for g in excl):
            continue
        if only and report.norm(m["dist"]) not in only:
            continue
        names.append(m["module"])
    if only and not names:
        raise interp.GiltripError("no extension modules found for %s in %s" % (", ".join(args.only),
                                                                              ", ".join(info["site_dirs"])))
    t0 = time.time()
    res = engine.check_modules(info["executable"], names, paths=args.path, jobs=args.jobs, timeout=args.timeout)
    rep = report.build(info, names, res, allow=_allow(args))
    rep["seconds"] = round(time.time() - t0, 2)
    rep["site_dirs"] = info["site_dirs"]
    if args.online:
        _online(rep, info)
    _emit(rep, args, "scan")
    return 0 if (args.exit_zero or not rep["culprits"]) else 1


def cmd_check(args):
    info = interp.resolve(args.python)
    res = engine.check_modules(info["executable"], args.modules, paths=args.path, jobs=1, timeout=args.timeout)
    bad = [m for m in args.modules if res["status"].get(m, "").startswith(("error", "crash", "timeout"))]
    rep = report.build(info, list(args.modules), res, allow=_allow(args))
    rep["checked"] = len(res["files"]) or len(args.modules)
    if args.online:
        _online(rep, info)
    _emit(rep, args, "check")
    if bad and args.format == "text":
        for m in bad:
            sys.stderr.write("giltrip: could not import %s: %s\n" % (m, res["status"][m]))
    if bad:
        return 2
    return 0 if (args.exit_zero or not rep["culprits"]) else 1


def cmd_run(args):
    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise interp.GiltripError("usage: giltrip run [options] -- COMMAND [ARGS...]")
    rc, procs, analysed, _ = runmod.run(cmd, jobs=args.jobs, timeout=args.timeout)
    reps = runmod.reports(analysed, allow=_allow(args))
    if not reps:
        sys.stderr.write("giltrip: no Python 3.13+ process started by %s was seen (is it a Python command?)\n" % cmd[0])
        return rc
    for rep in reps:
        if args.online:
            _online(rep, rep["python"])
        if args.format == "text":
            sys.stderr.flush()
            sys.stdout.write("\n")
        _emit(rep, args, "run")
    if args.report:
        import json
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(reps if len(reps) > 1 else reps[0], fh, indent=2, default=list)
    found = any(r["culprits"] for r in reps)
    if rc == 0 and args.fail and found:
        return 1
    return rc


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.cmd:
        parser().print_help()
        return 2
    try:
        return {"scan": cmd_scan, "check": cmd_check, "run": cmd_run}[args.cmd](args)
    except interp.GiltripError as e:
        sys.stderr.write("giltrip: %s\n" % e)
        return 2

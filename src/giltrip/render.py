"""Text, JSON and Markdown output."""
import json
import os
import sys

from .report import by_dist, pick_repro


def _c(code, s, color):
    return "\033[%sm%s\033[0m" % (code, s) if color else s


def _pypi_note(c, abi=""):
    if c.get("pypi_error"):
        return "PyPI check failed: " + c["pypi_error"]
    if c.get("latest") is None:
        return None
    n = c.get("latest_ft_wheels") or 0
    wheels = "%s %s wheel%s" % (n or "no", abi or "free-threaded", "" if n == 1 else "s")
    if c.get("version") and c["latest"] != c["version"]:
        return "newer release on PyPI: %s (you have %s, it has %s): try upgrading" % (c["latest"], c["version"], wheels)
    return "already the latest release on PyPI (%s), so upgrading won't fix it: tell upstream" % wheels


def text(rep, color=False, verbose=False, mode="scan", out=None):
    w = (out or sys.stdout).write
    py = rep["python"]
    w("%s %s  Python %s %s (%s)\n" % (_c("1", "giltrip", color), mode, py.get("version"), py.get("abi_tag", ""),
                                      py.get("executable")))
    if mode == "run":
        w("watched %d Python process%s, %d extension modules loaded\n" % (
            rep.get("processes", 0), "" if rep.get("processes") == 1 else "es", rep["checked"]))
        if rep.get("free_threaded") is False:
            w(_c("33", "note: this interpreter is a regular (GIL) build, so there is nothing to re-enable.\n", color))
        if rep.get("gil_forced_on"):
            w(_c("33", "note: PYTHON_GIL=1 was set, so the GIL was on from the start.\n", color))
    else:
        w("checked %d extension modules\n" % rep["checked"])
    if py.get("python_gil_env") and mode == "scan":
        w("note: PYTHON_GIL=%s is set in your shell; giltrip ignored it for the check\n" % py["python_gil_env"])
    w("\n")
    cul = rep["culprits"]
    if not cul:
        w(_c("32", "OK: nothing here turns the GIL back on.\n", color))
    else:
        n_d = len(by_dist(cul))
        w(_c("31;1", "GIL re-enabled by %d module%s from %d distribution%s:\n" % (
            len(cul), "" if len(cul) == 1 else "s", n_d, "" if n_d == 1 else "s"), color))
        for (dname, ver), items in by_dist(cul):
            w("\n  %s %s  %s\n" % (_c("1", dname, color), ver, _c("2", items[0]["source"], color)))
            for c in items:
                extra = []
                if c.get("first"):
                    extra.append("turned it on")
                if c.get("triggered_by"):
                    extra.append("loaded by import %s" % c["triggered_by"])
                w("    %s%s\n" % (_c("31", c["module"], color), ("  (" + ", ".join(extra) + ")") if extra else ""))
                if c.get("imported_at"):
                    w("      imported at %s\n" % c["imported_at"])
                if verbose and c.get("chain"):
                    for fr in c["chain"]:
                        w(_c("2", "        %s\n" % fr, color))
            note = _pypi_note(items[0], py.get("abi_tag", ""))
            if note:
                w("    %s\n" % _c("36", note, color))
            w(_c("2", "    repro: %s\n" % pick_repro(items)["repro"], color))
    if rep["allowed"]:
        w("\nallowed by config (%d): %s\n" % (len(rep["allowed"]), ", ".join(c["module"] for c in rep["allowed"])))
    if rep["errors"]:
        w("\ncould not import %d module%s%s\n" % (len(rep["errors"]), "" if len(rep["errors"]) == 1 else "s",
                                                 ":" if verbose else " (use -v to list)"))
        if verbose:
            for e in rep["errors"]:
                w("  %s: %s\n" % (e["module"], e["error"]))
    if verbose and rep["ok"]:
        w("\nkeep the GIL off (%d): %s\n" % (len(rep["ok"]), ", ".join(rep["ok"])))
    if cul:
        w("\nEach of these needs Py_mod_gil = Py_MOD_GIL_NOT_USED (C API), freethreading_compatible=True (Cython),\n"
          "py::mod_gil_not_used() (pybind11), FREE_THREADED (nanobind) or #[pymodule(gil_used = false)] (PyO3)\n"
          "upstream.\n"
          "Until then: PYTHON_GIL=0 keeps the GIL off at your own risk.\n")


def as_json(rep, out=None):
    (out or sys.stdout).write(json.dumps(rep, indent=2, default=list) + "\n")


def markdown(rep, mode="scan"):
    py = rep["python"]
    lines = ["### giltrip %s: Python %s %s" % (mode, py.get("version"), py.get("abi_tag", "")), ""]
    cul = rep["culprits"]
    if not cul:
        lines.append("No extension module re-enables the GIL (%d checked)." % rep["checked"])
    else:
        lines += ["**%d module%s re-enable the GIL** (%d checked):" % (
            len(cul), "" if len(cul) == 1 else "s", rep["checked"]), "",
            "| distribution | version | module | how it got in | source |", "|---|---|---|---|---|"]
        for c in cul:
            how = c.get("imported_at") or (("via `import %s`" % c["triggered_by"]) if c.get("triggered_by") else "")
            lines.append("| %s | %s | `%s` | %s | %s |" % (c["dist"] or "-", c["version"] or "-", c["module"],
                                                       how.replace("|", "\\|"), c["source"]))
        lines += ["", "Reproduce one: `%s`" % pick_repro(cul)["repro"]]
    if rep["allowed"]:
        lines += ["", "Allowed by config: " + ", ".join("`%s`" % c["module"] for c in rep["allowed"])]
    return "\n".join(lines) + "\n"


def github(rep, mode="scan"):
    """Step summary + annotations when running in GitHub Actions."""
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as fh:
            fh.write(markdown(rep, mode) + "\n")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for c in rep["culprits"]:
            sys.stdout.write("::warning title=GIL re-enabled::%s (%s %s) re-enables the GIL on %s\n" % (
                c["module"], c["dist"] or "?", c["version"] or "", rep["python"].get("abi_tag", "")))

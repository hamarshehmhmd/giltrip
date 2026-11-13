# giltrip run-mode shim. `giltrip run` puts this directory on PYTHONPATH so every Python process the
# command starts loads it as sitecustomize. It records each extension module that gets loaded, the GIL
# state before and after, and the import chain, then hands over to any real sitecustomize.
import os
import sys


def _giltrip_install():
    out = os.environ.get("GILTRIP_OUT")
    gil = getattr(sys, "_is_gil_enabled", None)
    if not out or gil is None or not os.path.isdir(out):
        return
    import importlib.machinery as machinery
    import json
    import threading
    import traceback

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(out, "%d.jsonl" % os.getpid())
    lock = threading.Lock()

    def emit(rec):
        rec["pid"] = os.getpid()
        with lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    emit({"kind": "start", "executable": sys.executable, "gil": bool(gil()),
          "python_gil_env": os.environ.get("PYTHON_GIL")})

    def chain():
        keep = []
        for f in traceback.extract_stack()[:-2]:
            fn = f.filename
            if fn.startswith("<frozen") or os.path.dirname(os.path.abspath(fn)) == here:
                continue
            keep.append("%s:%d" % (fn, f.lineno))
        return keep[-12:]

    before = {}

    class Loader(machinery.ExtensionFileLoader):
        def create_module(self, spec):
            before[spec.name] = bool(gil())
            return super().create_module(spec)

        def exec_module(self, module):
            try:
                return super().exec_module(module)
            finally:
                emit({"kind": "ext", "module": self.name, "file": self.path,
                      "gil_before": before.pop(self.name, None), "gil_after": bool(gil()), "chain": chain()})

    class Finder:
        @classmethod
        def find_spec(cls, name, path=None, target=None):
            for f in list(sys.meta_path):
                if f is cls:
                    continue
                fs = getattr(f, "find_spec", None)
                if fs is None:
                    continue
                spec = fs(name, path, target)
                if spec is not None:
                    if type(spec.loader) is machinery.ExtensionFileLoader:
                        spec.loader = Loader(spec.loader.name, spec.loader.path)
                    return spec
            return None

    sys.meta_path.insert(0, Finder)

    import atexit

    def _end():
        try:
            emit({"kind": "end", "gil": bool(gil()), "argv": list(sys.argv)})
        except Exception:
            pass

    atexit.register(_end)


def _giltrip_chain():
    """Run the sitecustomize we are shadowing, if there is one, and take our directory off sys.path."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
    import importlib.machinery
    import importlib.util

    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", sys.path)
    if spec and spec.origin and os.path.dirname(os.path.abspath(spec.origin)) != here:
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sitecustomize"] = mod
        spec.loader.exec_module(mod)


try:
    _giltrip_install()
except Exception as _e:  # never break the user's program
    sys.stderr.write("giltrip: shim disabled: %s\n" % _e)
_giltrip_chain()

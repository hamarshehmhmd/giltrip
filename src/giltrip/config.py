"""[tool.giltrip] in pyproject.toml: allow = [...], exclude = [...]."""
import os

try:
    import tomllib
except ImportError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


def load(start="."):
    path = os.path.join(start, "pyproject.toml")
    if not os.path.exists(path) or tomllib is None:
        return {}
    with open(path, "rb") as fh:
        try:
            data = tomllib.load(fh)
        except Exception:
            return {}
    cfg = data.get("tool", {}).get("giltrip", {}) or {}
    return {k: list(cfg.get(k, [])) for k in ("allow", "exclude") if isinstance(cfg.get(k, []), list)}

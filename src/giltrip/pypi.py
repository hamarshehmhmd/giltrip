"""Optional: ask PyPI whether a newer release of a culprit ships free-threaded wheels."""
import json
import re
import urllib.request

UA = {"User-Agent": "giltrip (+https://github.com/hamarshehmhmd/giltrip)"}


def ft_wheels(filenames, abi_tag):
    """Wheel files whose ABI tag is usable on this free-threaded interpreter (cp314t, or abi3t from 3.15)."""
    out = []
    minor = int(re.match(r"cp\d(\d+)t", abi_tag).group(1)) if re.match(r"cp\d(\d+)t", abi_tag) else 0
    for fn in filenames:
        if not fn.endswith(".whl"):
            continue
        parts = fn[:-4].split("-")
        if len(parts) < 5:
            continue
        abis = parts[-2].split(".")
        pys = parts[-3].split(".")
        if abi_tag in abis:
            out.append(fn)
        elif "abi3t" in abis and minor >= 15 and any(
                re.fullmatch(r"cp3(\d+)", p) and int(p[3:]) <= minor for p in pys):
            out.append(fn)
    return out


def latest(name, abi_tag, timeout=10, opener=None):
    url = "https://pypi.org/pypi/%s/json" % name
    try:
        req = urllib.request.Request(url, headers=UA)
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001 - network is optional
        return {"pypi_error": str(e)[:200]}
    files = [u.get("filename", "") for u in data.get("urls", [])]
    return {"latest": data.get("info", {}).get("version"), "latest_ft_wheels": len(ft_wheels(files, abi_tag))}

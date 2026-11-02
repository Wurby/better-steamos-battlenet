"""Valve binary VDF (shortcuts.vdf) codec, plus the one text-VDF edit we need.

Binary types: 0x00 map, 0x01 string, 0x02 uint32, 0x08 end-of-map. Dicts keep order.
"""
import re
import struct

from .errors import BnetSteamError


class VdfError(BnetSteamError):
    pass


def loads(data):
    try:
        obj, _ = _read_map(data, 0)
    except (IndexError, ValueError, struct.error) as e:
        raise VdfError(f"not a valid binary VDF file ({e})") from e
    return obj


def _read_map(data, pos):
    out = {}
    while True:
        t = data[pos]
        pos += 1
        if t == 0x08:
            return out, pos
        end = data.index(b"\x00", pos)
        key = data[pos:end].decode("utf-8")
        pos = end + 1
        if t == 0x00:
            out[key], pos = _read_map(data, pos)
        elif t == 0x01:
            end = data.index(b"\x00", pos)
            out[key] = data[pos:end].decode("utf-8")
            pos = end + 1
        elif t == 0x02:
            out[key] = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        else:
            raise ValueError(f"unknown type {t:#x} at byte {pos - 1}")


def dumps(obj):
    return _write_map(obj) + b"\x08"


def _write_map(obj):
    out = bytearray()
    for key, val in obj.items():
        k = key.encode("utf-8") + b"\x00"
        if isinstance(val, dict):
            out += b"\x00" + k + _write_map(val) + b"\x08"
        elif isinstance(val, int):
            out += b"\x02" + k + struct.pack("<I", val)
        else:
            out += b"\x01" + k + val.encode("utf-8") + b"\x00"
    return bytes(out)


def roundtrips(raw):
    """True if parsing then re-serialising reproduces `raw` byte for byte.
    We only edit files for which this holds, so a codec gap can't corrupt them."""
    try:
        return dumps(loads(raw)) == raw
    except VdfError:
        return False


def braces_balanced(text):
    return text.count("{") == text.count("}") and "{" in text


def _close_brace(text, open_):
    """Index of the brace closing the one at `open_`, or -1."""
    depth = 0
    for i in range(open_, len(text)):
        depth += text[i] == "{"
        depth -= text[i] == "}"
        if depth == 0:
            return i
    return -1


def set_compat_tool(text, appid, tool):
    """Add a CompatToolMapping entry for `appid` to config.vdf text (idempotent)."""
    start = text.find('"CompatToolMapping"')
    if start < 0:
        raise VdfError("config.vdf has no CompatToolMapping section (has Steam run yet?)")
    open_ = text.index("{", start)
    i = _close_brace(text, open_)
    if i < 0:
        raise VdfError("config.vdf CompatToolMapping section is not closed")
    if re.search(rf'"{appid}"\s*\{{', text[open_:i]):
        return text
    line0 = text.rfind("\n", 0, start) + 1
    e = text[line0:start] + "\t"
    block = (f'{e}"{appid}"\n{e}{{\n{e}\t"name"\t\t"{tool}"\n'
             f'{e}\t"config"\t\t""\n{e}\t"priority"\t\t"250"\n{e}}}\n')
    pos = text.index("\n", open_) + 1
    return text[:pos] + block + text[pos:]


def remove_compat_tool(text, appid):
    """Drop the CompatToolMapping entry for `appid` (no-op if there is none)."""
    start = text.find('"CompatToolMapping"')
    if start < 0:
        return text
    open_ = text.index("{", start)
    end = _close_brace(text, open_)
    m = re.search(rf'^[ \t]*"{appid}"\s*\{{', text[open_:end], re.M) if end > 0 else None
    if not m:
        return text
    entry_end = _close_brace(text, open_ + m.end() - 1)
    if entry_end < 0:
        return text
    nl = text.find("\n", entry_end)
    return text[:open_ + m.start()] + text[len(text) if nl < 0 else nl + 1:]

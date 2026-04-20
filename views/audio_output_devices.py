# -*- coding: utf-8 -*-
import os
import subprocess

import constants


def _decode_subprocess_output(raw: bytes) -> str:
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le")
    return raw.decode("utf-8")


def _parse_mpv_help_line(line: str):
    s = line.strip()
    il = s.lower().find("wasapi/")
    if il < 0:
        return None
    rest = s[il:]
    paren_idx = rest.find("(")
    if paren_idx < 0:
        token = rest.strip().strip('`"\' \t')
        if token.lower().startswith("wasapi/"):
            dev_id = token[len("wasapi/") :].strip()
            if dev_id:
                return {"id": dev_id, "name": dev_id}
        return None

    ao_segment = rest[:paren_idx].strip().strip('`"\' \t')
    if not ao_segment.lower().startswith("wasapi/"):
        return None
    dev_id = ao_segment[len("wasapi/") :].strip()
    if not dev_id:
        return None

    desc_tail = rest[paren_idx:]
    if desc_tail.endswith(")"):
        name = desc_tail[1:-1].strip()
    else:
        name = desc_tail[1:].strip()
    return {"id": dev_id, "name": name or dev_id}


def _dedupe(items):
    seen = set()
    out = []
    for x in items:
        k = (x["id"], x["name"])
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def enumerate_playback_devices():
    if os.name != "nt":
        return []

    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.run(
        [
            constants.MPV_PATH,
            "--no-config",
            "--audio-device=help",
            "--no-video",
            "--force-window=no",
        ],
        capture_output=True,
        timeout=30,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )

    raw = (proc.stdout or b"") + (proc.stderr or b"")
    text = _decode_subprocess_output(raw)
    out = []
    for line in text.splitlines():
        item = _parse_mpv_help_line(line)
        if item:
            out.append(item)

    if not out:
        return []
    return _dedupe(out)


def getDeviceList():
    return enumerate_playback_devices()

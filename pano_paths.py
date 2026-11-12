# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Panorama Viewer NG - panorama path resolution
                                 A QGIS plugin
        copyright            : (C) 2026 by Theo Gatsios
        email                : theogat@protonmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 3 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/

Turns whatever is stored in the layer's link field into something the plugin
can read:

  local path      /data/pano.jpg, C:\\pano.jpg, \\\\server\\share\\pano.jpg
  file:// URL     file:///data/pano.jpg
  smb:// URL      smb://server/share/dir/pano.jpg  -> the mounted share
  http(s) URL     downloaded with requests
"""

import os
import sys
import shutil
import subprocess
from urllib.parse import urlparse, unquote
from urllib.request import url2pathname


class PanoSource:
    """kind: 'local' (path), 'web' (url), or 'error' (message for the user)"""

    def __init__(self, kind, value, message=None):
        self.kind = kind
        self.value = value
        self.message = message


def _cifs_mounts():
    """(server, share) -> mount point, from /proc/mounts (fstab / mount.cifs)."""
    mounts = {}
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[2] in ("cifs", "smb3", "smbfs"):
                    src = parts[0].replace("\\040", " ").strip("/")
                    target = parts[1].replace("\\040", " ")
                    if "/" in src:
                        server, share = src.split("/", 1)
                        mounts[(server.lower(), share.lower())] = target
    except OSError:
        pass
    return mounts


def _smb_candidates(server, share, rest):
    """Local paths where smb://server/share/rest may be reachable."""
    cands = []
    rest_parts = [p for p in rest.split("/") if p]

    if sys.platform == "win32":
        cands.append("\\\\" + "\\".join([server, share] + rest_parts))
        return cands

    if sys.platform == "darwin":
        cands.append(os.path.join("/Volumes", share, *rest_parts))
        return cands

    # Linux: GVFS (file manager mounts) -- server/share are lower-cased there
    uid = os.getuid() if hasattr(os, "getuid") else None
    if uid is not None:
        gvfs = "/run/user/{}/gvfs".format(uid)
        for srv, shr in ((server.lower(), share.lower()), (server, share)):
            cands.append(os.path.join(
                gvfs, "smb-share:server={},share={}".format(srv, shr), *rest_parts))
    # Linux: cifs mounts (fstab / mount -t cifs)
    mp = _cifs_mounts().get((server.lower(), share.lower()))
    if mp:
        cands.append(os.path.join(mp, *rest_parts))
    return cands


def _try_gio_mount(server, share):
    """Ask GVFS to mount the share (works when credentials are saved in the
    keyring or the share allows guest access). Never prompts or hangs."""
    gio = shutil.which("gio")
    if not gio:
        return False
    try:
        subprocess.run(
            [gio, "mount", "smb://{}/{}".format(server, share)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def _base_dirs(base_dirs):
    """Folders a relative link may be relative to: the caller's list, then the
    QGIS project folder (de-duplicated, existing folders only)."""
    dirs = [d for d in (base_dirs or []) if d]
    try:
        from qgis.core import QgsProject
        proj = QgsProject.instance()
        dirs.append(proj.homePath())
        dirs.append(os.path.dirname(proj.fileName()))
    except Exception:
        pass
    out = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _resolve_relative(link, base_dirs):
    """Absolute path of a relative link (./pano.jpg, ../x/pano.jpg, pano.jpg)
    if it exists under one of the base folders, else None."""
    rel = link.replace("\\", os.sep).replace("/", os.sep)
    for base in _base_dirs(base_dirs):
        cand = os.path.normpath(os.path.join(base, rel))
        if os.path.isfile(cand):
            return cand
    return None


def resolve(link, base_dirs=None):
    """Return a PanoSource for the value stored in the link field.

    Relative paths (./panoramas/a.jpg) are looked up next to the QGIS project
    file, then in any extra folders given in base_dirs (e.g. the layer's folder).
    """
    if link is None:
        return PanoSource("error", None, "The selected feature has no panorama link.")
    link = str(link).strip()
    if not link or link.upper() == "NULL":
        return PanoSource("error", None, "The selected feature has no panorama link.")

    # plain local / UNC path
    if os.path.isfile(link):
        return PanoSource("local", link)

    parsed = urlparse(link)
    scheme = parsed.scheme.lower()

    # relative path: ./pano.jpg, ../pano.jpg, panoramas/pano.jpg
    if not scheme or len(scheme) == 1:
        found = _resolve_relative(link, base_dirs)
        if found:
            return PanoSource("local", found)
        if not scheme:
            tried = _base_dirs(base_dirs)
            hint = (" Looked relative to: {}.".format(", ".join(tried)) if tried else
                    " Save the QGIS project so relative paths have a base folder.")
            return PanoSource("error", None, "File not found: {}.{}".format(link, hint))

    # Windows drive letters parse as a one-letter scheme ("C:\...")
    if len(scheme) == 1:
        return PanoSource("error", None, "File not found: {}".format(link))

    if scheme in ("http", "https"):
        return PanoSource("web", link)

    if scheme == "file":
        path = url2pathname(unquote(parsed.path))
        if parsed.netloc and parsed.netloc != "localhost":
            path = "\\\\" + parsed.netloc + path.replace("/", "\\") if sys.platform == "win32" \
                else "//" + parsed.netloc + path
        if os.path.isfile(path):
            return PanoSource("local", path)
        return PanoSource("error", None, "File not found: {}".format(path))

    if scheme in ("smb", "cifs"):
        server = parsed.hostname or parsed.netloc
        parts = [p for p in unquote(parsed.path).split("/") if p]
        if not server or not parts:
            return PanoSource("error", None, "Incomplete network path: {}".format(link))
        share, rest = parts[0], "/".join(parts[1:])

        for attempt in (1, 2):
            for cand in _smb_candidates(server, share, rest):
                if os.path.isfile(cand):
                    return PanoSource("local", cand)
            # not reachable: on Linux try mounting the share once, then re-check
            if attempt == 1 and sys.platform.startswith("linux") and _try_gio_mount(server, share):
                continue
            break

        share_url = "smb://{}/{}".format(server, share)
        if any(os.path.isdir(root) for root in _smb_candidates(server, share, "")):
            return PanoSource("error", None,
                              "File not found on {}: /{}".format(share_url, rest))
        if sys.platform == "win32":
            hint = "Check that you can open \\\\{}\\{} in File Explorer.".format(server, share)
        elif sys.platform == "darwin":
            hint = "Connect to {} in Finder (Go → Connect to Server) and try again.".format(share_url)
        else:
            hint = ("Open {} in your file manager once (so it is mounted and the "
                    "password saved), then try again.").format(share_url)
        return PanoSource("error", None,
                          "Network share not reachable: {}. {}".format(share_url, hint))

    return PanoSource("error", None,
                      "Unsupported link type '{}://'. Use a file path, file://, smb:// "
                      "or http(s):// link.".format(scheme))

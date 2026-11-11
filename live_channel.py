# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Panorama Viewer NG - live channel for the external browser window
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

A browser tab opened on index_live.html stays connected to the plugin's
local web server:

  plugin -> browser   GET  /live/events   Server-Sent Events ("load this
                                          viewer page", "rotate to angle")
  browser -> plugin   POST /live/view     view changes (yaw / pitch / zoom)
                                          so the map's direction sector follows
  browser -> plugin   GET  /live/annotate/check   is a sign selected / set up?
                      POST /live/annotate         frozen frame + box to save
                      (both wait for QGIS' answer and return {"ok", "message"})

Only the most recent command matters, so the hub keeps just that one plus a
version counter; a (re)connecting tab immediately gets the current panorama.
"""

import json
import threading

from qgis.PyQt.QtCore import QObject, pyqtSignal


class LiveHub:
    """Thread-safe command channel shared by all browser tabs.

    Sticky commands ("show this panorama", messages) are remembered, so a tab
    that (re)connects gets the current state at once. One-off commands
    ("rotate", "annotate") only reach tabs connected at that moment."""

    KEEPALIVE = 15  # seconds between keep-alive comments on idle streams

    def __init__(self):
        self._cond = threading.Condition()
        self._sticky_v = 0
        self._sticky = None
        self._oneoff_v = 0
        self._oneoff = None
        self._clients = 0
        self._closed = False

    @property
    def clients(self):
        with self._cond:
            return self._clients

    def publish(self, message, sticky=None):
        """Send a command (dict) to the connected tabs. By default only
        "load" and "message" commands are sticky."""
        if sticky is None:
            sticky = message.get("type") in ("load", "message")
        with self._cond:
            if sticky:
                self._sticky_v += 1
                self._sticky = json.dumps(message)
            else:
                self._oneoff_v += 1
                self._oneoff = json.dumps(message)
            self._cond.notify_all()

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def stream(self, write):
        """Run one SSE connection until the client leaves or the hub closes.
        `write(bytes)` raises OSError when the browser tab is gone."""
        with self._cond:
            self._clients += 1
            seen_sticky = 0
            seen_oneoff = self._oneoff_v   # don't replay old one-off commands
        try:
            while True:
                with self._cond:
                    if (self._sticky_v == seen_sticky and self._oneoff_v == seen_oneoff
                            and not self._closed):
                        self._cond.wait(self.KEEPALIVE)
                    if self._closed:
                        return
                    out = []
                    if self._sticky_v != seen_sticky and self._sticky is not None:
                        seen_sticky = self._sticky_v
                        out.append(self._sticky)
                    if self._oneoff_v != seen_oneoff and self._oneoff is not None:
                        seen_oneoff = self._oneoff_v
                        out.append(self._oneoff)
                if out:
                    for m in out:
                        write("data: {}\n\n".format(m).encode("utf-8"))
                else:
                    write(b": keep-alive\n\n")
        except OSError:
            return
        finally:
            with self._cond:
                self._clients -= 1


class PendingRequest:
    """A browser request that needs an answer from QGIS' main thread."""

    def __init__(self, kind, payload=None):
        self.kind = kind          # "check" or "annotate"
        self.payload = payload    # parsed JSON for "annotate"
        self.ok = False
        self.message = "QGIS did not answer"
        self._event = threading.Event()

    def done(self, ok, message):
        self.ok, self.message = bool(ok), str(message)
        self._event.set()

    def wait(self, timeout):
        return self._event.wait(timeout)


class LiveBridge(QObject):
    """Carries messages from the server thread into QGIS' main thread.
    Connect the signals to methods of a QObject living in the main thread
    so Qt queues the calls across threads."""

    view_changed = pyqtSignal(str)
    request = pyqtSignal(object)   # PendingRequest


MAX_ANNOTATION_BYTES = 40 * 1024 * 1024
ANSWER_TIMEOUT = 30  # seconds the browser waits for QGIS


def _ask_qgis(handler, bridge, req):
    """Hand a request to the main thread, wait for its answer, reply as JSON."""
    bridge.request.emit(req)
    if not req.wait(ANSWER_TIMEOUT):
        req.message = "QGIS did not answer in time"
    body = json.dumps({"ok": req.ok, "message": req.message}).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_live_request(handler, hub, bridge):
    """Route /live/* requests. Returns True when the request was handled."""
    path = handler.path.split("?", 1)[0]

    if handler.command == "GET" and path == "/live/annotate/check":
        _ask_qgis(handler, bridge, PendingRequest("check"))
        return True

    if handler.command == "POST" and path == "/live/annotate":
        length = int(handler.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_ANNOTATION_BYTES:
            handler.send_error(413)
            return True
        try:
            payload = json.loads(handler.rfile.read(length).decode("utf-8"))
        except ValueError:
            handler.send_error(400)
            return True
        _ask_qgis(handler, bridge, PendingRequest("annotate", payload))
        return True

    if handler.command == "GET" and path == "/live/events":
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()

        def write(data):
            handler.wfile.write(data)
            handler.wfile.flush()

        hub.stream(write)
        handler.close_connection = True
        return True

    if handler.command == "POST" and path == "/live/view":
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(min(length, 4096)).decode("utf-8", "replace")
        handler.send_response(204)
        handler.end_headers()
        if body.startswith("{"):
            bridge.view_changed.emit(body)
        return True

    return False

/*
 * CanvasPano - a small equirectangular panorama viewer that needs no WebGL.
 * Part of Panorama Viewer NG, (C) 2026 Theo Gatsios, GPL-3.0-or-later.
 *
 * Renders by reprojecting every screen pixel onto the source image on a plain
 * 2D canvas, so it works in browsers where WebGL / GPU access is disabled.
 * Angles follow Pannellum's convention: yaw 0 = image centre, positive to the
 * right; pitch positive up; hfov = horizontal field of view in degrees.
 */
(function (global) {
    "use strict";

    var DEG = Math.PI / 180;
    var MAX_SRC_WIDTH = 8192;       // larger panoramas are downscaled once on load
    var MAX_IDLE_PIXELS = 1600000;  // cap for the full-quality frame
    var DRAG_SCALE = 0.5;           // resolution factor while interacting
    var IDLE_DELAY = 140;           // ms without input before the full-quality frame

    function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

    function CanvasPano(container, opts) {
        this.container = container;
        this.yaw = opts.yaw || 0;
        this.pitch = opts.pitch || 0;
        this.hfov = opts.hfov || 90;
        this.minHfov = opts.minHfov || 30;
        this.maxHfov = opts.maxHfov || 120;
        this.onChange = opts.onChange || null;

        this.canvas = document.createElement("canvas");
        this.ctx = this.canvas.getContext("2d");
        container.appendChild(this.canvas);

        this.msg = document.createElement("div");
        this.msg.id = "pano_msg";
        this.msg.textContent = "Loading panorama\u2026";
        container.appendChild(this.msg);

        this.src = null;           // Uint32Array of source pixels
        this.srcW = 0;
        this.srcH = 0;
        this.pending = false;
        this.quality = "idle";
        this.idleTimer = null;
        this.changed = false;

        this._bindEvents();
        this._load(opts.panorama);
        var self = this;
        global.addEventListener("resize", function () { self.render("idle"); });
    }

    CanvasPano.prototype._load = function (url) {
        var self = this;
        var img = new Image();
        img.onload = function () {
            var w = img.naturalWidth, h = img.naturalHeight;
            if (w > MAX_SRC_WIDTH) { h = Math.round(h * MAX_SRC_WIDTH / w); w = MAX_SRC_WIDTH; }
            var c = document.createElement("canvas");
            c.width = w; c.height = h;
            var cx = c.getContext("2d", { willReadFrequently: true });
            cx.drawImage(img, 0, 0, w, h);
            self.src = new Uint32Array(cx.getImageData(0, 0, w, h).data.buffer);
            self.srcW = w; self.srcH = h;
            self.msg.style.display = "none";
            self.render("idle");
        };
        img.onerror = function () {
            self.msg.textContent = "Could not load the panorama image.";
        };
        img.src = url + (url.indexOf("?") < 0 ? "?" : "&") + "t=" + Date.now();
    };

    /* ---------------------------------------------------------------- API */
    CanvasPano.prototype.getYaw = function () { return this.yaw; };
    CanvasPano.prototype.getPitch = function () { return this.pitch; };
    CanvasPano.prototype.getHfov = function () { return this.hfov; };
    CanvasPano.prototype.setYaw = function (y) { this.yaw = this._wrap(y); this.render("idle"); };
    CanvasPano.prototype.setPitch = function (p) { this.pitch = clamp(p, -89, 89); this.render("idle"); };
    CanvasPano.prototype.setHfov = function (f) { this.hfov = clamp(f, this.minHfov, this.maxHfov); this.render("idle"); };

    CanvasPano.prototype._wrap = function (y) {
        y = ((y + 180) % 360 + 360) % 360 - 180;
        return y;
    };

    /* ---------------------------------------------------------------- input */
    CanvasPano.prototype._interacted = function () {
        this.changed = true;
        this.render("drag");
        var self = this;
        clearTimeout(this.idleTimer);
        this.idleTimer = setTimeout(function () { self.render("idle"); }, IDLE_DELAY);
    };

    CanvasPano.prototype._bindEvents = function () {
        var self = this, el = this.container;
        var dragging = false, lastX = 0, lastY = 0, pinchDist = 0;

        function start(x, y) {
            dragging = true; lastX = x; lastY = y;
            el.classList.add("dragging");
        }
        function move(x, y) {
            if (!dragging) return;
            var w = el.clientWidth || 1, h = el.clientHeight || 1;
            var vfov = self._vfov(w, h);
            self.yaw = self._wrap(self.yaw - (x - lastX) * self.hfov / w);
            self.pitch = clamp(self.pitch + (y - lastY) * vfov / h, -89, 89);
            lastX = x; lastY = y;
            self._interacted();
        }
        function end() {
            if (!dragging) return;
            dragging = false;
            el.classList.remove("dragging");
        }

        el.addEventListener("mousedown", function (e) { if (e.button === 0) { start(e.clientX, e.clientY); e.preventDefault(); } });
        global.addEventListener("mousemove", function (e) { move(e.clientX, e.clientY); });
        global.addEventListener("mouseup", end);

        el.addEventListener("wheel", function (e) {
            e.preventDefault();
            var f = e.deltaY > 0 ? 1.08 : 1 / 1.08;
            self.hfov = clamp(self.hfov * f, self.minHfov, self.maxHfov);
            self._interacted();
        }, { passive: false });

        el.addEventListener("touchstart", function (e) {
            if (e.touches.length === 1) start(e.touches[0].clientX, e.touches[0].clientY);
            else if (e.touches.length === 2) {
                dragging = false;
                pinchDist = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                                       e.touches[0].clientY - e.touches[1].clientY);
            }
        }, { passive: true });
        el.addEventListener("touchmove", function (e) {
            e.preventDefault();
            if (e.touches.length === 1) move(e.touches[0].clientX, e.touches[0].clientY);
            else if (e.touches.length === 2 && pinchDist) {
                var d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                                   e.touches[0].clientY - e.touches[1].clientY);
                self.hfov = clamp(self.hfov * pinchDist / d, self.minHfov, self.maxHfov);
                pinchDist = d;
                self._interacted();
            }
        }, { passive: false });
        el.addEventListener("touchend", function () { end(); pinchDist = 0; });

        global.addEventListener("keydown", function (e) {
            var step = self.hfov / 12, used = true;
            if (e.key === "ArrowLeft") self.yaw = self._wrap(self.yaw - step);
            else if (e.key === "ArrowRight") self.yaw = self._wrap(self.yaw + step);
            else if (e.key === "ArrowUp") self.pitch = clamp(self.pitch + step, -89, 89);
            else if (e.key === "ArrowDown") self.pitch = clamp(self.pitch - step, -89, 89);
            else if (e.key === "+" || e.key === "=") self.hfov = clamp(self.hfov / 1.1, self.minHfov, self.maxHfov);
            else if (e.key === "-") self.hfov = clamp(self.hfov * 1.1, self.minHfov, self.maxHfov);
            else used = false;
            if (used) { e.preventDefault(); self._interacted(); }
        });
    };

    CanvasPano.prototype._vfov = function (w, h) {
        return 2 * Math.atan(Math.tan(this.hfov * DEG / 2) * h / w) / DEG;
    };

    /* ---------------------------------------------------------------- render */
    CanvasPano.prototype.render = function (quality) {
        this.quality = quality;
        if (this.pending) return;
        this.pending = true;
        var self = this;
        requestAnimationFrame(function () {
            self.pending = false;
            self._draw(self.quality);
            if (self.changed && self.onChange) {
                self.changed = false;
                self.onChange({ yaw: self.yaw, pitch: self.pitch, hfov: self.hfov });
            }
        });
    };

    CanvasPano.prototype._draw = function (quality) {
        if (!this.src) return;
        var cw = this.container.clientWidth || 1, ch = this.container.clientHeight || 1;
        var dpr = global.devicePixelRatio || 1;
        var scale = quality === "drag" ? DRAG_SCALE : dpr;
        var W = Math.max(1, Math.round(cw * scale)), H = Math.max(1, Math.round(ch * scale));
        if (quality !== "drag" && W * H > MAX_IDLE_PIXELS) {
            var k = Math.sqrt(MAX_IDLE_PIXELS / (W * H));
            W = Math.round(W * k); H = Math.round(H * k);
        }
        if (this.canvas.width !== W || this.canvas.height !== H) {
            this.canvas.width = W; this.canvas.height = H;
            this.out = this.ctx.createImageData(W, H);
            this.out32 = new Uint32Array(this.out.data.buffer);
        }
        CanvasPano.project(this.src, this.srcW, this.srcH, this.out32, W, H,
                           this.yaw, this.pitch, this.hfov, quality !== "drag");
        this.ctx.putImageData(this.out, 0, 0);
    };


    /* Reproject an equirectangular image (Uint32 RGBA pixels, sw x sh) into a
       W x H perspective view. Used by the viewer and by the browser-window
       sign annotation, so both produce exactly the same picture. */
    CanvasPano.project = function (src, sw, sh, out32, W, H, yaw, pitch, hfov, bilinear) {

        var tx = Math.tan(hfov * DEG / 2), ty = tx * H / W;
        var cp = Math.cos(pitch * DEG), sp = Math.sin(pitch * DEG);
        var cy = Math.cos(yaw * DEG), sy = Math.sin(yaw * DEG);
        var uK = sw / (2 * Math.PI), vK = sh / Math.PI;

        // per-column camera x, per-row camera y
        var xs = new Float32Array(W), ys = new Float32Array(H);
        for (var i = 0; i < W; i++) xs[i] = (2 * (i + 0.5) / W - 1) * tx;
        for (var j = 0; j < H; j++) ys[j] = (1 - 2 * (j + 0.5) / H) * ty;

        var p = 0;
        for (j = 0; j < H; j++) {
            var y = ys[j];
            // pitch rotation (about x), applied to (x, y, 1)
            var y1 = y * cp + sp;
            var z1 = -y * sp + cp;
            for (i = 0; i < W; i++, p++) {
                var x = xs[i];
                // yaw rotation (about y)
                var x2 = x * cy + z1 * sy;
                var z2 = -x * sy + z1 * cy;
                var lon = Math.atan2(x2, z2);
                var lat = Math.atan2(y1, Math.sqrt(x2 * x2 + z2 * z2));
                var u = (lon + Math.PI) * uK - 0.5;
                var v = (Math.PI / 2 - lat) * vK - 0.5;
                if (!bilinear) {
                    var ui = (u + 0.5) | 0, vi = (v + 0.5) | 0;
                    if (ui >= sw) ui -= sw; else if (ui < 0) ui += sw;
                    if (vi >= sh) vi = sh - 1; else if (vi < 0) vi = 0;
                    out32[p] = src[vi * sw + ui];
                } else {
                    var u0 = Math.floor(u), v0 = Math.floor(v);
                    var fu = u - u0, fv = v - v0;
                    var u1 = u0 + 1, v1 = v0 + 1;
                    if (u0 < 0) u0 += sw; if (u1 >= sw) u1 -= sw; if (u0 >= sw) u0 -= sw;
                    if (v0 < 0) v0 = 0; if (v1 >= sh) v1 = sh - 1; if (v0 >= sh) v0 = sh - 1;
                    var a = src[v0 * sw + u0], b = src[v0 * sw + u1];
                    var c = src[v1 * sw + u0], d = src[v1 * sw + u1];
                    var w00 = (1 - fu) * (1 - fv), w10 = fu * (1 - fv), w01 = (1 - fu) * fv, w11 = fu * fv;
                    var r = (a & 255) * w00 + (b & 255) * w10 + (c & 255) * w01 + (d & 255) * w11;
                    var g = ((a >>> 8) & 255) * w00 + ((b >>> 8) & 255) * w10 + ((c >>> 8) & 255) * w01 + ((d >>> 8) & 255) * w11;
                    var bl = ((a >>> 16) & 255) * w00 + ((b >>> 16) & 255) * w10 + ((c >>> 16) & 255) * w01 + ((d >>> 16) & 255) * w11;
                    out32[p] = 0xFF000000 | ((bl + 0.5) << 16) | ((g + 0.5) << 8) | (r + 0.5);
                }
            }
        }
    };

    /* Decode an image URL into the pixel buffer project() expects. */
    CanvasPano.loadSource = function (url, callback) {
        var img = new Image();
        img.onload = function () {
            var w = img.naturalWidth, h = img.naturalHeight;
            if (w > MAX_SRC_WIDTH) { h = Math.round(h * MAX_SRC_WIDTH / w); w = MAX_SRC_WIDTH; }
            var c = document.createElement("canvas");
            c.width = w; c.height = h;
            var cx = c.getContext("2d", { willReadFrequently: true });
            cx.drawImage(img, 0, 0, w, h);
            callback(null, { src: new Uint32Array(cx.getImageData(0, 0, w, h).data.buffer), w: w, h: h });
        };
        img.onerror = function () { callback(new Error("Could not load " + url)); };
        img.src = url;
    };

    global.CanvasPano = CanvasPano;
})(window);

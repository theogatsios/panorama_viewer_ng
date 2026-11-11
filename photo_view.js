/*
 * PhotoView - a flat (non-panoramic) image viewer with zoom and pan.
 * Part of Panorama Viewer NG, (C) 2026 Theo Gatsios, GPL-3.0-or-later.
 *
 * getView() reports which part of the original image is on screen, so the
 * annotation tools can capture that region at the photo's full resolution.
 */
(function (global) {
    "use strict";

    function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

    function PhotoView(container, opts) {
        this.container = container;
        this.canvas = document.createElement("canvas");
        this.ctx = this.canvas.getContext("2d");
        container.appendChild(this.canvas);

        this.msg = document.createElement("div");
        this.msg.id = "pano_msg";
        this.msg.textContent = "Loading image\u2026";
        container.appendChild(this.msg);

        this.img = null;
        this.scale = 1;      // screen px per image px
        this.x0 = 0;         // image coordinate at the left edge of the view
        this.y0 = 0;         // image coordinate at the top edge of the view
        this.fitted = true;  // follow window resizes while not zoomed by the user
        this.pending = false;

        this._bindEvents();
        this._load(opts.src);
        var self = this;
        global.addEventListener("resize", function () {
            if (self.fitted) self.fit(); else self.render();
        });
    }

    PhotoView.prototype._load = function (url) {
        var self = this, img = new Image();
        img.onload = function () {
            self.img = img;
            self.msg.style.display = "none";
            self.fit();
        };
        img.onerror = function () { self.msg.textContent = "Could not load the image."; };
        img.src = url + (url.indexOf("?") < 0 ? "?" : "&") + "t=" + Date.now();
    };

    PhotoView.prototype._size = function () {
        return [this.container.clientWidth || 1, this.container.clientHeight || 1];
    };

    PhotoView.prototype._fitScale = function () {
        var s = this._size();
        return Math.min(s[0] / this.img.naturalWidth, s[1] / this.img.naturalHeight);
    };

    /* Whole image visible, centred. */
    PhotoView.prototype.fit = function () {
        if (!this.img) return;
        var s = this._size();
        this.scale = this._fitScale();
        this.x0 = (this.img.naturalWidth - s[0] / this.scale) / 2;
        this.y0 = (this.img.naturalHeight - s[1] / this.scale) / 2;
        this.fitted = true;
        this.render();
    };

    /* Zoom by factor f, keeping screen point (sx, sy) fixed. */
    PhotoView.prototype.zoomAt = function (f, sx, sy) {
        if (!this.img) return;
        var min = this._fitScale() * 0.5, max = 16;
        var ns = clamp(this.scale * f, min, max);
        var ix = this.x0 + sx / this.scale, iy = this.y0 + sy / this.scale;
        this.scale = ns;
        this.x0 = ix - sx / ns;
        this.y0 = iy - sy / ns;
        this.fitted = false;
        this.render();
    };

    /* Which part of the original image is on screen (for annotation). */
    PhotoView.prototype.getView = function () {
        var s = this._size();
        return {
            type: "photo",
            x0: this.x0, y0: this.y0, scale: this.scale,
            width: s[0], height: s[1],
            imageWidth: this.img ? this.img.naturalWidth : 0,
            imageHeight: this.img ? this.img.naturalHeight : 0
        };
    };

    PhotoView.prototype.render = function () {
        if (this.pending) return;
        this.pending = true;
        var self = this;
        requestAnimationFrame(function () { self.pending = false; self._draw(); });
    };

    PhotoView.prototype._draw = function () {
        var s = this._size(), dpr = global.devicePixelRatio || 1;
        var W = Math.round(s[0] * dpr), H = Math.round(s[1] * dpr);
        if (this.canvas.width !== W || this.canvas.height !== H) {
            this.canvas.width = W; this.canvas.height = H;
        }
        var c = this.ctx;
        c.setTransform(1, 0, 0, 1, 0, 0);
        c.fillStyle = "#111";
        c.fillRect(0, 0, W, H);
        if (!this.img) return;
        c.imageSmoothingEnabled = this.scale < 2;   // crisp pixels when zoomed far in
        c.imageSmoothingQuality = "high";
        var k = this.scale * dpr;
        c.setTransform(k, 0, 0, k, -this.x0 * k, -this.y0 * k);
        c.drawImage(this.img, 0, 0);
    };

    PhotoView.prototype._bindEvents = function () {
        var self = this, el = this.container;
        var dragging = false, lx = 0, ly = 0, pinch = 0;

        function local(e) {
            var r = el.getBoundingClientRect();
            return [e.clientX - r.left, e.clientY - r.top];
        }

        el.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return;
            dragging = true; lx = e.clientX; ly = e.clientY;
            el.classList.add("dragging"); e.preventDefault();
        });
        global.addEventListener("mousemove", function (e) {
            if (!dragging) return;
            self.x0 -= (e.clientX - lx) / self.scale;
            self.y0 -= (e.clientY - ly) / self.scale;
            lx = e.clientX; ly = e.clientY;
            self.fitted = false;
            self.render();
        });
        global.addEventListener("mouseup", function () {
            dragging = false; el.classList.remove("dragging");
        });
        el.addEventListener("wheel", function (e) {
            e.preventDefault();
            var p = local(e);
            self.zoomAt(e.deltaY > 0 ? 1 / 1.15 : 1.15, p[0], p[1]);
        }, { passive: false });
        el.addEventListener("dblclick", function (e) {
            var p = local(e);
            if (self.fitted) self.zoomAt(1 / self.scale, p[0], p[1]);   // 1:1 at the cursor
            else self.fit();
        });

        el.addEventListener("touchstart", function (e) {
            if (e.touches.length === 1) {
                dragging = true; lx = e.touches[0].clientX; ly = e.touches[0].clientY;
            } else if (e.touches.length === 2) {
                dragging = false;
                pinch = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                                   e.touches[0].clientY - e.touches[1].clientY);
            }
        }, { passive: true });
        el.addEventListener("touchmove", function (e) {
            e.preventDefault();
            if (e.touches.length === 1 && dragging) {
                self.x0 -= (e.touches[0].clientX - lx) / self.scale;
                self.y0 -= (e.touches[0].clientY - ly) / self.scale;
                lx = e.touches[0].clientX; ly = e.touches[0].clientY;
                self.fitted = false; self.render();
            } else if (e.touches.length === 2 && pinch) {
                var d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                                   e.touches[0].clientY - e.touches[1].clientY);
                var r = el.getBoundingClientRect();
                var cx = (e.touches[0].clientX + e.touches[1].clientX) / 2 - r.left;
                var cy = (e.touches[0].clientY + e.touches[1].clientY) / 2 - r.top;
                self.zoomAt(d / pinch, cx, cy);
                pinch = d;
            }
        }, { passive: false });
        el.addEventListener("touchend", function () { dragging = false; pinch = 0; });

        global.addEventListener("keydown", function (e) {
            var s = self._size();
            if (e.key === "+" || e.key === "=") self.zoomAt(1.25, s[0] / 2, s[1] / 2);
            else if (e.key === "-") self.zoomAt(0.8, s[0] / 2, s[1] / 2);
            else if (e.key === "0") self.fit();
            else return;
            e.preventDefault();
        });
    };

    /* Render the visible region of an image at its own resolution (image px),
       black where the view extends past the image. Used for annotation in the
       browser window; QGIS does the same from the original file. */
    PhotoView.captureRegion = function (img, view, maxSide) {
        var w = view.width / view.scale, h = view.height / view.scale;
        var k = Math.min(1, (maxSide || 8192) / Math.max(w, h));
        var c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(w * k));
        c.height = Math.max(1, Math.round(h * k));
        var cx = c.getContext("2d");
        cx.fillStyle = "#000";
        cx.fillRect(0, 0, c.width, c.height);
        cx.setTransform(k, 0, 0, k, -view.x0 * k, -view.y0 * k);
        cx.drawImage(img, 0, 0);
        return { canvas: c, pxPerImagePx: k };
    };

    global.PhotoView = PhotoView;
})(window);

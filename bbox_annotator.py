# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Panorama Viewer NG - annotation (bounding boxes on the panorama view)
                                 A QGIS plugin
        copyright            : (C) 2026 by Elpida Gkouvra (original module)
        copyright            : (C) 2026 by Theo Gatsios (integration into NG)
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
"""
"""
bbox_annotator.py

Χειροκίνητη σχεδίαση bounding boxes πάνω στο widget ενός panorama viewer
και αποθήκευση της εικόνας (base64) + annotations (JSON) σε πίνακα/layer του QGIS.

- Δεν εξαρτάται από την υλοποίηση του viewer: παγώνει ("freeze") το τρέχον
  frame του widget και σχεδιάζεις πάνω σε αυτό.
- Γράφει μέσω QgsVectorLayer, άρα δουλεύει ίδια σε PostGIS, GeoPackage, SpatiaLite.
- Συμβατό με QGIS 3.x (Qt5) και QGIS 4 (Qt6) μέσω qgis.PyQt.

Σύνδεση στο plugin:
    from .bbox_annotator import AnnotationController
    self.annot = AnnotationController(self.viewer_widget, layer_name="pinakides",
                                      fields={"annotated": "image_b64"}, iface=self.iface)
    self.annotate_action.triggered.connect(self.annot.start)

Χειρισμός στο overlay:
  Αριστερό drag      : σχεδίαση bbox (νέο drag αντικαθιστά το προηγούμενο)
  Δεξί κλικ σε bbox  : διαγραφή του bbox
  Backspace / Delete : αναίρεση τελευταίου bbox
  Enter              : αποθήκευση
  Esc                : ακύρωση
"""
import json

from qgis.PyQt.QtCore import (Qt, QRect, QPoint, QBuffer, QByteArray,
                              QIODevice, QDateTime, pyqtSignal)
from qgis.PyQt.QtGui import QPainter, QPen, QColor, QFont, QGuiApplication, QImage
from qgis.PyQt.QtWidgets import QWidget, QInputDialog
from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
                       QgsCoordinateTransform, QgsCoordinateReferenceSystem, Qgis)


# --------------------------------------------------------------------------- #
# Λήψη εικόνας από το widget του viewer
# --------------------------------------------------------------------------- #
def _looks_empty(img):
    """True αν η εικόνα είναι κενή ή μονόχρωμη (τυπικό μαύρο grab από WebGL)."""
    if img.isNull():
        return True
    small = img.scaled(16, 16)
    first = small.pixel(0, 0)
    return all(small.pixel(x, y) == first for x in range(16) for y in range(16))


def capture_widget(widget):
    """Επιστρέφει QImage με ό,τι δείχνει αυτή τη στιγμή το widget."""
    img = widget.grab().toImage()
    if not _looks_empty(img):
        return img
    # Fallback για QWebEngineView / QOpenGLWidget: screenshot της περιοχής οθόνης
    top_left = widget.mapToGlobal(QPoint(0, 0))
    screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
    geo = screen.geometry()
    pix = screen.grabWindow(0, top_left.x() - geo.x(), top_left.y() - geo.y(),
                            widget.width(), widget.height())
    return pix.toImage()


def qimage_to_b64(img, fmt="PNG", quality=-1):
    """Κωδικοποίηση QImage σε base64 string (PNG: lossless, JPG: μικρότερο)."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt, quality)
    buf.close()
    return bytes(ba.toBase64()).decode("ascii")


def b64_to_qimage(b64):
    """Αντίστροφο, χρήσιμο για έλεγχο / export dataset."""
    img = QImage()
    img.loadFromData(QByteArray.fromBase64(b64.encode("ascii")))
    return img


def render_boxes(image, anns, color=QColor(255, 0, 0), width=3, draw_labels=False):
    """Επιστρέφει αντίγραφο της εικόνας με τα περιγράμματα σχεδιασμένα πάνω της."""
    out = image.convertToFormat(QImage.Format.Format_RGB32)
    p = QPainter(out)
    p.setPen(QPen(color, width))
    p.setFont(QFont("Sans", 12, QFont.Weight.Bold))
    for a in anns:
        x, y, w, h = a["bbox"]
        p.drawRect(x, y, w, h)
        if draw_labels:
            p.drawText(x, max(14, y - 4), a["label"])
    p.end()
    return out


def crop_image(image, bbox, pad=0.15):
    """Crop γύρω από το bbox με περιθώριο `pad` (ποσοστό του μεγέθους του bbox)."""
    x, y, w, h = bbox
    dx, dy = int(w * pad), int(h * pad)
    r = QRect(x - dx, y - dy, w + 2 * dx, h + 2 * dy).intersected(image.rect())
    return image.copy(r)


def update_selected_feature(layer, values):
    """
    Γράφει τις τιμές `values` ({όνομα_πεδίου: τιμή}) στο ΜΟΝΑΔΙΚΟ επιλεγμένο
    feature του layer. Επιστρέφει (ok, μήνυμα).
    """
    sel = layer.selectedFeatureIds()
    if len(sel) != 1:
        return False, f"Select exactly one feature (currently selected: {len(sel)})"
    fid = sel[0]
    fields = layer.fields()
    attrs = {}
    for name, v in values.items():
        idx = fields.indexFromName(name)
        if idx < 0 or v is None:
            continue
        if isinstance(v, (dict, list)) and fields.field(idx).typeName().lower() not in ("json", "jsonb"):
            v = json.dumps(v, ensure_ascii=False)
        attrs[idx] = v
    if not attrs:
        return False, "None of the target fields exist in the layer"
    if layer.isEditable():
        ok = all(layer.changeAttributeValue(fid, i, v) for i, v in attrs.items())
        return ok, "" if ok else "Could not write to the edit buffer"
    ok = layer.dataProvider().changeAttributeValues({fid: attrs})
    if ok:
        layer.reload()
    return ok, "" if ok else layer.dataProvider().lastError()


# --------------------------------------------------------------------------- #
# Overlay σχεδίασης
# --------------------------------------------------------------------------- #
class BBoxOverlay(QWidget):
    finished = pyqtSignal(object, list)   # (QImage, [{"label", "bbox":[x,y,w,h]}])
    cancelled = pyqtSignal()

    MIN_SIZE = 5  # px, μικρότερα drags αγνοούνται

    def __init__(self, viewer_widget, labels, max_boxes=None, ask_label=True, frame=None):
        super().__init__(viewer_widget)
        self.viewer = viewer_widget
        self.labels = list(labels) or ["object"]
        self.max_boxes = max_boxes      # π.χ. 1: νέο bbox αντικαθιστά το προηγούμενο
        self.ask_label = ask_label      # False: χωρίς διάλογο, χρησιμοποιείται labels[0]
        # frame: a ready image to annotate (e.g. a photo region at full resolution);
        # otherwise the viewer widget is captured as shown on screen
        self.frame = frame if frame is not None and not frame.isNull() else capture_widget(viewer_widget)
        self.boxes = []          # [(QRect σε συντεταγμένες overlay, label)]
        self._start = None
        self._current = None

        self.setGeometry(viewer_widget.rect())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        viewer_widget.installEventFilter(self)
        self.show()
        self.raise_()
        self.setFocus()

    # --- ακολουθεί το μέγεθος του viewer --- #
    def eventFilter(self, obj, event):
        if obj is self.viewer and event.type() == event.Type.Resize:
            self.setGeometry(self.viewer.rect())
        return False

    # --- σχεδίαση --- #
    def paintEvent(self, _event):
        p = QPainter(self)
        p.drawImage(self.rect(), self.frame)
        p.setFont(QFont("Sans", 10, QFont.Weight.Bold))

        for rect, label in self.boxes:
            p.setPen(QPen(QColor(0, 230, 118), 2))
            p.drawRect(rect)
            tw = p.fontMetrics().horizontalAdvance(label) + 8
            tag = QRect(rect.left(), rect.top() - 18, tw, 18)
            p.fillRect(tag, QColor(0, 230, 118))
            p.setPen(QColor(0, 0, 0))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)

        if self._start and self._current:
            p.setPen(QPen(QColor(255, 214, 0), 2, Qt.PenStyle.DashLine))
            p.drawRect(QRect(self._start, self._current).normalized())

        bar = QRect(0, self.height() - 24, self.width(), 24)
        p.fillRect(bar, QColor(0, 0, 0, 160))
        p.setPen(QColor(255, 255, 255))
        p.drawText(bar, Qt.AlignmentFlag.AlignCenter,
                   f"boxes: {len(self.boxes)}   |   Drag: draw box   Right-click: delete   "
                   "Backspace: undo   Enter: save   Esc: cancel")
        p.end()

    # --- ποντίκι --- #
    def mousePressEvent(self, e):
        pos = e.position().toPoint() if hasattr(e, "position") else e.pos()
        if e.button() == Qt.MouseButton.LeftButton:
            self._start = self._current = pos
        elif e.button() == Qt.MouseButton.RightButton:
            for i in range(len(self.boxes) - 1, -1, -1):
                if self.boxes[i][0].contains(pos):
                    del self.boxes[i]
                    break
        self.update()

    def mouseMoveEvent(self, e):
        if self._start:
            self._current = e.position().toPoint() if hasattr(e, "position") else e.pos()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self._start:
            return
        rect = QRect(self._start, self._current).normalized().intersected(self.rect())
        self._start = self._current = None
        self.update()
        if rect.width() < self.MIN_SIZE or rect.height() < self.MIN_SIZE:
            return
        if not self.ask_label:
            if self.max_boxes and len(self.boxes) >= self.max_boxes:
                self.boxes.pop(0)
            self.boxes.append((rect, self.labels[0]))
            self.update()
            return
        default = self.labels.index(self._last_label) if getattr(self, "_last_label", None) in self.labels else 0
        label, ok = QInputDialog.getItem(self, "Object class", "Label:",
                                         self.labels, default, True)
        if ok and label.strip():
            label = label.strip()
            if label not in self.labels:
                self.labels.append(label)
            self._last_label = label
            if self.max_boxes and len(self.boxes) >= self.max_boxes:
                self.boxes.pop(0)
            self.boxes.append((rect, label))
        self.update()

    # --- πληκτρολόγιο --- #
    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
        elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and self.boxes:
            self.boxes.pop()
            self.update()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.boxes:
            self.finished.emit(self.frame, self._annotations_in_image_px())
            self.close()

    def _annotations_in_image_px(self):
        """Μετατροπή από συντεταγμένες overlay σε pixels εικόνας (λαμβάνει υπόψη HiDPI)."""
        sx = self.frame.width() / max(1, self.width())
        sy = self.frame.height() / max(1, self.height())
        W, H = self.frame.width(), self.frame.height()
        out = []
        for rect, label in self.boxes:
            x = max(0, min(W, round(rect.x() * sx)))
            y = max(0, min(H, round(rect.y() * sy)))
            w = max(1, min(W - x, round(rect.width() * sx)))
            h = max(1, min(H - y, round(rect.height() * sy)))
            out.append({"label": label, "bbox": [x, y, w, h]})  # COCO: [x, y, w, h]
        return out

    def closeEvent(self, e):
        self.viewer.removeEventFilter(self)
        super().closeEvent(e)


# --------------------------------------------------------------------------- #
# Αποθήκευση σε layer
# --------------------------------------------------------------------------- #
def _set_if_exists(feat, fields, name, value):
    idx = fields.indexFromName(name)
    if idx < 0 or value is None:
        return
    if isinstance(value, (dict, list)) and fields.field(idx).typeName().lower() not in ("json", "jsonb"):
        value = json.dumps(value, ensure_ascii=False)
    feat.setAttribute(idx, value)


def save_annotation(layer, image, doc, point=None, point_crs=None, extra=None,
                    img_format="PNG", quality=-1):
    """
    Γράφει μία εγγραφή στο layer. Πεδία που χρησιμοποιούνται αν υπάρχουν:
    image_b64, annotations, width, height, created_at, image_format
    και όποια κλειδιά του `extra` αντιστοιχούν σε ονόματα πεδίων (π.χ. panorama_id, yaw).
    Επιστρέφει (ok, μήνυμα σφάλματος).
    """
    fields = layer.fields()
    feat = QgsFeature(fields)
    _set_if_exists(feat, fields, "image_b64", qimage_to_b64(image, img_format, quality))
    _set_if_exists(feat, fields, "annotations", doc)
    _set_if_exists(feat, fields, "width", image.width())
    _set_if_exists(feat, fields, "height", image.height())
    _set_if_exists(feat, fields, "image_format", img_format.lower())
    _set_if_exists(feat, fields, "created_at", QDateTime.currentDateTimeUtc())
    for k, v in (extra or {}).items():
        _set_if_exists(feat, fields, k, v)

    if point is not None and layer.isSpatial():
        geom = QgsGeometry.fromPointXY(QgsPointXY(point))
        src = point_crs or QgsCoordinateReferenceSystem("EPSG:4326")
        if src != layer.crs():
            geom.transform(QgsCoordinateTransform(src, layer.crs(), QgsProject.instance()))
        feat.setGeometry(geom)

    if layer.isEditable():          # ο χρήστης κάνει commit μόνος του
        ok = layer.addFeature(feat)
        return ok, "" if ok else "Could not add the feature to the edit buffer"
    ok, _ = layer.dataProvider().addFeatures([feat])
    if ok:
        layer.triggerRepaint()
    return ok, "" if ok else layer.dataProvider().lastError()


# --------------------------------------------------------------------------- #
# Controller: αυτό συνδέεις στο plugin
# --------------------------------------------------------------------------- #
class AnnotationController:
    """
    Δύο τρόποι λειτουργίας (target):
      "selected": ενημερώνει το επιλεγμένο σημείο του `layer` (πινακίδες που
                  ψηφιοποίησες χειροκίνητα). Αυτό είναι το default.
      "insert"  : δημιουργεί νέα εγγραφή με γεωμετρία από το get_context()["point"].

    output: τι εικόνα αποθηκεύεται
      "annotated": ολόκληρο το frame με το περίγραμμα σχεδιασμένο πάνω του
      "crop"     : μόνο η περιοχή της πινακίδας (με περιθώριο crop_pad)
      "both"     : και τα δύο

    layer_name: όνομα του layer με τις πινακίδες, όπως φαίνεται στο πάνελ Layers.
                Αναζητείται σε κάθε start(), άρα δουλεύει και μετά από επαναφόρτωση.
                (Εναλλακτικά δίνεις απευθείας ένα QgsVectorLayer στο `layer`.)

    fields: ονόματα πεδίων του πίνακα (άλλαξέ τα ώστε να ταιριάζουν με τον
            πίνακα του μοντέλου σου). Όσα δεν υπάρχουν στον πίνακα αγνοούνται.

    get_context: callable -> dict, καλείται όταν παγώνει το frame, π.χ.
                 {"panorama_id": "...", "yaw": 12.3, "pitch": -4.0, "fov": 90}
                 (για target="insert" και {"point": QgsPointXY, "crs": ...})
    """

    DEFAULT_FIELDS = {
        "annotated": "image_b64",     # frame με περίγραμμα
        "crop": "crop_b64",           # crop της πινακίδας
        "bbox": "bbox",               # [x, y, w, h] σε pixels του frame
        "meta": "annotations",        # πλήρες JSON (bbox, διαστάσεις, όψη)
        "source": "source",           # "manual", για να ξεχωρίζουν από του μοντέλου
    }

    def __init__(self, viewer_widget, layer=None, labels=("object",), get_context=None,
                 layer_name=None,
                 target="selected", output="annotated", fields=None,
                 img_format="JPG", quality=90, crop_pad=0.15,
                 box_color=QColor(0, 255, 0), box_width=2,
                 ask_label=False, iface=None):
        self.viewer = viewer_widget
        self.layer = layer
        self.layer_name = layer_name    # αν δοθεί, το layer βρίσκεται με το όνομα σε κάθε start()
        self.labels = list(labels)
        self.get_context = get_context
        self.target = target
        self.output = output
        self.fields = {**self.DEFAULT_FIELDS, **(fields or {})}
        self.img_format = img_format
        self.quality = quality
        self.crop_pad = crop_pad
        self.box_color = box_color
        self.box_width = box_width
        self.ask_label = ask_label
        self.iface = iface
        self.overlay = None
        self._ctx = {}

    def _resolve_layer(self):
        """Βρίσκει το layer με το όνομα, ώστε να δουλεύει και μετά από επαναφόρτωση."""
        if self.layer_name:
            found = QgsProject.instance().mapLayersByName(self.layer_name)
            self.layer = found[0] if found else None
        if self.layer is None:
            self._notify(False, f"Layer '{self.layer_name}' not found")
            return False
        return True

    def start(self, frame=None):
        if self.overlay is not None:
            return
        if not self._resolve_layer():
            return
        if self.target == "selected" and self.layer.selectedFeatureCount() != 1:
            self._notify(False, "Select one feature on the map first")
            return
        self._ctx = dict(self.get_context() or {}) if self.get_context else {}
        self.overlay = BBoxOverlay(self.viewer, self.labels,
                                   max_boxes=1 if self.target == "selected" else None,
                                   ask_label=self.ask_label, frame=frame)
        self.overlay.finished.connect(self._on_finished)
        self.overlay.cancelled.connect(self._clear)

    def _b64(self, img):
        return qimage_to_b64(img, self.img_format, self.quality)

    def _on_finished(self, frame, anns):
        ok, msg = self.save(frame, anns, self._ctx)
        self._notify(ok, msg)
        self._clear()

    def save(self, frame, anns, context=None):
        """Write frame + boxes to the target layer. Returns (ok, message).
        Used by the in-QGIS overlay and by the browser window annotation."""
        ctx = dict(context or {})
        point, crs = ctx.pop("point", None), ctx.pop("crs", None)
        # photo regions: also give each box in the original image's pixels
        origin, k = ctx.get("frame_origin"), float(ctx.get("frame_scale") or 1.0)
        if origin is not None:
            anns = [dict(a, image_bbox=[round(origin[0] + a["bbox"][0] / k),
                                        round(origin[1] + a["bbox"][1] / k),
                                        round(a["bbox"][2] / k), round(a["bbox"][3] / k)])
                    for a in anns]
        f = self.fields
        annotated = render_boxes(frame, anns, self.box_color, self.box_width,
                                 draw_labels=self.ask_label)
        values = {
            f["bbox"]: anns[0]["bbox"] if len(anns) == 1 else [a["bbox"] for a in anns],
            f["meta"]: {"image": {"width": frame.width(), "height": frame.height()},
                        "bbox_format": "xywh_pixels", "annotations": anns, "view": ctx},
            f["source"]: "manual",
        }
        if self.output in ("annotated", "both"):
            values[f["annotated"]] = self._b64(annotated)
        if self.output in ("crop", "both"):
            values[f["crop"]] = self._b64(crop_image(frame, anns[0]["bbox"], self.crop_pad))
        values.update(ctx)  # panorama_id, yaw, ... αν υπάρχουν τέτοια πεδία

        if self.target == "selected":
            ok, err = update_selected_feature(self.layer, values)
        else:
            img = annotated if self.output != "crop" else crop_image(frame, anns[0]["bbox"], self.crop_pad)
            ok, err = save_annotation(self.layer, img, values[f["meta"]], point, crs,
                                      {k: v for k, v in values.items() if k != f["meta"]},
                                      self.img_format, self.quality)
        return ok, ("Image saved to the selected feature" if ok else f"Error: {err}")

    def _clear(self):
        self.overlay = None

    def _notify(self, ok, msg):
        if self.iface:
            ml = getattr(Qgis, "MessageLevel", Qgis)
            level = getattr(ml, "Success", 3) if ok else getattr(ml, "Warning", 1)
            self.iface.messageBar().pushMessage("Annotation", msg, level=level, duration=4)
        else:
            print(msg)

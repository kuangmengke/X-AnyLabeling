# -*- coding: utf-8 -*-
"""Label review dialog.

Find all images containing a chosen label, then review them one by one:
- highlight the target label's boxes on the preview (other shapes dimmed)
- navigate prev/next, jump to the main editor for modification
- mark images as checked / unchecked (persisted to the label JSON)
"""

import json
import os

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

CHECKED_FIELD = "checked"

TARGET_COLOR = QColor(46, 204, 113, 255)  # green: target label
OTHER_COLOR = QColor(150, 150, 150, 130)  # gray: other labels
PREVIEW_MAX_W = 920
PREVIEW_MAX_H = 620


class LabelReviewDialog(QtWidgets.QDialog):
    """Non-modal dialog to review annotations by label."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.image_file_list = []
        self.label_files = {}  # label -> list of match dicts
        self.current_label = ""
        self.all_matches = []
        self.matches = []
        self.current_index = -1

        self.refresh_files()
        self.init_ui()
        self.scan_all()

    # ------------------------------------------------------------------ scan
    def refresh_files(self):
        """Re-read the image list from the main file list widget."""
        self.image_file_list = []
        count = self.parent.file_list_widget.count()
        for c in range(count):
            self.image_file_list.append(
                self.parent.file_list_widget.item(c).text()
            )

    def _label_file_for(self, image_file):
        label_dir, filename = os.path.split(image_file)
        if self.parent.output_dir:
            label_dir = self.parent.output_dir
        return os.path.join(
            label_dir, os.path.splitext(filename)[0] + ".json"
        )

    def scan_all(self):
        """Scan all images once, grouping matches by label."""
        self.label_files = {}
        progress = QProgressDialog(
            self.tr("Scanning annotations..."),
            self.tr("Cancel"),
            0,
            len(self.image_file_list),
            self,
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setWindowTitle(self.tr("Label Review"))
        progress.setMinimumWidth(400)
        progress.show()

        for i, image_file in enumerate(self.image_file_list):
            progress.setValue(i)
            if progress.wasCanceled():
                break
            label_file = self._label_file_for(image_file)
            if not os.path.exists(label_file):
                continue
            try:
                with open(label_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            shapes = data.get("shapes", [])
            checked = data.get(CHECKED_FIELD, False)
            for shape in shapes:
                label = shape.get("label", "")
                if not label:
                    continue
                entry = self.label_files.setdefault(
                    label,
                    {
                        "label": label,
                        "files": {},
                    },
                )
                match = entry["files"].get(image_file)
                if match is None:
                    match = {
                        "image": image_file,
                        "label_file": label_file,
                        "n": 0,
                        "checked": bool(checked),
                        "boxes": [],
                    }
                    entry["files"][image_file] = match
                match["n"] += 1
                pts = shape.get("points", [])
                if len(pts) >= 2:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    match["boxes"].append(
                        (min(xs), min(ys), max(xs), max(ys))
                    )
        progress.close()

        # Rebuild the combo box with per-label counts
        self.label_combo.blockSignals(True)
        self.label_combo.clear()
        for label in sorted(self.label_files):
            count = len(self.label_files[label]["files"])
            self.label_combo.addItem(f"{label} ({count})", label)
        self.label_combo.blockSignals(False)

        if self.label_combo.count() == 0:
            self.status_label.setText(
                self.tr("No annotations found in the current project.")
            )
            self.matches = []
            self.populate_list()
            return

        # Keep current label if still present, otherwise pick the first one
        index = self.label_combo.findData(self.current_label)
        if index < 0:
            index = 0
        self.label_combo.setCurrentIndex(index)
        self.on_label_changed()

    def on_label_changed(self):
        self.current_label = self.label_combo.currentData()
        files = self.label_files.get(self.current_label, {}).get(
            "files", {}
        )
        self.all_matches = list(files.values())
        self.apply_filter()

    def apply_filter(self):
        if self.unchecked_only_checkbox.isChecked():
            self.matches = [
                m for m in self.all_matches if not m["checked"]
            ]
        else:
            self.matches = list(self.all_matches)
        self.populate_list()

    # ------------------------------------------------------------------- UI
    def init_ui(self):
        self.setWindowTitle(self.tr("Label Review"))
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(1280, 720)

        layout = QVBoxLayout(self)

        # Top bar
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel(self.tr("Label:")))
        self.label_combo = QComboBox()
        self.label_combo.setMinimumWidth(240)
        top_layout.addWidget(self.label_combo)
        self.unchecked_only_checkbox = QCheckBox(
            self.tr("Unchecked only")
        )
        top_layout.addWidget(self.unchecked_only_checkbox)
        self.rescan_button = QPushButton(self.tr("Rescan"))
        top_layout.addWidget(self.rescan_button)
        top_layout.addStretch(1)
        self.status_label = QLabel("")
        top_layout.addWidget(self.status_label)
        layout.addLayout(top_layout)

        # Middle: list + preview
        middle_layout = QHBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(340)
        self.list_widget.setMaximumWidth(420)
        middle_layout.addWidget(self.list_widget, 0)

        preview_group = QWidget()
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel(self.tr("Select an image to preview"))
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(PREVIEW_MAX_W, PREVIEW_MAX_H)
        self.preview_label.setStyleSheet(
            "border: 1px solid #888; background: #222;"
        )
        preview_layout.addWidget(self.preview_label)
        self.info_label = QLabel("")
        preview_layout.addWidget(self.info_label)
        middle_layout.addWidget(preview_group, 1)
        layout.addLayout(middle_layout, 1)

        # Bottom buttons
        button_layout = QHBoxLayout()
        self.prev_button = QPushButton(self.tr("Previous"))
        self.next_button = QPushButton(self.tr("Next"))
        self.open_button = QPushButton(self.tr("Open in Editor"))
        self.check_button = QPushButton(self.tr("Mark Checked"))
        self.uncheck_button = QPushButton(self.tr("Mark Unchecked"))
        button_layout.addWidget(self.prev_button)
        button_layout.addWidget(self.next_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.open_button)
        button_layout.addWidget(self.check_button)
        button_layout.addWidget(self.uncheck_button)
        layout.addLayout(button_layout)

        # Connections
        self.label_combo.currentIndexChanged.connect(self.on_label_changed)
        self.unchecked_only_checkbox.toggled.connect(self.apply_filter)
        self.rescan_button.clicked.connect(self.scan_all)
        self.list_widget.currentRowChanged.connect(
            self.on_list_row_changed
        )
        self.list_widget.itemDoubleClicked.connect(
            lambda _item: self.open_in_editor()
        )
        self.prev_button.clicked.connect(self.prev)
        self.next_button.clicked.connect(self.next)
        self.open_button.clicked.connect(self.open_in_editor)
        self.check_button.clicked.connect(
            lambda: self.set_checked(True)
        )
        self.uncheck_button.clicked.connect(
            lambda: self.set_checked(False)
        )

    def populate_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for i, match in enumerate(self.matches):
            checked_mark = " ✓" if match["checked"] else ""
            item = QListWidgetItem(
                f"{os.path.basename(match['image'])}"
                f"  ({match['n']} box)" + checked_mark
            )
            item.setToolTip(match["image"])
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if self.matches:
            self.list_widget.setCurrentRow(0)
        else:
            self.current_index = -1
            self.preview_label.setText(
                self.tr("No matching images for this label.")
            )
            self.info_label.setText("")

    # ------------------------------------------------------------- display
    def on_list_row_changed(self, row):
        self.current_index = row
        self.render_preview()

    def render_preview(self):
        if self.current_index < 0 or self.current_index >= len(
            self.matches
        ):
            return
        match = self.matches[self.current_index]
        pixmap = QPixmap(match["image"])
        if pixmap.isNull():
            self.preview_label.setText(
                self.tr("Failed to load image:\n") + match["image"]
            )
            self.info_label.setText("")
            return

        orig_w, orig_h = pixmap.width(), pixmap.height()
        scaled = pixmap.scaled(
            PREVIEW_MAX_W,
            PREVIEW_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = scaled.width() / orig_w
        sy = scaled.height() / orig_h

        painter = QPainter(scaled)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        try:
            with open(match["label_file"], "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        for shape in data.get("shapes", []):
            pts = shape.get("points", [])
            if len(pts) < 2:
                continue
            is_target = shape.get("label", "") == self.current_label
            pen = QPen(
                TARGET_COLOR if is_target else OTHER_COLOR,
                3 if is_target else 1,
            )
            painter.setPen(pen)
            qpts = [QPointF(p[0] * sx, p[1] * sy) for p in pts]
            if (
                shape.get("shape_type", "polygon") == "rectangle"
                and len(qpts) == 2
            ):
                painter.drawRect(QRectF(qpts[0], qpts[1]).normalized())
            else:
                painter.drawPolygon(QPolygonF(qpts))
            if is_target:
                x1 = min(p[0] for p in pts) * sx
                y1 = min(p[1] for p in pts) * sy
                font = painter.font()
                font.setBold(True)
                font.setPointSize(9)
                painter.setFont(font)
                painter.drawText(
                    QPointF(x1, max(12.0, y1 - 6)),
                    self.current_label,
                )
        painter.end()

        self.preview_label.setPixmap(scaled)
        checked_text = (
            self.tr("checked") if match["checked"] else self.tr("unchecked")
        )
        self.info_label.setText(
            f"{self.current_index + 1}/{len(self.matches)} | "
            f"{os.path.basename(match['image'])} | "
            f"{match['n']} {self.tr('box(es)')} | {checked_text} | "
            f"{orig_w}x{orig_h}"
        )

    # ------------------------------------------------------------ actions
    def prev(self):
        if self.current_index > 0:
            self.list_widget.setCurrentRow(self.current_index - 1)

    def next(self):
        if self.current_index < len(self.matches) - 1:
            self.list_widget.setCurrentRow(self.current_index + 1)

    def open_in_editor(self):
        if self.current_index < 0:
            return
        match = self.matches[self.current_index]
        self.parent.load_file(match["image"])

    def set_checked(self, checked):
        if self.current_index < 0:
            return
        match = self.matches[self.current_index]
        try:
            with open(match["label_file"], "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        data[CHECKED_FIELD] = bool(checked)
        try:
            with open(match["label_file"], "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            return

        match["checked"] = bool(checked)
        # Keep the main editor's in-memory state in sync if this file is open
        if (
            getattr(self.parent, "filename", None) == match["image"]
            and hasattr(self.parent, "other_data")
        ):
            self.parent.other_data[CHECKED_FIELD] = bool(checked)
            sync = getattr(self.parent, "_sync_annotation_checked_state", None)
            if sync:
                sync()

        # Refresh the list item text
        item = self.list_widget.item(self.current_index)
        if item is not None:
            checked_mark = " ✓" if match["checked"] else ""
            item.setText(
                f"{os.path.basename(match['image'])}"
                f"  ({match['n']} box)" + checked_mark
            )
        self.render_preview()

        # If "unchecked only" filter is active, remove it from the list
        if (
            self.unchecked_only_checkbox.isChecked()
            and match["checked"]
        ):
            self.apply_filter()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            self.prev()
            event.accept()
        elif event.key() == Qt.Key.Key_Right:
            self.next()
            event.accept()
        else:
            super().keyPressEvent(event)

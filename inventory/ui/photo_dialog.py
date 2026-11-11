# inventory/ui/photo_dialog.py
"""Съёмка фото устройства (опционально, если установлен OpenCV)."""
from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from inventory.ui.widgets import big_button, secondary_button


class PhotoCaptureDialog(QDialog):
    def __init__(self, photos_dir: Path, parent=None):
        super().__init__(parent)
        self.photos_dir = photos_dir
        self.photo_path: str | None = None
        self.capture = None
        self._failed_frames = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_frame)
        self.setWindowTitle("Фото устройства")
        self.setMinimumSize(800, 620)
        self._build_ui()
        self._start_camera()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.preview = QLabel("Подключение камеры...")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(720, 480)
        self.preview.setStyleSheet("background-color: #111820; border-radius: 10px;")
        layout.addWidget(self.preview, 1)

        buttons = QHBoxLayout()
        self.cancel_btn = secondary_button("Отмена", height=56, font_size=16)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)

        self.file_btn = secondary_button("📁 Из файла", height=56, font_size=16)
        self.file_btn.clicked.connect(self._pick_file)
        buttons.addWidget(self.file_btn, 1)

        self.capture_btn = big_button("📷 Снять фото", "#d35400", height=56, font_size=17)
        self.capture_btn.clicked.connect(self._capture)
        self.capture_btn.setEnabled(False)
        buttons.addWidget(self.capture_btn, 2)
        layout.addLayout(buttons)

    def _pick_file(self) -> None:
        """Прикрепить готовый снимок с диска (например, пересланный с телефона)."""
        default_dir = self.photos_dir if self.photos_dir.exists() else Path.home()
        src, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите фото устройства",
            str(default_dir),
            "Фото (*.jpg *.jpeg *.png *.bmp);;Все файлы (*.*)",
        )
        if not src:
            return
        try:
            self.photos_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(src).suffix.lower() or ".jpg"
            target = self.photos_dir / f"{uuid4().hex}{suffix}"
            shutil.copyfile(src, target)
            self.photo_path = str(target)
            self._release_camera()
            self.accept()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Фото", f"Не удалось прикрепить файл:\n{exc}")

    def _start_camera(self) -> None:
        try:
            import cv2

            self.capture = cv2.VideoCapture(0)
            if not self.capture.isOpened():
                self.preview.setText("Камера не открылась. Проверьте подключение.")
                return
            self.preview.setText("Камера подключена. Ожидание кадра...")
            self.capture_btn.setEnabled(True)
            self.timer.start(40)
        except Exception as exc:  # noqa: BLE001
            self.preview.setText(f"Фото недоступно:\n{exc}")

    def _update_frame(self) -> None:
        if not self.capture:
            return
        ok, frame = self.capture.read()
        if not ok or frame is None:
            self._failed_frames += 1
            if self._failed_frames == 50:
                self.preview.setPixmap(QPixmap())
                self.preview.setText(
                    "Нет сигнала с камеры.\nПроверьте подключение или закройте окно."
                )
            return
        self._failed_frames = 0
        try:
            import cv2

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb.shape
            image = QImage(
                rgb.data, width, height, channels * width, QImage.Format.Format_RGB888
            ).copy()
            pixmap = QPixmap.fromImage(image).scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview.setPixmap(pixmap)
        except Exception:  # noqa: BLE001
            pass

    def _capture(self) -> None:
        if not self.capture:
            return
        ok, frame = self.capture.read()
        if not ok or frame is None:
            QMessageBox.warning(self, "Фото", "Не удалось получить кадр")
            return
        try:
            import cv2

            height, width = frame.shape[:2]
            scale = min(1.0, 1280 / max(height, width))
            if scale < 1.0:
                frame = cv2.resize(
                    frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA
                )
            self.photos_dir.mkdir(parents=True, exist_ok=True)
            path = self.photos_dir / f"{uuid4().hex}.jpg"
            cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            self.photo_path = str(path)
            self._release_camera()
            self.accept()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Фото", f"Не удалось сохранить фото:\n{exc}")

    def _release_camera(self) -> None:
        self.timer.stop()
        if self.capture:
            try:
                self.capture.release()
            except Exception:  # noqa: BLE001
                pass
            self.capture = None

    def closeEvent(self, event) -> None:
        self._release_camera()
        super().closeEvent(event)

    def reject(self) -> None:
        self._release_camera()
        super().reject()

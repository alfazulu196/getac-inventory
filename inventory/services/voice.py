# inventory/services/voice.py
"""Голосовые заметки: запись с микрофона и офлайн-распознавание Vosk."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from inventory.config import APP_BASE, VoiceConfig, resolve_path


class VoiceService:
    """Ленивая загрузка Vosk, запись и распознавание."""

    def __init__(self, config: VoiceConfig):
        self.config = config
        self.model_path: Path = resolve_path(config.model_path, APP_BASE)
        self.model: Any = None
        self.recognizer: Any = None
        self.stream: Any = None
        self.frames: list[Any] = []
        self.status = "Не загружено"
        self.last_error: str | None = None
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def model_found(self) -> bool:
        return self.model_path.is_dir()

    @property
    def available(self) -> bool:
        return self.config.enabled and self.model_found and self.model is not None

    def _ascii_model_path(self) -> Path | None:
        r"""Путь модели в ASCII-каталоге, если исходный путь с кириллицей.

        Vosk на Windows открывает файлы модели через кодовую страницу ANSI,
        поэтому не-ASCII путь (например «Барбацуца\! Медвед\…») даёт ошибку
        «Folder does not contain model files». Решение: один раз скопировать
        модель в %LOCALAPPDATA%\\GetacInventory\\vosk_model (ASCII) и грузить
        оттуда. Копия контролируется маркером с исходным путём.
        """
        try:
            str(self.model_path).encode("ascii")
            return None  # путь чистый — зеркало не нужно
        except UnicodeEncodeError:
            pass
        base = Path(
            os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        ) / "GetacInventory" / "vosk_model"
        marker = base / "_source.txt"
        needed = False
        try:
            wanted = str(self.model_path)
            if not marker.exists() or marker.read_text(encoding="utf-8") != wanted:
                needed = True
            elif not (base / "am" / "final.mdl").is_file():
                needed = True
            if needed:
                shutil.rmtree(base, ignore_errors=True)
                base.mkdir(parents=True, exist_ok=True)
                shutil.copytree(self.model_path, base, dirs_exist_ok=True)
                marker.write_text(wanted, encoding="utf-8")
            return base
        except Exception:  # noqa: BLE001 — нет прав/места: остаёмся на исходном пути
            return None

    def load(self) -> bool:
        if not self.config.enabled:
            self.status = "Голос отключён в настройках"
            return False
        if self.model is not None:
            return True
        if not self.model_found:
            self.status = f"Модель не найдена: {self.model_path}"
            return False
        load_path = self.model_path
        mirror = self._ascii_model_path()
        if mirror is not None:
            load_path = mirror
        try:
            from vosk import Model  # тяжёлый импорт — только при загрузке

            self.status = "Загрузка модели..."
            self.model = Model(str(load_path))
            self.status = "Готово"
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001 — модель может быть повреждена
            self.status = f"Ошибка модели: {exc}"
            self.last_error = str(exc)
            return False

    def list_input_devices(self) -> list[dict[str, Any]]:
        try:
            import sounddevice as sd

            devices = []
            default_input = sd.default.device[0] if sd.default.device else None
            for index, device in enumerate(sd.query_devices()):
                if device.get("max_input_channels", 0) > 0:
                    devices.append(
                        {
                            "index": index,
                            "name": device.get("name", f"Устройство {index}"),
                            "channels": device.get("max_input_channels", 0),
                            "default": index == default_input,
                        }
                    )
            return devices
        except Exception:  # noqa: BLE001
            return []

    def start_recording(self) -> bool:
        if self._recording:
            return True
        if not self.load():
            return False
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer

            self.recognizer = KaldiRecognizer(self.model, self.config.sample_rate)
            self.frames = []
            self.stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype="int16",
                device=self.config.device,
                callback=self._audio_callback,
            )
            self.stream.start()
            self._recording = True
            self.status = "Запись..."
            return True
        except Exception as exc:  # noqa: BLE001
            self.status = f"Ошибка записи: {exc}"
            self.last_error = str(exc)
            self._recording = False
            self._close_stream()
            return False

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.last_error = str(status)
        self.frames.append(indata.copy())

    def stop_and_recognize(self) -> str:
        if not self._recording:
            return ""
        self._recording = False
        self._close_stream()
        if not self.frames:
            self.status = "Речь не распознана: тишина"
            return ""
        try:
            assert self.recognizer is not None
            for frame in self.frames:
                self.recognizer.AcceptWaveform(frame.tobytes())
            self.frames = []
            result = json.loads(self.recognizer.FinalResult())
            text = str(result.get("text", "")).strip()
            text = self.apply_dictionary(text)
            self.status = "Распознано" if text else "Речь не распознана"
            return text
        except Exception as exc:  # noqa: BLE001
            self.status = f"Ошибка распознавания: {exc}"
            self.last_error = str(exc)
            return ""
        finally:
            self.frames = []

    def cancel_recording(self) -> None:
        self._recording = False
        self._close_stream()
        self.frames = []
        self.status = "Запись отменена"

    def _close_stream(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass
            self.stream = None

    def apply_dictionary(self, text: str) -> str:
        """Заменяет типовые ошибки распознавания на термины."""
        result = text
        for wrong, right in self.config.dictionary.items():
            if not wrong:
                continue
            result = re.sub(
                rf"(?<!\w){re.escape(wrong)}(?!\w)",
                right,
                result,
                flags=re.IGNORECASE,
            )
        return result

    def test_microphone_level(self, duration: float = 1.0) -> int:
        """Возвращает максимальную амплитуду короткой записи (0 при ошибке)."""
        try:
            import numpy as np
            import sounddevice as sd

            frames = max(1, int(duration * self.config.sample_rate))
            audio = sd.rec(
                frames,
                samplerate=self.config.sample_rate,
                channels=1,
                dtype="int16",
                device=self.config.device,
            )
            sd.wait()
            if audio.size == 0:
                return 0
            return int(np.abs(audio).max())
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return 0

    def close(self) -> None:
        self.cancel_recording()
        self.model = None
        self.recognizer = None
        self.status = "Закрыто"

# tests/test_theme.py
"""Полевая тема (v1.6.8): стиль/шрифт, перекраска inline-подписей, конфиг, настройки."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from inventory.config import AppConfig, load_config, save_config  # noqa: E402
from inventory.ui.widgets import (  # noqa: E402
    APP_STYLE,
    FIELD_STYLE,
    apply_app_style,
    ensure_field_restyle,
)

_APP: QApplication | None = None


def _qapp() -> QApplication:
    global _APP
    if QApplication.instance() is None:
        _APP = QApplication([])
    return QApplication.instance()


def test_field_mode_sets_style_and_big_bold_font() -> None:
    app = _qapp()
    apply_app_style(app, field_mode=False)
    assert app.styleSheet() == APP_STYLE
    assert app.font().pointSize() == 10
    assert not app.font().bold()

    apply_app_style(app, field_mode=True)
    assert app.styleSheet() == FIELD_STYLE
    assert app.font().pointSize() == 12
    assert app.font().bold()

    # Обычная тема обратно — не утекать в соседние тесты.
    apply_app_style(app, field_mode=False)
    assert app.styleSheet() == APP_STYLE


def test_field_mode_restyles_muted_inline_labels() -> None:
    app = _qapp()
    label = QLabel("подсказка")
    label.setStyleSheet("color: #9fb3c8; font-size: 13px;")
    apply_app_style(app, field_mode=True)
    assert "#9fb3c8" not in label.styleSheet()
    assert "#ffffff" in label.styleSheet()
    assert "font-size: 13px;" in label.styleSheet()  # не-цвета не трогаем

    # Диалоги, созданные ПОСЛЕ включения темы, тоже перекрашиваются.
    late = QLabel("поздний")
    late.setStyleSheet("color: #9fb3c8;")
    ensure_field_restyle()
    assert "#ffffff" in late.styleSheet()

    apply_app_style(app, field_mode=False)


def test_field_mode_button_contrast_and_revert() -> None:
    """Вторичная кнопка в полевой теме — контраст; выключение возвращает вид."""
    from inventory.ui.widgets import secondary_button

    app = _qapp()
    apply_app_style(app, field_mode=False)
    plain = secondary_button("Тест")
    assert "#3b4a5f" in plain.styleSheet()
    assert "border: 3px" not in plain.styleSheet()

    apply_app_style(app, field_mode=True)
    styled_now = secondary_button("Тест")  # создана уже в полевом режиме
    assert "#ffe066" in styled_now.styleSheet()  # жёлтая рамка/текст

    ensure_field_restyle()  # и ранее созданная перекрашивается
    assert "#ffe066" in plain.styleSheet()

    apply_app_style(app, field_mode=False)
    ensure_field_restyle()  # выключение темы возвращает обычный стиль
    assert "#ffe066" not in plain.styleSheet()
    assert "#3b4a5f" in plain.styleSheet()


def test_field_mode_config_roundtrip(tmp_path: Path) -> None:
    cfg = AppConfig()
    assert cfg.field_mode is False
    cfg.field_mode = True
    path = tmp_path / "config.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.field_mode is True


def test_settings_dialog_saves_and_applies_field_mode(
    tmp_path: Path, monkeypatch
) -> None:
    app = _qapp()
    import inventory.ui.settings_dialog as settings_dialog
    from inventory.ui.settings_dialog import SettingsDialog

    # Не трогаем настоящий config.json разработчика.
    monkeypatch.setattr(
        settings_dialog, "save_config", lambda config, path=None: None)

    config = AppConfig()
    paths = {
        "data": tmp_path / "data",
        "photos": tmp_path / "photos",
        "exports": tmp_path / "exports",
        "backups": tmp_path / "backups",
        "logs": tmp_path / "logs",
        "db": tmp_path / "inventory.db",
    }
    dialog = SettingsDialog(config, paths, repo=None)
    assert dialog.field_check.isChecked() is False

    dialog.field_check.setChecked(True)
    dialog._save()
    assert config.field_mode is True
    assert app.styleSheet() == FIELD_STYLE  # применилось живо, без перезапуска

    # Выключаем обратно — вернуть обычную тему остальным тестам.
    apply_app_style(app, field_mode=False)

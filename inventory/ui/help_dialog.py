# inventory/ui/help_dialog.py
"""Шпаргалка в поле: один экран с правилами работы приложения.

Вызывается кнопкой «?» на экране обхода. Только чтение, без сети.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
)

from inventory.config import APP_VERSION

_HELP_TEXT = """
<h2>Как пользоваться: короткая шпаргалка</h2>

<h3>1 · Локация</h3>
<p>Перед обходом выберите <b>📍 Локацию</b> (этаж, помещение, шкаф).
Все отсканированные устройства записываются в текущую локацию.
Без локации запись не сохраняется.</p>

<h3>2 · Скан</h3>
<p>Сканер включается автоматически (клавиши лазера — обычно
<b>F11/F12</b>, см. «Настройки → Сканер»). Надёжный порядок: сначала
<b>MAC</b>, потом <b>S/N</b>. Если под наклейкой только товарный EAN —
отсканируйте его: приложение запомнит, но дублем устройства не посчитает.</p>

<h3>3 · Активная карточка</h3>
<p>После скана карточка появляется в центре — она «активная».
Статус (ОК / Не работает / Замена…) ставится <b>чипами под карточкой</b> —
карточка «Подробно» нужна для модели, IP, канала NVR, фотографий и связи.</p>

<h3>4 · Связи</h3>
<p>«🔌 Связь»: отсканируйте MAC коммутатора выше и укажите порт.
Камера или точка без связи попадает в сводку проблем отчёта
(«камеры/точки без связи»).</p>

<h3>5 · Фото</h3>
<p>«📷 Фото» — камера ноутбука. «📱 Телефон» — Wi-Fi-страница с QR-кодом:
расшарьте хот-спот ноутбука, телефон сканирует QR и шлёт снимки,
они привязываются к активному устройству.</p>

<h3>6 · Голос</h3>
<p>«🎤 Заметка» — офлайн-диктовка к активному устройству (Vosk понимает
полевые слова: «камеру», «коммутатор» и т.д.). Если микрофона или модели
распознавания нет — работайте без голоса, заметку можно напечатать
в «Подробно».</p>

<h3>7 · Дубликаты</h3>
<p>Кнопка «👥 Дубликаты» собирает пары с одинаковым S/N/MAC в один список.
Обычно это осмотр одной камеры в двух местах или брак наклейки:
откройте карточку и исправьте либо удалите лишнюю запись.</p>

<h3>8 · Завершение и отчёт</h3>
<p>«Завершить сессию» — сводка и кнопки: сверить с прошлой поездкой
(Excel-файл или архивная сессия этой базы) и сформировать Excel:
титул, устройства, связи, регистраторы, сводка, сверка, проблемы.</p>

<h3>9 · Обход вдвоём</h3>
<p>Стартовый экран → «⤵ Импорт из базы…». Напарник приносит свою базу
и папку photos — устройства переносятся с новыми номерами без двойников,
локации сопоставляются, совпадения по S/N/MAC разбираются режимом:
пропустить / добавить как новые / заполнить пустое.</p>

<h3>10 · Если что-то пошло не так</h3>
<p>Журнал работы — в <code>data\\logs\\</code>, резервные копии базы — в
<code>data\\backups\\</code>. Сканер «замолчал» — «Настройки → Сканер»,
проверьте COM-порт. Голос не слушается — отключите и печатайте заметки.</p>
"""


class HelpDialog(QDialog):
    """Модальная шпаргалка: скролл, крупный текст, только чтение."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Справка")
        self.setMinimumSize(760, 640)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Справка · Инвентаризация объекта")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        browser = QTextBrowser()
        browser.setHtml(_HELP_TEXT)
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet(
            "QTextBrowser { background: #101820; color: #dbe5f0;"
            " font-size: 15px; border: 1px solid #2a3a4a; border-radius: 8px;"
            " padding: 10px; }"
        )
        scroll = QScrollArea()
        scroll.setWidget(browser)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(scroll, 1)

        footer = QLabel(f"Версия {APP_VERSION} · работает офлайн · детали в README")
        footer.setStyleSheet("color: #9fb3c8; font-size: 12px;")
        layout.addWidget(footer)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Понятно")
        close_button.setMinimumHeight(44)
        close_button.setMinimumWidth(140)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

# inventory/ui/reference_dialog.py
"""Полевой справочник: вкладки — S/N и MAC, кабели и PoE, стойки и юниты.

Вызывается кнопкой «📚» с экрана обхода и с стартового экрана.
Только чтение, без сети; текст зашит в модуль — попадает в exe-бандл
без изменений в datas.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)

from inventory.config import APP_VERSION

_BROWSER_STYLE = (
    "QTextBrowser { background: #101820; color: #dbe5f0;"
    " font-size: 15px; border: 1px solid #2a3a4a; border-radius: 8px;"
    " padding: 10px; }"
)

_TABLE_STYLE = (
    'border="1" cellspacing="0" cellpadding="4" width="100%"'
)

_SN_MAC_HTML = """
<h2>Где искать S/N и MAC</h2>
<p><b>Общее правило:</b> S/N и MAC — на наклейке или гравировке корпуса.
MAC — 12 шестнадцатеричных знаков парами через двоеточие или дефис
(<code>AA:BB:CC:DD:EE:FF</code>), может называться «MAC», «MAC ID»,
«Physical Address». S/N идёт рядом со штрих-кодом. Первые 3 октета MAC —
производитель: приложение показывает вендора по базе OUI.</p>

<h3>IP-камеры</h3>
<ul>
<li><b>Корпусные</b> — наклейка снизу или сзади корпуса.</li>
<li><b>Купольные</b> — снять купол: наклейка на базе или на плате.
Не потеряйте уплотнительное кольцо.</li>
<li><b>Hikvision</b>: S/N под штрих-кодом, MAC отдельной наклейкой или
сзади. Проверка в поле: SADP или веб-интерфейс → Конфигурация →
Система → О системе.</li>
</ul>

<h3>Коммутаторы</h3>
<p>Настольные — наклейка на дне; стоечные — на боку или задней панели.
<b>D-Link</b>: строка «Serial Number», MAC под ней. У модельного ряда
иногда два штрих-кода: EAN (товарный) и S/N — нужен второй.</p>

<h3>Точки доступа</h3>
<ul>
<li><b>Ubiquiti</b>: снизу, строки «SN» и «MAC» отдельно.</li>
<li><b>MikroTik</b>: «Serial Number» и MAC на дне корпуса.</li>
<li><b>TP-Link</b>: наклейка снизу, MAC рядом со штрих-кодом.</li>
</ul>

<h3>Регистраторы, серверы, ИБП</h3>
<p>NVR — наклейка сверху или сзади, у Hikvision рядом с разъёмами.
Серверы: <b>Dell</b> — Service Tag на выдвижной планке спереди или сзади;
<b>HP</b> — наклейка с S/N на боку или сзади. <b>ИБП</b> — наклейка сзади
или снизу: S/N нужен для гарантии, MAC обычно отсутствует.</p>

<h3>Если MAC на корпусе не найден</h3>
<ul>
<li>Веб-интерфейс устройства → «О системе» / «System Info».</li>
<li>Наклейка на блоке питания или съёмном модуле.</li>
<li>MAC-таблица коммутатора: пометьте порт, найдите «висящий» на нём MAC.</li>
<li>Не тратьте время в поле: впишите S/N, MAC попадёт в «✅ Чек-лист
докрутки» при завершении сессии.</li>
</ul>

<p><i>Совет: сфотографируйте наклейку (клавиша <b>F</b>) и вводите
значения по снимку — на морозе и в тени это быстрее.</i></p>
"""

_CABLE_POE_HTML = f"""
<h2>Расцветка витой пары (T568A / T568B)</h2>
<table {_TABLE_STYLE}>
<tr><th>Пин</th><th>T568A</th><th>T568B</th></tr>
<tr><td>1</td><td>бело-зелёный</td><td><b>бело-оранжевый</b></td></tr>
<tr><td>2</td><td>зелёный</td><td><b>оранжевый</b></td></tr>
<tr><td>3</td><td>бело-оранжевый</td><td><b>бело-зелёный</b></td></tr>
<tr><td>4</td><td>голубой</td><td>голубой</td></tr>
<tr><td>5</td><td>бело-голубой</td><td>бело-голубой</td></tr>
<tr><td>6</td><td>оранжевый</td><td><b>зелёный</b></td></tr>
<tr><td>7</td><td>бело-коричневый</td><td>бело-коричневый</td></tr>
<tr><td>8</td><td>коричневый</td><td>коричневый</td></tr>
</table>
<p>Прямой кабель — <b>одинаковая</b> распиновка с обеих сторон (у нас
обычно B). Кроссовый (A на одном конце) сегодня почти не нужен — порты
умеют Auto-MDIX. Длина сегмента: до 100 м (90 м стационарной прокладки
+ патч-корды).</p>

<h3>Категории</h3>
<ul>
<li><b>Cat5e</b> — 1 Гбит/с до 100 м.</li>
<li><b>Cat6</b> — 1 Гбит/с до 100 м; 10G только до 55 м.</li>
<li><b>Cat6a</b> — 10 Гбит/с до 100 м.</li>
</ul>

<h2>PoE: что выдержит порт</h2>
<table {_TABLE_STYLE}>
<tr><th>Стандарт</th><th>Мощность порта</th><th>Типовая нагрузка</th></tr>
<tr><td>802.3af (PoE)</td><td>до 15,4 Вт</td><td>обычные IP-камеры, телефоны</td></tr>
<tr><td>802.3at (PoE+)</td><td>до 30 Вт</td><td>PTZ-камеры с ИК/обогревом, современные AP</td></tr>
<tr><td>802.3bt (PoE++)</td><td>60 / 90 Вт</td><td>мощные PTZ, прожекторы, коммутаторы-«каскады»</td></tr>
</table>
<p><b>Пассивный PoE</b> — нестандартный (часто 24 В у MikroTik/Ubiquiti):
проверяйте вольтаж инжектора, несовпадение сжигает порт или устройство.
Ориентир потребления: камера 5–12 Вт, точка доступа 8–25 Вт,
IP-телефон 5–12 Вт.</p>

<h3>Оптика и SFP</h3>
<ul>
<li>Модули работают <b>парами</b> по длине волны: 1310/1550 нм (WDM) —
одна жила, разнонаправленные.</li>
<li>Цвет патч-корда: жёлтый — одномод, бирюзовый — OM3, фиолетовый — OM4,
оранжевый — старый OM1/OM2.</li>
<li>Коннекторы не трогать пальцами; заглушки возвращать на место.</li>
</ul>

<h3>Практика в поле</h3>
<p>Линк — зелёный индикатор порта (мигает = есть трафик). Связь
«устройство → коммутатор и порт» фиксируйте через «🔌 Связь» — камеры и
точки без upstream попадут в чек-лист докрутки.</p>
"""

_RACK_UNITS_HTML = f"""
<h2>Стойки: нумерация юнитов</h2>
<p><b>Юниты считаются снизу вверх:</b> <code>U1</code> — самый нижний.
Стойка 19″ (482,6 мм между стойками), 1U ≈ 44,45 мм (1,75″).</p>

<h3>Типовая высота оборудования</h3>
<table {_TABLE_STYLE}>
<tr><th>Оборудование</th><th>Обычно занимает</th></tr>
<tr><td>Патч-панель, коммутатор, органайзер</td><td>1U</td></tr>
<tr><td>Сервер</td><td>1U–2U</td></tr>
<tr><td>ИБП</td><td>2U–4U</td></tr>
<tr><td>Полка, VDC, лицевые панели</td><td>1U–2U</td></tr>
</table>

<h3>Как записывать в приложении</h3>
<ul>
<li>В поле «Юнит» указывайте <b>нижний</b> юнит диапазона:
устройство на U5–U8 записываем как <code>U5</code>. Можно и так:
<code>U5-U8</code> — при сортировке пойдёт сразу после U5.</li>
<li><b>Пустые юниты вписывать не надо</b> — они видны на фото стойки.
Органайзеры, заглушки и вент-панели — не устройства.</li>
<li>Обход стойки ведите одним проходом (сверху вниз или снизу вверх),
не прыгайте — меньше дублей.</li>
<li>В отчёте Excel юниты сортируются автоматически: U2 &lt; U5 &lt;
U5-U8 &lt; текст &lt; пусто.</li>
<li>Фото стойки целиком <b>до</b> разбора: кнопка «🖥 Стойка по фото
(ИИ)» распознает видимые юниты и создаст черновик пачкой.</li>
</ul>

<h3>Помнить</h3>
<p>Записывайте, что стоит <b>сейчас</b>, а не по старой схеме: план
могли обновить, а кабели — переиспользовать. Сомнения — в комментарий
к устройству или голосовой заметкой (клавиша <b>V</b>).</p>
"""

REFERENCE_TABS: tuple[tuple[str, str], ...] = (
    ("🏷 S/N и MAC", _SN_MAC_HTML),
    ("🔌 Кабели и PoE", _CABLE_POE_HTML),
    ("🖥 Стойки и юниты", _RACK_UNITS_HTML),
)


class ReferenceDialog(QDialog):
    """Модальный справочник: вкладки, крупный текст, только чтение."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Справочник")
        self.setMinimumSize(860, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Справочник · полевые шпаргалки")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        tabs = QTabWidget()
        for tab_title, html in REFERENCE_TABS:
            browser = QTextBrowser()
            browser.setHtml(html)
            browser.setOpenExternalLinks(False)
            browser.setStyleSheet(_BROWSER_STYLE)
            scroll = QScrollArea()
            scroll.setWidget(browser)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            tabs.addTab(scroll, tab_title)
        layout.addWidget(tabs, 1)

        footer = QLabel(f"Версия {APP_VERSION} · работает офлайн")
        footer.setStyleSheet("color: #9fb3c8; font-size: 12px;")
        layout.addWidget(footer)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.setMinimumHeight(44)
        close_button.setMinimumWidth(140)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

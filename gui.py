# gui.py
import time
import serial  
import winsound  
import ctypes  # Низкоуровневая библиотека Windows для отправки системных клавиш лазера
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QTextEdit, QMessageBox)
from PyQt6.QtCore import QThread, pyqtSignal

# НАСТРОЙКА ЖЕЛЕЗА GETAC
SCANNER_COM_PORT = "COM3" 

class ScannerThread(QThread):
    barcode_scanned = pyqtSignal(str)
    log_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.ser = None

    def run(self):
        self.log_signal.emit(f"[ПОРТ] Подключение к {SCANNER_COM_PORT}...")
        while True:
            try:
                # Открываем порт в режиме разделяемого доступа и держим открытым
                self.ser = serial.Serial(port=SCANNER_COM_PORT, baudrate=9600, timeout=0.1)
                self.log_signal.emit(f"[ПОРТ] Успешное подключение к {SCANNER_COM_PORT}!")
                
                while True:
                    # Проверяем, пришли ли данные от сканера после зажигания луча
                    if self.ser and self.ser.is_open and self.ser.in_waiting > 0:
                        barcode_data = self.ser.readline().decode('utf-8', errors='ignore').strip()
                        if barcode_data:
                            self.barcode_scanned.emit(barcode_data)
                            
                    self.msleep(20)
            except Exception as e:
                self.log_signal.emit(f"[ПОРТ] Ожидание подключения... ({e})")
                self.msleep(1000)

class GetacScannerApp(QMainWindow):
    def __init__(self, on_scan_callback, on_voice_callback, on_reset_callback):
        super().__init__()
        self.on_scan_callback = on_scan_callback
        self.on_voice_callback = on_voice_callback
        self.on_reset_callback = on_reset_callback
        self.is_recording = False 
        self.initUI()
        
        # Запуск параллельного потока опроса сканера
        self.scanner_thread = ScannerThread()
        self.scanner_thread.barcode_scanned.connect(self.handle_scanned_barcode)
        self.scanner_thread.log_signal.connect(self.print_system_log)
        self.scanner_thread.start()

    def initUI(self):
        self.setWindowTitle("Getac V110 - Сенсорный Терминал c Лазером")
        self.setGeometry(100, 100, 800, 700)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # ВЕРХНЯЯ ПАНЕЛЬ: Статус ИИ и Сброс проекта
        top_layout = QHBoxLayout()
        self.status_label = QLabel("СТАТУС: Инициализация...")
        self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #f1c40f; padding: 5px;")
        top_layout.addWidget(self.status_label)
        
        self.reset_btn = QPushButton("🆕 Новый проект")
        self.reset_btn.setStyleSheet("""
            QPushButton { font-size: 14px; font-weight: bold; background-color: #e74c3c; color: white; min-height: 45px; max-width: 160px; border-radius: 8px; }
            QPushButton:pressed { background-color: #c0392b; }
        """)
        self.reset_btn.clicked.connect(self.handle_reset_click)
        top_layout.addWidget(self.reset_btn)
        layout.addLayout(top_layout)
        
        # ЦЕНТР: Большое синее поле вывода информации
        self.info_area = QTextEdit()
        self.info_area.setReadOnly(True)
        self.info_area.setStyleSheet("font-size: 18px; background-color: #2c3e50; color: #ecf0f1; border-radius: 6px; padding: 10px;")
        layout.addWidget(self.info_area)
        
        # ОГРОМНАЯ ОРАНЖЕВАЯ КНОПКА АКТИВАЦИИ ЛАЗЕРА С ЭКРАНА ПЛАНШЕТА
        self.laser_btn = QPushButton("🔥 ЗАЖЕЧЬ ЛАЗЕР СКАНЕРА")
        self.laser_btn.setStyleSheet("""
            QPushButton { font-size: 22px; font-weight: bold; background-color: #d35400; color: white; min-height: 100px; border-radius: 12px; margin-bottom: 5px; }
            QPushButton:pressed { background-color: #e67e22; }
        """)
        self.laser_btn.clicked.connect(self.trigger_hardware_laser)
        layout.addWidget(self.laser_btn)
        
        # НИЗ: Сенсорные кнопки управления микрофоном Vosk
        btn_layout = QHBoxLayout()
        self.start_voice_btn = QPushButton("🎤 НАЧАТЬ ЗАПИСЬ")
        self.start_voice_btn.setStyleSheet("font-size: 18px; font-weight: bold; background-color: #2e7d32; color: white; min-height: 80px; border-radius: 12px;")
        btn_layout.addWidget(self.start_voice_btn)
        
        self.stop_voice_btn = QPushButton("🛑 СТОП И СОХРАНИТЬ")
        self.stop_voice_btn.setStyleSheet("font-size: 18px; font-weight: bold; background-color: #d32f2f; color: white; min-height: 80px; border-radius: 12px;")
        self.stop_voice_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_voice_btn)
        layout.addLayout(btn_layout)

    def print_system_log(self, text):
        """Выводит системные сообщения портов в текстовое поле"""
        self.info_area.append(f"<span style='color: #f1c40f;'>{text}</span>")
        if "Успешное" in text:
            self.status_label.setText("СТАТУС: Готов к работе")
            self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #2ecc71; padding: 5px;")

    def trigger_hardware_laser(self):
        """Эмулирует поочередное нажатие F11 и F12 для гарантированного зажигания лазера Getac"""
        self.status_label.setText("СТАТУС: ЛАЗЕР АКТИВЕН, СКАНИРУЙТЕ...")
        self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #9b59b6; padding: 5px;")
        
        # Виртуальные коды клавиш Windows
        VK_F11 = 0x7A  
        VK_F12 = 0x7B  
        
        # 1. Симулируем нажатие/отпускание F11
        ctypes.windll.user32.keybd_event(VK_F11, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_F11, 0, 2, 0)
        
        # 2. Небольшая задержка и симуляция F12
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_F12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_F12, 0, 2, 0)
        
        winsound.Beep(2500, 60)

    def handle_scanned_barcode(self, barcode):
        """Вызывается автоматически, когда COM-порт перехватил считанный штрихкод"""
        winsound.Beep(3000, 80) 
        self.status_label.setText("СТАТУС: ИИ анализирует код...")
        self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #f39c12; padding: 5px;")
        
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        
        try:
            # Передаем данные в main.py для ИИ-анализа и записи датафрейма в Excel
            self.on_scan_callback(barcode, self.info_area)
            # Победный трехнотный мажорный аккорд «Та-да!»
            winsound.Beep(2000, 100)
            winsound.Beep(2500, 100)
            winsound.Beep(3000, 150)
        except Exception as e:
            self.info_area.append(f"<span style='color: #e74c3c;'>[ОШИБКА ОБРАБОТКИ]: {e}</span>")
            winsound.Beep(400, 400)
            
        self.status_label.setText("СТАТУС: Готов к работе")
        self.status_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #2ecc71; padding: 5px;")

    def handle_reset_click(self):
        """Логика сброса и архивации проекта с диалоговым окном"""
        reply = QMessageBox.question(
            self, "Запуск нового проекта", "Архивировать текущий проект и начать с DEV-001?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self.on_reset_callback():
                self.info_area.clear()
                self.info_area.append("<span style='color: #2ecc71;'><b>[СИСТЕМА] Проект успешно заархивирован. Счетчик сброшен на DEV-001.</b></span><br>" + "="*40)
                winsound.Beep(2500, 300)

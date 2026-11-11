# main.py
import sys
import os
from PyQt6.QtWidgets import QApplication
from datetime import datetime
import pandas as pd

from config import OUTPUT_EXCEL, EXCEL_COLUMNS
import database
import ai_client
import voice
import gui

df_inventory = None
current_device_id = 1

def load_or_create_db():
    global df_inventory, current_device_id
    df_inventory = database.init_database(OUTPUT_EXCEL)
    current_device_id = len(df_inventory) + 1

load_or_create_db()

def on_device_scanned(barcode, ui_text_area):
    global df_inventory, current_device_id
    
    detected_type, detected_brand = ai_client.analyze_barcode(barcode)
    
    sn_field = barcode if "серийный" in detected_type.lower() or "модель" in detected_type.lower() else ""
    mac_field = barcode if "mac" in detected_type.lower() else ""
    
    new_row = {
        "ID устройства": f"DEV-{current_device_id:03d}",
        "Тип устройства": f"Определено ИИ: {detected_type}",
        "Производитель / Модель": detected_brand,
        "Серийный номер (S/N)": sn_field,
        "MAC-адрес": mac_field,
        "IP-адрес": "", 
        "Подключен к (Устройство / Порт)": "",
        "Место установки (Этаж / Кабинет / Шкаф)": "",
        "Способ питания": "",
        "Текущий статус / Примечание": f"Сканирование {datetime.now().strftime('%H:%M:%S')}"
    }
    
    df_inventory = pd.concat([df_inventory, pd.DataFrame([new_row])], ignore_index=True)
    database.save_to_excel(df_inventory, OUTPUT_EXCEL)
    
    ui_text_area.append(
        f"<b>Добавлено устройство: DEV-{current_device_id:03d}</b><br>"
        f"Тип: {detected_type} | Бренд: {detected_brand}<br>"
        f"Код: {barcode}<br>"
        f"----------------------------------------"
    )
    current_device_id += 1

def on_ui_voice_action():
    """Управляет записью на основе экранного триггера PyQt6"""
    global df_inventory
    
    # Проверяем, запущена ли сейчас запись в интерфейсе приложения
    # Если кнопка СТОП активна в GUI, значит мы сейчас нажимаем её, чтобы закончить запись
    # Но так как этот метод вызывается из stop_voice_recording в gui.py, 
    # мы просто останавливаем запись и забираем текст.
    recognized_text = voice.stop_manual_recording_and_recognize()
    
    if df_inventory is not None and not df_inventory.empty:
        last_index = df_inventory.index[-1]
        current_note = str(df_inventory.at[last_index, "Текущий статус / Примечание"])
        df_inventory.at[last_index, "Текущий статус / Примечание"] = current_note + f" | Голос: {recognized_text}"
        database.save_to_excel(df_inventory, OUTPUT_EXCEL)
        
    return recognized_text

def on_ui_start_recording_requested():
    """Просто запускает захват звука в voice.py"""
    voice.start_manual_recording()

def on_project_reset_requested():
    global df_inventory, current_device_id
    try:
        if os.path.exists(OUTPUT_EXCEL):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_path = f"getac_ai_inventory_archive_{timestamp}.xlsx"
            os.rename(OUTPUT_EXCEL, archive_path)
            
        current_device_id = 1
        df_inventory = pd.DataFrame(columns=EXCEL_COLUMNS)
        database.save_to_excel(df_inventory, OUTPUT_EXCEL)
        return True
    except Exception:
        return False

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    main_window = gui.GetacScannerApp(
        on_scan_callback=on_device_scanned, 
        on_voice_callback=on_ui_voice_action,
        on_reset_callback=on_project_reset_requested
    )
    
    # Небольшой хак: связываем нажатие старта в gui напрямую с модулем голоса
    main_window.start_voice_btn.clicked.connect(on_ui_start_recording_requested)
    
    main_window.show()
    sys.exit(app.exec())

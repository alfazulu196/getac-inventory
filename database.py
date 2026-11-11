# database.py
import os
import pandas as pd
from datetime import datetime
from config import EXCEL_COLUMNS

def init_database(filepath):
    """Загружает или создает чистый файл Excel"""
    if os.path.exists(filepath):
        try:
            return pd.read_excel(filepath)
        except Exception:
            return pd.DataFrame(columns=EXCEL_COLUMNS)
    else:
        df = pd.DataFrame(columns=EXCEL_COLUMNS)
        save_to_excel(df, filepath)
        return df

def save_to_excel(df_to_save, filepath):
    """Безопасное сохранение с корректным автоподгоном колонок"""
    try:
        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            df_to_save.to_excel(writer, index=False, sheet_name="Инвентаризация")
            worksheet = writer.sheets["Инвентаризация"]
            
            # Корректный перебор колонок openpyxl
            for col in worksheet.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = col[0].column_letter  # Исправлено: берем букву от первой ячейки столбца
                worksheet.column_dimensions[col_letter].width = max(max_len + 4, 18)
        print(f"[УСПЕХ EXCEL] Файл сохранен: {filepath}")
        return True
    except PermissionError:
        # Если Excel открыт, создаем копию
        backup_time = datetime.now().strftime("%H%M%S")
        alt_path = filepath.replace(".xlsx", f"_{backup_time}.xlsx")
        print(f"[ОБХОД EXCEL] Файл занят. Спасаем в копию: {alt_path}")
        return save_to_excel(df_to_save, alt_path)
    except Exception as e:
        print(f"[ОШИБКА EXCEL] {e}")
        return False

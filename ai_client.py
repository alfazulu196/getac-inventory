# ai_client.py
import requests
from config import OLLAMA_URL, MODEL_NAME

def analyze_barcode(scanned_str):
    """Определяет тип данных и бренд по строке штрихкода"""
    prompt = f"""
    Ты инженер-интегратор систем безопасности. Проанализируй строку со сканера: '{scanned_str}'.
    Определи тип данных по правилам:
    1. Если в строке 12 символов (MAC-адрес), напиши тип 'MAC-адрес'.
    2. Если есть буквы и цифры (серийник), напиши тип 'Серийный номер'.
    3. Если только цифры (товарный штрихкод, как EAN-13), напиши тип 'Модель'.

    Ответь СТРОГО одной строкой в формате (ничего другого не пиши):
    Тип | Бренд
    Например: Серийный номер | Hikvision
    Или: Модель | Неизвестно
    """
    try:
        response = requests.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False
        }, timeout=10)
        
        result_text = response.json().get("response", "").strip()
        if "|" in result_text:
            parts = result_text.split("|")
            return parts[0].strip(), parts[1].strip()
    except Exception:
        pass
    
    return "Модель", "Неизвестно"

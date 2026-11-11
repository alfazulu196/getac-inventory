# voice.py
import time
import sounddevice as sd
import numpy as np
import json
import os
from vosk import Model, KaldiRecognizer

MODEL_PATH = "model"
recording_stream = None
audio_data_list = []

if os.path.exists(MODEL_PATH) and os.path.isdir(MODEL_PATH):
    try:
        print("[ГОЛОС] Инициализация локальной модели Vosk...")
        vosk_model = Model(MODEL_PATH)
        recognizer = KaldiRecognizer(vosk_model, 16000)
        IS_SIMULATION = False
    except Exception as e:
        print(f"[ГОЛОС] Ошибка Vosk: {e}. Режим симуляции.")
        IS_SIMULATION = True
else:
    print("[ГОЛОС] Локальная папка 'model' не найдена. Режим симуляции.")
    IS_SIMULATION = True

def start_manual_recording():
    """Запускает аудиопоток с микрофона Getac при нажатии экранной кнопки"""
    global recording_stream, audio_data_list, recognizer
    if IS_SIMULATION:
        return
        
    audio_data_list = []
    # Сбрасываем прошлое состояние распознавателя
    recognizer = KaldiRecognizer(vosk_model, 16000)
    
    def callback(indata, frames, time_info, status):
        audio_data_list.append(indata.copy())
        
    # Открываем бесконечный поток записи звука
    recording_stream = sd.InputStream(samplerate=16000, channels=1, dtype='int16', callback=callback)
    recording_stream.start()
    print("[ГОЛОС] Запись запущена...")

def stop_manual_recording_and_recognize():
    """Останавливает аудиопоток и возвращает распознанный ИИ текст"""
    global recording_stream, audio_data_list, recognizer
    if IS_SIMULATION:
        time.sleep(0.5)
        return "1 этаж, серверная комната, шкаф у окна (демо-режим)"
        
    if recording_stream:
        recording_stream.stop()
        recording_stream.close()
        recording_stream = None
        print("[ГОЛОС] Запись остановлена. Начат анализ текста...")
        
    if not audio_data_list:
        return "Речь не распознана (тишина)"
        
    # Склеиваем все куски аудио из буфера памяти в один монолитный массив
    full_audio = np.concatenate(audio_data_list, axis=0)
    
    # Скармливаем весь массив ИИ Vosk за один проход
    recognizer.AcceptWaveform(full_audio.tobytes())
    
    final_result = json.loads(recognizer.FinalResult())
    text_output = final_result.get("text", "").strip()
    
    if not text_output:
        return "Речь не распознана (попробуйте говорить громче)"
        
    print(f"[ГОЛОС] Успешно распознано: '{text_output}'")
    return text_output

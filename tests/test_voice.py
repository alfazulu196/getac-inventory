# tests/test_voice.py
from inventory.config import VoiceConfig
from inventory.services.voice import VoiceService


def test_dictionary_replaces_terms():
    service = VoiceService(VoiceConfig())
    text = service.apply_dictionary("Камутатор стоит в шкафу, камиру повесили у входа")
    assert "коммутатор" in text
    assert "камеру" in text


def test_voice_disabled():
    service = VoiceService(VoiceConfig(enabled=False))
    assert service.load() is False
    assert service.available is False
    assert "отключён" in service.status


def test_missing_model_is_reported(tmp_path):
    service = VoiceService(VoiceConfig(model_path=str(tmp_path / "missing")))
    assert service.load() is False
    assert "не найдена" in service.status

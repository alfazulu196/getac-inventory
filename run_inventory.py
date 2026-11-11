# -*- coding: utf-8 -*-
"""Запуск новой версии приложения инвентаризации.

Варианты:
    python run_inventory.py
    python -m inventory.app
Старый прототип по-прежнему запускается как раньше: python main.py
"""
from __future__ import annotations

import sys

from inventory.app import main

if __name__ == "__main__":
    sys.exit(main())

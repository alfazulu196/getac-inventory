@echo off
rem ============================================================
rem  Deploys the InventorySurvey vision model (qwen2.5vl:7b)
rem  to this PC via the internet: downloads the GGUF weights
rem  from HuggingFace, verifies SHA-256, builds the model.
rem  Run on the Getac V110 while it has internet access.
rem  Guide: CHEK_LIST_VYEZD.md, section 1 (variant A).
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_7b_v110.ps1" %*

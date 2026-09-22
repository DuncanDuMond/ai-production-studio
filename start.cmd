@echo off
cd /d "%~dp0"
set DEEPEVAL_TELEMETRY_OPT_OUT=YES
set RAGAS_DO_NOT_TRACK=true
set HAYSTACK_TELEMETRY_ENABLED=false
uv run --frozen python -m uvicorn studio.app:app --host 127.0.0.1 --port 8765

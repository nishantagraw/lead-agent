@echo off
cd /d "c:\Users\megha\infinite club\lead-agent"
start "" python lead_agent_frontend.py
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5000

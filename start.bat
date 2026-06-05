@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Запуск бота botxd2 (@Bestriiarss_bot)
echo ============================================
python bot.py
echo.
echo Бот остановлен. Нажмите любую клавишу для выхода.
pause >nul

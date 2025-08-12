import sys, io, ctypes

# 1) Tell Windows to switch the console to UTF-8 code page
#    (65001 is the UTF-8 code page on Windows)
ctypes.windll.kernel32.SetConsoleOutputCP(65001)
ctypes.windll.kernel32.SetConsoleCP(65001)

# 2) Rewrap stdout/stderr as UTF-8 text streams
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import asyncio
import logging

from utils.logger import setup_logger
from mt5_executor import connect
from telegram_handler import run_listener
from admin_panel import start as start_admin

if __name__ == "__main__":
    # initialize our logging (writes trading_bot.log, mt5_detailed.log, console)
    setup_logger()

    # connect to MT5 (starts terminal, logs in)
    if not connect():
        logging.error("MT5 connection failed - exiting")
        exit(1)

    # start your admin/dashboard UI (Streamlit or whatever)
    start_admin()

    logging.info("Starting Telegram MT5 bot…")
    # launch the Telegram listener
    asyncio.run(run_listener())

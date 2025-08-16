import logging
import colorlog
import MetaTrader5 as mt5
import sys  

_logging_enabled = False  # start “detailed” off

def set_logging_enabled(enabled: bool):
    """Toggle detailed DEBUG logs (ours + MT5). Errors always pass."""
    global _logging_enabled
    _logging_enabled = enabled

    # Flip the MT5 library’s own logger
    mt5_logger = logging.getLogger("MetaTrader5")
    mt5_logger.setLevel(logging.DEBUG if enabled else logging.WARNING)

class ToggleFilter(logging.Filter):
    def filter(self, record):
        # Always allow errors & above
        if record.levelno >= logging.ERROR:
            return True
        # Otherwise only if detailed logging is on
        return _logging_enabled

def setup_logger():
    if sys.platform.startswith('win'):
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', buffering=1)
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', buffering=1) 

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Clear existing handlers
    if root.hasHandlers():
        root.handlers.clear()

    # 1) Main info log: always INFO+
    fh_info = logging.FileHandler("trading_bot.log", encoding='utf-8')  
    fh_info.setLevel(logging.INFO)
    fh_info.setFormatter(
        logging.Formatter("%(asctime)s – %(levelname)s – %(message)s")
    )
    root.addHandler(fh_info)

    # 2) Detailed debug log: DEBUG+, but filter by toggle
    fh_dbg = logging.FileHandler("mt5_detailed.log", encoding='utf-8') 
    fh_dbg.setLevel(logging.DEBUG)
    fh_dbg.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    fh_dbg.addFilter(ToggleFilter())
    root.addHandler(fh_dbg)

    # 3) Console: DEBUG+ with same toggle
    ch = colorlog.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s: %(message)s",
            log_colors={
                'DEBUG':    'cyan',
                'INFO':     'green',
                'WARNING':  'yellow',
                'ERROR':    'bold_red',
                'CRITICAL': 'bold_red,bg_white',
            }
        )
    )
    ch.addFilter(ToggleFilter())
    root.addHandler(ch)
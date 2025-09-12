import logging
import colorlog
import MetaTrader5 as mt5
import sys  

# Add custom DETAILED log level (more verbose than DEBUG)
logging.DETAILED = 5
logging.addLevelName(logging.DETAILED, "DETAILED")

# Add a convenience method to Logger class
def detailed(self, message, *args, **kws):
    self._log(logging.DETAILED, message, args, **kws)
logging.Logger.detailed = detailed

# Add module-level function for consistency with logging.info, etc.
def module_detailed(msg, *args, **kwargs):
    logging.log(logging.DETAILED, msg, *args, **kwargs)
logging.detailed = module_detailed

_logging_enabled = False  # start “detailed” off

def set_logging_enabled(enabled: bool):
    """Toggle detailed DEBUG logs (ours + MT5). Errors always pass."""
    global _logging_enabled
    _logging_enabled = enabled

    # Flip the MT5 library’s own logger
    mt5_logger = logging.getLogger("MetaTrader5")
    mt5_logger.setLevel(logging.DEBUG if enabled else logging.WARNING)

def return_detailed_logging():
    return _logging_enabled

class ToggleFilter(logging.Filter):
    def filter(self, record):
        # Always allow INFO & above
        if record.levelno >= logging.INFO:
            return True
        # Allow levels below INFO (including DETAILED and DEBUG) only if detailed logging is on
        return _logging_enabled

def setup_logger():
    if sys.platform.startswith('win'):
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', errors='replace', buffering=1)
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', buffering=1) 

    root = logging.getLogger()
    root.setLevel(logging.DETAILED)  # Set to the lowest custom level to allow DETAILED logs

    # Clear existing handlers
    if root.hasHandlers():
        root.handlers.clear()

    # Main log file: INFO+ always, lower levels (DETAILED/DEBUG) if enabled
    fh = logging.FileHandler("trading_bot.log", encoding='utf-8')  
    fh.setLevel(logging.DETAILED)
    fh.setFormatter(
        logging.Formatter("%(asctime)s – %(levelname)s – %(message)s")
    )
    fh.addFilter(ToggleFilter())
    root.addHandler(fh)

    # New detailed log file: Always logs DETAILED+ (including DEBUG, INFO, etc.)
    fh_detailed = logging.FileHandler("detailed_log.log", encoding='utf-8')  
    fh_detailed.setLevel(logging.DETAILED)
    fh_detailed.setFormatter(
        logging.Formatter("%(asctime)s – %(levelname)s – %(message)s")
    )
    # No filter: always logs DETAILED and above
    root.addHandler(fh_detailed)

    # Console: Lower levels (DETAILED/DEBUG) with toggle, colors updated for DETAILED
    ch = colorlog.StreamHandler()
    ch.setLevel(logging.DETAILED)
    ch.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s: %(message)s",
            log_colors={
                'DETAILED': 'blue',
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
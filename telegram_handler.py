import re, json, logging, pathlib, winsound
import MetaTrader5 as mt5
from telethon import TelegramClient, events
from mt5_executor import modify_by_symbol, send_order, close_pos, modify_position, resolve_symbol, load_broker_creds

BASE_DIR      = pathlib.Path(__file__).parent
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"
CRED_PATH     = BASE_DIR / "config" / "credentials.json"  
logging.getLogger("telethon").setLevel(logging.WARNING)

# Load Telegram credentials
creds = json.load(open(CRED_PATH))
group_ids_str = creds.get('group_ids', [])
group_ids_full = [int(gid.strip()) for gid in re.split(r'[, \n]+', ','.join(group_ids_str)) if gid.strip()]  # Parse list flexibly
active_group_index = creds.get('active_group_index', 0)

client = TelegramClient('session', creds['api_id'], creds['api_hash'])

# Load settings
settings = json.load(open(SETTINGS_PATH))
listen_to_all = settings.get('listen_to_all_channels', True)

if listen_to_all:
    active_chats = group_ids_full
    logging.info(f"[TG] Listen to all: {len(active_chats)} channels active")
else:
    if group_ids_full and 0 <= active_group_index < len(group_ids_full):
        active_chats = [group_ids_full[active_group_index]]
        logging.info(f"[TG] Single channel active: index {active_group_index} (ID: {active_chats[0]})")
    else:
        active_chats = []
        logging.warning("[TG] No active channel selected (invalid index)")

if not active_chats:
    logging.error("[TG] No channels to listen to. Exiting.")
    exit(1)

# Play a beep on errors
def alert_sound():
    p = pathlib.Path(__file__).parent / "audio" / "error.wav"
    winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)
    
# Regex for buy/sell opens: match "Ich kaufe|verkaufe" or "I Buy/Sell", symbol, optional CALL/PUT + optional strike
trade_re = re.compile(
    r"(?:Ich\s+(Kaufe|Verkaufe)|I\s+(Buy|Sell))\s+"
    r"([A-Za-z0-9/._]+)"                # symbol
    r"(?:\s+(Call|Put)(?:\s*(\d+))?)?",   # optional CALL/PUT + optional strike
    re.IGNORECASE
)

# Regex for closes
close_re = re.compile(
    r"(?:Ich\s+schließe|CLOSE)\s+"
    r"([A-Za-z0-9/._]+)"                # symbol
    r"(?:\s+(Call|Put)(?:\s*(\d+))?)?",   # optional CALL/PUT + optional strike
    re.IGNORECASE
)

# Regex for setting SL
sl_symbol_re = re.compile(
    r"Ich setze den SL bei\s+"
    r"([A-Za-z0-9/.]+)"                # symbol
    r"(?:\s+(Call|Put)(?:\s*(\d+))?)?"    # optional CALL/PUT + optional strike
    r"\sauf\s([\d.]+)",           # SL price
    re.IGNORECASE
)

tp_symbol_re = re.compile(
    r"(?:Ich setze den TP bei)\s+([A-Za-z0-9/.]+)"  # symbol
    r"(?:\s*(Call|Put))?\s*"                         # optional Call/Put
    r"(?:\s*(\d+))?\sauf\s([\d.]+)",               # optional strike + TP price
    re.IGNORECASE
)

# Inline SL/TP
sl_re = re.compile(r"SL[: ]+([\d.]+)", re.IGNORECASE)
tp_re = re.compile(r"TP[: ]+([\d.]+)", re.IGNORECASE)

# Multiplier flag
mult_re = re.compile(r"maximalen Multiplikator", re.IGNORECASE)

# Call/Put detector
put_call_re = re.compile(r"\b(call|put)\b", re.IGNORECASE)

# Persistent SL/TP state
state = {"sl": 0.0, "tp": 0.0}

# Listen to active_chats (list, could be 1 or more)
@client.on(events.NewMessage(chats=active_chats))
async def handler(ev):
    msg = ev.raw_text.strip()
    settings = json.load(open(SETTINGS_PATH))  # Reload in case changed, but usually static
    logging.info("[TG] Msg from chat %s: %r" % (ev.chat_id, msg))
    # Determine multiplier and active broker
    use_max = bool(mult_re.search(msg))
    active, _ = load_broker_creds()
    # 1) OPEN trade
    if m := trade_re.search(msg):
        verb = (m.group(1) or m.group(2)).lower()
        action = 'buy' if verb in ('kaufe','buy') else 'sell'
        symbol_txt, opt, strike = m.group(3), m.group(4), m.group(5)
        # Check if PUT/CALL and setting
        if (opt or put_call_re.search(msg)) and not settings['accept_PUT_CALL']:
            logging.info(f"[SIGNAL] Ignored OPEN {action.upper()} {symbol_txt} {opt or ''} strike={strike or '—'} because accept_PUT_CALL is False")
            return
        # Resolve symbol
        try:
            symbol = resolve_symbol(symbol_txt)
        except ValueError as e:
            logging.error(e)
            alert_sound()
            return
        logging.info(f"[SIGNAL] OPEN {action.upper()} {symbol} {opt or ''} strike={strike or '—'} ×{'MAX' if use_max else 'std'}")
        # Send the order at market price
        res = send_order(
            action=action,
            symbol=symbol,
            price=0,
            sl=state['sl'],
            tp=state['tp'],
            multiplier=use_max,
            opt=opt,
            strike=float(strike) if strike else None
        )
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            logging.error(f"OPEN failed: {res.comment}")
            alert_sound()
        return
    # 2) CLOSE trade
    if m := close_re.search(msg):
        symbol_txt, opt, strike = m.group(1), m.group(2), m.group(3)
        # Check if PUT/CALL and setting
        if (opt or put_call_re.search(msg)) and not settings['accept_PUT_CALL']:
            logging.info(f"[SIGNAL] Ignored CLOSE {symbol_txt} {opt or ''} strike={strike or '—'} because accept_PUT_CALL is False")
            return
        try:
            symbol = resolve_symbol(symbol_txt)
        except ValueError as e:
            logging.error(e)
            alert_sound()
            return
        logging.info(f"[SIGNAL] CLOSE {symbol} {opt or ''} strike={strike or '—'}")
        res = close_pos(symbol)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            logging.error(f"CLOSE failed: {res.comment}")
            alert_sound()
        return
    if m := sl_symbol_re.search(msg):
        symbol_txt, opt, strike, slv = m.group(1), m.group(2), m.group(3), float(m.group(4))
        # Check if PUT/CALL and setting
        if (opt or put_call_re.search(msg)) and not settings['accept_PUT_CALL']:
            logging.info(f"[SIGNAL] Ignored SET SL ALL {symbol_txt} {opt or ''} strike={strike or '—'} because accept_PUT_CALL is False")
            return
        try:
            symbol = resolve_symbol(symbol_txt)
        except ValueError as e:
            logging.error(e)
            alert_sound()
            return
        logging.info(f"[SIGNAL] SET SL ALL {symbol} {opt or ''} strike={strike or '—'} → {slv}")
        res = modify_by_symbol(symbol, sl=slv)
        if not res or getattr(res, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            logging.error(f"[MT5] SL modify failed for {symbol}: {getattr(res, 'retcode', 'unknown')} {getattr(res, 'comment', '')}")
            alert_sound()
            return
    if m := tp_symbol_re.search(msg):
        sym_txt, opt, strike, tpv = m.group(1), m.group(2), m.group(3), float(m.group(4))
        # Check if PUT/CALL and setting
        if (opt or put_call_re.search(msg)) and not settings['accept_PUT_CALL']:
            logging.info(f"[SIGNAL] Ignored MOD TP ALL {sym_txt} {opt or ''} strike={strike or '—'} because accept_PUT_CALL is False")
            return
        try:
            symbol = resolve_symbol(sym_txt)
        except ValueError as e:
            logging.error(e)
            alert_sound()
            return
        logging.info(f"[SIGNAL] MOD TP ALL {symbol} {opt or ''} strike={strike or '—'} → {tpv}")
        res = modify_by_symbol(symbol, tp=tpv)
        if not res or getattr(res, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            logging.error(f"[MT5] TP modify failed for {symbol}: {getattr(res, 'retcode', 'unknown')} {getattr(res, 'comment', '')}")
            alert_sound()
            return
    if m := sl_re.search(msg):
        if put_call_re.search(msg) and not settings['accept_PUT_CALL']:
            logging.info(f"[SIGNAL] Ignored STATE SL because contains CALL/PUT and accept_PUT_CALL is False")
            return
        state['sl'] = float(m.group(1))
        logging.info(f"[SIGNAL] STATE SL={state['sl']}")
        return
    if m := tp_re.search(msg):
        if put_call_re.search(msg) and not settings['accept_PUT_CALL']:
            logging.info(f"[SIGNAL] Ignored STATE TP because contains CALL/PUT and accept_PUT_CALL is False")
            return
        state['tp'] = float(m.group(1))
        logging.info(f"[SIGNAL] STATE TP={state['tp']}")
        return
    logging.debug("[TG] no match")

async def run_listener():
    try:
        # Start and log who & where we're listening
        await client.start()
        me = await client.get_me()
        logging.info(f"[TG] Logged in as {me.username} (id={me.id})")
        # Log active channels
        for gid in active_chats:
            try:
                ch = await client.get_entity(gid)
                title = getattr(ch, "title", None) or getattr(ch, "username", None) or str(gid)
                logging.info(f"[TG] Listening to: {title}")
            except ValueError:
                logging.warning(f"[TG] Could not resolve channel id {gid} (not in your dialogs or access denied)")
        await client.run_until_disconnected()
    except Exception as e:
        logging.exception("[TG] Unexpected connection error")
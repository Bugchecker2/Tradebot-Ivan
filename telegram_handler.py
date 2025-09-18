import re, json, logging, pathlib, winsound, asyncio
import hashlib
import MetaTrader5 as mt5
from telethon import TelegramClient, events
from mt5_executor import modify_by_symbol, send_order, close_pos, modify_position, resolve_symbol, load_broker_creds

BASE_DIR      = pathlib.Path(__file__).parent
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"
CRED_PATH     = BASE_DIR / "config" / "credentials.json"  
logging.getLogger("telethon").setLevel(logging.WARNING)

# Load Telegram credentials (for client init)
creds = json.load(open(CRED_PATH))
client = TelegramClient('session', creds['api_id'], creds['api_hash'])

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
    r"([A-Za-z0-9/.]+)"                # symbol
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

# The event handler (without decorator - will be added dynamically)
async def the_handler(event):
    msg = event.raw_text.strip()
    settings = json.load(open(SETTINGS_PATH))  # Reload in case changed, but usually static
    logging.info("[TG] Msg from chat %s: %r" % (event.chat_id, msg))
    # Determine multiplier and active broker
    use_max = bool(mult_re.search(msg))
    active, _ = load_broker_creds()
    # 1) OPEN trade
    if m := trade_re.search(msg):
        verb = (m.group(1) or m.group(2)).lower()
        symbol_txt, opt, strike = m.group(3), m.group(4), m.group(5)
        if opt: opt = m.group(4).lower()
        action = 'buy' if verb in ('kaufe','buy') else 'sell'
        # If buy at Put then this is sell !!  (however SL/TP settings don't fit -> better deactivate PUT_CALL) 
        if (opt == 'put' and verb in ('kaufe', 'buy')): action = 'sell'
        # Check if PUT/CALL and setting / Ignore Put/Call at sell
        if (opt or put_call_re.search(msg)) and (not settings['accept_PUT_CALL'] or (settings['accept_PUT_CALL'] and verb in ('verkaufe','sell'))):
            logging.info(f"[SIGNAL] Ignored OPEN {action.upper()} {symbol_txt} {opt or ''} strike={strike or '—'} because accept_PUT_CALL is False or it's a sell")
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

async def update_listener_chats():
    client.remove_event_handler(the_handler, events.NewMessage)
    creds = json.load(open(CRED_PATH))
    group_ids_full = [int(gid.strip()) for gid in creds.get('group_ids', []) if gid.strip()]
    active_group_index = creds.get('active_group_index', 0)
    settings = json.load(open(SETTINGS_PATH))
    listen_to_all = settings.get('listen_to_all_channels', True)
    
    # Determine potential chats
    if listen_to_all:
        potential_chats = group_ids_full
    else:
        if group_ids_full and 0 <= active_group_index < len(group_ids_full):
            potential_chats = [group_ids_full[active_group_index]]
            logging.info(f"[TG] Config update: Single channel active: index {active_group_index} (ID: {potential_chats[0]})")
        else:
            potential_chats = []
            logging.warning("[TG] Config update: No active channel selected (invalid index)")
    
    if not potential_chats:
        logging.warning("[TG] No channels to listen to after update. No handler added.")
        return
    valid_chats = []
    for gid in potential_chats:
        try:
            entity = await client.get_entity(gid)
            title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(gid)
            valid_chats.append(gid)
        except ValueError as e:
            logging.warning(f"[TG] Could not access channel id {gid} (not in dialogs or access denied): {e}")
        except Exception as e:
            logging.warning(f"[TG] Unexpected error resolving channel id {gid}: {e}")
    
    if not valid_chats:
        logging.error("[TG] No accessible channels after update. Check your membership/access. No handler added.")
        return
    # Add the event handler for valid chats
    client.add_event_handler(the_handler, events.NewMessage(chats=valid_chats))

def config_hash(paths):
    m = hashlib.md5()
    for p in paths:
        with open(p, 'rb') as f:
            m.update(f.read())
    return m.hexdigest()

async def monitor_config():
    last = config_hash([CRED_PATH, SETTINGS_PATH])
    while True:
        await asyncio.sleep(10)
        now = config_hash([CRED_PATH, SETTINGS_PATH])
        if now != last:
            logging.info("Config changed -> updating")
            await update_listener_chats()
            last = now
            
async def run_listener():
    monitor_task = None
    while True:
        try:
            await client.start()
            me = await client.get_me()
            logging.info(f"[TG] Logged in as {me.username} (id={me.id})")
            await update_listener_chats()
            monitor_task = asyncio.create_task(monitor_config())
            await client.run_until_disconnected()
        except ConnectionError as e:
            logging.error(f"Connection failed: {e}. Retrying in 30s...")
            await asyncio.sleep(30)
        except Exception as e:
            logging.exception("[TG] Unexpected error")
            break
        finally:
            if monitor_task:
                monitor_task.cancel()
            await client.disconnect()
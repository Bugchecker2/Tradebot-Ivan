from collections import Counter
import os
import json
import math
import logging
import pathlib
import re
import MetaTrader5 as mt5
import winsound
from utils.symbols_alias import GROUPED_ALIASES

# — Global state —
INITIAL_BALANCE: float = None
_INITIALIZED: bool = False

# — Paths —
BASE_DIR      = pathlib.Path(__file__).parent
LEV_PATH     = BASE_DIR / "config" / "lever_map.json"
try:
    LEVERAGE_MAP = json.loads(open(LEV_PATH, encoding="utf-8").read())
except Exception:
    LEVERAGE_MAP = {"DEFAULT": 10}

CONFIG_PATH   = BASE_DIR / "config" / "mt5_credentials.json"
SETTINGS_PATH = BASE_DIR / "config" / "settings.json"
TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# — Load bot settings —
def load_settings() -> dict:
    return json.load(open(SETTINGS_PATH, encoding="utf-8"))

# — Beep on errors —
def alert_sound():
    p = BASE_DIR / "audio" / "error.wav"
    winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)


# — Beep on success —
def success_sound():
    p = BASE_DIR / "audio" / "success.wav"
    winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)
# — Pick up active broker creds —
def load_broker_creds() -> tuple[str, dict]:
    data = json.load(open(CONFIG_PATH, encoding="utf-8"))
    active = data.get("active")
    if not active or active not in data:
        alert_sound()
        raise KeyError("Active broker not set or not found in mt5_credentials.json")
    return active, data[active]
# — Initialize / login MT5 once per run —
def connect() -> bool:
    global INITIAL_BALANCE, _INITIALIZED
    if _INITIALIZED:
        return True

    # 1) Load which broker is active
    try:
        active, creds = load_broker_creds()
    except Exception as e:
        logging.error(f"[MT5] {e}")
        alert_sound()
        return False

    # 2) Verify terminal executable exists
    if not os.path.exists(TERMINAL_PATH):
        logging.error(f"[MT5] terminal not found: {TERMINAL_PATH}")
        alert_sound()
        return False

    # 3) Kill any existing session
    mt5.shutdown()

    # 4) Initialize the MT5 terminal
    if not mt5.initialize(path=TERMINAL_PATH):
        code, msg = mt5.last_error()
        logging.error(f"[MT5] initialize() failed ({code}): {msg}")
        alert_sound()
        return False

    # 5) Perform login with the active broker’s credentials
    if not mt5.login(
        login=int(creds["account_id"]),
        password=creds["password"],
        server=creds["server"]
    ):
        code, msg = mt5.last_error()
        logging.error(f"[MT5] login() failed ({code}): {msg}")
        alert_sound()
        return False

    logging.info(f"[MT5] Connected to {active} ({creds['server']})")

    # 6) Cache starting balance
    INITIAL_BALANCE = mt5.account_info().balance
    logging.info(f"[MT5] Base capital set to {INITIAL_BALANCE:.2f}")

    _INITIALIZED = True
    return True

# — Symbol resolution helper —
ALIAS_TO_SYMBOL = {alias.upper(): symbol for symbol, aliases in GROUPED_ALIASES.items()for alias in aliases + [symbol]}

def resolve_symbol(sym: str) -> str:
    raw = sym.strip().upper()
    norm = raw.replace("/", "")

    for cand in (norm, raw):
        info = mt5.symbol_info(cand)
        if info:
            if not info.visible:
                mt5.symbol_select(cand, True)
            return cand

    canonical = ALIAS_TO_SYMBOL.get(norm) or ALIAS_TO_SYMBOL.get(raw)
    if canonical:
        info = mt5.symbol_info(canonical)
        if info:
            if not info.visible:
                mt5.symbol_select(canonical, True)
            return canonical

    candidates = []
    for s in mt5.symbols_get():
        name = s.name.upper()
        desc = getattr(s, "description", "").upper()
        desc_words = desc.split()

        if (raw in name or norm in name or
            raw in desc_words or norm in desc_words):
            candidates.append(s)

    if candidates:
        best = min(candidates, key=lambda s: len(s.description))
        if not best.visible:
            mt5.symbol_select(best.name, True)
        return best.name

    alert_sound()
    raise ValueError(f"No symbol_info for '{sym}'")

# — Build option ticker —
def build_option_symbol(base: str, strike: float, opt: str) -> str:
    side = "C" if opt.lower().startswith("c") else "P"
    return f"{base.upper()}-{int(strike)}-{side}"

import math
import MetaTrader5 as mt5
from mt5_executor import alert_sound, INITIAL_BALANCE


def switch_broker(new_broker:str):
    try:
        data = json.load(open(CONFIG_PATH, encoding="utf-8"))
    except Exception as e:
        logging.error()
        alert_sound()
        return False
    if new_broker not in data:
        logging.error(f"[MT5] Broker '{new_broker}' not found in config")
        alert_sound()
        return False
    data["active"]=new_broker
    try:
        with open(CONFIG_PATH, 'w', encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"[MT5] Failed to save config: {e}")
        alert_sound()
        return False
    logging.info(f"[MT5] Switched active broker to {new_broker}")
    global _INITIALIZED
    _INITIALIZED = False
    return connect()

def search_leverage_in_map(name: str) -> float:
    with open(CONFIG_PATH, 'r', encoding="utf-8") as f:
        data = json.load(f)
    active_broker = data.get("active")
    if not active_broker:
        logging.error("[Get leverage] No active broker")
        return 10.0
    active_config = data.get(active_broker)
    if not active_config:
        logging.error("[Get leverage] No active config")
        return 10.0
    name_file = active_config.get("leverage_json_file")
    if not name_file:
        logging.error("[Get leverage] No name file")
        return 10.0
    LEVERAGE_MAP_PATH = BASE_DIR / "leverage_maps" / name_file
    if not LEVERAGE_MAP_PATH.exists():
        logging.warning("[Get leverage] Fallback LEVERAGE_MAP_PATH not exist")
        return 10.0
    
    with open(LEVERAGE_MAP_PATH, 'r', encoding="utf-8") as f:
        leverage_data = json.load(f)
    sym = name.upper()
    for category in leverage_data:
        if category == "platform" or category == "Stocks":  #SKIP
            continue
        items = leverage_data[category]
        if isinstance(items, list):
            for item in items:
                instr = item.get("Instrument", "").upper()
                if instr == sym:
                    return float(item["Leverage"])
    logging.warning("[Get leverage] Fallback No Symbol in Leverage Map")
    return 10.0

def get_leverage(symbol: str) -> float:
    sym = symbol.upper()
    info = mt5.symbol_info(sym)
    if not info:
        return 10.0
    
    with open(CONFIG_PATH, 'r', encoding="utf-8") as f:
        data = json.load(f)
    active = data.get("active")
    if not active:
        return 10.0  

    broker_name = active.lower()

    value = getattr(info, "path", "")
    if isinstance(value, bytes):
        path = value.decode(errors="ignore")
    else:
        path = str(value) if value is not None else ""
    path_norm = path.lower().strip()
    
    lev = search_leverage_in_map(sym)
    if lev != 10.0:
        return lev
    
    # Fallback based on broker type
    if "metaquotes" in broker_name:
        return 1.0
    elif "demo" in broker_name:
        active_config = data.get(active)
        if not active_config:
            return 10.0
        name_file = active_config.get("leverage_json_file")
        if not name_file:
            return 10.0
        LEVERAGE_MAP_PATH = BASE_DIR / "leverage_maps" / name_file
        if not LEVERAGE_MAP_PATH.exists():
            return 10.0
        with open(LEVERAGE_MAP_PATH, 'r', encoding="utf-8") as f:
            leverage_data = json.load(f)
        leverages = []
        for category in leverage_data:
            if category == "platform": 
                continue
            items = leverage_data[category]
            if isinstance(items, list):
                for item in items:
                    leverages.append(float(item["Leverage"]))
        if not leverages:
            logging.warning("[Get leverage] No leverages found in map for demo fallback")
            return 10.0
        most_common = Counter(leverages).most_common(1)
        if most_common:
            return most_common[0][0]
        return 10.0
    else:
        # Real accounts fallback
        segments = [s.strip() for s in re.split(r'[\\/,\;\|\-]+', path_norm) if s.strip()]
        rules = [
            (["fx crosses", "fx exotics"], 20.0),
            (["fx majors"], 30.0),
            (["spot metals/XAUEUR"], 300.0), 
            (["crypto"], 2.0),
            (["crypto"], 2.0),
            (["stocks"], 5.0),  # Include stocks in rules for consistency
        ]
        if "stock" in path_norm:  
            return 5.0
        for seg in segments:
            for keywords, lev_val in rules:
                for kw in keywords:
                    if (
                        seg == kw
                        or seg.startswith(kw + " ")
                        or seg.startswith(kw)
                        or seg.endswith(" " + kw)
                        or seg.endswith(kw)
                    ):
                        return lev_val
        logging.warning("[Get leverage] Fallback at path, no exact path for symbol")
        return 10.0

def calc_lot(symbol: str, settings: dict, balance: float, price: float, 
             start_capital: float, free_margin: float) -> float:
    """
    LOT = (AvailableMoney * lot_percent * leverage) / (price * contract_size)
    Margin
    Margin provided function order_calc_margin

    AvailableMoney:
      • if reinvest=False & lot_method=='percent_remaining': 
            start_capital – sum(margin of all open positions)
      • if reinvest=False & lot_method=='percent_start': 
            start_capital
      • if reinvest=True  & lot_method=='percent_remaining': 
            free_margin
      • if reinvest=True  & lot_method=='percent_start': 
            current balance
    """
    info = mt5.symbol_info(symbol)
    if not info or info.trade_contract_size<=0 or price<=0:
        return settings.get("default_lot", 0.01)

    # 1) Determine available money
    reinvest = settings.get("reinvest", False)
    method   = settings.get("lot_method", "percent_remaining")
    if reinvest:
        avail = free_margin if method=="percent_remaining" else balance
    else:
        sum_margin = 0.0
        for p in mt5.positions_get() or []: #TODO: handle if 0 positions on account
            p_info = mt5.symbol_info(p.symbol)
            if p_info:
                p_cs = p_info.trade_contract_size
                p_lev = get_leverage(p.symbol)
                margin = (p.volume * p_cs * p.price_open) / p_lev if p_lev > 0 else 0
                margin_mt5 = mt5.order_calc_margin(p.type, p.symbol, p.volume, p.price_open)
                sum_margin += margin
            else:
                logging.error("[Margin Calculation] no info for symbol adde defaul margine")
                sum_margin += p.volume * 100000 * p.price_open / 30  # or default
        avail = start_capital - sum_margin if method == "percent_remaining" else start_capital
        avail = max(avail, 0.0)
    logging.info(f"[Margin Calculation] avail. money = {avail}")

    pct      = settings["lot_percent"] / 100.0
    cap_pct  = settings.get("max_cap_percent", 20) / 100.0
    risk_amt = min(avail * pct, start_capital * cap_pct)

    # 3) Leverage & contract size
    leverage      = get_leverage(symbol)
    logging.info(f"[LEVERAGE TEST] Calculated leverage = {leverage}")
    contract_size = info.trade_contract_size

    # 4) Raw lot formula
    raw_lot = (risk_amt * leverage) / (price * contract_size)

    # 5) Snap to broker’s steps
    step, vmin, vmax = info.volume_step, info.volume_min, info.volume_max
    qty = max(vmin, min(math.floor(raw_lot/step)*step, vmax))
    return round(qty, 8)

# — Main trading function —
def send_order(
    action: str,
    symbol: str,
    price: float = 0,
    tp: float   = 0,
    sl: float   = 0,
    comment_id: str = None,
    multiplier: bool  = False, #no need
    opt: str         = None,
    strike: float    = None
) -> object:
    if not connect():
        alert_sound()
        return fake(-9, "init failed")

    # Option symbol handling
    if opt and strike is not None:
        symbol = build_option_symbol(symbol, strike, opt)

    info = mt5.symbol_info(symbol)
    if not info or info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
        logging.error(f"[MT5] cannot trade {symbol}")
        alert_sound()
        return fake(-1, "disabled")

    # Market price fallback
    if price <= 0:
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logging.error(f"[MT5] no market tick for {symbol}")
            alert_sound()
            return fake(-3, "no price")
        price = tick.ask if action.lower() == "buy" else tick.bid

    # — Multiplier lot: use broker’s margin requirement —
    settings = load_settings()
    acct_info   = mt5.account_info()
    balance     = acct_info.balance
    free_margin = acct_info.margin_free
    start_cap   = INITIAL_BALANCE
    # if multiplier:
    #     # compute how much volume your risk % buys given initial margin requirement
    #     pct        = settings["lot_percent"] / 100.0
    #     base       = INITIAL_BALANCE if settings.get("lot_method") == "percent_start" else balance
    #     risk_amt   = min(base * pct, base * (settings.get("max_cap_percent", 20)/100.0))
    #     # margin_initial is the margin required per one contract; fallback to trade_contract_size*price
    #     margin_req = getattr(info, "margin_initial", 0)
    #     if not margin_req or margin_req <= 0:
    #         margin_req = info.trade_contract_size * price
    #     raw = risk_amt / margin_req
    #     step, vmin, vmax = info.volume_step, info.volume_min, info.volume_max
    #     lot        = max(vmin, min(math.floor(raw/step)*step, vmax))
    #     logging.info(f"[MT5] MULT lot={lot:.4f} (risk={settings['lot_percent']}%, margin_req={margin_req:.4f}) for {symbol}@{price:.4f}")
    # else:
    lot = calc_lot(symbol, settings, balance, price, start_cap, free_margin)
    logging.info(f"[MT5] lot={lot:.4f} for {symbol}@{price:.4f}")

    # Build & send
    req = {
        "action":     mt5.TRADE_ACTION_DEAL,
        "symbol":     symbol,
        "volume":     lot,
        "type":       mt5.ORDER_TYPE_BUY if action.lower()=="buy" else mt5.ORDER_TYPE_SELL,
        "price":      price,
        "sl":         sl,
        "tp":         tp,
        "deviation":  20,
        "magic":      234000,
        "comment":    "TeleBot",
        "type_time":  mt5.ORDER_TIME_GTC
    }
    if comment_id:
        req["comment_id"] = comment_id

    for fm in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = fm
        logging.info(f"[MT5] trying fill_mode={fm}")
        res = mt5.order_send(req)
        if not res:
            logging.error("[MT5] send returned None")
            alert_sound()
            return fake(-2, "none")
        if res.retcode != 10030:
            logging.debug(f"[MT5] result {res.retcode} {res.comment}")
            success_sound()
            return res
        logging.warning(f"[MT5] unsupported fill_mode={fm}")

    return fake(10030, "unsupported")

# — Close positions —
def close_pos(symbol: str) -> object:
    if not connect():
        alert_sound()
        return fake(-9, "init failed")
    
    # Check if symbol exists
    try:
        symbol = resolve_symbol(symbol)
    except ValueError as e:
        logging.error(f"[MT5] {e}")
        alert_sound()
        return fake(-1, "no symbol")
    
    pos_list = mt5.positions_get(symbol=symbol) or []
    if not pos_list:
        logging.error(f"[MT5] no pos for {symbol}")
        alert_sound()
        return fake(-4, "none")
    for p in pos_list:
        opp   = mt5.ORDER_TYPE_SELL if p.type==mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(p.symbol)
        if not tick:
            logging.error(f"[MT5] no tick for {p.symbol}")
            alert_sound()
            continue
        price = tick.bid if opp==mt5.ORDER_TYPE_SELL else tick.ask
        req = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "position":  p.ticket,
            "symbol":    p.symbol,
            "volume":    p.volume,
            "type":      opp,
            "price":     price,
            "deviation": 20,
            "magic":     p.magic,
            "comment":   "Close",
            "type_time": mt5.ORDER_TIME_GTC
        }
        for fm in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            req["type_filling"] = fm
            res = mt5.order_send(req)
            if res and res.retcode != 10030:
                success_sound()
                return res
    return fake(10030, "none")

# — Modify existing position —
def modify_position(ticket: int, sl: float = None, tp: float = None) -> object:
    if not connect():
        alert_sound()
        return fake(-9, "init failed")
    pl = mt5.positions_get(ticket=ticket) or []
    if not pl:
        logging.error(f"[MT5] no ticket {ticket}")
        alert_sound()
        return fake(-4, "none")
    p = pl[0]
    req = {
        "action":    mt5.TRADE_ACTION_SLTP,
        "position":  ticket,
        "symbol":    p.symbol,
        "type_time": mt5.ORDER_TIME_GTC
    }
    if sl is not None: req["sl"] = sl
    if tp is not None: req["tp"] = tp
    logging.info(f"[MT5] modify ticket={ticket} SL={sl} TP={tp}")
    res = mt5.order_send(req)
    if not res:
        logging.error("[MT5] modify returned None")
        alert_sound()
        return fake(-5, "none")
    success_sound()
    return res
# — Modify existing position by symbol (new function for setting SL/TP on trades by symbol) —
def modify_by_symbol(symbol: str, sl: float = 0.0, tp: float = 0.0) -> object:
    if not connect():
        alert_sound()
        return fake(-9, "init failed")
    try:
        symbol = resolve_symbol(symbol)
    except ValueError as e:
        logging.error(f"[MT5] {e}")
        alert_sound()
        return fake(-1, "no symbol")
    
    pl = mt5.positions_get(symbol=symbol) or []
    if not pl:
        logging.error(f"[MT5] no pos for {symbol}")
        alert_sound()
        return fake(-4, "none")
    
    # Assuming multiple positions possible, modify all
    for p in pl:
        req = {
            "action":    mt5.TRADE_ACTION_SLTP,
            "position":  p.ticket,
            "symbol":    p.symbol,
            "type_time": mt5.ORDER_TIME_GTC
        }
        if sl is not None: req["sl"] = sl
        if tp is not None: req["tp"] = tp
        logging.info(f"[MT5] modify ticket={p.ticket} SL={sl} TP={tp}")
        res = mt5.order_send(req)
        if not res:
            logging.error("[MT5] modify returned None")
            alert_sound()
            return fake(-5, "none")
        success_sound()
        # If multiple, could collect results, but for simplicity return last
    return res

# — Close by ticket ID —
def close_pos_by_ticket(ticket: int) -> object:
    if not connect():
        alert_sound()
        return fake(-9, "init failed")
    pl = mt5.positions_get(ticket=ticket) or []
    if not pl:
        logging.error(f"[MT5] no ticket {ticket}")
        alert_sound()
        return fake(-4, "none")
    p = pl[0]
    opp   = mt5.ORDER_TYPE_SELL if p.type==mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(p.symbol)
    if not tick:
        logging.error(f"[MT5] no tick for {p.symbol}")
        alert_sound()
        return fake(-3, "no price")
    price = tick.bid if opp==mt5.ORDER_TYPE_SELL else tick.ask
    req = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "position":  p.ticket,
        "symbol":    p.symbol,
        "volume":    p.volume,
        "type":      opp,
        "price":     price,
        "deviation": 20,
        "magic":     p.magic,
        "comment":   "Close",
        "type_time": mt5.ORDER_TIME_GTC
    }
    for fm in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = fm
        logging.info(f"[MT5] trying fill_mode={fm}")
        res = mt5.order_send(req)
        if not res:
            logging.error("[MT5] send returned None")
            alert_sound()
            return fake(-2, "none")
        if res.retcode != 10030:
            logging.debug(f"[MT5] result {res.retcode} {res.comment}")
            success_sound()
            return res
        logging.warning(f"[MT5] unsupported fill_mode={fm}")
    return fake(10030, "unsupported")

# — Fake return type —
def fake(code: int, comment: str) -> object:
    class R:
        pass
    r = R()
    r.retcode = code
    r.deal = 0
    r.order = 0
    r.volume = 0.0
    r.price = 0.0
    r.bid = 0.0
    r.ask = 0.0
    r.comment = comment
    r.request_id = 0
    r.retcode_external = 0
    return r
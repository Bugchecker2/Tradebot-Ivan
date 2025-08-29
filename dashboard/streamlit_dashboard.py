import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).parent.parent))
import streamlit.components.v1 as components
from mt5_executor import get_leverage, switch_broker
import streamlit as st
import pathlib
import os
import json
import pandas as pd
import MetaTrader5 as mt5
import subprocess
import psutil
import signal
import logging
import sys, datetime

# for auto‑refresh
from streamlit_autorefresh import st_autorefresh

# Add project root for imports
sys.path.append(str(pathlib.Path(__file__).parent.parent))
from utils import logger

# --- Logger init ---
logger.setup_logger()

# --- Helper Functions ---
def is_bot_running():
    for proc in psutil.process_iter(['cmdline']):
        cmdline = proc.info.get('cmdline') or []
        if any("main.py" in part for part in cmdline):
            return True
    return False

def start_bot():
    return subprocess.Popen([sys.executable, "main.py"])

def stop_bot():
    for proc in psutil.process_iter(['pid', 'cmdline']):
        cmdline = proc.info.get('cmdline') or []
        if any("main.py" in part for part in cmdline):
            try:
                os.kill(proc.info['pid'], signal.SIGTERM)
                logging.info(f"Stopped bot process: PID {proc.info['pid']}")
            except Exception as e:
                logging.error(f"Failed to kill bot: {e}")

def stop_dashboard():
    os.kill(os.getpid(), signal.SIGTERM)

def load_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))

def clear_file(path):
    try:
        path.write_text("")
        return True
    except Exception as e:
        st.error(f"Failed to clear {path}: {e}")
        return False

NOISY_DEBUG_KEYWORDS = [
    "Handling update Updates",
    "Receiving items from the network",
    "Handling container",
    "Timeout waiting for updates expired",
]

def colorize_log_line(line):
    if "ERROR" in line:
        return f'<span style="color:red;font-weight:bold;">{line}</span>'
    elif "WARNING" in line:
        return f'<span style="color:orange;font-weight:bold;">{line}</span>'
    elif "INFO" in line:
        return f'<span style="color:green;">{line}</span>'
    elif "DEBUG" in line:
        if any(keyword in line for keyword in NOISY_DEBUG_KEYWORDS):
            return None
        return f'<span style="color:gray;">{line}</span>'
    else:
        return line

def render_colored_log(log_path: pathlib.Path, show_debug: bool):
    if not log_path.exists():
        st.write(f"(No log at {log_path})")
        return
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not show_debug:
        lines = [l for l in lines if "DEBUG" not in l]
    
    displayed = lines[-200:]
    colored = [colorize_log_line(l) for l in displayed if colorize_log_line(l) is not None]
    
    # Render in a scrollable div and auto-scroll to bottom
    log_html = "<br>".join(colored)
    html = f"""
    <div id="log-container" style="height: 500px; width: 700px; overflow-y: auto; border: 1px solid #ddd; padding: 10px; background-color: #f8f9fa;">
        {log_html}
    </div>

    <script>
    (function() {{
        const container = document.getElementById('log-container');
        const threshold = 20;  // pixels tolerance for "at bottom"

        // Restore saved scroll position
        let savedScrollTop = parseFloat(localStorage.getItem('logScrollTop')) || 0;
        let savedScrollHeight = parseFloat(localStorage.getItem('logScrollHeight')) || 0;
        let isFirstLoad = localStorage.getItem('logScrollTop') === null;

        container.scrollTop = savedScrollTop;

        if (isFirstLoad) {{
            container.scrollTop = container.scrollHeight;
        }} else if (savedScrollHeight > 0 && savedScrollTop + container.clientHeight >= savedScrollHeight - threshold) {{
            container.scrollTop = container.scrollHeight;
        }}

        // Save scroll position on scroll events
        container.addEventListener('scroll', () => {{
            localStorage.setItem('logScrollTop', container.scrollTop);
            localStorage.setItem('logScrollHeight', container.scrollHeight);
        }});
    }})();
    </script>
    """
    components.html(html, height=550,width=750, scrolling=False)
# --- Paths ---
BASE_DIR      = pathlib.Path(__file__).parent.resolve()
CRED_PATH     = BASE_DIR.parent / "config" / "credentials.json"
MT5_CRED_PATH = BASE_DIR.parent / "config" / "mt5_credentials.json"
SETTINGS_PATH = BASE_DIR.parent / "config" / "settings.json"
BOT_INFO_PATH = BASE_DIR.parent / "config" / "bot_info.json"
LOG_INFO      = BASE_DIR.parent / "trading_bot.log"
LOG_DEBUG     = BASE_DIR.parent / "mt5_detailed.log"

# --- Streamlit UI ---
st.set_page_config(page_title="📊 Telegram-MT5 Dashboard", layout="wide")
st.title("📊 Telegram-MT5 Bot Dashboard")

# Sidebar navigation
tab = st.radio("Select View:", [
    "Manage Credentials", 
    "Manage Settings", 
    "Monitor", 
    "View Logs", 
    "Manage Bot"
    ], horizontal=True)
st.markdown("""
    <style>
    #telegram-mt-5-bot-dashboard {
        position: fixed; 
        top: 0%;  
        left: 0;
        width: 100%; 
        z-index: 9999999;  
        background-color: white;
        padding: 10px 0;  
        margin: 0; 
        border-bottom: 1px solid #ddd;
    }
    div.element-container:has([role="radiogroup"]) {
        position: sticky;
        top: 5%;
        right: 50%;  
        z-index: 1000000; 
        background-color: white;  
        padding: 10px 0; 
        border-bottom: 1px solid #ddd; 
    }
    </style>
""", unsafe_allow_html=True)

if tab == "Manage Credentials":
    st.header("🔑 Credentials")

    # — Telegram credentials —
    creds = load_json(CRED_PATH, {}) or {}
    api_id   = st.text_input("API ID",   value=str(creds.get("api_id", "")))
    api_hash = st.text_input("API Hash", value=creds.get("api_hash", ""))
    group_id = st.text_input("Group ID", value=str(creds.get("group_id", "")))
    if st.button("Save Telegram Credentials"):
        save_json(CRED_PATH, {"api_id": api_id, "api_hash": api_hash, "group_id": group_id})
        st.success("Telegram credentials saved")

    st.markdown("---")
    st.subheader("⚙️ MT5 Brokers")

    # load unified MT5 credentials file
    MT5_CRED_PATH = BASE_DIR.parent / "config" / "mt5_credentials.json"
    mt5_data = load_json(MT5_CRED_PATH, {}) or {}

    # extract broker names (all keys except 'active')
    broker_names = [k for k in mt5_data.keys() if k != "active"]
    if not broker_names:
        st.error("No brokers defined in mt5_credentials.json")
    else:
        # edit-selected broker
        selected = st.selectbox("Edit Broker", broker_names, key="edit_broker")
        b = mt5_data[selected]
        account_id = st.text_input("Account ID", value=str(b.get("account_id", "")), key="mt5_acct")
        password   = st.text_input("Password",   value=b.get("password",     ""), type="password", key="mt5_pwd")
        server     = st.text_input("Server",     value=b.get("server",       ""), key="mt5_srv")

        if st.button("Save This Broker", key="save_broker"):
            mt5_data[selected]["account_id"] = account_id
            mt5_data[selected]["password"] = password
            mt5_data[selected]["server"] = server
            save_json(MT5_CRED_PATH, mt5_data)
            st.success(f"Credentials updated for **{selected}**")

    st.markdown("---")
    st.subheader("Add New Broker")
    new_name = st.text_input("New Broker Name", key="new_broker_name")
    new_account_id = st.text_input("New Account ID", key="new_mt5_acct")
    new_password = st.text_input("New Password", type="password", key="new_mt5_pwd")
    new_server = st.text_input("New Server", key="new_mt5_srv")

    if st.button("Add New Broker", key="add_broker"):
        if new_name and new_name not in mt5_data:
            mt5_data[new_name] = {
                "account_id": new_account_id,
                "password": new_password,
                "server": new_server,
                "leverage_rules": []
            }
            save_json(MT5_CRED_PATH, mt5_data)
            st.success(f"New broker **{new_name}** added")
        else:
            st.error("Broker name is empty or already exists.")

    st.markdown("---")
    # select active broker
    broker_names = [k for k in mt5_data.keys() if k != "active"]  # Reload broker names in case a new one was added
    if broker_names:
        active = mt5_data.get("active", broker_names[0])
        chosen = st.selectbox("Active Broker", broker_names,
                              index=broker_names.index(active) if active in broker_names else 0, key="active_broker")
        if st.button("Set Active Broker", key="set_active"):
            mt5_data["active"] = chosen
            save_json(MT5_CRED_PATH, mt5_data)
            switch_broker(mt5_data["active"])
            st.success(f"Active broker switched to **{chosen}**")

elif tab == "Manage Settings":
    st.header("⚙️ Bot Settings (settings.json)")
    settings = load_json(SETTINGS_PATH, {})
    if not settings:
        st.error("Failed to load settings.json or file is empty")
    else:
        with st.form("settings_form"):
            lot_method      = st.selectbox(
                "Lot Method",
                ["percent_remaining", "percent_start"],
                index=["percent_remaining", "percent_start"].index(settings.get("lot_method", "percent_remaining"))
            )
            reinvest        = st.checkbox("Reinvest Profits", value=settings.get("reinvest", True))
            accept_PUT_CALL = st.checkbox("Accept PUT/CALL options", value=settings.get("accept_PUT_CALL", True))
            lot_percent     = st.number_input("Lot Percent (%)", min_value=0.1, max_value=100.0,
                                              value=float(settings.get("lot_percent", 5)))
            max_cap_percent = st.number_input("Max Capital Percent (%)", min_value=1, max_value=100,
                                              value=int(settings.get("max_cap_percent", 20)))
            default_lot     = st.number_input("Default Lot Size", min_value=0.001,
                                              value=float(settings.get("default_lot", 0.01)), format="%.4f")
            submitted = st.form_submit_button("Save Settings")
            if submitted:
                new_settings = {
                    "lot_method":      lot_method,
                    "lot_percent":     lot_percent,
                    "max_cap_percent": max_cap_percent,
                    "reinvest":        reinvest,
                    "default_lot":     default_lot,
                    "accept_PUT_CALL": accept_PUT_CALL,
                }
                save_json(SETTINGS_PATH, new_settings)
                st.success("Settings saved successfully")

elif tab == "Monitor":
    st.header("📈 Monitor Trades")
    st_autorefresh(interval=1_000, key="monitor_autorefresh")
#    if st.button("🔄 Refresh Monitor"): pass

    if not mt5.initialize():
        st.error("Failed to initialize MT5. Check credentials and terminal path.")
    else:
        info = mt5.account_info()
        # P/L Today metric
        if 'start_balance' not in st.session_state:
            st.session_state['start_balance'] = info.balance
        day_pl = info.balance - st.session_state['start_balance']
        st.metric('P/L Today', f"{day_pl:.2f}")

        col1, col2, col3 = st.columns(3)
        col1.metric("Balance",     f"{info.balance:.2f}")
        col2.metric("Used Margin", f"{info.margin:.2f}")
        col3.metric("Free Margin", f"{info.margin_free:.2f}")

        positions = mt5.positions_get() or []
        rows = []
        libertex_rows = []  # New: For Libertex-style table
        for p in positions:
            sym_info = mt5.symbol_info(p.symbol)
            contract_size = sym_info.trade_contract_size if sym_info else 1
            lots = p.volume
            units = lots * contract_size
            opened_dt = datetime.datetime.fromtimestamp(p.time, datetime.UTC)
            m_libertex = units * p.price_open  # Notional (market value)
            margin_req = getattr(sym_info, "margin_initial", 0)
            if margin_req > 0:
                margin_used = margin_req * lots
            else:
                leverage = get_leverage(p.symbol)
                margin_used = (contract_size * p.price_open * lots) / leverage

            # New: Calculate profit % based on margin (matches Libertex)
            profit_pct = (p.profit / margin_used * 100) if margin_used else 0
            margin_mt5 = mt5.order_calc_margin(p.type, p.symbol, p.volume, p.price_open)

            rows.append({
                "Ticket":     p.ticket,
                "Path": sym_info.path,
                "Symbol":     p.symbol,
                "Leverage":   f"{leverage:.0f}" or " - ",
                "Volum" :     f"{p.volume:.2f}",
                "Contract size": f"{contract_size:.0f}",
#                "Units":      f"{units:.2f}",
                "Units":       f"{units:.2f}" if units < 10 else f"{units:.0f}",
                "Open Price": f"{p.price_open:.2f}",
                "Market Value": f"{units * p.price_open:.2f}",
                "Margin":       f"{margin_used:.2f}",
                "Margin by mt5": f"{margin_mt5:.2f}",
#                "Opened At":    opened_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "Profit":     f"{p.profit:.2f}"  
            })

            # New: Build Libertex-style row
            operation = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"  # 0=buy, 1=sell
            actual_margin = info.margin
            commission_est = - (0.001 * (units * p.price_open))
            if p.type == mt5.POSITION_TYPE_BUY:  # Rollover for longs 
                commission_est -= (0.0001 * (units * p.price_open))  # Tune based on time open
            adjusted_profit = p.profit + commission_est
            profit_pct = (adjusted_profit / actual_margin * 100) if actual_margin else 0
            tick = mt5.symbol_info_tick(p.symbol)
            price_close = tick.ask if operation == "BUY" else tick.bid
            profit_close = (price_close - p.price_open) * units + commission_est

            libertex_rows.append({
                "Symbol":     p.symbol,
                "Date ": opened_dt.strftime("%d %B %Y, %H:%M:%S"), 
                "Price": f"{p.price_open:.2f}",
                "Operation": f"{operation} ↑" if operation == "BUY" else f"{operation} ↓",
                "Multiplier": f"x {leverage:.0f}",
                "Volume": f"€{margin_used:.2f} (x {leverage:.0f}) = €{m_libertex:.0f}",
                "Profit": f"€{profit_close:.2f}"
            })

        df = pd.DataFrame(rows)

        if df.empty:
            st.write("No open positions.")
        else:
            df = df[
#                ["Ticket","Path","Symbol","Leverage","Volum","Contract size","Units","Open Price","Market Value","Margin","Margin by mt5","Opened At","Profit"]
                ["Ticket","Path", "Symbol","Leverage","Volum","Contract size","Units","Open Price","Market Value","Margin","Margin by mt5","Profit"]
            ]
            st.table(df)
#            pd.set_option('display.max_colwidth', 10)

            # New: Separate Libertex-style table
            if libertex_rows:
                st.subheader("Libertex-Style Position Details")
                df_libertex = pd.DataFrame(libertex_rows)
                st.table(df_libertex)

elif tab == "View Logs":
    st.header("📝 View Logs")

    # ─── Auto‑refresh on new log lines ───
    # rerun the script every 1 s so we pick up any appended lines immediately
    st_autorefresh(interval=1_000, key="logs_autorefresh")

    last_lof_message = None

    # ─── Controls ───
    col1, col2 = st.columns([1,3])
    with col1:
        if st.button("🗑️ Clear All Logs"):
            ok1 = clear_file(LOG_INFO)
            ok2 = clear_file(LOG_DEBUG)
            if ok1 and ok2:
                st.success("Logs cleared")

        if 'detailed_logs' not in st.session_state:
            st.session_state['detailed_logs'] = False

        st.checkbox(
            "Enable detailed MT5 logs",
            key='detailed_logs',
            on_change=logger.set_logging_enabled,
            args=(st.session_state['detailed_logs'],)
        )

    # ─── Render the first 50 lines ───
    with col2:
        st.subheader("Trading Bot Log")
        render_colored_log(LOG_INFO, show_debug=st.session_state['detailed_logs'])

        st.subheader("MT5 Detailed Log")
        render_colored_log(LOG_DEBUG, show_debug=st.session_state['detailed_logs'])


elif tab == "Manage Bot":
    st.header("🤖 Bot Control")

    if st.button("▶️ Start Bot"):
        start_bot()
        logging.info("Bot started by user from dashboard")
        st.success("Bot started")
        pass

    if st.button("⛔ Stop Bot"):
        stop_bot()
        logging.info("Bot stopped by user from dashboard")
        st.success("Bot stopped")
        pass
 
    if st.button("🛑 Stop Dashboard"):
        stop_dashboard()
    st.markdown(f"**Status:** {'🟢 Running' if is_bot_running() else '🔴 Stopped'}")
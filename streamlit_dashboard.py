import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).parent.parent))
import requests
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
import re

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
def toggle_detailed_logs():
    logger.set_logging_enabled(st.session_state['detailed_logs'])

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
LOG_DETAILED  = BASE_DIR.parent / "detailed_log.log"

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
#####################
if tab == "Manage Credentials":
    st.header("🔑 Credentials")

    # Load settings for the toggle
    settings = load_json(SETTINGS_PATH, {})
    creds = load_json(CRED_PATH, {}) or {}

    listen_to_all = st.checkbox("Listen to all channels", value=settings.get("listen_to_all_channels", True))
    group_ids_text = st.text_area(
        "Group IDs", 
        value="\n".join(creds.get("group_ids", [])), 
        help="Enter channel/group IDs (negative integers, e.g., -1001234567890)"
    )
    
    # Parse for preview/validation
    group_ids_parsed = [gid.strip() for gid in re.split(r'[, \n]+', group_ids_text.replace('\n', ',')) if gid.strip()]
    
    if not listen_to_all and group_ids_parsed:
        # If not all, show selectbox for active index
        default_idx = creds.get("active_group_index", 0)
        selected_idx = st.selectbox(
            "Active Channel (switch here)", 
            options=range(len(group_ids_parsed)), 
            index=min(default_idx, len(group_ids_parsed) - 1),
            format_func=lambda i: f"Index {i}: {group_ids_parsed[i]}"
        )
    else:
        selected_idx = 0  # Default, not used if all

    api_id   = st.text_input("API ID",   value=str(creds.get("api_id", "")))
    api_hash = st.text_input("API Hash", value=creds.get("api_hash", ""))
    
    if st.button("Save Telegram Credentials"):
        # Parse and save group_ids
        new_creds = {
            "api_id": api_id, 
            "api_hash": api_hash, 
            "group_ids": group_ids_parsed
        }
        if not listen_to_all:
            new_creds["active_group_index"] = selected_idx
        save_json(CRED_PATH, new_creds)
        
        # Save toggle to settings
        settings["listen_to_all_channels"] = listen_to_all
        save_json(SETTINGS_PATH, settings)
        
        st.success("Telegram credentials and settings saved!")
        
        # Preview
        st.info(f"Parsed {len(group_ids_parsed)} channel(s). {'All active.' if listen_to_all else f'Active: {group_ids_parsed[selected_idx]}'}")


    st.markdown("---")
    st.subheader("⚙️ MT5 Brokers")

    # load unified MT5 credentials file
    MT5_CRED_PATH = BASE_DIR.parent / "config" / "mt5_credentials.json"
    mt5_data = load_json(MT5_CRED_PATH, {}) or {}

    # extract broker names (all keys except 'active')
    broker_names = [k for k in mt5_data.keys() if k != "active"]
    
    if not broker_names:
        st.warning("No brokers defined yet. You can add one below.")

    # Mode selection (default: Modify)
    mode = st.radio("Broker Action", ["Modify Broker", "Add Broker"], index=0, horizontal=True, key="broker_mode")

    broker_name = None
    can_proceed = False
    selected_broker = None

    if mode == "Modify Broker":
        if broker_names:
            selected_broker = st.selectbox("Select Broker to Modify", options=broker_names, key="select_broker")
            broker_name = selected_broker
            can_proceed = True
        else:
            st.warning("No existing brokers to modify. Switch to 'Add Broker' mode.")
    else:  # Add Broker
        broker_name = st.text_input("New Broker Name", key="new_broker_name")
        can_proceed = True

    # Common fields - prefill if modifying, else empty
    if mode == "Modify Broker" and broker_names:
        account_id = st.text_input("Account ID", value=str(mt5_data[selected_broker].get("account_id", "")), key="account_id")
        password = st.text_input("Password", value=mt5_data[selected_broker].get("password", ""), type="password", key="password")
        server = st.text_input("Server", value=mt5_data[selected_broker].get("server", ""), key="server")
    else:
        account_id = st.text_input("Account ID", value="", key="account_id")
        password = st.text_input("Password", value="", type="password", key="password")
        server = st.text_input("Server", value="", key="server")

    # Leverage Map with JSON uploader
    current_leverage = ""
    if mode == "Modify Broker" and selected_broker:
        current_leverage = mt5_data[selected_broker].get("leverage_json_file", "")
        if current_leverage:
            st.info(f"Current Leverage Map: {current_leverage}")

    uploaded_file = st.file_uploader(
        "Leverage Map JSON File", 
        type=["json"], 
        key=f"leverage_uploader_{'modify' if mode == 'Modify Broker' else 'add'}"
    )
    leverage_file = current_leverage
    if uploaded_file is not None:
        # Ensure directory exists
        leverage_dir = BASE_DIR.parent / "leverage_maps"
        leverage_dir.mkdir(parents=True, exist_ok=True)
        save_path = leverage_dir / uploaded_file.name
        with open(save_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        leverage_file = uploaded_file.name
        st.success(f"JSON file '{uploaded_file.name}' uploaded to leverage_maps/")

    # Button
    action = "Modify" if mode == "Modify Broker" else "Add"
    button_disabled = not (broker_name and account_id and password and server and can_proceed)
    
    if st.button(f"{action} Broker", disabled=button_disabled, key="broker_action"):
        if broker_name and account_id and password and server:
            if mode == "Modify Broker":
                if broker_name in mt5_data:
                    mt5_data[broker_name].update({
                        "account_id": account_id,
                        "password": password,
                        "server": server,
                        "leverage_json_file": leverage_file
                    })
                    st.success(f"Broker **{broker_name}** modified successfully!")
                else:
                    st.error("Selected broker not found.")
            else:  # Add
                if broker_name not in mt5_data:
                    mt5_data[broker_name] = {
                        "account_id": account_id,
                        "password": password,
                        "server": server,
                        "leverage_json_file": leverage_file
                    }
                    st.success(f"New broker **{broker_name}** added successfully!")
                else:
                    st.error("Broker name already exists.")
            
            save_json(MT5_CRED_PATH, mt5_data)
        else:
            st.error("Please fill all fields.")
    st.markdown("---")
    
    # select active broker (reload names in case changed)
    broker_names = [k for k in mt5_data.keys() if k != "active"]  # Reload in case a new one was added/modified
    if broker_names:
        active = mt5_data.get("active", broker_names[0] if broker_names else None)
        chosen_index = broker_names.index(active) if active in broker_names else 0
        chosen = st.selectbox("Active Broker", broker_names,
                              index=chosen_index, key="active_broker")
        if st.button("Set Active Broker", key="set_active"):
            mt5_data["active"] = chosen
            save_json(MT5_CRED_PATH, mt5_data)
            switch_broker(mt5_data["active"])
            st.success(f"Active broker switched to **{chosen}**")
    else:
        st.info("No brokers available to set as active.")


            ######

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

    if not mt5.initialize():
        st.error("Failed to initialize MT5. Check credentials and terminal path.")
    else:
        info = mt5.account_info()
        account_currency = info.currency  # Get account currency, e.g., 'EUR'

        # Fetch exchange rate from MT5
        tick = mt5.symbol_info_tick("EURUSD")
        if tick:
            rate_eur_usd = (tick.bid + tick.ask) / 2  # Mid price
        else:
            rate_eur_usd = 1.168  # Fallback
        rate_usd_eur = 1 / rate_eur_usd

        # Toggle switch
        toggle_usd = st.toggle(f"Show in {'USD' if account_currency == 'EUR' else 'EUR'}", False)
        if toggle_usd:
            display_currency = 'USD' if account_currency == 'EUR' else 'EUR'
        else:
            display_currency = account_currency

        symbol = '$' if display_currency == 'USD' else '€'

        def convert(value, orig_cur):
            if orig_cur == display_currency:
                return value
            if orig_cur == 'EUR' and display_currency == 'USD':
                return value * rate_eur_usd
            if orig_cur == 'USD' and display_currency == 'EUR':
                return value * rate_usd_eur
            return value  # Default same

        # P/L Today metric
        if 'start_balance' not in st.session_state:
            st.session_state['start_balance'] = info.balance
        day_pl = info.balance - st.session_state['start_balance']
        st.metric('P/L Today', f"{convert(day_pl, account_currency):.2f} {symbol}")

        col1, col2, col3 = st.columns(3)
        col1.metric("Balance",     f"{convert(info.balance, account_currency):.2f} {symbol}")
        col2.metric("Used Margin", f"{convert(info.margin, account_currency):.2f} {symbol}")
        col3.metric("Free Margin", f"{convert(info.margin_free, account_currency):.2f} {symbol}")

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
            margin_orig_cur = account_currency if margin_req > 0 else 'USD'
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
                "Path":       sym_info.path[:25],
                "Symbol":     p.symbol,
                "Leverage":   f"{leverage:.0f}" or " - ",
                "Volum" :     f"{p.volume:.2f}",
                "Contract size": f"{contract_size:.0f}",
#                "Units":      f"{units:.2f}",
                "Units":       f"{units:.2f}" if units < 10 else f"{units:.0f}",
                "Open Price": f"{convert(p.price_open, 'USD'):.2f}",
                "Market Value": f"{convert(units * p.price_open, 'USD'):.2f}",
                "Margin":       f"{convert(margin_used, margin_orig_cur):.2f}",
                "Margin by mt5": f"{convert(margin_mt5, account_currency):.2f}",
#                "Opened At":    opened_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "Profit":     f"{convert(p.profit, account_currency):.2f}"  
            })
            operation = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"  # 0=buy, 1=sell
            actual_margin = info.margin
            commission_est = - (0.001 * (units * p.price_open))
            if p.type == mt5.POSITION_TYPE_BUY:  #
                commission_est -= (0.0001 * (units * p.price_open))  
            adjusted_profit = p.profit + commission_est
            profit_pct = (adjusted_profit / actual_margin * 100) if actual_margin else 0
            tick = mt5.symbol_info_tick(p.symbol)
            price_close = tick.ask if operation == "BUY" else tick.bid
            profit_close = (price_close - p.price_open) * units + commission_est
            if operation == "SELL": profit_close = -profit_close

            libertex_rows.append({
                "Symbol":  p.symbol,
                "Date ": opened_dt.strftime("%d %B %Y, %H:%M:%S"), 
                "Price": f"{convert(p.price_open, 'USD'):.2f}",
                "Operation": f"{operation} ↑" if operation == "BUY" else f"{operation} ↓",
                "Multiplier": f"x {leverage:.0f}",
                "Volume": f"{symbol}{convert(margin_used, margin_orig_cur):.2f} (x {leverage:.0f}) = {symbol}{convert(m_libertex, 'USD'):.0f}",
                "Profit": f"{symbol}{convert(profit_close, 'USD'):.2f}"
            })

        df = pd.DataFrame(rows)

        if df.empty:
            st.write("No open positions.")
        else:
            df = df[
                ["Ticket","Path", "Symbol","Leverage","Volum","Contract size","Units","Open Price","Market Value","Margin","Margin by mt5","Profit"]
            ]
            st.table(df)

            # New: Separate Libertex-style table
            if libertex_rows:
                st.subheader("Libertex-Style Position Details")
                df_libertex = pd.DataFrame(libertex_rows)
                st.table(df_libertex)

if tab == "View Logs":
    st.header("📝 View Logs")
    
    # ─── Auto‑refresh on new log lines ───
    # rerun the script every 1 s so we pick up any appended lines immediately    
    st_autorefresh(interval=1_000, key="logs_autorefresh")

    # ─── Controls ───
    col1, col2 = st.columns([1,3])
    with col1:
        if st.button("🗑️ Clear All Logs"):
            # Assuming clear_file is defined
            ok = clear_file(LOG_INFO)
            # Also clear detailed log if needed
            ok_detailed = clear_file(LOG_DETAILED)
            if ok and ok_detailed:
                st.success("Logs cleared")

        if 'detailed_logs' not in st.session_state:
            st.session_state['detailed_logs'] = logger.return_detailed_logging()

        st.checkbox(
            "Enable detailed logs (standard or detailed view)",
            key='detailed_logs',
            on_change=toggle_detailed_logs
            # No need for value=; key handles it and persists in session_state
        )

    # ─── Render the main log ───
    with col2:
        if st.session_state['detailed_logs']:
            st.subheader("Detailed Trading Bot Log")
            # Assuming render_colored_log is defined and handles show_debug
            render_colored_log(LOG_DETAILED, show_debug=True)
        else:
            st.subheader("Trading Bot Log")
            render_colored_log(LOG_INFO, show_debug=False)

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
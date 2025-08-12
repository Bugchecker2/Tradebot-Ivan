import threading, time, MetaTrader5 as mt5, logging

def panel():
    while True:
        pos=mt5.positions_get(); bal=mt5.account_info().balance
        logging.info(f"Open trades: {len(pos)}, Balance: {bal}")
        time.sleep(30)

def start():
    t=threading.Thread(target=panel,daemon=True); t.start()
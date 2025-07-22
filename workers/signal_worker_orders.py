import csv
import sqlite3
import time
import pandas as pd
from datetime import datetime
from kiteconnect import KiteConnect
import json
import requests
# Load config
with open('Config/config.json') as f:
    config = json.load(f)

api_key = config['api_key']
access_token = config['access_token']
telegram_bot_token= config.get("telegram_bot_token")
telegram_chat_id = config.get("telegram_chat_id")

# Load config
with open('strategy_config.json') as j:
    strategy_config = json.load(j)
order_log_path = "order_log.json"
active_orders = []



kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

with open('Config/instrument_config.json') as j:
    instr_config = json.load(j)
mcx_interval = instr_config['mcx_fno']['interval']
# mcx_volume_threshold = config['mcx_fno']['volume_threshold']

DB_PATH = 'signals.db'
CSV_PATH = 'InstrumentsData/instruments_mcx.csv'

def load_symbol_token_map():
    symbol_token_map = {}
    try:
        with open(CSV_PATH, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            
            # Normalize fieldnames for case-insensitive access
            reader.fieldnames = [field.strip().lower() for field in reader.fieldnames]

            for row in reader:
                symbol = row['tradingsymbol'].strip().upper()
                token = int(row['instrument_token'].strip())
                symbol_token_map[symbol] = token
    except Exception as e:
        print(f"❌ Failed to load symbol-token map: {e}")
    return symbol_token_map

symbol_token_map2 = load_symbol_token_map()
print(f"✅ Loaded {len(symbol_token_map2)} tokens.")
print("📌 Sample:", list(symbol_token_map2.items())[:3])


def read_instruments_from_csv(path=CSV_PATH):
    try:
        with open(path, 'r') as file:
            reader = csv.DictReader(file)
            return [row['tradingsymbol'].strip().upper() for row in reader if 'tradingsymbol' in row]
    except Exception as e:
        print(f"❌ Failed to read instruments CSV: {e}")
        return []


def store_signal_in_db(result):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Ensure table exists
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                signal_type TEXT,
                signal_time TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                volume_delta REAL
            )
        ''')

        # Check for duplicates
        cursor.execute(
            "SELECT COUNT(*) FROM signals WHERE symbol = ? AND signal_time = ?",
            (result['symbol'], result['signal_time'])
        )
        exists = cursor.fetchone()[0]

        if exists == 0:
            cursor.execute('''
                INSERT INTO signals (
                    symbol, signal_type, signal_time,
                    open, high, low, close,
                    volume, volume_delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                result['symbol'],
                result['signal_type'],
                result['signal_time'],
                float(result['open']),
                float(result['high']),
                float(result['low']),
                float(result['close']),
                float(result['volume']),
                float(result['volume_delta'])
            ))
            conn.commit()
            print(f"✅ INSERTED: {result['symbol']} ({result['signal_type']}) @ {result['signal_time']}")
            send_telegram_alert(
                symbol=result['symbol'],
                signal_type=result['signal_type'],
                price=result['close'],
                time=result['signal_time'],
                volume_delta=result['volume_delta']
            )

        else:
            print(f"⚠️ DUPLICATE: {result['symbol']} @ {result['signal_time']}")

        conn.close()
    except Exception as e:
        print(f"❌ Error storing signal: {e}")


def is_market_time(now):
    start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=00, second=0, microsecond=0)
    return start <= now <= end


def background_signal_job():
    instruments = read_instruments_from_csv()
    if not instruments:
        print("❌ No instruments found. Exiting.")
        return

    print("🚀 Running signal worker in 30-minute test mode...")

    while True:
        now = datetime.now()

        if is_market_time(now):
            for symbol in instruments:
                print(f"→ Checking {symbol}...")
                signal_count = 0

                instrument_token = symbol_token_map2.get(symbol)
                if not instrument_token:
                    print(f"⛔ Token not found for {symbol}")
                    print("📎 Available symbols in map:", list(symbol_token_map2.keys())[:5])
                    continue

                from_date = now.replace(hour=9, minute=30, second=0, microsecond=0)
                to_date = now

                try:
                    candles = kite.historical_data(
                        instrument_token=instrument_token,
                        from_date=from_date,
                        to_date=to_date,
                        interval=mcx_interval,
                        continuous=False
                    )

                    candles_today = [c for c in candles if pd.to_datetime(c['date']).date() == now.date()]
                    df = pd.DataFrame(candles_today)
                    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume']

                    # 🛠 Convert to proper data types
                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
                    df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])  # remove rows with NaNs
                    # Optional: sort by date if needed
                    df = df.sort_values('date')

                    if len(df) < 3:
                        print(f"⛔ Not enough candles for {symbol}")
                        continue

                    for i in range(2, len(df)):
                        prev2 = df.iloc[i - 2]
                        prev1 = df.iloc[i - 1]
                        current = df.iloc[i]

                        volume_delta = ((current['volume'] - prev1['volume']) / prev1['volume']) * 100 if prev1['volume'] != 0 else 0
                        volume_threshold = 100  # 🔍 sensitive for test######################################################

                        signal = None
                        if current['close'] > max(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                            signal = 'BULLISH'
                            result = place_entry_order(symbol, signal, current['close'])
                            if result:
                                active_orders.append(result)

                        elif current['close'] < min(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                            signal = 'BEARISH'
                            result = place_entry_order(symbol, signal, current['close'])
                            if result:
                                active_orders.append(result)

                        if signal:
                            signal_data = {
                                'symbol': symbol,
                                'signal_type': signal,
                                'signal_time': pd.to_datetime(current['date']).strftime("%Y-%m-%d %H:%M:%S"),
                                'open': float(current['open']),
                                'high': float(current['high']),
                                'low': float(current['low']),
                                'close': float(current['close']),
                                'volume': float(current['volume']),
                                'volume_delta': float(round(volume_delta, 2))
                            }
                            store_signal_in_db(signal_data)
                            signal_count += 1

                    print(f"✅ {symbol}: {signal_count} signal(s) inserted.")
                    

                except Exception as e:
                    print(f"❌ Error with {symbol}: {e}")

        time.sleep(60)  # 🔁 run every minute


def place_entry_order(symbol, signal, close_price):
    qty = strategy_config['quantity']
    order_type = strategy_config['order_type']
    sl_percent = strategy_config['sl_percent']
    target_percent = strategy_config['target_percent']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if signal == 'BULLISH':
        entry_side = kite.TRANSACTION_TYPE_BUY
        sl_price = round(close_price - (close_price * sl_percent / 100), 2)
        target_price = round(close_price + (close_price * target_percent / 100), 2)
    elif signal == 'BEARISH':
        entry_side = kite.TRANSACTION_TYPE_SELL
        sl_price = round(close_price + (close_price * sl_percent / 100), 2)
        target_price = round(close_price - (close_price * target_percent / 100), 2)
    else:
        return None

    try:
        order_id = kite.place_order(
            tradingsymbol=symbol,
            exchange=kite.EXCHANGE_NSE,
            transaction_type=entry_side,
            quantity=qty,
            order_type=order_type,
            product=kite.PRODUCT_MIS,
            variety=kite.VARIETY_REGULAR
        )
        return {
            "symbol": symbol,
            "signal": signal,
            "entry_order_id": order_id,
            "timestamp": timestamp,
            "entry_price": close_price,
            "sl_price": sl_price,
            "target_price": target_price,
            "sl_order_id": None,
            "target_order_id": None
        }
    except Exception as e:
        print(f"[ORDER ERROR] {e}")
        return None

def place_exit_order(order, exit_type):
    side = kite.TRANSACTION_TYPE_SELL if order['signal'] == 'BULLISH' else kite.TRANSACTION_TYPE_BUY
    price = order['sl_price'] if exit_type == 'SL' else order['target_price']

    try:
        exit_order_id = kite.place_order(
            tradingsymbol=order['symbol'],
            exchange=kite.EXCHANGE_NSE,
            transaction_type=side,
            quantity=config['quantity'],
            order_type=kite.ORDER_TYPE_LIMIT,
            price=price,
            product=kite.PRODUCT_MIS,
            validity=kite.VALIDITY_DAY,
            variety=kite.VARIETY_REGULAR
        )
        return exit_order_id
    except Exception as e:
        print(f"[EXIT ORDER ERROR] {e}")
        return None

def monitor_and_manage_orders(active_orders):
    for order in active_orders:
        try:
            status = kite.order_history(order['entry_order_id'])
            if is_order_executed(status):
                if not order['sl_order_id']:
                    order['sl_order_id'] = place_exit_order(order, 'SL')
                    order['target_order_id'] = place_exit_order(order, 'TARGET')
                check_and_cancel_orders(order)
        except Exception as e:
            print(f"[MONITOR ERROR] {e}")

def is_order_executed(status_list):
    for item in status_list:
        if item['status'] == 'COMPLETE':
            return True
    return False

def check_and_cancel_orders(order):
    try:
        sl_status = kite.order_history(order['sl_order_id']) if order['sl_order_id'] else []
        tgt_status = kite.order_history(order['target_order_id']) if order['target_order_id'] else []

        if is_order_executed(sl_status):
            if order['target_order_id']:
                kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order['target_order_id'])
        elif is_order_executed(tgt_status):
            if order['sl_order_id']:
                kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order['sl_order_id'])
    except Exception as e:
        print(f"[CANCEL ERROR] {e}")



def send_telegram_alert(symbol, signal_type, price, time, volume_delta):
    try:
        # bot_token = config.get("telegram_bot_token")
        # chat_id = config.get("telegram_chat_id")

        if not telegram_bot_token or not telegram_chat_id:
            raise ValueError("Missing Telegram config keys")

        text = (
            f"📡 *{signal_type}* signal on *{symbol}*\n"
            f"Price: ₹{price}\n"
            f"Time: {time}\n"
            f"Volume%: {volume_delta}%"
        )

        url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print(f"📬 Telegram alert sent for {symbol}")
        else:
            print(f"❌ Telegram send failed: {response.text}")
    except Exception as e:
        print(f"❌ Telegram error: {e}")

if __name__ == "__main__":
    background_signal_job()

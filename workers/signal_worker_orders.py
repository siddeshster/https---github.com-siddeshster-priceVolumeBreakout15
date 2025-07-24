import csv
import sqlite3
import time
import pandas as pd
from datetime import datetime, timedelta
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
with open('Config/strategy_config.json') as j:
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
CSV_PATH = 'InstrumentsData/instruments_mcx_order.csv'

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

    print("🚀 Running signal worker in 30-minute test mode...") # Note: The sleep is 60 seconds, not 30 minutes for the loop itself

    while True:
        now = datetime.now()

        # Check if the current time aligns with the end of a candle interval
        # For a 5-minute interval, this would be at hh:05, hh:10, hh:15, etc.
        # We want to check signals *after* a candle has completely closed.
        # So, if mcx_interval is "5minute", we should run the logic *just after*
        # the 5th, 10th, 15th minute, etc. (e.g., at hh:05:01, hh:10:01)
        # However, fetching historical data up to `now` and then taking the last
        # complete candle is a more robust approach, especially if the script
        # starts mid-interval.

        if is_market_time(now):
            for symbol in instruments:
                print(f"→ Checking {symbol}...")
                signal_count = 0

                instrument_token = symbol_token_map2.get(symbol)
                if not instrument_token:
                    print(f"⛔ Token not found for {symbol}")
                    print("📎 Available symbols in map:", list(symbol_token_map2.keys())[:5])
                    continue

                # Fetch historical data up to the current time.
                # The 'to_date' will include the most recent incomplete candle if it's not a precise interval end.
                from_date = now.replace(hour=9, minute=0, second=0, microsecond=0) # Start from market open or a reasonable earlier time
                to_date = now

                try:
                    candles = kite.historical_data(
                        instrument_token=instrument_token,
                        from_date=from_date,
                        to_date=to_date,
                        interval=mcx_interval,
                        continuous=False
                    )

                    # Filter candles for today and convert to DataFrame
                    candles_today = [c for c in candles if pd.to_datetime(c['date']).date() == now.date()]
                    df = pd.DataFrame(candles_today)

                    if df.empty:
                        print(f"⛔ No candles found for {symbol} today.")
                        continue

                    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume']

                    # 🛠 Convert to proper data types
                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
                    df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])  # remove rows with NaNs
                    # Sort by date to ensure correct order
                    df = df.sort_values('date').reset_index(drop=True)

                    # Determine the number of complete candles
                    # The last candle in the fetched data might be incomplete.
                    # We need to exclude it if its `date` (timestamp of candle close) is equal to `now`
                    # or very close to it, indicating it's still forming.

                    # Let's assume `mcx_interval` is "5minute". The candle 'date' represents
                    # the start of the interval (or end, depending on API). If it's the start,
                    # a candle that opened at hh:mm will close at hh:mm + interval.
                    # Kite Connect's historical data `date` field typically represents the
                    # start of the candle interval. So, a candle with `date` 10:00:00 will close at 10:05:00
                    # for a 5-minute interval.

                    # To get only *closed* candles, we should consider candles whose closing time
                    # has passed `now`.
                    interval_minutes = int("".join(filter(str.isdigit, mcx_interval))) # Extracts 5 from "5minute"

                    # Filter for candles that are definitively closed
                    # A candle is considered closed if its 'date' + interval_minutes is less than or equal to 'now'
                    # Or, more simply, if `df.iloc[-1]['date']` is `now - interval` (approximately), then it's closed.
                    # If `df.iloc[-1]['date']` is `now` or very close, it's the current incomplete candle.

                    # A robust way is to drop the very last candle if `now` falls within its interval.
                    # The `date` column from Kite Connect historical data represents the start time of the candle.
                    # So, if the current time `now` is between the start time of the last candle and (start time + interval),
                    # then the last candle is incomplete.
                    
                    if not df.empty:
                        last_candle_start_time = pd.to_datetime(df.iloc[-1]['date'])
                        last_candle_end_time = last_candle_start_time + timedelta(minutes=interval_minutes)

                        # If current time is before the end of the last candle, the last candle is incomplete.
                        # Or, if 'now' is exactly the start of a new candle, and the last candle's end time
                        # is at or before 'now', then the last candle is complete.
                        # It's safer to just consider candles whose end time has definitely passed.

                        # A simpler way: if the last candle's `date` (start time) is the same as the current minute
                        # (adjusted for interval), it's likely incomplete.
                        # It's usually safe to just take `df.iloc[:-1]` if you're checking frequently
                        # and want to ensure you're only processing fully closed candles.

                        # Let's refine this: If the fetched data includes a candle whose 'date' (start time)
                        # plus the interval duration extends into the future relative to `now`, it's incomplete.
                        # Or, if the last candle's 'date' is equal to the current `now` (minus some small buffer),
                        # it's the current candle being formed.

                        # The safest bet: always exclude the very last candle received from the API
                        # if you are querying up to `now`, as it's highly likely to be still forming.
                        processed_df = df.iloc[:-1] # Consider all but the very last candle

                        if len(processed_df) < 3:
                            print(f"⛔ Not enough *closed* candles for {symbol}. Need at least 3, got {len(processed_df)}.")
                            continue

                        # Loop through the *closed* candles for signal generation
                        for i in range(2, len(processed_df)):
                            prev2 = processed_df.iloc[i - 2]
                            prev1 = processed_df.iloc[i - 1]
                            current_closed_candle = processed_df.iloc[i] # This is now guaranteed to be a closed candle

                            volume_delta = ((current_closed_candle['volume'] - prev1['volume']) / prev1['volume']) * 100 if prev1['volume'] != 0 else 0
                            volume_threshold = 100  # 🔍 sensitive for test######################################################

                            signal = None
                            if current_closed_candle['close'] > max(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                                signal = 'BULLISH'
                                # Orders should be placed based on the closing price of the signal candle.
                                result = place_entry_order(symbol, signal, current_closed_candle['close'])
                                if result:
                                    active_orders.append(result)

                            elif current_closed_candle['close'] < min(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                                signal = 'BEARISH'
                                result = place_entry_order(symbol, signal, current_closed_candle['close'])
                                if result:
                                    active_orders.append(result)

                            if signal:
                                signal_data = {
                                    'symbol': symbol,
                                    'signal_type': signal,
                                    'signal_time': pd.to_datetime(current_closed_candle['date']).strftime("%Y-%m-%d %H:%M:%S"),
                                    'open': float(current_closed_candle['open']),
                                    'high': float(current_closed_candle['high']),
                                    'low': float(current_closed_candle['low']),
                                    'close': float(current_closed_candle['close']),
                                    'volume': float(current_closed_candle['volume']),
                                    'volume_delta': float(round(volume_delta, 2))
                                }
                                store_signal_in_db(signal_data)
                                signal_count += 1

                        print(f"✅ {symbol}: {signal_count} signal(s) inserted.")
                    else:
                        print(f"⛔ No complete candles found for {symbol} to process.")

                except Exception as e:
                    print(f"❌ Error with {symbol}: {e}")

        time.sleep(60)  # 🔁 run every minute

# Example usage (assuming you have the necessary KiteConnect setup and dummy functions working)
# background_signal_job()


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
            exchange=kite.EXCHANGE_MCX,
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
            exchange=kite.EXCHANGE_MCX,
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

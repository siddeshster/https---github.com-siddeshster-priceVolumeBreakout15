# signal_worker_logicupdated.py
import csv
import sqlite3
import time
import pandas as pd
from datetime import datetime, timedelta
from kiteconnect import KiteConnect
import json
import requests
import re
import pytz

# from app_vpa import is_market_time, parse_interval_to_timedelta # Assuming these are available or defined locally if not imported from app_vpa

# Load config
with open('Config/config.json') as f:
    config = json.load(f)

api_key = config['api_key']
access_token = config['access_token']
telegram_bot_token = config.get("telegram_bot_token")
telegram_chat_id = config.get("telegram_chat_id")

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

with open('Config/instrument_config.json') as j:
    instr_config = json.load(j)
mcx_interval_str = instr_config['mcx_fno']['interval']
IST_TIMEZONE = pytz.timezone('Asia/Kolkata')
DB_PATH = 'signals.db'
CSV_PATH = 'InstrumentsData/instruments_mcx.csv'

# --- Utility functions (assuming they are defined or imported correctly) ---
# For demonstration, I'll include placeholder definitions if they're not provided in context
# In your actual setup, ensure these are correctly imported or defined.

# Placeholder for is_market_time and parse_interval_to_timedelta if they're not in app_vpa or are defined elsewhere
def is_market_time(dt_obj):
    # Simplified market time check for MCX (e.g., 9 AM to 11:30 PM IST)
    # This needs to be robust for actual market hours.
    hour = dt_obj.hour
    minute = dt_obj.minute
    # Example for MCX: 9:00 to 23:30 (next day for 00:00 to 09:00)
    if (hour > 9 or (hour == 9 and minute >= 0)) and \
       (hour < 23 or (hour == 23 and minute <= 30)):
        return True
    return False

def parse_interval_to_timedelta(interval_str):
    if interval_str.endswith('minute'):
        minutes = int(interval_str.replace('minute', '').strip())
        return timedelta(minutes=minutes)
    elif interval_str.endswith('hour'):
        hours = int(interval_str.replace('hour', '').strip())
        return timedelta(hours=hours)
    raise ValueError(f"Unsupported interval format: {interval_str}")

# --- End Utility functions ---

# Load instrument map
symbol_token_map2 = {}
def read_instruments_from_csv():
    instruments = []
    with open(CSV_PATH, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('exchange') == 'MCX' and row.get('instrument_type') == 'FUT':
                trading_symbol = row['tradingsymbol']
                # Add try-except for robust integer conversion
                try:
                    instrument_token = int(row['instrument_token'])
                    instruments.append(trading_symbol)
                    symbol_token_map2[trading_symbol] = instrument_token
                except (ValueError, KeyError) as e:
                    print(f"Skipping row for {trading_symbol} due to invalid or missing 'instrument_token': {row.get('instrument_token', 'N/A')} - {e}")
    return instruments

# Parse the interval once at startup
try:
    mcx_interval_td = parse_interval_to_timedelta(mcx_interval_str)
    print(f"✅ Parsed MCX interval: {mcx_interval_td}")
except ValueError as e:
    print(f"Fatal error parsing interval: {e}. Exiting.")
    exit()

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
                signal_time TEXT, -- Storing as TEXT in ISO format (UTC)
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                volume_delta REAL,
                UNIQUE(symbol, signal_time) -- Add a unique constraint to prevent duplicates at the DB level
            )
        ''')

        # Clean the signal_time string just in case it contains " IST" or other artifacts
        # This is a defensive measure; ideally, result['signal_time'] should already be clean UTC string.
        signal_time_to_store = result['signal_time']
        if " IST" in signal_time_to_store:
            signal_time_to_store = signal_time_to_store.replace(" IST", "").strip()


        # Check for duplicates using the new, consistent UTC string format
        cursor.execute(
            "SELECT COUNT(*) FROM signals WHERE symbol = ? AND signal_time = ?",
            (result['symbol'], signal_time_to_store)
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
                signal_time_to_store, # This will now be the UTC ISO string, potentially cleaned
                float(result['open']),
                float(result['high']),
                float(result['low']),
                float(result['close']),
                float(result['volume']),
                float(result['volume_delta'])
            ))
            conn.commit()
            print(f"✅ INSERTED: {result['symbol']} ({result['signal_type']}) @ {signal_time_to_store}")
            send_telegram_alert(
                symbol=result['symbol'],
                signal_type=result['signal_type'],
                price=result['close'],
                # Pass the original IST aware time to the alert, if preferred for users
                time=result['original_signal_time_ist'].strftime("%Y-%m-%d %H:%M:%S %Z"),
                volume_delta=result['volume_delta']
            )

        else:
            print(f"⚠️ DUPLICATE: {result['symbol']} @ {signal_time_to_store} (already in DB)")

        conn.close()
    except sqlite3.IntegrityError as e: # Catch specific integrity error for unique constraint
        print(f"⚠️ DUPLICATE (DB Constraint): {result['symbol']} @ {signal_time_to_store} - {e}")
        # This catch block is important if you add the UNIQUE constraint
        conn.rollback() # Rollback the transaction
        conn.close()
    except Exception as e:
        print(f"❌ Error storing signal: {e}")


def get_last_complete_candle_time(now_aware, interval_td):
    # This function needs to return an IST-aware datetime, as before.
    # The conversion to UTC for storage happens later.
    total_minutes = now_aware.hour * 60 + now_aware.minute
    interval_minutes = int(interval_td.total_seconds() / 60)

    last_complete_interval_minute_start = (total_minutes // interval_minutes) * interval_minutes

    last_complete_candle_end_time = now_aware.replace(
        hour=last_complete_interval_minute_start // 60,
        minute=last_complete_interval_minute_start % 60,
        second=0, microsecond=0
    )
    return last_complete_candle_end_time


def background_signal_job():
    instruments = read_instruments_from_csv()
    if not instruments:
        print("❌ No instruments found. Exiting.")
        return

    print("🚀 Running signal worker...")

    # last_processed_candle_end_times should hold IST-aware datetimes
    last_processed_candle_end_times = {symbol: IST_TIMEZONE.localize(datetime.min.replace(year=1900)) for symbol in instruments}


    while True:
        now_naive = datetime.now()
        now_aware = IST_TIMEZONE.localize(now_naive)

        if is_market_time(now_naive):
            for symbol in instruments:
                print(f"→ Checking {symbol}...")

                instrument_token = symbol_token_map2.get(symbol)
                if not instrument_token:
                    print(f"⛔ Token not found for {symbol}")
                    print("📎 Available symbols in map:", list(symbol_token_map2.keys())[:5])
                    continue

                lookback_start_time = now_aware - (mcx_interval_td * 5)
                market_open_today = now_aware.replace(hour=9, minute=0, second=0, microsecond=0)
                from_date = max(lookback_start_time, market_open_today)

                to_date_for_historical = get_last_complete_candle_time(now_aware, mcx_interval_td)

                if to_date_for_historical <= last_processed_candle_end_times[symbol]:
                    continue

                try:
                    candles = kite.historical_data(
                        instrument_token=instrument_token,
                        from_date=from_date,
                        to_date=to_date_for_historical,
                        interval=mcx_interval_str,
                        continuous=False
                    )

                    df = pd.DataFrame(candles)
                    if df.empty:
                        print(f"⛔ No historical data fetched for {symbol} up to {to_date_for_historical}.")
                        continue

                    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume']

                    # Convert 'date' column to IST-aware datetime
                    df['date'] = pd.to_datetime(df['date']).dt.tz_convert(IST_TIMEZONE)

                    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
                    df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])
                    df = df.sort_values('date').reset_index(drop=True)

                    if len(df) < 3:
                        print(f"⛔ Not enough *complete* candles for {symbol} to generate a signal ({len(df)} found).")
                        continue

                    prev2 = df.iloc[-3]
                    prev1 = df.iloc[-2]
                    current_closed_candle = df.iloc[-1]

                    if current_closed_candle['date'] <= last_processed_candle_end_times[symbol]:
                        print(f"⏳ {symbol}: Candle ending at {current_closed_candle['date'].strftime('%Y-%m-%d %H:%M:%S %Z')} already processed.")
                        continue

                    volume_delta = ((current_closed_candle['volume'] - prev1['volume']) / prev1['volume']) * 100 if prev1['volume'] != 0 else 0
                    volume_threshold = 100

                    signal = None
                    if current_closed_candle['close'] > max(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                        signal = 'BULLISH'
                    elif current_closed_candle['close'] < min(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                        signal = 'BEARISH'

                    if signal:
                        next_candle_start_time_ist = current_closed_candle['date'] + mcx_interval_td
                        # Convert the signal time to UTC for consistent DB storage
                        next_candle_start_time_utc = next_candle_start_time_ist.astimezone(pytz.utc)

                        signal_data = {
                            'symbol': symbol,
                            'signal_type': signal,
                            # Store in UTC ISO format (no timezone suffix, as it's UTC)
                            'signal_time': next_candle_start_time_utc.strftime("%Y-%m-%d %H:%M:%S"),
                            'original_signal_time_ist': next_candle_start_time_ist, # Keep for Telegram alert
                            'open': float(current_closed_candle['open']),
                            'high': float(current_closed_candle['high']),
                            'low': float(current_closed_candle['low']),
                            'close': float(current_closed_candle['close']),
                            'volume': float(current_closed_candle['volume']),
                            'volume_delta': float(round(volume_delta, 2))
                        }
                        store_signal_in_db(signal_data)
                        last_processed_candle_end_times[symbol] = current_closed_candle['date']
                    else:
                        print(f"No signal for {symbol} based on last closed candle ending at {current_closed_candle['date'].strftime('%Y-%m-%d %H:%M:%S %Z')}")
                        last_processed_candle_end_times[symbol] = current_closed_candle['date']

                except Exception as e:
                    print(f"❌ Error with {symbol}: {e}")

        time.sleep(30) # Check every 30 seconds

def send_telegram_alert(symbol, signal_type, price, time, volume_delta):
    try:
        if not telegram_bot_token or not telegram_chat_id:
            raise ValueError("Missing Telegram config keys")

        text = (
            f"📡 *{signal_type}* signal on *{symbol}*\n"
            f"Price: ₹{price}\n"
            f"Time: {time}\n" # This 'time' argument already contains the formatted IST string
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
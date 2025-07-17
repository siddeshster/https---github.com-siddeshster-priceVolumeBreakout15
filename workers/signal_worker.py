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

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

with open('Config/instrument_config.json') as f:
    config = json.load(f)
mcx_interval = config['mcx_fno']['interval']
mcx_volume_threshold = config['mcx_fno']['volume_threshold']

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
    end = now.replace(hour=23, minute=30, second=0, microsecond=0)
    return start <= now <= end


def background_signal_job():
    instruments = read_instruments_from_csv()
    if not instruments:
        print("❌ No instruments found. Exiting.")
        return

    print("🚀 Running signal worker in 3-minute test mode...")

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
                        interval=mcx_interval,  # ⏱ test mode######################################################
                        continuous=False
                    )

                    candles_today = [c for c in candles if pd.to_datetime(c['date']).date() == now.date()]
                    df = pd.DataFrame(candles_today)
                    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume']

                    if len(df) < 3:
                        print(f"⛔ Not enough candles for {symbol}")
                        continue

                    for i in range(2, len(df)):
                        prev2 = df.iloc[i - 2]
                        prev1 = df.iloc[i - 1]
                        current = df.iloc[i]

                        volume_delta = ((current['volume'] - prev1['volume']) / prev1['volume']) * 100 if prev1['volume'] != 0 else 0
                        volume_threshold = mcx_volume_threshold  # 🔍 sensitive for test######################################################

                        signal = None
                        if current['close'] > max(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                            signal = 'BULLISH'
                        elif current['close'] < min(prev1['close'], prev2['close']) and volume_delta > volume_threshold:
                            signal = 'BEARISH'

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

def send_telegram_alert(symbol, signal_type, price, time,volume_delta):
    try:
        bot_token = config["telegram_bot_token"]
        chat_id = config["telegram_chat_id"]

        text = f"📡 *{signal_type}* signal on *{symbol}*\nPrice: ₹{price}\nTime: {time}\nVolume%:{volume_delta}%"
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        payload = {
            "chat_id": chat_id,
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

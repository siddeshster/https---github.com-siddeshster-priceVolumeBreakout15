import pandas as pd
import sqlite3
import time
from datetime import datetime
from app_vpa import process_signal_for_instrument

DB_PATH = "database.db"
CSV_PATH = "InstrumentsData/instruments_mcx.csv"

def run_signal_job():
    while True:
        print(f"\n[INFO] Starting signal scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            df = pd.read_csv(CSV_PATH)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            # Create table with UNIQUE constraint on symbol + signal_time + signal_type
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
                    volume_delta REAL,
                    UNIQUE(symbol, signal_type, signal_time)
                )
            ''')

            for _, row in df.iterrows():
                symbol = row['tradingsymbol']
                result = process_signal_for_instrument(symbol)

                if result:
                    try:
                        cursor.execute('''
                            INSERT INTO signals (
                                symbol, signal_type, signal_time,
                                open, high, low, close,
                                volume, volume_delta
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            result['symbol'], result['signal'], result['signal_time'],
                            result['open'], result['high'], result['low'], result['close'],
                            result['volume'], result['volume_delta']
                        ))
                        print(f"[NEW SIGNAL] {result['signal']} - {symbol} @ {result['signal_time']} | Close: {result['close']}, ΔVol: {result['volume_delta']}%")
                    except sqlite3.IntegrityError:
                        print(f"[SKIPPED] Duplicate {result['signal']} signal for {symbol} @ {result['signal_time']}")

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[ERROR] {str(e)}")

        print("[INFO] Sleeping for 2 minutes...\n")
        time.sleep(120)


if __name__ == "__main__":
    run_signal_job()

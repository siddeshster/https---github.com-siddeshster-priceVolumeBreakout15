from flask import Flask, render_template, request
from kiteconnect import KiteConnect
import json
import pandas as pd
from datetime import datetime, timedelta
from time import sleep
app = Flask(__name__)

# Load config
with open('config.json') as f:
    config = json.load(f)

api_key = config['api_key']
access_token = config['access_token']
prev_day_interval= config['prev_day_interval']

# // minute
# // 3minute
# // 5minute
# // 10minute
# // 15minute
# // 30minute
# // 60minute
# // day
# // week
# // month


kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

# Load instrument list from CSV
# instruments_df = pd.read_csv("instruments_short.csv")
# instrument_map = dict(zip(instruments_df['tradingsymbol'], instruments_df['instrument_token']))

instruments_df = pd.read_csv("instruments_nfo.csv")
instrument_map = dict(zip(instruments_df['tradingsymbol'], instruments_df['instrument_token']))

# instruments_df_2 = pd.read_csv("instruments_short.csv")
# instrument_map_2 = dict(zip(instruments_df['tradingsymbol'], instruments_df['instrument_token']))


def get_previous_day_stats(token, prev_date_str):
    previous_day = datetime.strptime(prev_date_str, "%Y-%m-%d")
    from_date = previous_day.replace(hour=9, minute=15)
    to_date = previous_day.replace(hour=15, minute=30)

    candles = kite.historical_data(token, from_date, to_date, prev_day_interval)
    if not candles:
        return {}

    df = pd.DataFrame(candles)
    if df.empty or not {'close', 'high', 'low'}.issubset(df.columns):
        return {}

    return {
        "date": prev_date_str,
        "highest_close": df['close'].max(),
        "lowest_close": df['close'].min(),
        "high": df['high'].max(),
        "low": df['low'].min()
    }

def get_current_day_data(token, interval, prev_stats, threshold, date_str, sort_order):
    day = datetime.strptime(date_str, "%Y-%m-%d")
    from_date = day.replace(hour=9, minute=15)
    to_date = day.replace(hour=23, minute=30)

    candles = kite.historical_data(token, from_date, to_date, interval)
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df['volume_change_%'] = df['volume'].pct_change() * 100
    df['signal'] = ""
    df['reversal'] = ""

    for i in range(1, len(df)):
        close = df.loc[i, 'close']
        prev_close = df.loc[i - 1, 'close']
        volume_change = df.loc[i, 'volume_change_%']

        if close > prev_stats['highest_close'] and volume_change > threshold:
            df.loc[i, 'signal'] = "BULLISH"
        elif close < prev_stats['lowest_close'] and volume_change > threshold:
            df.loc[i, 'signal'] = "BEARISH"

        if prev_close > prev_stats['highest_close'] and close < prev_stats['highest_close'] and volume_change > threshold:
            df.loc[i, 'reversal'] = "BEARISH REVERSAL"
        elif prev_close < prev_stats['lowest_close'] and close > prev_stats['lowest_close'] and volume_change > threshold:
            df.loc[i, 'reversal'] = "BULLISH REVERSAL"

    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d %H:%M:%S')
    df = df.sort_values(by='date', ascending=(sort_order == 'asc'))

    return df

@app.route('/', methods=['GET', 'POST'])
def index():
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    if request.method == 'POST':
        trading_symbol = request.form['instrument']
        instrument_token = instrument_map.get(trading_symbol)
        date = request.form['date']
        prev_date = request.form['prev_date']
        interval = request.form['interval']
        volume_threshold = float(request.form['volume_breakout'])
        sort_order = 'asc' if 'sortSwitch' in request.form else 'desc'

        prev_stats = get_previous_day_stats(instrument_token, prev_date)
        if not prev_stats:
            return render_template("index.html",
                                   instrument_map=instrument_map,
                                   symbol=trading_symbol,
                                   date=date,
                                   prev_date=prev_date,
                                   interval=interval,
                                   volume_threshold=volume_threshold,
                                   sort_order=sort_order,
                                   prev_stats={},
                                   data=[],
                                   no_data=True)

        df = get_current_day_data(
            instrument_token,
            interval,
            prev_stats,
            volume_threshold,
            date,
            sort_order
        )

        return render_template("index.html",
                               instrument_map=instrument_map,
                               symbol=trading_symbol,
                               date=date,
                               prev_date=prev_date,
                               interval=interval,
                               volume_threshold=volume_threshold,
                               sort_order=sort_order,
                               prev_stats=prev_stats,
                               data=df.to_dict(orient='records'),
                               no_data=df.empty)

    # GET request: initial load
    return render_template("index.html",
                           instrument_map=instrument_map,
                           data=[],
                           prev_stats={},
                           no_data=False,
                           sort_order='desc',
                           date=today.strftime('%Y-%m-%d'),
                           prev_date=yesterday.strftime('%Y-%m-%d'))

@app.route('/signals', methods=['GET', 'POST'])
def signal_scan():
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    results = []

    if request.method == 'POST':
        date = request.form['date']
        prev_date = request.form['prev_date']
        interval = request.form['interval']
        volume_threshold = float(request.form['volume_breakout'])
        filter_type = request.form.get('filter', 'all')

        for symbol, token in instrument_map.items():
            try:
                sleep(0.35)
                prev_stats = get_previous_day_stats(token, prev_date)
                if not prev_stats:
                    continue

                df = get_current_day_data(token, interval, prev_stats, volume_threshold, date, 'desc')

                signal_counts = df['signal'].value_counts()
                reversal_counts = df['reversal'].value_counts()

                # Only add rows where at least one signal or reversal is present
                for signal in signal_counts.index:
                    if signal:  # avoid empty string
                        results.append({
                            'symbol': symbol,
                            'type': signal,
                            'count': int(signal_counts[signal])
                        })

                for reversal in reversal_counts.index:
                    if reversal:  # avoid empty string
                        results.append({
                            'symbol': symbol,
                            'type': reversal,
                            'count': int(reversal_counts[reversal])
                        })

            except Exception as e:
                print(f"Error scanning {symbol}: {e}")
                continue

        return render_template("signals.html", results=results,
                               date=date, prev_date=prev_date,
                               interval=interval, volume_threshold=volume_threshold,
                               filter=filter_type)

    return render_template("signals.html", results=[],
                           date=today.strftime('%Y-%m-%d'),
                           prev_date=yesterday.strftime('%Y-%m-%d'),
                           interval='5minute', volume_threshold=100,
                           filter='all')
@app.route("/ltp-signals")
def ltp_signals():
    from_date_30 = datetime.now() - timedelta(days=30)
    from_date_90 = datetime.now() - timedelta(days=90)
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    results = []

    for symbol, token in instrument_map.items():
        try:
            # Today OHLC (use 'day' to get daily LTP etc.)
            ohlc_today = kite.ohlc([token])[str(token)]
            ltp = ohlc_today['last_price']
            high_today = ohlc_today['high']
            vol_today = ohlc_today['volume']

            # Yesterday's candle (1-day historical)
            y_hist = kite.historical_data(token, yesterday, today, "day")
            if not y_hist:
                continue
            y_candle = y_hist[0]
            y_high = y_candle['high']
            y_low = y_candle['low']
            y_close = y_candle['close']
            y_vol = y_candle['volume']

            # 30-day historical
            candles_30 = kite.historical_data(token, from_date_30, datetime.now(), "day")
            df_30 = pd.DataFrame(candles_30)
            high_30_max = df_30['high'].max()
            low_30_max = df_30['low'].min()

            # 90-day historical
            candles_90 = kite.historical_data(token, from_date_90, datetime.now(), "day")
            df_90 = pd.DataFrame(candles_90)
            high_90_max = df_90['high'].max()
            low_90_max = df_90['low'].min()

            signal = ""
            if ltp > high_30_max and ltp > high_90_max and vol_today > y_vol:
                signal = "BULLISH"
            elif ltp < low_30_max and ltp < low_90_max and vol_today > y_vol:
                signal = "BEARISH"

            if signal:
                results.append({
                    "symbol": symbol,
                    "ltp": ltp,
                    "today_high": high_today,
                    "today_volume": vol_today,
                    "y_high": y_high,
                    "y_low": y_low,
                    "y_close": y_close,
                    "y_volume": y_vol,
                    "30_max_high": high_30_max,
                    "30_max_low": low_30_max,
                    "90_max_high": high_90_max,
                    "90_max_low": low_90_max,
                    "signal": signal
                })

        except Exception as e:
            print(f"Error for {symbol}: {e}")
            continue

    return render_template("ltp_signals.html", results=results)


if __name__ == "__main__":
    app.run(debug=True)

from flask import Flask, render_template, request
from kiteconnect import KiteConnect
import json
import pandas as pd
from datetime import datetime, timedelta

app = Flask(__name__)

# Load config
with open('config.json') as f:
    config = json.load(f)

api_key = config['api_key']
access_token = config['access_token']

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

# Load instrument list from CSV
instruments_df = pd.read_csv("instruments.csv")
instrument_map = dict(zip(instruments_df['tradingsymbol'], instruments_df['instrument_token']))

def get_previous_day_stats(token, prev_date_str):
    previous_day = datetime.strptime(prev_date_str, "%Y-%m-%d")
    from_date = previous_day.replace(hour=9, minute=15)
    to_date = previous_day.replace(hour=15, minute=30)

    candles = kite.historical_data(token, from_date, to_date, "15minute")
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

if __name__ == "__main__":
    app.run(debug=True)

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
instruments_df = pd.read_csv("instruments_nfo.csv")
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
    to_date = day.replace(hour=15, minute=30)

    candles = kite.historical_data(token, from_date, to_date, interval)
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    required = {'date', 'open', 'high', 'low', 'close', 'volume'}
    if df.empty or not required.issubset(df.columns):
        return pd.DataFrame()

    df['volume_change_%'] = df['volume'].pct_change() * 100
    df['signal'] = ""

    for i in range(1, len(df)):
        close = df.loc[i, 'close']
        volume_change = df.loc[i, 'volume_change_%']
        if close > prev_stats['highest_close'] and volume_change > threshold:
            df.loc[i, 'signal'] = "BULLISH"
        elif close < prev_stats['lowest_close'] and volume_change > threshold:
            df.loc[i, 'signal'] = "BEARISH"

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
        reversal_threshold = float(request.form['reversal_breakout'])
        sort_order = 'asc' if 'sortSwitch' in request.form else 'desc'

        prev_stats = get_previous_day_stats(instrument_token, prev_date)
        if not prev_stats:
            return render_template("index.html", instrument_map=instrument_map,
                                   symbol=trading_symbol, date=date, prev_date=prev_date,
                                   interval=interval, volume_threshold=volume_threshold,
                                   reversal_threshold=reversal_threshold, sort_order=sort_order,
                                   prev_stats={}, data=[], no_data=True)

        df = get_current_day_data(instrument_token, interval, prev_stats, volume_threshold, date, sort_order)

        return render_template("index.html", instrument_map=instrument_map,
                               symbol=trading_symbol, date=date, prev_date=prev_date,
                               interval=interval, volume_threshold=volume_threshold,
                               reversal_threshold=reversal_threshold, sort_order=sort_order,
                               prev_stats=prev_stats, data=df.to_dict(orient='records'), no_data=df.empty)

    return render_template("index.html", instrument_map=instrument_map,
                           data=[], prev_stats={}, no_data=False,
                           sort_order='desc',
                           date=today.strftime('%Y-%m-%d'),
                           prev_date=yesterday.strftime('%Y-%m-%d'))

@app.route('/ltp-signals')
def ltp_signals():
    from_date_today = datetime.now().replace(hour=9, minute=15)
    to_date_today = datetime.now().replace(hour=15, minute=30)
    today_str = datetime.now().strftime('%Y-%m-%d')
    yesterday = datetime.now() - timedelta(days=1)
    prev_date_str = yesterday.strftime('%Y-%m-%d')

    result = []

    for _, row in instruments_df.iterrows():
        symbol = row['tradingsymbol']
        token = int(row['instrument_token'])

        try:
            # Today 5-minute data
            today_data = kite.historical_data(token, from_date_today, to_date_today, "5minute")
            if not today_data:
                continue
            today_df = pd.DataFrame(today_data)
            today_df['date'] = pd.to_datetime(today_df['date'])
            ltp = today_df.iloc[-1]['close']
            high = today_df['high'].max()
            volume = today_df['volume'].sum()

            # Yesterday data
            y_from = yesterday.replace(hour=9, minute=15)
            y_to = yesterday.replace(hour=15, minute=30)
            y_data = kite.historical_data(token, y_from, y_to, "day")
            if not y_data:
                continue
            y_df = pd.DataFrame(y_data)
            y_close = y_df.iloc[-1]['close']
            y_high = y_df['high'].max()
            y_low = y_df['low'].min()
            y_volume = y_df['volume'].sum()

            # Last 30 days
            from_30 = datetime.now() - timedelta(days=30)
            data_30 = kite.historical_data(token, from_30, datetime.now(), "day")
            df_30 = pd.DataFrame(data_30)
            max_high_30 = df_30['high'].max()
            max_low_30 = df_30['low'].min()
            high_30 = df_30['high'].iloc[-1]
            low_30 = df_30['low'].iloc[-1]

            # Last 90 days
            from_90 = datetime.now() - timedelta(days=90)
            data_90 = kite.historical_data(token, from_90, datetime.now(), "day")
            df_90 = pd.DataFrame(data_90)
            max_high_90 = df_90['high'].max()
            max_low_90 = df_90['low'].min()
            high_90 = df_90['high'].iloc[-1]
            low_90 = df_90['low'].iloc[-1]

            # Signal Logic
            signal = ""
            if ltp > max_high_30 and ltp > max_high_90 and volume > y_volume:
                signal = "BULLISH"
            elif ltp < max_low_30 and ltp < max_low_90 and volume > y_volume:
                signal = "BEARISH"

            result.append({
                'symbol': symbol,
                'ltp': ltp,
                'high': high,
                'volume': volume,
                'yest_high': y_high,
                'yest_low': y_low,
                'yest_close': y_close,
                'yest_volume': y_volume,
                'low_30': low_30,
                'high_30': high_30,
                'max_high_30': max_high_30,
                'max_low_30': max_low_30,
                'low_90': low_90,
                'high_90': high_90,
                'max_high_90': max_high_90,
                'max_low_90': max_low_90,
                'signal': signal
            })

        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            continue

    return render_template("ltp_signals.html", data=result)


if __name__ == "__main__":
    app.run(debug=True)

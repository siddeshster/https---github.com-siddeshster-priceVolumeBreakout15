
from flask import Flask, render_template, request
from kiteconnect import KiteConnect
import json
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session
import hashlib
from flask import session
from datetime import timedelta
import sqlite3
from workers.signal_worker import send_telegram_alert
from workers.signal_nse_stock_fno_worker import send_telegram_alert
from workers.signal_nse_stock_worker import send_telegram_alert
from workers.signal_worker import send_telegram_alert
from datetime import datetime, timedelta
import pandas as pd
from flask import jsonify, request


app = Flask(__name__)
app.secret_key = 'Paswd#1234'
app.permanent_session_lifetime = timedelta(days=1)


# Load config
with open('Config/config.json') as f:
    config = json.load(f)

api_key = config['api_key']
access_token = config['access_token']

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

# Load instrument list from CSV
instruments_df = pd.read_csv("InstrumentsData/instruments_short.csv")
instrument_map = dict(zip(instruments_df['tradingsymbol'], instruments_df['instrument_token']))

def get_current_day_data(token, interval, threshold, date_str, sort_order):
    day = datetime.strptime(date_str, "%Y-%m-%d")
    from_date = day.replace(hour=9, minute=00)
    to_date = day.replace(hour=23, minute=30)

    candles = kite.historical_data(token, from_date, to_date, interval)
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df['volume_change_%'] = df['volume'].pct_change() * 100
    df['signal'] = ""

    for i in range(2, len(df)):
        close = df.loc[i, 'close']
        prev_close = df.loc[i - 1, 'close']
        vol_change = df.loc[i, 'volume_change_%']
        vol_m1 = df.loc[i - 1, 'volume_change_%']
        vol_m2 = df.loc[i - 2, 'volume_change_%']

        if close > prev_close and vol_change > threshold and vol_m1 < 0 and vol_m2 < 0:
            df.loc[i, 'signal'] = "BULLISH"
        elif close < prev_close and vol_change > threshold and vol_m1 < 0 and vol_m2 < 0:
            df.loc[i, 'signal'] = "BEARISH"

    # SIGNAL 2
    df['signal2'] = ""
    df['volume_change_%'] = df['volume'].pct_change() * 100
    for i in range(2, len(df)):
        close = df.loc[i, 'close']
        prev_close_1 = df.loc[i - 1, 'close']
        prev_close_2 = df.loc[i - 2, 'close']
        vol_change = df.loc[i, 'volume_change_%']
        prev_vol_1 = df.loc[i - 1, 'volume_change_%']
        prev_vol_2 = df.loc[i - 2, 'volume_change_%']

        if (
                close > max(prev_close_1, prev_close_2)
                and vol_change > threshold
                and prev_vol_1 < vol_change
                and prev_vol_2 < vol_change
        ):
            df.loc[i, 'signal2'] = "BULLISH"

        elif (
                close < min(prev_close_1, prev_close_2)
                and vol_change > threshold
                and prev_vol_1 < vol_change
                and prev_vol_2 < vol_change
        ):
            df.loc[i, 'signal2'] = "BEARISH"

    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d %H:%M:%S')
    df = df.sort_values(by='date', ascending=(sort_order == 'asc'))

    # print(df[['date', 'close', 'volume', 'signal', 'signal2']].tail(10))  
    return df


@app.route('/', methods=['GET', 'POST'])
def index():
    if 'username' not in session:  # ✅ match the session key used at login
        return redirect(url_for('login'))

    today = datetime.now().date()
    results = []
    no_data = False

    if request.method == 'POST':
        trading_symbol = request.form['instrument']
        instrument_token = instrument_map.get(trading_symbol)
        date = request.form['date']
        interval = request.form['interval']
        volume_threshold = float(request.form['volume_breakout'])
        sort_order = 'asc' if 'sortSwitch' in request.form else 'desc'

        df = get_current_day_data(instrument_token, interval, volume_threshold, date, sort_order)

        return render_template("index_vpa.html", instrument_map=instrument_map,
                               symbol=trading_symbol,
                               date=date,
                               interval=interval,
                               volume_threshold=volume_threshold,
                               sort_order=sort_order,
                               data=df.to_dict(orient='records'),
                               no_data=df.empty)

    return render_template("index_vpa.html",
                           instrument_map=instrument_map,
                           data=[],
                           no_data=False,
                           sort_order='desc',
                           date=today.strftime('%Y-%m-%d'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    with open('Config/users.json') as f:
        users = json.load(f)

    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Validate credentials from users.json
        if username in users and users[username]['password'] == password:
            session.permanent = True  # 🔐 Keep session alive until logout
            session['username'] = username
            return redirect(url_for('index'))
        else:
            error = "Invalid credentials"

    return render_template("login.html", error=error)


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))  # or index/homepage


@app.before_request
def extend_session():
    session.permanent = True


@app.route("/signals")
def view_signals():
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT * FROM signals
        WHERE DATE(signal_time) = ?
        ORDER BY signal_time DESC
    """, (today,))
    rows = cursor.fetchall()
    conn.close()

    return render_template("signals.html", signals=rows)

@app.route("/signals/table")
def signal_table_partial_mcx():
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT * FROM signals
        WHERE DATE(signal_time) = ?
        ORDER BY signal_time DESC
    """, (today,))
    rows = cursor.fetchall()
    conn.close()

    return render_template("partials/signal_rows.html", signals=rows)
# ---------------------------------------------------------------------------
@app.route("/signals_nse_stock_fno")
def view_signals_nse_stock_fno():
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT * FROM signals_nse_stock_fno
        WHERE DATE(signal_time) = ?
        ORDER BY signal_time DESC
    """, (today,))
    rows = cursor.fetchall()
    conn.close()

    return render_template("signals_nse_stock_fno.html", signals=rows)


@app.route("/signals_nse_stock_fno/table")
def signal_table_partial_nse_stock_fno():
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT * FROM signals_nse_stock_fno
        WHERE DATE(signal_time) = ?
        ORDER BY signal_time DESC
    """, (today,))
    rows = cursor.fetchall()
    conn.close()

    return render_template("partials/signal_rows_nse_fno.html", signals=rows)
# ---------------------------------------------------------------------------
@app.route("/signals_nse_stock")
def view_signals_nse_stock():
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT * FROM signals_nse_stocks
        WHERE DATE(signal_time) = ?
        ORDER BY signal_time DESC
    """, (today,))
    rows = cursor.fetchall()
    conn.close()

    return render_template("signals_nse_stock.html", signals=rows)


@app.route("/signals_nse_stock/table")
def signal_table_partial_nse_stock():
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT * FROM signals_nse_stocks
        WHERE DATE(signal_time) = ?
        ORDER BY signal_time DESC
    """, (today,))
    rows = cursor.fetchall()
    conn.close()

    return render_template("partials/signal_rows_nse_stocks.html", signals=rows)

# ---------------------------------------------------------------------------


@app.route("/test-telegram", methods=["POST"])
def test_telegram():

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    send_telegram_alert("TESTSYM", "BULLISH", 123.45, now_str)
    return jsonify({"message": "✅ Test Telegram Alert Sent"})



if __name__ == "__main__":
    app.run(debug=True)

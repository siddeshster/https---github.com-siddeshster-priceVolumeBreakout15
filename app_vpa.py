
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify 

from kiteconnect import KiteConnect
import json
import pandas as pd
from datetime import datetime, timedelta

import sqlite3
from workers.signal_worker import send_telegram_alert
from workers.signal_nse_stock_fno_worker import send_telegram_alert
from workers.signal_nse_stock_worker import send_telegram_alert
from workers.signal_worker import send_telegram_alert

from auth_utils import verify_user, get_user_roles,update_session_status

from functools import wraps

from admin_routes import admin_dashboard, admin_bp
import logging
from logging.handlers import RotatingFileHandler
import os

# Create logs directory if not exists
if not os.path.exists('logs'):
    os.makedirs('logs')

log_file = 'logs/app.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


app = Flask(__name__)
app.secret_key = 'Paswd#1234'
app.permanent_session_lifetime = timedelta(days=1)
app.register_blueprint(admin_bp)


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

def login_required(role=None):
    def wrapper(func):
        @wraps(func)
        def secure_view(*args, **kwargs):
            if 'username' not in session:
                flash("🔒 Please login to access this page", "warning")
                return redirect(url_for('login'))

            if role:
                roles = session.get('roles', {})
                if roles.get(role) != 'Y':
                    flash(f"🚫 Access denied: Missing permission '{role}'", "danger")
                    return redirect(url_for('index'))

            return func(*args, **kwargs)
        return secure_view
    return wrapper


@app.route('/vpa_analysis', methods=['GET', 'POST'])
@login_required()
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
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if verify_user(username, password):
            conn = sqlite3.connect('signals.db')
            cursor = conn.cursor()
            cursor.execute("SELECT status, validfrom, validto FROM USERS WHERE username = ?", (username,))
            user = cursor.fetchone()
            conn.close()

            if user:
                status, validfrom, validto = user
                today = datetime.today().date()
                # if status == 'ACTIVE' and validfrom <= str(today) <= validto:
                if status == 'ACTIVE':
                    session['username'] = username
                    session['roles'] = get_user_roles(username)
                    return redirect(url_for('dashboard'))
                    update_sessions(username)
                else:
                    flash("⚠️ User access expired or inactive.", "danger")
            else:
                flash("❌ User not found.", "danger")
        else:
            flash("❌ Invalid credentials", "danger")

    return render_template("login.html",datetime=datetime)




@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM USERS_ROLES WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()

    user_roles = {
        'nse_stock': row['NSE_STOCK'] == 'Y',
        'nse_fno': row['NSE_FNO'] == 'Y',
        'mcx_fno': row['MCX_FNO'] == 'Y',
    }

    return render_template('landing.html', username=username, roles=user_roles,datetime=datetime)


@app.route('/logout')
def logout():
    session.clear()
    flash("🔓 Logged out successfully", "info")
    return redirect(url_for("login"))
# @app.route('/logout')
# def logout():
#     username = session.get('username')
#     if username:
#         now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
#         conn = sqlite3.connect('signals.db')
#         c = conn.cursor()
#         c.execute("""
#             UPDATE USERS_SESSION SET logout_time = ?, LOGIN_status = 'LOGGED_OUT'
#             WHERE username = ?
#         """, (now, username))
#         conn.commit()
#         conn.close()

#         print(f"🚪 User {username} logged out at {now}")

#     session.clear()
#     flash("Logged out successfully.", "success")
#     return redirect(url_for('login'))


@app.before_request
def extend_session():
    session.permanent = True

@app.route("/signals")
@login_required("MCX_FNO")
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
@login_required("NSE_FNO")
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
@login_required("NSE_STOCK")
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

# @app.route("/test-telegram", methods=["POST"])
# def test_telegram():

#     now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#     send_telegram_alert("TESTSYM", "BULLISH", 123.45, now_str)
#     return jsonify({"message": "✅ Test Telegram Alert Sent"})




if __name__ == "__main__":
    app.run(debug=True)




# def login():
#     if request.method == 'POST':
#         username = request.form['username']
#         password = request.form['password']

#         if verify_user(username, password):
#             conn = sqlite3.connect('signals.db')
#             cursor = conn.cursor()
#             cursor.execute("SELECT status, validfrom, validto FROM USERS WHERE username = ?", (username,))
#             user = cursor.fetchone()
#             conn.close()

#             if user:
#                 status, validfrom, validto = user
#                 today = datetime.today().date()
#                 # if status == 'ACTIVE' and validfrom <= str(today) <= validto:
#                 if status == 'ACTIVE':
#                     session['username'] = username
#                     session['roles'] = get_user_roles(username)
#                     return redirect(url_for('dashboard'))

#                 else:
#                     flash("⚠️ User access expired or inactive.", "danger")
#             else:
#                 flash("❌ User not found.", "danger")
#         else:
#             flash("❌ Invalid credentials", "danger")

#     return render_template("login.html",datetime=datetime)


# @app.route('/logout', methods=['POST'])
# def logout():
#     session.clear()
#     return redirect(url_for('login'))  # or index/homepage
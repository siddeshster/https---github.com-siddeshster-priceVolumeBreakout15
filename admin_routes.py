from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3, json, os
from datetime import datetime
from werkzeug.utils import secure_filename

admin_bp = Blueprint('admin_bp', __name__)
CONFIG_PATH = 'Config/config.json'
INSTRUMENT_CONFIG_PATH = 'Config/instrument_config.json'
UPLOAD_FOLDER = 'InstrumentsData'
ALLOWED_EXTENSIONS = {'csv'}

# ---------------- Admin Access Check ----------------
def is_admin():
    return session.get('user_type') == 'ADMIN'

@admin_bp.before_app_request
def restrict_admin_pages():
    if request.path.startswith('/admin') and not session.get('user_type') == 'ADMIN':
        return redirect(url_for('login'))


# -------------------- Admin Page --------------------
@admin_bp.route("/admin")
def admin_dashboard():
    with open(CONFIG_PATH) as f:
        kite_config = json.load(f)

    with open(INSTRUMENT_CONFIG_PATH) as f:
        instrument_config = json.load(f)

    conn = sqlite3.connect("signals.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM USERS")
    users = cursor.fetchall()
    cursor.execute("SELECT * FROM USERS_ROLES")
    roles = cursor.fetchall()
    cursor.execute("SELECT * FROM USERS_SESSION")
    sessions = cursor.fetchall()
    conn.close()

    return render_template("admin.html",
                           kite_config=kite_config,
                           instrument_config=instrument_config,
                           users=users,
                           roles=roles,
                           sessions=sessions)

# ---------------- Kite Config Update ----------------
@admin_bp.route('/update_kite_config', methods=['POST'])
def update_kite_config():
    data = {
        "api_key": request.form['api_key'],
        "access_token": request.form['access_token'],
        "telegram_bot_token": request.form.get('telegram_bot_token', ''),
        "telegram_chat_id": request.form.get('telegram_chat_id', '')
    }
    with open(CONFIG_PATH, 'w') as f:
        json.dump(data, f, indent=4)
    flash("✅ Kite Config Updated", "success")
    return redirect(url_for('admin_bp.admin_dashboard'))

# ---------------- Scheduler Toggle ------------------
@admin_bp.route('/update_scheduler', methods=['POST'])
def update_scheduler():
    enabled = request.form.get("scheduler_enabled") == "on"
    config_data = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            config_data = json.load(f)
    config_data['scheduler_enabled'] = enabled
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config_data, f, indent=4)
    flash(f"✅ Scheduler {'Enabled' if enabled else 'Disabled'}", "info")
    return redirect(url_for('admin_bp.admin_dashboard'))

# ----------- User Roles & Sessions Update -----------
@admin_bp.route('/update_user_roles', methods=['POST'])
def update_user_roles():
    username = request.form['username']
    user_type = request.form.get('user_type', 'USER')
    nse_fno = 'Y' if request.form.get('nse_fno') else 'N'
    nse_stock = 'Y' if request.form.get('nse_stock') else 'N'
    mcx_fno = 'Y' if request.form.get('mcx_fno') else 'N'
    alerts = 'Y' if request.form.get('alerts') else 'N'
    login_status = request.form.get('login_status', 'OFF')

    conn = sqlite3.connect("signals.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM USERS_ROLES WHERE username = ?", (username,))
    cursor.execute("""INSERT INTO USERS_ROLES (username, user_type, NSE_FNO, NSE_STOCK, MCX_FNO, ALERTS)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                   (username, user_type, nse_fno, nse_stock, mcx_fno, alerts))

    cursor.execute("UPDATE USERS_SESSION SET login_status = ? WHERE username = ?", (login_status, username))
    conn.commit()
    conn.close()
    flash(f"✅ Roles updated for {username}", "info")
    return redirect(url_for('admin_bp.admin_dashboard'))

# ---------------- Instrument Upload ----------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@admin_bp.route("/upload_instruments", methods=["POST"])
def upload_instruments():
    segment = request.form["segment"]
    file = request.files["instrument_file"]
    if file and allowed_file(file.filename):
        filename = f"instruments_{segment}.csv"
        path = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
        file.save(path)
        flash(f"✅ Uploaded for {segment.upper()}", "success")
    else:
        flash("❌ Invalid file type", "danger")
    return redirect(url_for('admin_bp.admin_dashboard'))

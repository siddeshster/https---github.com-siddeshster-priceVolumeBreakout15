# auth_utils.py
import sqlite3
from werkzeug.security import check_password_hash

def verify_user(username, password):
    conn = sqlite3.connect('signals.db')
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM USERS_AUTH WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return row and check_password_hash(row[0], password)

def get_user_roles(username):
    conn = sqlite3.connect('signals.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM USERS_ROLES WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}

def update_session_status(username, status):
    conn = sqlite3.connect('signals.db')
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO USERS_SESSION (USERNAME, LOGIN_STATUS)
        VALUES (?, ?)
        ON CONFLICT(USERNAME) DO UPDATE SET LOGIN_STATUS = excluded.LOGIN_STATUS
    """, (username, status))

    conn.commit()
    conn.close()


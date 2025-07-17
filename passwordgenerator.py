from werkzeug.security import generate_password_hash
import sqlite3

username = "vedant"
password = "admin@123"  # plain text
hash_pw = generate_password_hash(password)

conn = sqlite3.connect("signals.db")
cursor = conn.cursor()
cursor.execute("INSERT INTO USERS_AUTH (username, password_hash) VALUES (?, ?)", (username, hash_pw))
conn.commit()
conn.close()

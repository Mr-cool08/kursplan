import sqlite3
import secrets
import os
import hashlib


def generate_secret_key():
    # Generate a random 32-byte key using secrets module.
    return secrets.token_hex(32)






def create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum):
    # Create a new booking in the database.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bookings (name, email, organization_number, utbildning, antal, ort, lokal, datum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, email, organization_number, utbildning, antal, ort, lokal, datum),
    )
    conn.commit()
    conn.close()
def check_user_exists(email):
    # Check if a user with the given email exists in the database.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.close()
    return user is not None




def create_user(name, email, password, organization_number):
    # Create a new user in the database.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (name, email, password, organization_number) VALUES (?, ?, ?, ?)",
        (name, email, password, organization_number),
    )
    cursor.execute("SELECT id, name, organization_number, email FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.commit()
    conn.close()
    return user




def ensure_test_user(email, password):
    # Ensure that a default test user exists in the database.
    # Create a default test user if it does not already exist.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        return


    cursor.execute(
        "INSERT INTO users (name, email, password, organization_number) VALUES (?, ?, ?, ?)",
        ("test user", email, password, "0000000000"),
    )
    conn.commit()
    conn.close()

def login_user(email, password):
    # Authenticate a user based on email and password.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, organization_number FROM users WHERE email = ? AND password = ?", (email, password))
    user = cursor.fetchone()
    conn.close()
    return user # Returns None if no matching user is found


def create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum):
    # Create a new booking in the database.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO bookings (name, email, organization_number, utbildning, antal, ort, lokal, datum) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, email, organization_number, utbildning, antal, ort, lokal, datum),
    )
    conn.commit()
    conn.close()

def create_databse():
    
        conn = sqlite3.connect('database.db') # Todo change to external database
        cursor = conn.cursor()
    
        # Create the 'bookings' table
        cursor.execute('''CREATE TABLE IF NOT EXISTS bookings
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        email TEXT NOT NULL,
                        organization_number TEXT,
                        utbildning TEXT,
                        antal INTEGER,
                        ort TEXT,
                        lokal TEXT,
                        datum TEXT,
                        status TEXT NOT NULL DEFAULT "pending")''')

        
        
        # Create the 'users' table
        cursor.execute('''CREATE TABLE IF NOT EXISTS users
                                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                name TEXT NOT NULL,
                                organization_number TEXT,
                                email TEXT NOT NULL UNIQUE,
                                password TEXT NOT NULL,
                                status TEXT NOT NULL DEFAULT "pending")''')
        conn.commit()
        conn.close()

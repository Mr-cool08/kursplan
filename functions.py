import sqlite3
import secrets
import os
import hashlib


def generate_secret_key():
    # Generate a random 32-byte key using secrets module.
    return secrets.token_hex(32)


def booking_exists(name, email, phone, language, time_start, time_end):
    # Check if a booking with the same details already exists.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM bookings WHERE name = ? AND email = ? AND phone = ? AND language = ? AND time_start = ? AND time_end = ?",
        (name, email, phone, language, time_start, time_end),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0






def create_booking(name, email, ):



def ensure_test_user(email, password):
    # Ensure that a default test user exists in the database.
    # Create a default test user if it does not already exist.
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    cursor.execute("SELECT email FROM logins WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        return


    cursor.execute(
        "INSERT INTO users (name, email, password, organization_number) VALUES (?, ?, ?, ?, ?, ?)",
        ("test user", email, password, "0000000000", "", ""),
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
                        organization_number TEXT,
                        utbildning TEXT,
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

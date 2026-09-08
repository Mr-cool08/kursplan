import sqlite3
import secrets
import os
import hashlib

import pytest
import database



@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_database.db"

    database.create_database(str(path))

    return str(path)
def test_generate_secret_key():
    key1 = database.generate_secret_key()
    key2 = database.generate_secret_key()
    assert key1 != key2  # Ensure that two generated keys are not the same
    
def test_create_booking(db_path): 
    # Test creating a booking in the database.
    name = "Test User"
    email = "test@user.se"
    organization_number = "1234567890"
    utbildning = "Test Course"
    antal = 10
    ort = "Test City"
    lokal = "Test Location"
    datum = "2024-01-01"
    booking_id = database.create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path)
    assert isinstance(booking_id, int)
    assert booking_id > 0



def test_create_user(db_path):
    # Test creating a user in the database.
    name = "Test User"
    email = "test@user.se"
    password = "testpassword"
    organization_number = "1234567890"
    user_id = database.create_user(name, email, password, organization_number, db_path)
    assert isinstance(user_id, tuple)
    assert len(user_id) == 4  # Ensure the returned tuple has the correct number of elements


def test_check_user_exists(db_path):
    # Test checking if a user exists in the database.
    email = "should_not_exist@gmail.com"
    exists = database.check_user_exists(email, db_path)
    assert exists is False
    name = "Test User"
    email = "test@user.se"
    password = "testpassword"
    organization_number = "1234567890"
    user_id = database.create_user(name, email, password, organization_number, db_path)
    exists = database.check_user_exists(email, db_path)
    assert exists is True
    
    
def test_ensure_test_user(db_path):
    # Test ensuring that a default test user exists in the database.
    email = "test@user.se"
    password = "testpassword"
    database.ensure_test_user(email, password, db_path)
    assert database.check_user_exists(email, db_path) is True

def test_create_booking_and_user(db_path):
    # Test creating a booking and a user in the database.
    name = "Test User"
    email = "test@user.se"
    organization_number = "1234567890"
    utbildning = "Test Course"
    antal = 10
    ort = "Test City"
    lokal = "Test Location"
    datum = "2024-01-01"
    
    booking_id = database.create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path)
    assert isinstance(booking_id, int)
    created_user = database.create_user(name, email, "testpassword", organization_number, db_path)
    assert isinstance(created_user, tuple)
    
def test_create_database(db_path):
    # Test creating the database and tables.
    database.create_database(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    assert cursor.fetchone() is not None
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bookings'")
    assert cursor.fetchone() is not None
    conn.close()
import os
import uuid

import pytest
import psycopg
from psycopg import sql
import database



@pytest.fixture
def db_path():
    base_url = os.getenv("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")

    schema = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(base_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    test_url = psycopg.conninfo.make_conninfo(
        base_url, options=f"-c search_path={schema}"
    )
    try:
        database.create_database(test_url)
        yield test_url
    finally:
        with psycopg.connect(base_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
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
    user = database.create_user(
        name, email, password, organization_number, "user", db_path
    )
    assert isinstance(user, tuple)
    assert len(user) == 4
    assert user[1] == name
    assert user[3] == email


def test_check_user_exists(db_path):
    # Test checking if a user exists in the database.
    email = "should_not_exist@gmail.com"
    exists = database.check_user_exists(email, db_path)
    assert exists is False
    name = "Test User"
    email = "test@user.se"
    password = "testpassword"
    organization_number = "1234567890"
    database.create_user(
        name, email, password, organization_number, "user", db_path
    )
    exists = database.check_user_exists(email, db_path)
    assert exists is True
    
    
def test_ensure_test_user(db_path):
    # Test ensuring that a default test user exists in the database.
    email = "test@user.se"
    password = "testpassword"
    database.ensure_test_user(email, password, db_path)
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
    created_user = database.create_user(
        name, email, "testpassword", organization_number, "user", db_path
    )
    assert isinstance(created_user, tuple)
    
def test_booking_queries_and_updates(db_path):
    booking_id = database.create_booking(
        "Test User", "test@user.se", "1234567890", "Test Course", 2,
        "Test City", "Test Location", "2024-01-01", db_path,
    )

    assert database.get_booking_by_id(booking_id, db_path)[0] == booking_id
    assert database.get_email_by_booking_id(booking_id, db_path) == "test@user.se"
    assert database.get_utbildning_by_booking_id(booking_id, db_path) == "Test Course"
    assert len(database.get_bookings_by_email("test@user.se", db_path)) == 1
    assert len(database.get_bookings_by_status("pending", db_path)) == 1
    assert len(database.get_booking_by_name("Test User", db_path)) == 1

    database.book_bookings([booking_id], "2024-02-01", "New City", db_path)
    assert database.get_booking_by_status("accepted", db_path)[0][0] == booking_id

    database.update_status(booking_id, "cancelled", db_path)
    assert database.get_booking_by_status("cancelled", db_path)[0][0] == booking_id
    database.remove_booking(booking_id, db_path)
    assert database.get_booking_by_id(booking_id, db_path) is None


def test_create_database(db_path):
    with psycopg.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        ).fetchall()
    assert {row[0] for row in rows} >= {"users", "bookings"}
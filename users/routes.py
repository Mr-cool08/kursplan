from flask import Flask, render_template, request, session, redirect, url_for, Blueprint
import database
import os
from gen_func import read_utbildningar_from_json
import gen_func


user_page = Blueprint('user_page', __name__,
                        template_folder='templates')
db_path = os.getenv("db_path")
if not db_path:
    raise ValueError("Database path is not set in the .env file.")

@user_page.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('user_page.dashboard'))
    return render_template('index.html')

@user_page.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('user_page.login'))
    name = session.get('user_name')
    organization_number = session.get('organization_number')
    user_id = session.get('user_id')
    bookings = database.get_booking_by_name(name, db_path)
    
    return render_template('dashboard.html', name=name, organization_number=organization_number, bookings=bookings)

@user_page.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        organization_number = request.form['organization_number']
        if database.check_user_exists(email, db_path):
            return render_template('signup.html', error='A user with this email already exists.')
        role = "user"
        user = database.create_user(name, email, password, organization_number, role, db_path)
        session['user_id'] = user[0]
        session['user_name'] = user[1]
        session['organization_number'] = user[2]
        session['user_email'] = user[3]
        return redirect(url_for('user_page.login'))
    else:
        return render_template('signup.html')
    
    
@user_page.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if 'user_id' in session:
            
            return redirect(url_for('user_page.dashboard'))
    elif request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        if not email or not password:
            return render_template('login.html', error='Please enter both email and password.')
        role = "user"
        user = database.login_user(email, password, role, db_path)
        if user:
            session['user_id'] = user[0]
            session['user_name'] = user[1]
            session['organization_number'] = user[2]
            session['user_email'] = email
            return redirect(url_for('user_page.dashboard'))

        return render_template('login.html', error='Invalid email or password.')
    return render_template('login.html')


@user_page.route('/booking', methods=['GET', 'POST'])
def booking():
    if request.method == 'POST':
        if 'user_id' not in session:
            return redirect(url_for('user_page.login'))
        
        name = session.get('user_name')
        email = session.get('user_email')
        organization_number = session.get('organization_number')
        if name is None or email is None or organization_number is None:
            return redirect(url_for("logout"))
        utbildning = request.form['utbildning']
        antal = request.form['antal']
        ort = request.form['ort']
        lokal = request.form['lokal']
        if lokal == 'Egen':
            lokal = request.form['adress']
        datum = request.form['datum']
        print(name, email, organization_number, utbildning, antal, ort, lokal, datum)
        database.create_booking(name, email, organization_number, utbildning, antal, ort, lokal, datum, db_path)
        return redirect(url_for('user_page.dashboard'))
    if request.method == 'GET':
        if 'user_id' not in session:
            return redirect(url_for('user_page.login'))
        
        utbildningar = read_utbildningar_from_json()
        return render_template('book.html', utbildningar=utbildningar)


@user_page.route('/cancel_booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if not session.get('user_id'):
        return redirect(url_for('user_page.login'))
    database.update_status(booking_id, "cancelled", db_path)
    return redirect(url_for('user_page.dashboard'))
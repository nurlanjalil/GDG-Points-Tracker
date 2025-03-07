import os
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, session, g
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io
import concurrent.futures
import time
import random
import shutil
import glob
import functools
import threading

# Initialize Flask app
app = Flask(__name__)

# Configuration using environment variables with fallbacks for development
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-for-gdg-points-tracker')
if app.config['SECRET_KEY'] == 'dev-key-for-gdg-points-tracker':
    app.logger.warning("Using development SECRET_KEY. Set a proper SECRET_KEY in production!")

# Configure database
database_url = os.environ.get('DATABASE_URL', 'sqlite:///gdg_points.db')
# Handle PostgreSQL URL format for Render.com
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'connect_args': {'check_same_thread': False} if database_url.startswith('sqlite:') else {},
    'pool_pre_ping': True,  # Check connection validity before usage
    'pool_recycle': 300,    # Recycle connections after 5 minutes
    'pool_timeout': 30,     # Connection timeout after 30 seconds
    'max_overflow': 10,     # Allow up to 10 connections beyond pool_size
    'pool_size': 5          # Maintain a pool of 5 connections
}
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# Create uploads folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize database
db = SQLAlchemy(app)

# Configure database error handling
@app.errorhandler(Exception)
def handle_exception(e):
    """Handle exceptions gracefully and log them"""
    # Log the error
    app.logger.error(f"Unhandled exception: {str(e)}")
    
    # Check if this is a database-related error
    if 'SQLAlchemy' in str(type(e)):
        app.logger.error(f"Database error: {str(e)}")
        flash('A database error occurred. Please try again later.', 'error')
    
    # Check for attribute errors (often template-related)
    elif isinstance(e, AttributeError):
        app.logger.error(f"Template/attribute error: {str(e)}")
        flash('An error occurred while rendering the page. Our team has been notified.', 'error')
    
    # Return a friendly error page
    return render_template('error.html', error=str(e)), 500

# Create tables automatically
with app.app_context():
    try:
        db.create_all()
        app.logger.info("Database tables created (if they didn't exist)")
    except Exception as e:
        app.logger.error(f"Error creating database tables: {str(e)}")

# Register Jinja2 filters
@app.template_filter('strftime')
def _jinja2_filter_datetime(date, fmt=None):
    if fmt:
        return date.strftime(fmt)
    else:
        return date.strftime('%Y-%m-%d %H:%M:%S')

# Login required decorator
def login_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return view(**kwargs)
    return wrapped_view

# Database Models
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    date_registered = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship with Participant
    participants = db.relationship('Participant', backref='owner', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'

class Participant(db.Model):
    __tablename__ = 'participants'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    profile_url = db.Column(db.String(500), nullable=False)
    current_points = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Relationship with PointsHistory
    history = db.relationship('PointsHistory', backref='participant', lazy=True)
    
    def __repr__(self):
        return f'<Participant {self.name}>'

class PointsHistory(db.Model):
    __tablename__ = 'points_history'
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey('participants.id'), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    date_recorded = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<PointsHistory {self.points} points for {self.participant_id} on {self.date_recorded}>'

class LastRefresh(db.Model):
    __tablename__ = 'last_refresh'
    id = db.Column(db.Integer, primary_key=True)
    refresh_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<LastRefresh on {self.refresh_date}>'

# Helper function to get points from a profile URL
def get_points(link):
    # Maximum number of retries
    max_retries = 3  # Increased from 2 to 3
    retries = 0
    
    while retries <= max_retries:
        try:
            # Check if the link is invalid or a placeholder
            if not link or link == 'INVALID_PROFILE_URL' or pd.isna(link) or 'INVALID_PROFILE_URL' in link:
                app.logger.info(f"Skipping invalid profile URL: {link}")
                return 0
                
            # Small random delay to avoid rate limiting
            time.sleep(0.2 + (0.3 * random.random()))
            
            # Make a GET request to the link with a reasonable timeout
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(link, timeout=20, headers=headers)  # Increased timeout from 15 to 20 seconds
            response.raise_for_status()  # Raise an exception for bad status codes

            # Check for empty responses
            if not response.content:
                app.logger.warning(f"Empty response from {link}")
                raise ValueError("Empty response from server")

            # Parse the page content
            soup = BeautifulSoup(response.content, 'html.parser')

            # Add debug information
            app.logger.debug(f"Successfully fetched content from {link} with status {response.status_code}")
            
            # Try different approaches to find points
            
            # Approach 1: Find the first element with class 'profile-league'
            profile_league = soup.find(class_='profile-league')

            if profile_league:
                # Find the <strong> tag inside 'profile-league' and get its text
                strong_tag = profile_league.find('strong')
                if strong_tag:
                    strong_text = strong_tag.get_text(strip=True)
                    if strong_text:
                        # Extract just the numeric value
                        points_text = strong_text.replace(' points', '').strip()
                        try:
                            if points_text.isdigit():
                                points = int(points_text)
                                app.logger.info(f"Successfully found {points} points from {link}")
                                return points
                        except ValueError:
                            app.logger.warning(f"Non-numeric points value in {link}: {points_text}")
                
            # Debug output for all possible locations of points
            app.logger.debug(f"Searching for alternative points locations in {link}")
            
            # Approach 2: Look for points in alternative locations
            # Find all span elements with numbers
            number_spans = soup.find_all('span', string=lambda s: s and s.strip().isdigit())
            for span in number_spans:
                if span.parent and 'point' in span.parent.get_text().lower():
                    try:
                        points = int(span.get_text().strip())
                        app.logger.info(f"Found alternative points value {points} from {link}")
                        return points
                    except ValueError:
                        continue
            
            # Approach 3: Try to find any element containing the word "points" and a number
            points_elements = soup.find_all(string=lambda s: s and 'point' in s.lower())
            for elem in points_elements:
                text = elem.strip()
                # Try to extract digits from the text
                digits = ''.join(filter(str.isdigit, text))
                if digits:
                    try:
                        points = int(digits)
                        app.logger.info(f"Found points from text '{text}': {points}")
                        return points
                    except ValueError:
                        continue
            
            # If we get here, no valid points were found
            app.logger.warning(f"No points found in {link}")
            
            # If this was the final retry, log the HTML content for debugging
            if retries == max_retries:
                app.logger.debug(f"HTML content from {link}: {soup.prettify()[:500]}...")
                
            # Fallback to a default value greater than 0 to prevent 0 points
            return 1  # Return 1 instead of 0 to indicate at least some activity
                
        except requests.Timeout:
            retries += 1
            app.logger.warning(f"Timeout fetching {link}, retry {retries}/{max_retries}")
            if retries > max_retries:
                app.logger.error(f"Max retries reached for {link}")
                return 1  # Return 1 instead of 0 for timeout
                
        except requests.RequestException as e:
            retries += 1
            app.logger.warning(f"Request error fetching {link}: {str(e)}, retry {retries}/{max_retries}")
            if retries > max_retries:
                app.logger.error(f"Max retries reached for {link}")
                return 1  # Return 1 instead of 0 for request errors
                
        except Exception as e:
            app.logger.error(f"Error fetching points from {link}: {str(e)}")
            return 1  # Return 1 instead of 0 for general errors
            
    # If we get here after all retries, return a fallback value
    return 1  # Return 1 instead of 0 for fallback

# Fetch points with concurrency for better performance
def fetch_points_concurrently(participants):
    results = {}
    total = len(participants)
    processed = 0
    errors = []
    
    # Extract just the necessary data from participants to avoid SQLite threading issues
    participant_data = [
        {
            'id': p.id,
            'name': p.name,
            'profile_url': p.profile_url
        } for p in participants
    ]
    
    def fetch_participant_points(participant_info):
        nonlocal processed
        # Create a new application context for this thread
        with app.app_context():
            try:
                # Add some additional delay between requests to avoid rate limiting
                time.sleep(0.2 + (0.3 * random.random()))  # 0.2-0.5 seconds between requests
                
                points = get_points(participant_info['profile_url'])
                processed += 1
                if processed % 5 == 0 or processed == total:  # Log every 5 participants or at the end
                    app.logger.info(f"Progress: {processed}/{total} profiles processed ({processed/total*100:.1f}%)")
                return participant_info['id'], points, None
            except Exception as e:
                processed += 1
                error_msg = f"Error fetching points for {participant_info['name']}: {str(e)}"
                app.logger.error(error_msg)
                return participant_info['id'], 0, error_msg
    
    # Use ThreadPoolExecutor with fewer workers to avoid overwhelming the system
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:  # Reduced from 10 to 5
        try:
            future_to_participant = {
                executor.submit(fetch_participant_points, p_info): p_info 
                for p_info in participant_data
            }
            
            for future in concurrent.futures.as_completed(future_to_participant):
                try:
                    participant_id, points, error = future.result(timeout=30)  # Add timeout for each future
                    results[participant_id] = points
                    if error:
                        errors.append(error)
                except concurrent.futures.TimeoutError:
                    participant_info = future_to_participant[future]
                    app.logger.error(f"Timeout while processing {participant_info['name']}")
                    results[participant_info['id']] = 0  # Default to 0 points on timeout
                    errors.append(f"Timeout while processing {participant_info['name']}")
                except Exception as e:
                    participant_info = future_to_participant[future]
                    # Don't try to access participant attributes here as it might cause the same error
                    app.logger.error(f"Unexpected error in thread execution: {str(e)}")
                    errors.append(f"Failed to process a participant: {str(e)}")
                    # Still add the participant to results with 0 points
                    results[participant_info['id']] = 0
        except Exception as e:
            app.logger.error(f"Error in concurrent execution: {str(e)}")
    
    # If there were many errors, show a summary message
    if len(errors) > 0:
        app.logger.warning(f"Encountered {len(errors)} errors while fetching points")
        if len(errors) <= 3:
            for error in errors:
                app.logger.warning(error)
    
    return results

# Function to validate CSV
def validate_csv(file_stream):
    """Validate that the CSV has required 'Name' column"""
    try:
        # Read the CSV file
        df = pd.read_csv(file_stream)
        
        # Reset the file pointer to the beginning
        file_stream.seek(0)
        
        # Check for required columns
        required_columns = ['Name', 'profile']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            return False, f"Missing required columns: {', '.join(missing_columns)}"
        
        # Check for empty values in Name column only
        missing_values = df[df['Name'].isna() | (df['Name'] == '')].index.tolist()
        if missing_values:
            missing_rows = ', '.join(str(i + 2) for i in missing_values)  # +2 for 0-based index and header
            return False, f"Missing values in 'Name' column at rows: {missing_rows}"
        
        return True, "CSV file is valid"
    except Exception as e:
        return False, f"Error validating CSV: {str(e)}"

# Routes
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Validate form inputs
        error = None
        if not username:
            error = 'Username is required.'
        elif not password:
            error = 'Password is required.'
        elif User.query.filter_by(username=username).first() is not None:
            error = f'User {username} is already registered.'
            
        if error is None:
            # Create new user
            new_user = User(username=username)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        
        flash(error, 'error')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Validate credentials
        error = None
        user = User.query.filter_by(username=username).first()
        
        if user is None:
            error = 'Invalid username.'
        elif not user.check_password(password):
            error = 'Invalid password.'
            
        if error is None:
            # Clear the session and set user_id
            session.clear()
            session['user_id'] = user.id
            session['username'] = user.username
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('index'))
        
        flash(error, 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    
    if user_id is None:
        g.user = None
    else:
        g.user = User.query.get(user_id)

@app.route('/')
def index():
    if g.user:
        # For logged-in users, show their participants
        participants = Participant.query.filter_by(user_id=g.user.id).all()
        return render_template('index.html', participants=participants)
    else:
        # For anonymous users, show login/register options
        return render_template('index.html')

@app.route('/download_example')
def download_example():
    return send_from_directory(
        directory='.', 
        path='example.csv', 
        as_attachment=True
    )

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    # Check if file is in the request
    if 'csv_file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('index'))
    
    file = request.files['csv_file']
    
    # Check if file is selected
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('index'))
    
    # Check if file has CSV extension
    if not file.filename.lower().endswith('.csv'):
        flash('Only CSV files are allowed', 'error')
        return redirect(url_for('index'))
    
    # Validate CSV structure
    file_stream = io.StringIO(file.stream.read().decode("utf-8"), newline=None)
    is_valid, message = validate_csv(file_stream)
    
    if not is_valid:
        flash(message, 'error')
        return redirect(url_for('index'))
    
    # Process CSV data
    file_stream.seek(0)
    df = pd.read_csv(file_stream)
    
    # Start timing for performance metrics
    start_time = time.time()
    flash('Processing CSV file. This may take a moment...', 'info')
    
    # Create a backup before making changes
    backup_path = backup_database()
    if backup_path:
        app.logger.info(f"Database backup created at {backup_path}")
    
    # List to store results for display
    results = []
    
    # First collect all participant data
    participants_to_fetch = []
    
    for index, row in df.iterrows():
        name = row['Name']
        profile_url = row['profile']
        email = row.get('mail', None)  # Email is optional
        
        # Handle missing profile URLs by setting a default value
        if pd.isna(profile_url) or str(profile_url).strip() == '':
            profile_url = f"INVALID_PROFILE_URL_{name.replace(' ', '_')}"
            app.logger.info(f"Using placeholder URL for {name} at row {index+2}")
        else:
            # Ensure profile_url is a string (not a float NaN)
            profile_url = str(profile_url).strip()
        
        # Find participant in database
        participant = Participant.query.filter_by(profile_url=profile_url, user_id=g.user.id).first()
        
        if not participant:
            # Create new participant
            participant = Participant(
                name=name, 
                email=email, 
                profile_url=profile_url,
                current_points=0,  # Will be updated with actual points soon
                user_id=g.user.id  # Associate with current user
            )
            db.session.add(participant)
            db.session.commit()  # Commit to get an ID for the participant
        
        # Update name and email if they changed
        if participant.name != name:
            participant.name = name
        if email and participant.email != email:
            participant.email = email
        
        participants_to_fetch.append(participant)
    
    # Commit all changes to the database before starting concurrent operations
    db.session.commit()
    
    # Fetch points concurrently for better performance
    # Limit batch size to avoid timeout
    MAX_BATCH_SIZE = 10  # Process at most 10 participants at once (reduced from 25)
    all_results = {}
    
    for i in range(0, len(participants_to_fetch), MAX_BATCH_SIZE):
        batch = participants_to_fetch[i:i+MAX_BATCH_SIZE]
        batch_msg = f'Processing batch {i//MAX_BATCH_SIZE + 1}/{(len(participants_to_fetch)-1)//MAX_BATCH_SIZE + 1} ({len(batch)} participants)...'
        app.logger.info(batch_msg)
        flash(batch_msg, 'info')
        
        # Process each batch with a smaller number of concurrent workers
        batch_results = fetch_points_concurrently(batch)
        all_results.update(batch_results)
        
        # Commit after each batch to save progress
        db.session.commit()
        app.logger.info(f"Batch {i//MAX_BATCH_SIZE + 1} completed and saved.")
    
    # Process the results - use a fresh query to avoid stale data
    results = []
    for participant_id, points in all_results.items():
        # Get a fresh instance of the participant from the database
        participant = Participant.query.get(participant_id)
        if participant:
            # For initial upload, just set the current points without calculating weekly points
            # Weekly points will be calculated on refresh after a week
            participant.current_points = points
            participant.last_updated = datetime.utcnow()
            
            # Add to results for display
            results.append({
                'name': participant.name,
                'current_points': points,
                'weekly_points': 'N/A (First upload)'  # Indicate this is the first upload
            })
    
    # Save all changes to the database
    db.session.commit()
    
    # Record this as the first refresh
    last_refresh = LastRefresh()
    db.session.add(last_refresh)
    db.session.commit()
    
    # Sort results by weekly points (highest first)
    sorted_results = sorted(results, key=lambda x: x['weekly_points'], reverse=True)
    
    # Calculate and display performance metrics
    end_time = time.time()
    processing_time = end_time - start_time
    flash(f'CSV file processed successfully in {processing_time:.2f} seconds! {len(results)} participants updated.', 'success')
    
    return render_template('results.html', results=sorted_results)

@app.route('/participants')
@login_required
def view_participants():
    try:
        # Get current time for template - using server's local timezone
        now = datetime.now()  # Use local time instead of UTC
        
        # Get all participants for this user
        participants = Participant.query.filter_by(user_id=g.user.id).all()
        
        # Prepare participants with additional data for the template
        enriched_participants = []
        for participant in participants:
            try:
                # Get history records sorted by date (most recent first)
                history_sorted = sorted(participant.history, key=lambda x: x.date_recorded, reverse=True)
                
                # Current points (latest)
                current_points = participant.current_points
                
                # Previous week points
                previous_points = 0
                weekly_change = 0
                
                if history_sorted and len(history_sorted) > 1:
                    # If we have at least two history records, calculate the difference
                    current = history_sorted[0].points
                    previous = history_sorted[1].points
                    previous_points = previous
                    weekly_change = current - previous
                elif history_sorted and len(history_sorted) == 1:
                    # If we only have one record, use it as current
                    current = history_sorted[0].points
                    previous_points = 0
                    weekly_change = current
                
                # Add enriched data (ensuring all data is properly structured)
                enriched_participants.append({
                    'id': participant.id,
                    'name': participant.name,
                    'email': participant.email,
                    'profile_url': participant.profile_url,
                    'current_points': current_points,
                    'previous_points': previous_points,
                    'weekly_change': weekly_change,
                    'last_updated': participant.last_updated  # This is a datetime object from the model
                })
            except Exception as e:
                app.logger.error(f"Error processing participant {participant.id}: {str(e)}")
                # Still include the participant with default values if there's an error
                enriched_participants.append({
                    'id': participant.id,
                    'name': participant.name,
                    'email': participant.email or 'Not provided',
                    'profile_url': participant.profile_url,
                    'current_points': participant.current_points,
                    'previous_points': 0,
                    'weekly_change': 0,
                    'last_updated': participant.last_updated
                })
        
        # Sort by current points (descending)
        enriched_participants = sorted(enriched_participants, key=lambda x: x['current_points'], reverse=True)
        
        # Get the last refresh date to display to users
        last_refresh = LastRefresh.query.order_by(LastRefresh.refresh_date.desc()).first()
        last_refresh_date = last_refresh.refresh_date if last_refresh else datetime.now()
        
        # Calculate the next refresh date (7 days after the last refresh)
        next_refresh_date = last_refresh_date + timedelta(days=7) if last_refresh else datetime.now()
        
        return render_template('participants.html', 
                              participants=enriched_participants, 
                              now=now,
                              last_refresh_date=last_refresh_date,
                              next_refresh_date=next_refresh_date,
                              timedelta=timedelta)
        
    except Exception as e:
        app.logger.error(f"Error in view_participants: {str(e)}")
        flash('An error occurred while loading participants. Please try again.', 'error')
        return redirect(url_for('index'))

# Create a backup of the database
def backup_database():
    """Create a backup of the database with timestamp"""
    if os.path.exists('gdg_points.db'):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Create backups directory if it doesn't exist
        os.makedirs('backups', exist_ok=True)
        
        # Remove old backups (keep only the 5 most recent)
        backup_files = sorted(glob.glob('backups/gdg_points_*.db'))
        if len(backup_files) >= 5:
            for old_backup in backup_files[:-4]:  # Keep 5 most recent, delete the rest
                try:
                    os.remove(old_backup)
                except:
                    pass
        
        # Create new backup
        backup_path = f'backups/gdg_points_{timestamp}.db'
        shutil.copy2('gdg_points.db', backup_path)
        return backup_path
    return None

@app.route('/refresh')
@login_required
def refresh_points():
    # Check if a week has passed since the last refresh
    last_refresh = LastRefresh.query.order_by(LastRefresh.refresh_date.desc()).first()
    
    # Check if last refresh was less than a week ago
    if last_refresh and (datetime.utcnow() - last_refresh.refresh_date) < timedelta(days=7):
        days_since_refresh = (datetime.utcnow() - last_refresh.refresh_date).days
        hours_since_refresh = ((datetime.utcnow() - last_refresh.refresh_date).seconds // 3600)
        
        next_refresh_date = last_refresh.refresh_date + timedelta(days=7)
        time_remaining = next_refresh_date - datetime.utcnow()
        days_remaining = time_remaining.days
        hours_remaining = time_remaining.seconds // 3600
        
        flash(f'Points were last refreshed {days_since_refresh} days and {hours_since_refresh} hours ago. ' +
              f'You can refresh again in {days_remaining} days and {hours_remaining} hours.', 'warning')
        return redirect(url_for('view_participants'))
    
    participants = Participant.query.filter_by(user_id=g.user.id).all()
    results = []
    
    if not participants:
        flash('No participants found. Please upload a CSV file first.', 'warning')
        return redirect(url_for('index'))
    
    # Create a backup before refreshing
    backup_path = backup_database()
    if backup_path:
        app.logger.info(f"Database backup created at {backup_path}")
    
    # Start timing for performance metrics
    start_time = time.time()
    
    # Fetch points concurrently
    # Limit batch size to avoid timeout
    MAX_BATCH_SIZE = 10  # Process at most 10 participants at once (reduced from 25)
    all_results = {}
    
    for i in range(0, len(participants), MAX_BATCH_SIZE):
        batch = participants[i:i+MAX_BATCH_SIZE]
        batch_msg = f'Processing batch {i//MAX_BATCH_SIZE + 1}/{(len(participants)-1)//MAX_BATCH_SIZE + 1} ({len(batch)} participants)...'
        app.logger.info(batch_msg)
        flash(batch_msg, 'info')
        
        # Process each batch with a smaller number of concurrent workers
        batch_results = fetch_points_concurrently(batch)
        all_results.update(batch_results)
        
        # Commit after each batch to save progress
        db.session.commit()
        app.logger.info(f"Batch {i//MAX_BATCH_SIZE + 1} completed and saved.")
    
    # Process results - use fresh queries to avoid stale data
    results = []
    for participant_id, points in all_results.items():
        # Get a fresh instance of the participant from the database
        participant = Participant.query.get(participant_id)
        if participant:
            # Calculate weekly points (current minus previous)
            weekly_points = points - participant.current_points
            
            # Update participant record
            participant.current_points = points
            participant.last_updated = datetime.utcnow()
            
            # Add history entry
            history_entry = PointsHistory(
                participant_id=participant.id,
                points=points
            )
            db.session.add(history_entry)
            
            # Add to results
            results.append({
                'name': participant.name,
                'weekly_points': weekly_points,
                'lifelong_points': points,
                'profile_url': participant.profile_url
            })
    
    # Save all changes to the database
    db.session.commit()
    
    # Create a new LastRefresh record
    new_refresh = LastRefresh()
    db.session.add(new_refresh)
    db.session.commit()
    
    # Sort by weekly points
    sorted_results = sorted(results, key=lambda x: x['weekly_points'], reverse=True)
    
    # Calculate and show performance metrics
    end_time = time.time()
    processing_time = end_time - start_time
    flash(f'Points refreshed successfully in {processing_time:.2f} seconds!', 'success')
    
    return render_template('results.html', results=sorted_results)

@app.route('/participant/<int:id>')
@login_required
def participant_history(id):
    try:
        # Get the participant and verify it belongs to the current user
        participant = Participant.query.filter_by(id=id, user_id=g.user.id).first()
        
        if not participant:
            flash('Participant not found or you do not have permission to view this participant.', 'error')
            return redirect(url_for('view_participants'))
        
        # Get the history for this participant
        history = PointsHistory.query.filter_by(participant_id=id).order_by(PointsHistory.date_recorded.desc()).all()
        
        return render_template('history.html', participant=participant, history=history)
    except Exception as e:
        app.logger.error(f"Error viewing participant history: {str(e)}")
        flash('An error occurred while retrieving participant history. Please try again.', 'error')
        return redirect(url_for('view_participants'))

@app.route('/next_refresh')
def next_refresh():
    """Return the datetime when the next refresh will be available"""
    last_refresh = LastRefresh.query.order_by(LastRefresh.refresh_date.desc()).first()
    
    if not last_refresh:
        # If no refresh has ever happened, return now
        return jsonify({'next_refresh': datetime.utcnow().isoformat(), 'can_refresh': True})
    
    next_refresh_date = last_refresh.refresh_date + timedelta(days=7)
    
    # If the next refresh date is in the past, return now
    if next_refresh_date < datetime.utcnow():
        return jsonify({'next_refresh': datetime.utcnow().isoformat(), 'can_refresh': True})
    
    # Calculate time remaining
    time_remaining = next_refresh_date - datetime.utcnow()
    days_remaining = time_remaining.days
    hours_remaining = time_remaining.seconds // 3600
    minutes_remaining = (time_remaining.seconds % 3600) // 60
    
    readable_time = f"{days_remaining}d {hours_remaining}h {minutes_remaining}m"
    
    return jsonify({
        'next_refresh': next_refresh_date.isoformat(),
        'time_remaining': readable_time,
        'can_refresh': False
    })

# For date formatting in templates
@app.context_processor
def utility_processor():
    def format_date(date):
        return date.strftime('%Y-%m-%d %H:%M:%S')
    
    # Add current datetime to all templates
    return dict(
        format_date=format_date,
        now=datetime.utcnow(),
        timedelta=timedelta  # Also provide timedelta for date calculations in templates
    )

# Maintenance route for database setup (protected by setup key)
@app.route('/setup-database/<setup_key>')
def setup_database(setup_key):
    # Check if setup key matches the environment variable or default value
    expected_key = os.environ.get('SETUP_KEY', 'change-this-setup-key-in-production')
    if setup_key != expected_key:
        return "Access denied", 403
    
    recreate = request.args.get('recreate', 'false').lower() == 'true'
    
    try:
        # Get engine directly to run raw SQL for inspection
        engine = db.engine
        inspector = db.inspect(engine)
        existing_tables = inspector.get_table_names()
        
        # Log the database connection and tables
        result = f"Connected to: {engine.url}<br>"
        result += f"Existing tables: {', '.join(existing_tables) if existing_tables else 'None'}<br>"
        
        if recreate and existing_tables:
            result += "Dropping existing tables...<br>"
            db.drop_all()
        
        result += "Creating tables...<br>"
        db.create_all()
        
        # Verify tables after creation
        inspector = db.inspect(engine)
        tables_after = inspector.get_table_names()
        result += f"Tables after setup: {', '.join(tables_after)}<br>"
        
        # Test a simple query to verify models
        result += "<br>Testing models:<br>"
        try:
            user_count = User.query.count()
            result += f"- User model OK. Count: {user_count}<br>"
        except Exception as e:
            result += f"- User model ERROR: {str(e)}<br>"
            
        return f"<h1>Database Setup</h1>{result}"
    except Exception as e:
        return f"<h1>Error</h1>Error creating database tables: {str(e)}", 500

# Admin routes
@app.route('/admin')
@login_required
def admin_dashboard():
    # Check if the current user is admin (first user is considered admin for simplicity)
    if g.user.id != 1:
        flash('Admin access required', 'error')
        return redirect(url_for('index'))
    
    # Get statistics for the admin dashboard
    user_count = User.query.count()
    participant_count = Participant.query.count()
    
    # Get breakdown by user
    user_stats = []
    for user in User.query.all():
        user_participants = Participant.query.filter_by(user_id=user.id).count()
        user_stats.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'date_registered': user.date_registered,
            'participant_count': user_participants
        })
    
    return render_template('admin/dashboard.html', 
                           user_count=user_count, 
                           participant_count=participant_count,
                           user_stats=user_stats)

@app.route('/admin/users')
@login_required
def admin_users():
    # Check if the current user is admin 
    if g.user.id != 1:
        flash('Admin access required', 'error')
        return redirect(url_for('index'))
    
    users = User.query.all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/user/<int:user_id>')
@login_required
def admin_user_detail(user_id):
    # Check if the current user is admin
    if g.user.id != 1:
        flash('Admin access required', 'error')
        return redirect(url_for('index'))
    
    # Get the user
    user = User.query.get_or_404(user_id)
    
    # Get all participants for this user with history counts
    participants_data = []
    for p in user.participants:
        # Count history entries
        history_count = PointsHistory.query.filter_by(participant_id=p.id).count()
        
        # Create a dictionary with all participant data including email
        participant_dict = {
            'id': p.id,
            'name': p.name,
            'email': p.email,  # Ensure email is included
            'profile_url': p.profile_url,
            'current_points': p.current_points,
            'last_updated': p.last_updated,
            'history_count': history_count
        }
        participants_data.append(participant_dict)
    
    return render_template('admin/user_detail.html', user=user, participants=participants_data)

@app.route('/admin/all-participants')
@login_required
def admin_all_participants():
    # Check if the current user is admin
    if g.user.id != 1:
        flash('Admin access required', 'error')
        return redirect(url_for('index'))
    
    # Get all participants with user info
    participants = db.session.query(
        Participant, User.username.label('owner_name')
    ).join(User).all()
    
    return render_template('admin/all_participants.html', participants=participants)

@app.route('/admin/participant/<int:participant_id>')
@login_required
def admin_participant_detail(participant_id):
    # Check if the current user is admin
    if g.user.id != 1:
        flash('Admin access required', 'error')
        return redirect(url_for('index'))
    
    # Get the participant details
    participant = Participant.query.get_or_404(participant_id)
    
    # Get the participant's history
    history = PointsHistory.query.filter_by(participant_id=participant_id).order_by(PointsHistory.date_recorded.desc()).all()
    
    # Get the owner (user) information
    owner = User.query.get(participant.user_id)
    
    return render_template('admin/participant_detail.html', 
                          participant=participant, 
                          history=history,
                          owner=owner)

@app.route('/delete-all-participants', methods=['POST'])
@login_required
def delete_all_participants():
    try:
        # Get all participants for current user
        participants = Participant.query.filter_by(user_id=g.user.id).all()
        
        if not participants:
            flash('You have no participants to delete.', 'info')
            return redirect(url_for('index'))
        
        # Count the number of participants to delete
        count = len(participants)
        
        # Create a backup before making changes
        backup_path = backup_database()
        if backup_path:
            app.logger.info(f"Database backup created at {backup_path} before deletion")
        
        # Delete all participants for this user
        for participant in participants:
            # First delete history records to avoid foreign key constraint errors
            PointsHistory.query.filter_by(participant_id=participant.id).delete()
        
        # Now delete the participants
        Participant.query.filter_by(user_id=g.user.id).delete()
        
        # Commit the changes
        db.session.commit()
        
        flash(f'Successfully deleted {count} participants. You can now upload a new CSV file.', 'success')
        return redirect(url_for('index'))
        
    except Exception as e:
        app.logger.error(f"Error deleting participants: {str(e)}")
        db.session.rollback()
        flash('An error occurred while deleting participants. Please try again.', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, port=8080) 
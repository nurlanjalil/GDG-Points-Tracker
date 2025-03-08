import os
import time
import io
import pandas as pd
import requests
import re
import csv
import logging
import random
import concurrent.futures
from bs4 import BeautifulSoup
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for, 
    flash, send_from_directory, jsonify, make_response
)
from werkzeug.utils import secure_filename

# Initialize Flask app
app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-for-gdg-points-tracker')
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# Create uploads folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configure error handling
@app.errorhandler(Exception)
def handle_exception(e):
    """Handle exceptions gracefully and log them"""
    # Log the error
    app.logger.error(f"Unhandled exception: {str(e)}")
    
    # Return a friendly error page
    return render_template('error.html', error=str(e)), 500

# Helper function to get points from a profile URL
def get_points(link):
    try:
        # Make a GET request to the link
        response = requests.get(link)
            response.raise_for_status()  # Raise an exception for bad status codes

            # Parse the page content
            soup = BeautifulSoup(response.content, 'html.parser')

        # Method 1: Find the first element with class 'profile-league'
                profile_league = soup.find(class_='profile-league')
                if profile_league:
            # Try to find the strong tag directly
                    strong_tag = profile_league.find('strong')
                    if strong_tag:
                points_text = strong_tag.get_text(strip=True)
                # Extract just the numeric part
                points_match = re.search(r'(\d+)', points_text)
                if points_match:
                    return int(points_match.group(1))
            
            # Fallback: Look for any text with numbers in the profile-league div
            league_text = profile_league.get_text()
            points_matches = re.findall(r'(\d+)\s*points', league_text, re.IGNORECASE)
            if points_matches:
                return int(points_matches[0])

        # Method 2: Look for any strong tag containing "points"
        for strong_tag in soup.find_all('strong'):
            if 'point' in strong_tag.get_text().lower():
                points_match = re.search(r'(\d+)', strong_tag.get_text())
                if points_match:
                    return int(points_match.group(1))
        
        # Method 3: Look for any text containing "points" with a number
        points_patterns = re.findall(r'(\d+)\s*points', soup.get_text(), re.IGNORECASE)
        if points_patterns:
            return int(points_patterns[0])

        # Nothing found
        return 0
            
            except Exception as e:
        print(f"Error fetching points: {str(e)}")
        return 0

# Function to fetch points concurrently for better performance
def fetch_points_concurrently(participants_data, progress_callback=None):
    results = {}
    total = len(participants_data)
    processed = 0
    
    def fetch_points(participant_info):
        nonlocal processed
            try:
            # Get points from the URL
                points = get_points(participant_info['profile_url'])
                processed += 1
            
            # Update progress if callback provided
            if progress_callback:
                progress_callback(processed, total)
                
            return participant_info['id'], points
            except Exception as e:
                processed += 1
            if progress_callback:
                progress_callback(processed, total)
            return participant_info['id'], 0
    
    # Use ThreadPoolExecutor to process URLs concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all tasks
            future_to_participant = {
            executor.submit(fetch_points, p): p['id'] for p in participants_data
            }
            
        # Process results as they complete
            for future in concurrent.futures.as_completed(future_to_participant):
                try:
                participant_id, points = future.result()
                    results[participant_id] = points
            except Exception:
                participant_id = future_to_participant[future]
                results[participant_id] = 0
    
    return results

def validate_csv(file_stream):
    """Validate the CSV file structure"""
    try:
        # Read the CSV file
        df = pd.read_csv(file_stream)
        
        # Check for required columns
        required_columns = ['Name', 'profile']
        for col in required_columns:
            if col not in df.columns:
                return False, f"Required column '{col}' is missing"
        
        # Don't validate profile URLs - we'll handle invalid ones by assigning 0 points
        return True, "CSV structure is valid"
    except Exception as e:
        return False, f"Error validating CSV: {str(e)}"

@app.route('/')
def index():
        return render_template('index.html')

@app.route('/download_example')
def download_example():
    """Download an example CSV file"""
    return send_from_directory('.', 'example.csv', as_attachment=True)

@app.route('/upload', methods=['POST'])
def upload():
    """Handle CSV upload and process immediately"""
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
    
    try:
        # Small delay to ensure the loading spinner is visible to users
        time.sleep(1)
        
        # Generate a unique filename
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        filename = f"{timestamp}_{secure_filename(file.filename)}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Save the file
        file.save(file_path)
        
        # Validate CSV structure
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        file_stream = io.StringIO(content, newline=None)
        is_valid, message = validate_csv(file_stream)
        
        if not is_valid:
            flash(message, 'error')
            return redirect(url_for('index'))
        
        # Process the CSV
        file_stream.seek(0)  # Reset to beginning of the file
        df = pd.read_csv(file_stream)
        
        # Process each participant
        participants_data = []
        invalid_url_count = 0
        
        # First pass - prepare data for concurrent processing
        for idx, row in df.iterrows():
            try:
                name = row['Name']
                profile_url = row['profile']
                
                # Check if profile URL is valid or missing
                if pd.isna(profile_url):
                    # Handle invalid URL by assigning 0 points
                    invalid_url_count += 1
                    # Add a participant with default values
                    participants_data.append({
                        'id': idx,
                        'name': name,
                        'profile_url': "#",
                        'points': 0
                    })
                else:
                    # Clean and format the URL
                    profile_url = str(profile_url).strip()
                    # Add to list for processing
                    participants_data.append({
                        'id': idx,
                        'name': name,
                        'profile_url': profile_url,
                        'points': None  # Will be filled in by concurrent processing
                    })
            except Exception as e:
                app.logger.error(f"Error processing participant {row['Name']}: {str(e)}")
                # Add a participant with error values
                participants_data.append({
                    'id': idx,
                    'name': row['Name'],
                    'profile_url': "#",
                    'points': 0
                })
        
        # Process valid URLs concurrently
        valid_participants = [p for p in participants_data if p['profile_url'] != "#"]
        if valid_participants:
            # Show processing message
            total_profiles = len(valid_participants)
            flash(f"Starting to process {total_profiles} profiles...", 'info')
            
            # Processing progress tracking
            processed_count = 0
            
            def update_progress(processed, total):
                nonlocal processed_count
                processed_count = processed
        
        # Fetch points concurrently
            points_results = fetch_points_concurrently(valid_participants, update_progress)
            
            # Add final progress status
            flash(f"Successfully processed {processed_count}/{total_profiles} profiles", 'success')
            
            # Update participants data with results
            for p in participants_data:
                if p['profile_url'] != "#":
                    p['points'] = points_results.get(p['id'], 0)
        
        # Create results list for the template
        results = [
            {
                'name': p['name'],
                'points': p['points'] if p['points'] is not None else 0,
                'profile_url': p['profile_url']
            }
            for p in participants_data
        ]
        
        # Sort by points (descending)
        results.sort(key=lambda x: x['points'], reverse=True)
        
        # Add message about invalid URLs
        if invalid_url_count > 0:
            flash(f'Found {invalid_url_count} missing profile URLs. These entries have been assigned 0 points.', 'warning')
        
        # Return results page
        return render_template('results.html', results=results)
        
    except Exception as e:
        app.logger.error(f"Error processing CSV: {str(e)}")
        flash(f'Error processing CSV: {str(e)}', 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    # Get port from environment variable (Render.com sets this)
    port = int(os.environ.get('PORT', 5000))
    # Bind to 0.0.0.0 to listen on all interfaces
    app.run(host='0.0.0.0', port=port, debug=False) 
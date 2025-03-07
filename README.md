# GDG Points Tracker

A Flask-based web application to track Google Developer Groups community program participants' points from Google Cloud Skills Boost profiles.

## Features

- **User Authentication System**: Each user can manage their own set of participants
- **CSV Upload**: Upload participant data through CSV files
- **Concurrent Processing**: Fast point tracking with multi-threading
- **Weekly Points Tracking**: Track points earned each week
- **Historical Data**: Maintain history of points over time
- **User-friendly Interface**: Clean Bootstrap interface with responsive design

## Screenshots

- Login/Registration screen
- Participant points dashboard
- CSV upload interface
- Points history view

## Installation

### Prerequisites

- Python 3.7+
- pip (Python package installer)

### Setup

1. Clone the repository:

```bash
git clone https://github.com/yourusername/gdg-points-tracker.git
cd gdg-points-tracker
```

2. Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Initialize the database:

```bash
python -c "from app import app, db; with app.app_context(): db.create_all()"
```

5. Run the application:

```bash
flask run
```

The application will be available at http://127.0.0.1:5000/

## Usage

1. Register a new account
2. Login to the system
3. Upload a CSV file with participant data
   - CSV must contain 'Name' and 'profile' columns
   - 'profile' column should contain Google Cloud Skills Boost profile URLs
4. View participants' current points
5. Refresh points weekly to track progress

## CSV Format

The application expects a CSV file with the following format:

| Name | profile | mail (optional) |
|------|---------|----------------|
| John Doe | https://www.cloudskillsboost.google/public_profiles/... | john@example.com |
| Jane Smith | https://www.cloudskillsboost.google/public_profiles/... | jane@example.com |

## Deployment

### Render.com Deployment

1. Create a new Web Service on Render
2. Connect your GitHub repository
3. Use the following settings:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
4. Add the following environment variables:
   - `SECRET_KEY`: A strong random secret key
   - `DATABASE_URL`: Your database URL (Render PostgreSQL or SQLite)

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Google Developer Groups program
- Google Cloud Skills Boost platform
- Flask and SQLAlchemy frameworks
- Bootstrap for the UI components 
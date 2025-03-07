# GDG Points Tracker

A simple Flask-based web application to track Google Developer Groups community program participants' points from Google Cloud Skills Boost profiles.

## Features

- **CSV Upload**: Upload participant data through CSV files
- **Fast Points Scraping**: Retrieves current points from Google Cloud Skills Boost profiles
- **Concurrent Processing**: Fast point tracking with multi-threading
- **Copy Functionality**: Easily copy points data for use in other applications
- **Simple Interface**: Clean Bootstrap interface with responsive design

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

4. Run the application:

```bash
python app.py
```

The application will be available at http://127.0.0.1:5000/

## Usage

1. Upload a CSV file with participant data
   - CSV must contain 'Name' and 'profile' columns
   - 'profile' column should contain Google Cloud Skills Boost profile URLs
2. View participants' current points
3. Use the "Copy Points Only" or "Copy Name and Points" buttons to copy data to clipboard

## CSV Format

The application expects a CSV file with the following format:

| Name | profile |
|------|---------|
| John Doe | https://www.cloudskillsboost.google/public_profiles/... |
| Jane Smith | https://www.cloudskillsboost.google/public_profiles/... |

## How It Works

The application uses BeautifulSoup to scrape points data from Google Cloud Skills Boost profile pages. When a CSV is uploaded, the app processes each profile URL in parallel to fetch the points values efficiently.

## Deployment

To deploy the application:

1. Install the requirements: `pip install -r requirements.txt`
2. Run the application: `python app.py` or `gunicorn app:app` for production

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Google Developer Groups program
- Google Cloud Skills Boost platform
- Flask framework
- Bootstrap for the UI components 
import logging
import os
from flask import Flask
from src.database import db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)

# Create a minimal Flask app
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or os.environ.get('SQLALCHEMY_DATABASE_URI') or 'sqlite:///timekpr.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize the database with the app
db.init_app(app)

# Use the app context to drop and recreate all tables
with app.app_context():
    _LOGGER.info("Dropping all tables...")
    db.drop_all()
    _LOGGER.info("Creating all tables...")
    db.create_all()
    _LOGGER.info("Database reset complete!")
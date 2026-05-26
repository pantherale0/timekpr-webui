FROM python:3.14-slim

WORKDIR /app

# Create instance directory for Flask app data (database)
RUN mkdir -p /app/instance && chmod 777 /app/instance

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ARG SERVER_VERSION=v0.0.0-nightly
ENV TIMEKPR_SERVER_VERSION=$SERVER_VERSION
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Expose the port the app runs on
EXPOSE 5000

# Command to run the application
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--bind", "0.0.0.0:5000", "app:app"]

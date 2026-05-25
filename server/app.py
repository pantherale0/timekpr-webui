from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import os
from datetime import datetime, date, timedelta
import json
import logging
import pytz

from src.database import (
    db,
    ManagedUser,
    UserTimeUsage,
    Settings,
    UserWeeklySchedule,
    UserDailyTimeInterval,
    coerce_time_spent_day,
    AgentDevice,
)
from src.agent_helper import AgentClient, AgentConnectionManager
from flask_sock import Sock
from src.task_manager import BackgroundTaskManager
from src.oidc_helper import OIDCHelper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Get timezone from environment variable or default to UTC
TIMEZONE_STR = os.environ.get('TZ', 'UTC')
try:
    LOCAL_TIMEZONE = pytz.timezone(TIMEZONE_STR)
    logging.info(f"Using timezone: {TIMEZONE_STR}")
except pytz.exceptions.UnknownTimeZoneError:
    logging.warning(f"Unknown timezone '{TIMEZONE_STR}', falling back to UTC")
    LOCAL_TIMEZONE = pytz.UTC
    TIMEZONE_STR = 'UTC'

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timekpr.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize the database
db.init_app(app)

# Initialize WebSocket support
sock = Sock(app)

# Initialize background task manager
task_manager = BackgroundTaskManager()
task_manager.init_app(app)

# Initialize OIDC helper
oidc_helper = OIDCHelper()

# Admin username remains hardcoded
ADMIN_USERNAME = 'admin'

# Make OIDC status available globally in templates
@app.context_processor
def inject_oidc_status():
    """Inject OIDC status and session user into templates"""
    return {
        'oidc_enabled': oidc_helper.is_enabled,
        'session_user': session.get('user')
    }

# Jinja2 filter to convert UTC datetime to local timezone
@app.template_filter('localtime')
def localtime_filter(dt):
    """Convert UTC datetime to local timezone"""
    if dt is None:
        return None

    # If datetime is naive (no timezone info), assume it's UTC
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)

    # Convert to local timezone
    local_dt = dt.astimezone(LOCAL_TIMEZONE)
    return local_dt

# Make timezone string available to templates
@app.context_processor
def inject_timezone():
    """Inject timezone info into all templates"""
    return {'timezone': TIMEZONE_STR}

import secrets

def ws_agent_handler(ws):
    """
    WebSocket endpoint for client agents.
    Handles dynamic pairing, manual approval review, and HMAC challenge-response handshake.
    """
    remote_ip = request.remote_addr or "127.0.0.1"
    if request.headers.get("X-Forwarded-For"):
        remote_ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
        
    logging.info(f"WebSocket connection attempt from {remote_ip}")
    
    # 1. Await initial "hello" registration message
    system_id = None
    try:
        hello_msg_raw = ws.receive(timeout=10)
        if not hello_msg_raw:
            logging.warning("Handshake timeout: empty hello message")
            return
            
        hello_msg = json.loads(hello_msg_raw)
        if hello_msg.get("type") != "hello":
            logging.warning(f"Unexpected initial message type: {hello_msg.get('type')}")
            ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Expected 'hello' type"}))
            return
            
        system_id = hello_msg.get("system_id")
        reg_token = hello_msg.get("registration_token")
        
        if not system_id:
            logging.warning("Initial hello missing system_id")
            ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Missing system_id"}))
            return

        # 2. Check and enforce Registration Token firewall
        expected_reg_token = AgentConnectionManager.REGISTRATION_TOKEN
        
        with app.app_context():
            # Lookup device in database
            device = AgentDevice.query.get(system_id)
            
            if not device:
                # If a registration token is required, verify it
                if expected_reg_token and reg_token != expected_reg_token:
                    logging.warning(f"Registration rejected: Invalid registration token from {system_id}")
                    ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Invalid registration token"}))
                    return
                
                # Register a new device in 'pending' state
                device = AgentDevice(system_id=system_id, system_ip=remote_ip, status='pending')
                db.session.add(device)
                db.session.commit()
                logging.info(f"New pending device registered: {system_id} from {remote_ip}")
            else:
                # Existing device, update IP snapshot
                device.system_ip = remote_ip
                db.session.commit()

            # 3. Handle device pairing states
            if device.status == 'pending':
                logging.info(f"Device {system_id} is PENDING approval. Waiting...")
                AgentConnectionManager.register_pending(system_id, ws)
                ws.send(json.dumps({"type": "pairing_status", "status": "pending"}))
                
                # Keep the socket open in pending state, waiting for admin approval trigger
                try:
                    while True:
                        msg = ws.receive()
                        if not msg:
                            break
                except Exception:
                    pass
                return
                
            elif device.status == 'rejected':
                logging.warning(f"Connection rejected: Device {system_id} is banned/rejected")
                ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Device rejected/banned"}))
                return
                
            elif device.status == 'approved':
                # Device is approved! Perform secure challenge-response
                challenge = secrets.token_hex(32)
                ws.send(json.dumps({
                    "type": "challenge",
                    "challenge": challenge
                }))
                
                # Wait for authentication signature response
                auth_msg_raw = ws.receive(timeout=10)
                if not auth_msg_raw:
                    logging.warning(f"Handshake timeout for approved device {system_id}")
                    return
                    
                auth_msg = json.loads(auth_msg_raw)
                if auth_msg.get("type") != "register":
                    logging.warning(f"Unexpected response type from {system_id}: {auth_msg.get('type')}")
                    return
                    
                signature = auth_msg.get("signature")
                if not signature:
                    logging.warning(f"Handshake from {system_id} missing signature")
                    return
                    
                # Verify using device-specific secure token
                if not AgentConnectionManager.verify_signature(challenge, system_id, signature):
                    logging.warning(f"Authentication signature verification failed for device {system_id}")
                    ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Invalid authentication signature"}))
                    return
                    
                # Authentication succeeded! Register active connection
                AgentConnectionManager.register(system_id, ws, remote_ip)
                ws.send(json.dumps({"type": "auth_result", "success": True, "message": "Authenticated successfully"}))
                
                # Dynamically update the system_ip snapshot for all managed users on this host!
                users = ManagedUser.query.filter_by(system_id=system_id).all()
                for u in users:
                    u.system_ip = remote_ip
                
                device.last_seen = datetime.utcnow()
                db.session.commit()
                logging.info(f"Device {system_id} authenticated successfully. Snapshotted IP {remote_ip} for {len(users)} users.")
                
    except Exception as e:
        logging.error(f"Error during WebSocket handshake / loop for {system_id}: {e}")
        return

    # 4. Main message listening loop for approved connections
    try:
        while True:
            msg_raw = ws.receive()
            if not msg_raw:
                break
                
            msg = json.loads(msg_raw)
            msg_type = msg.get("type")
            
            if msg_type == "command_response":
                correlation_id = msg.get("correlation_id")
                AgentConnectionManager.route_response(correlation_id, msg)
            else:
                logging.warning(f"Received unexpected message type from client {system_id}: {msg_type}")
                
    except Exception as e:
        logging.info(f"WebSocket connection closed for agent {system_id}: {e}")
    finally:
        if system_id:
            AgentConnectionManager.unregister_pending(system_id)
            AgentConnectionManager.unregister(system_id)

sock.route('/ws')(ws_agent_handler)

@app.route('/', methods=['GET', 'POST'])
def login():
    # If already logged in, go straight to dashboard
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))

    if oidc_helper.is_enabled:
        # SSO Auto-redirect flow
        state = oidc_helper.generate_state()
        session['oidc_state'] = state
        
        # Generate redirect URI pointing to our callback endpoint
        redirect_uri = url_for('oidc_callback', _external=True)
        
        try:
            auth_url = oidc_helper.get_authorization_url(state, redirect_uri)
            return redirect(auth_url)
        except Exception as e:
            logging.error(f"OIDC login redirection failed: {e}")
            flash(f"OIDC Login failed to initialize: OIDC provider is offline or misconfigured. Falling back to local credentials.", "warning")
            return render_template('login.html', error="OIDC provider connection error.")

    # Fallback: Traditional form-based local login
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Check admin password using hash comparison
        if username == ADMIN_USERNAME and Settings.check_admin_password(password):
            session['logged_in'] = True
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            error = 'Invalid credentials. Please try again.'
            flash(error, 'danger')
    
    return render_template('login.html', error=error)

@app.route('/callback')
def oidc_callback():
    if not oidc_helper.is_enabled:
        flash("OIDC is not enabled.", "danger")
        return redirect(url_for('login'))

    state_param = request.args.get('state')
    if not state_param or state_param != session.get('oidc_state'):
        flash("Authentication failed: Invalid state token (CSRF attempt prevented).", "danger")
        return redirect(url_for('login'))

    # Clear state after verification
    session.pop('oidc_state', None)

    code = request.args.get('code')
    if not code:
        flash("Authentication failed: No authorization code returned from provider.", "danger")
        return redirect(url_for('login'))

    try:
        redirect_uri = url_for('oidc_callback', _external=True)
        # Exchange code for tokens
        tokens = oidc_helper.exchange_code(code, redirect_uri)
        access_token = tokens.get('access_token')
        
        # Get user details from userinfo endpoint
        user_info = oidc_helper.get_user_info(access_token)
        
        # Extract details and log in
        session['logged_in'] = True
        session['user'] = {
            'username': user_info.get('preferred_username') or user_info.get('sub') or 'OIDC User',
            'email': user_info.get('email'),
            'name': user_info.get('name')
        }
        
        flash(f"Logged in successfully as {session['user']['username']}!", "success")
        return redirect(url_for('dashboard'))
    except Exception as e:
        logging.error(f"OIDC callback processing failed: {e}")
        flash(f"Authentication failed: {str(e)}", "danger")
        return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Get all valid users - make sure we're getting fresh data by expiring SQLAlchemy's cache
    db.session.expire_all()
    users = ManagedUser.query.filter_by(is_valid=True).all()
    
    # Track users with pending time adjustments
    pending_adjustments = {}
    
    # Prepare user data for the dashboard
    user_data = []
    for user in users:
        # Get usage data for charts
        usage_data = user.get_recent_usage(days=7)
        
        # Get time left today if available
        time_left = user.get_config_value('TIME_LEFT_DAY')
        if time_left is not None:
            time_left_hours = time_left // 3600
            time_left_minutes = (time_left % 3600) // 60
            time_left_formatted = f"{time_left_hours}h {time_left_minutes}m"
        else:
            time_left_formatted = "Unknown"
        
        # Do NOT format last_checked time - pass the datetime object directly
        # So the template can format it
        
        # Check for pending time adjustments
        if user.pending_time_adjustment is not None and user.pending_time_operation is not None:
            minutes = user.pending_time_adjustment // 60
            operation = user.pending_time_operation
            pending_adjustments[str(user.id)] = f"{operation}{minutes} minutes"
        
        user_data.append({
            'id': user.id,
            'username': user.username,
            'system_id': user.system_id,
            'system_ip': user.system_ip,
            'is_online': AgentConnectionManager.is_online(user.system_id),
            'last_checked': user.last_checked,  # Keep as datetime object
            'usage_data': usage_data,
            'time_left': time_left_formatted,
            'weekly_schedule': user.weekly_schedule
        })
    
    return render_template('dashboard.html', users=user_data, pending_adjustments=pending_adjustments)

@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Get all managed users
    users = ManagedUser.query.all()
    approved_devices = AgentDevice.query.filter_by(status='approved').all()
    pending_devices = AgentDevice.query.filter_by(status='pending').all()
    return render_template('admin.html', users=users, approved_devices=approved_devices, pending_devices=pending_devices)

@app.route('/api/device/approve/<system_id>', methods=['POST'])
def approve_device(system_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': 'Device not found'}), 404
        
    if device.status != 'pending':
        return jsonify({'success': False, 'message': f'Device is not pending (status: {device.status})'}), 400
        
    # Generate 64-character token (secrets.token_hex(32))
    secure_token = secrets.token_hex(32)
    device.secure_token = secure_token
    device.status = 'approved'
    db.session.commit()
    
    # Check if there is an active pending connection
    ws = AgentConnectionManager.get_pending_connection(system_id)
    if ws:
        try:
            ws.send(json.dumps({
                "type": "pairing_approved",
                "token": secure_token
            }))
            # Clean up pending connections
            AgentConnectionManager.unregister_pending(system_id)
        except Exception as e:
            logging.error(f"Failed to send pairing_approved to device {system_id}: {e}")
            
    logging.info(f"Approved device {system_id} and generated secure token.")
    return jsonify({'success': True, 'message': f'Device {system_id} approved successfully.'})

@app.route('/api/device/reject/<system_id>', methods=['POST'])
def reject_device(system_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
        
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': 'Device not found'}), 404
        
    device.status = 'rejected'
    device.secure_token = None
    db.session.commit()
    
    # Close any active or pending connection
    ws_pending = AgentConnectionManager.get_pending_connection(system_id)
    if ws_pending:
        try:
            ws_pending.close()
        except Exception:
            pass
        AgentConnectionManager.unregister_pending(system_id)
        
    ws_active = AgentConnectionManager.get_connection(system_id)
    if ws_active:
        try:
            ws_active.close()
        except Exception:
            pass
        AgentConnectionManager.unregister(system_id)
        
    logging.info(f"Rejected device {system_id} and closed connections.")
    return jsonify({'success': True, 'message': f'Device {system_id} rejected successfully.'})

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Handle password change
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # Validate inputs
        if not current_password or not new_password or not confirm_password:
            flash('All fields are required', 'danger')
        elif not Settings.check_admin_password(current_password):
            flash('Current password is incorrect', 'danger')
        elif new_password != confirm_password:
            flash('New passwords do not match', 'danger')
        elif len(new_password) < 4:
            flash('New password must be at least 4 characters long', 'danger')
        else:
            # Update the password with hashing
            Settings.set_admin_password(new_password)
            flash('Password updated successfully', 'success')
            
            # Redirect to avoid form resubmission
            return redirect(url_for('settings'))
    
    return render_template('settings.html')

@app.route('/api/task-status')
def get_task_status():
    """Get the status of the background task manager"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    status = task_manager.get_status()
    return jsonify({
        'success': True,
        'status': status
    })

@app.route('/restart-tasks')
def restart_tasks():
    """Restart the background task manager"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    task_manager.restart()
    flash('Background tasks restarted', 'success')
    
    # Redirect back to the referring page
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    else:
        return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('user', None)
    if oidc_helper.is_enabled:
        return redirect(url_for('login'))
    else:
        flash('You have been logged out', 'info')
        return redirect(url_for('login'))

@app.route('/users/add', methods=['GET', 'POST'])
def add_user():
    if not session.get('logged_in'):
        if request.method == 'GET':
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    if request.method == 'GET':
        return redirect(url_for('admin'))
    
    username = request.form.get('username')
    system_id = request.form.get('system_id')
    
    if not username or not system_id:
        flash('Both username and system ID are required', 'danger')
        return redirect(url_for('admin'))
    
    # Validate the submitted system_id exists and has status == 'approved'
    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash(f'System ID {system_id} is not registered or approved', 'danger')
        return redirect(url_for('admin'))
        
    # Check if user already exists
    existing_user = ManagedUser.query.filter_by(username=username, system_id=system_id).first()
    
    if existing_user:
        flash(f'User {username} on {system_id} already exists', 'warning')
        return redirect(url_for('admin'))
    
    # Create new user
    new_user = ManagedUser(username=username, system_id=system_id, system_ip="Offline")
    
    # Validate with agent
    agent_client = AgentClient(system_id=system_id)
    is_valid, message, config_dict = agent_client.validate_user(username)
    
    new_user.is_valid = is_valid
    new_user.last_checked = datetime.utcnow()
    
    if is_valid and config_dict:
        new_user.last_config = json.dumps(config_dict)
        
        # Add the user to get an ID first
        db.session.add(new_user)
        db.session.commit()
        
        # Add today's usage data
        today = date.today()
        time_spent = coerce_time_spent_day(config_dict.get('TIME_SPENT_DAY', 0))
        
        usage = UserTimeUsage(
            user_id=new_user.id,
            date=today,
            time_spent=time_spent
        )
        db.session.add(usage)
        db.session.commit()
        
        flash(f'User {username} added and validated successfully', 'success')
    else:
        db.session.add(new_user)
        db.session.commit()
        flash(f'User {username} added but validation failed: {message}', 'warning')
    
    return redirect(url_for('admin'))

@app.route('/users/validate/<int:user_id>')
def validate_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    # Validate with agent
    agent_client = AgentClient(system_id=user.system_id)
    is_valid, message, config_dict = agent_client.validate_user(user.username)
    
    user.is_valid = is_valid
    user.last_checked = datetime.utcnow()
    
    if is_valid and config_dict:
        user.last_config = json.dumps(config_dict)
        
        # Update today's usage data
        today = date.today()
        time_spent = coerce_time_spent_day(config_dict.get('TIME_SPENT_DAY', 0))
        
        # Look for an existing record for today
        usage = UserTimeUsage.query.filter_by(
            user_id=user.id,
            date=today
        ).first()
        
        if usage:
            usage.time_spent = time_spent
        else:
            # Create a new record
            usage = UserTimeUsage(
                user_id=user.id,
                date=today,
                time_spent=time_spent
            )
            db.session.add(usage)
        
        db.session.commit()
        flash(f'User {user.username} validated successfully', 'success')
    else:
        db.session.commit()
        flash(f'User validation failed: {message}', 'danger')
    
    return redirect(url_for('admin'))

@app.route('/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    username = user.username
    
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User {username} removed successfully', 'success')
    return redirect(url_for('admin'))

@app.route('/api/user/<int:user_id>/usage')
def get_user_usage(user_id):
    """API endpoint to get user usage data"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    days = request.args.get('days', 7, type=int)
    
    usage_data = user.get_recent_usage(days=days)
    
    # Format for chart.js
    labels = list(usage_data.keys())
    values = list(usage_data.values())
    
    # Convert seconds to hours for better readability
    values_hours = [round(v / 3600, 1) for v in values]
    
    return jsonify({
        'success': True,
        'labels': labels,
        'values': values_hours,
        'username': user.username
    })

@app.route('/weekly-schedule/<int:user_id>')
def weekly_schedule_user(user_id):
    """Display weekly schedule management page for a specific user"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Get the specific user
    user = ManagedUser.query.get_or_404(user_id)
    
    # Ensure the user has a weekly schedule record
    if not user.weekly_schedule:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
        db.session.commit()
    
    return render_template('weekly_schedule_single.html', user=user)

@app.route('/weekly-schedule/update', methods=['POST'])
def update_weekly_schedule():
    """Update weekly schedule for a user"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user_id = request.form.get('user_id')
    
    if not user_id:
        flash('User ID is required', 'danger')
        return redirect(url_for('admin'))
    
    try:
        user_id = int(user_id)
    except ValueError:
        flash('Invalid user ID', 'danger')
        return redirect(url_for('admin'))
    
    user = ManagedUser.query.get_or_404(user_id)
    
    # Get schedule data from form
    schedule_data = {}
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    
    for day in days:
        hours = request.form.get(day, '0')
        try:
            hours = float(hours)
            if hours < 0:
                hours = 0
            elif hours > 24:
                hours = 24
        except (ValueError, TypeError):
            hours = 0
        schedule_data[day] = hours  # Store as float hours to support fractional hours
    
    # Get or create weekly schedule
    if not user.weekly_schedule:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
        db.session.flush()  # Get the ID
        user.weekly_schedule = schedule
    else:
        schedule = user.weekly_schedule
    
    # Update the schedule
    schedule.set_schedule_from_dict(schedule_data)
    
    try:
        db.session.commit()
        flash(f'Weekly schedule updated for {user.username}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating schedule: {str(e)}', 'danger')
    
    return redirect(url_for('weekly_schedule_user', user_id=user.id))

@app.route('/api/user/<int:user_id>/intervals')
def get_user_intervals(user_id):
    """API endpoint to get user time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    # Get all intervals for this user
    intervals = UserDailyTimeInterval.query.filter_by(user_id=user.id).all()
    
    # Format intervals by day
    intervals_dict = {}
    for interval in intervals:
        intervals_dict[interval.day_of_week] = {
            'id': interval.id,
            'day_name': interval.get_day_name(),
            'start_hour': interval.start_hour,
            'start_minute': interval.start_minute,
            'end_hour': interval.end_hour,
            'end_minute': interval.end_minute,
            'is_enabled': interval.is_enabled,
            'is_synced': interval.is_synced,
            'time_range': interval.get_time_range_string(),
            'last_synced': interval.last_synced.strftime('%Y-%m-%d %H:%M') if interval.last_synced else None
        }
    
    return jsonify({
        'success': True,
        'intervals': intervals_dict,
        'username': user.username
    })

@app.route('/api/user/<int:user_id>/intervals/update', methods=['POST'])
def update_user_intervals(user_id):
    """API endpoint to update user time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    try:
        # Get interval data from request
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        intervals_data = data.get('intervals', {})
        
        for day_str, interval_data in intervals_data.items():
            try:
                day_of_week = int(day_str)
                if not (1 <= day_of_week <= 7):
                    continue
                
                # Get or create interval for this day
                interval = UserDailyTimeInterval.query.filter_by(
                    user_id=user.id,
                    day_of_week=day_of_week
                ).first()
                
                if not interval:
                    interval = UserDailyTimeInterval(
                        user_id=user.id,
                        day_of_week=day_of_week
                    )
                    db.session.add(interval)
                
                # Update interval properties
                interval.start_hour = int(interval_data.get('start_hour', 9))
                interval.start_minute = int(interval_data.get('start_minute', 0))
                interval.end_hour = int(interval_data.get('end_hour', 17))
                interval.end_minute = int(interval_data.get('end_minute', 0))
                interval.is_enabled = bool(interval_data.get('is_enabled', False))
                
                # Validate the interval
                if not interval.is_valid_interval():
                    return jsonify({
                        'success': False,
                        'message': f'Invalid time interval for {interval.get_day_name()}: start time must be before end time'
                    }), 400
                
                # Mark as modified (needs sync)
                interval.mark_modified()
                
            except (ValueError, KeyError) as e:
                return jsonify({
                    'success': False,
                    'message': f'Invalid data format: {str(e)}'
                }), 400
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Time intervals updated for {user.username}',
            'username': user.username
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Error updating intervals: {str(e)}'
        }), 500

@app.route('/api/user/<int:user_id>/intervals/sync-status')
def get_intervals_sync_status(user_id):
    """Get sync status of user's time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    # Get all intervals for this user
    intervals = UserDailyTimeInterval.query.filter_by(user_id=user.id).all()
    
    # Check if any intervals need sync
    needs_sync = any(not interval.is_synced for interval in intervals)
    
    # Get last sync time (most recent among all intervals)
    last_synced = None
    if intervals:
        synced_intervals = [i for i in intervals if i.last_synced]
        if synced_intervals:
            last_synced = max(i.last_synced for i in synced_intervals)
            last_synced = last_synced.strftime('%Y-%m-%d %H:%M')
    
    # Count enabled vs total intervals
    enabled_count = sum(1 for i in intervals if i.is_enabled)
    total_count = len(intervals)
    
    return jsonify({
        'success': True,
        'needs_sync': needs_sync,
        'last_synced': last_synced,
        'enabled_intervals': enabled_count,
        'total_intervals': total_count,
        'username': user.username
    })

@app.route('/api/schedule-sync-status/<int:user_id>')
def get_schedule_sync_status(user_id):
    """Get the sync status of a user's weekly schedule"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    if user.weekly_schedule:
        schedule_dict = user.weekly_schedule.get_schedule_dict()
        last_synced = None
        if user.weekly_schedule.last_synced:
            last_synced = user.weekly_schedule.last_synced.strftime('%Y-%m-%d %H:%M')
        
        return jsonify({
            'success': True,
            'is_synced': user.weekly_schedule.is_synced,
            'schedule': schedule_dict,
            'last_synced': last_synced,
            'last_modified': user.weekly_schedule.last_modified.strftime('%Y-%m-%d %H:%M') if user.weekly_schedule.last_modified else None
        })
    else:
        return jsonify({
            'success': True,
            'is_synced': True,  # No schedule means no sync needed
            'schedule': None,
            'last_synced': None,
            'last_modified': None
        })

@app.route('/stats/<int:user_id>')
def user_stats(user_id):
    """Display extended usage history for a single user"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))

    user = ManagedUser.query.get_or_404(user_id)

    daily_30   = user.get_recent_usage(days=30)
    weekly_13  = user.get_usage_weekly_grouped(weeks=13)
    monthly_12 = user.get_usage_monthly_grouped(months=12)
    all_monthly = user.get_all_usage_monthly()

    return render_template('stats.html',
        user=user,
        daily_30=daily_30,
        weekly_13=weekly_13,
        monthly_12=monthly_12,
        all_monthly=all_monthly,
    )

@app.route('/api/modify-time', methods=['POST'])
def modify_time():
    """Modify time left for a user"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    # Get parameters from request
    user_id = request.form.get('user_id')
    operation = request.form.get('operation')
    seconds = request.form.get('seconds')
    
    if not user_id or not operation or not seconds:
        return jsonify({'success': False, 'message': 'Missing required parameters'}), 400
    
    try:
        user_id = int(user_id)
        seconds = int(seconds)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid parameter format'}), 400
    
    # Validate operation
    if operation not in ['+', '-']:
        return jsonify({'success': False, 'message': "Operation must be '+' or '-'"}), 400
    
    # Get user from database
    user = ManagedUser.query.get_or_404(user_id)
    
    # Create Agent client
    agent_client = AgentClient(system_id=user.system_id)
    
    # Execute the command
    success, message = agent_client.modify_time_left(user.username, operation, seconds)
    
    if success:
        # Update user info to reflect changes
        is_valid, _, config_dict = agent_client.validate_user(user.username)
        if is_valid and config_dict:
            user.last_checked = datetime.utcnow()
            user.last_config = json.dumps(config_dict)
            # Clear any pending adjustments since we succeeded
            user.pending_time_adjustment = None
            user.pending_time_operation = None
            db.session.commit()
            
        return jsonify({
            'success': True,
            'message': message,
            'username': user.username,
            'refresh': True
        })
    else:
        # Store as pending adjustment if it failed
        # First clear any existing pending adjustment
        user.pending_time_adjustment = seconds
        user.pending_time_operation = operation
        db.session.commit()
        
        return jsonify({
            'success': True,  # We report success since we stored it for later
            'message': f"Computer seems to be offline. Time adjustment of {operation}{seconds} seconds has been queued and will be applied when the computer comes online.",
            'username': user.username,
            'pending': True,
            'refresh': True
        })

# With app context
if not os.environ.get('TESTING'):
    with app.app_context():
        db.create_all()
        print("Database tables verified")
        
        # Auto-migration: check if system_id column exists, if not add it
        try:
            from sqlalchemy import text
            result = db.session.execute(text("PRAGMA table_info(managed_user)")).fetchall()
            column_names = [row[1] for row in result]
            if 'system_id' not in column_names:
                db.session.execute(text("ALTER TABLE managed_user ADD COLUMN system_id VARCHAR(50)"))
                db.session.commit()
                print("Successfully migrated database: added system_id column to managed_user")
        except Exception as e:
            print(f"Database migration error: {e}")
        
        # Initialize admin password if it doesn't exist
        if not Settings.get_value('admin_password_hash', None) and not Settings.get_value('admin_password', None):
            Settings.set_admin_password('admin')
            print("Admin password initialized")
        
        # Start background tasks automatically
        task_manager.start()
        print("Background tasks started automatically")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
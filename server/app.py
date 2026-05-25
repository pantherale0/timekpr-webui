from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import os
from datetime import datetime, date, timedelta
import json
import logging
import pytz

from src.database import (
    db,
    ManagedUser,
    ManagedUserDeviceMap,
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
from sqlalchemy import text


def _format_seconds(seconds):
    if seconds is None:
        return "Unknown"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _mapping_config(mapping):
    if not mapping.last_config:
        return {}
    try:
        return json.loads(mapping.last_config)
    except (TypeError, ValueError):
        return {}


def _hostname_key(hostname):
    normalized = (hostname or '').strip()
    return normalized.casefold() if normalized else None


def _build_device_label_map(devices):
    hostname_counts = {}
    for device in devices:
        key = _hostname_key(device.system_hostname)
        if key:
            hostname_counts[key] = hostname_counts.get(key, 0) + 1

    label_map = {}
    for device in devices:
        key = _hostname_key(device.system_hostname)
        label_map[device.system_id] = device.format_display_name(
            include_suffix=bool(key and hostname_counts.get(key, 0) > 1)
        )
    return label_map


def _get_device_label_map():
    return _build_device_label_map(AgentDevice.query.all())


def _device_display_label(system_id, label_map=None):
    if not system_id:
        return 'Unknown device'

    labels = label_map if label_map is not None else _get_device_label_map()
    return labels.get(system_id, system_id)


def _mapping_display_label(mapping, label_map=None):
    return f"{mapping.linux_username}@{_device_display_label(mapping.system_id, label_map)}"


def _refresh_managed_user_summary(user):
    valid_mappings = [mapping for mapping in user.device_mappings if mapping.is_valid]
    user.is_valid = bool(valid_mappings)

    if not valid_mappings:
        user.last_checked = datetime.utcnow()
        user.last_config = json.dumps({
            "TIME_SPENT_DAY": 0,
            "TIME_LEFT_DAY": None,
            "MAPPING_COUNT": len(user.device_mappings),
            "ONLINE_MAPPING_COUNT": 0,
        })
        return

    shared_spent = 0
    time_left_values = []
    for mapping in valid_mappings:
        config = _mapping_config(mapping)
        shared_spent += coerce_time_spent_day(config.get("TIME_SPENT_DAY", 0))
        time_left = config.get("TIME_LEFT_DAY")
        if isinstance(time_left, int):
            time_left_values.append(time_left)

    user.last_checked = max(
        (mapping.last_checked for mapping in valid_mappings if mapping.last_checked),
        default=datetime.utcnow(),
    )
    user.last_config = json.dumps({
        "TIME_SPENT_DAY": shared_spent,
        "TIME_LEFT_DAY": min(time_left_values) if time_left_values else None,
        "MAPPING_COUNT": len(user.device_mappings),
        "ONLINE_MAPPING_COUNT": sum(
            1 for mapping in user.device_mappings if AgentConnectionManager.is_online(mapping.system_id)
        ),
    })

    today = date.today()
    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
    if usage:
        usage.time_spent = shared_spent
    else:
        db.session.add(UserTimeUsage(user_id=user.id, date=today, time_spent=shared_spent))


INTERVAL_STEP_MINUTES = 15
INTERVAL_DAY_NAMES = {
    1: 'Monday',
    2: 'Tuesday',
    3: 'Wednesday',
    4: 'Thursday',
    5: 'Friday',
    6: 'Saturday',
    7: 'Sunday',
}


def _serialize_interval(interval):
    return {
        'id': interval.id,
        'day_name': interval.get_day_name(),
        'sort_order': interval.sort_order,
        'start_hour': interval.start_hour,
        'start_minute': interval.start_minute,
        'end_hour': interval.end_hour,
        'end_minute': interval.end_minute,
        'is_enabled': interval.is_enabled,
        'is_synced': interval.is_synced,
        'time_range': interval.get_time_range_string(),
        'last_synced': interval.last_synced.strftime('%Y-%m-%d %H:%M') if interval.last_synced else None,
    }


def _normalize_interval_entries(raw_entries):
    if raw_entries is None:
        return []
    if isinstance(raw_entries, dict):
        return [raw_entries]
    if not isinstance(raw_entries, list):
        raise ValueError('Each day must contain a list of intervals')
    return raw_entries


def _build_intervals_for_day(day_of_week, raw_entries):
    if day_of_week not in INTERVAL_DAY_NAMES:
        raise ValueError(f'Invalid day of week: {day_of_week}')

    interval_rows = []
    for raw_interval in _normalize_interval_entries(raw_entries):
        if not isinstance(raw_interval, dict):
            raise ValueError(f'Invalid interval payload for {INTERVAL_DAY_NAMES[day_of_week]}')

        if not bool(raw_interval.get('is_enabled', True)):
            continue

        interval_rows.append(UserDailyTimeInterval(
            day_of_week=day_of_week,
            sort_order=len(interval_rows),
            start_hour=int(raw_interval.get('start_hour', 9)),
            start_minute=int(raw_interval.get('start_minute', 0)),
            end_hour=int(raw_interval.get('end_hour', 17)),
            end_minute=int(raw_interval.get('end_minute', 0)),
            is_enabled=True,
        ))

    ordered_rows = UserDailyTimeInterval.sort_intervals(interval_rows)
    for index, interval in enumerate(ordered_rows):
        interval.sort_order = index

    if not UserDailyTimeInterval.validate_interval_collection(
        ordered_rows,
        step_minutes=INTERVAL_STEP_MINUTES,
    ):
        raise ValueError(
            f'Invalid time intervals for {INTERVAL_DAY_NAMES[day_of_week]}: '
            f'intervals must be ordered, non-overlapping, and use '
            f'{INTERVAL_STEP_MINUTES}-minute increments'
        )

    return ordered_rows


def _build_disabled_interval_placeholder(day_of_week):
    return UserDailyTimeInterval(
        day_of_week=day_of_week,
        sort_order=0,
        start_hour=0,
        start_minute=0,
        end_hour=0,
        end_minute=15,
        is_enabled=False,
    )

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
        system_hostname = hello_msg.get("system_hostname")
        if isinstance(system_hostname, str):
            system_hostname = system_hostname.strip() or None
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
                device = AgentDevice(
                    system_id=system_id,
                    system_hostname=system_hostname,
                    system_ip=remote_ip,
                    status='pending',
                )
                db.session.add(device)
                db.session.commit()
                logging.info(f"New pending device registered: {system_id} from {remote_ip}")
            else:
                # Existing device, update latest hostname and IP snapshot
                if "system_hostname" in hello_msg:
                    device.system_hostname = system_hostname
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
                
                device.last_seen = datetime.utcnow()
                db.session.commit()
                logging.info(
                    "Device %s authenticated successfully. Updated device IP snapshot to %s.",
                    system_id,
                    remote_ip,
                )
                
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
    users = ManagedUser.query.all()
    
    # Track users with pending time adjustments
    pending_adjustments = {}
    
    # Prepare user data for the dashboard
    user_data = []
    for user in users:
        # Get usage data for charts
        usage_data = user.get_recent_usage(days=7)
        mapping_count = len(user.device_mappings)
        online_mapping_count = sum(
            1 for mapping in user.device_mappings if AgentConnectionManager.is_online(mapping.system_id)
        )
        valid_mapping_count = sum(1 for mapping in user.device_mappings if mapping.is_valid)
        time_left_formatted = _format_seconds(user.get_config_value('TIME_LEFT_DAY'))
        
        # Check for pending time adjustments
        if user.pending_time_adjustment is not None and user.pending_time_operation is not None:
            minutes = user.pending_time_adjustment // 60
            operation = user.pending_time_operation
            pending_adjustments[str(user.id)] = f"{operation}{minutes} minutes"
        
        user_data.append({
            'id': user.id,
            'username': user.username,
            'is_online': online_mapping_count > 0,
            'mapping_count': mapping_count,
            'online_mapping_count': online_mapping_count,
            'valid_mapping_count': valid_mapping_count,
            'last_checked': user.last_checked,
            'usage_data': usage_data,
            'time_left': time_left_formatted,
            'weekly_schedule': user.weekly_schedule
        })

    users_sorted = sorted(user_data, key=lambda item: item['username'].lower())
    return render_template('dashboard.html', users=users_sorted, pending_adjustments=pending_adjustments)

@app.route('/admin')
def admin():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Get all managed users
    users = ManagedUser.query.order_by(ManagedUser.username.asc()).all()
    device_labels = _get_device_label_map()
    approved_devices = AgentDevice.query.filter_by(status='approved').all()
    pending_devices = AgentDevice.query.filter_by(status='pending').all()
    return render_template(
        'admin.html',
        users=users,
        approved_devices=approved_devices,
        pending_devices=pending_devices,
        device_labels=device_labels,
    )

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
    device_label = _device_display_label(system_id)
    
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
    return jsonify({'success': True, 'message': f'Device {device_label} approved successfully.'})

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
    device_label = _device_display_label(system_id)
    
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
    return jsonify({'success': True, 'message': f'Device {device_label} rejected successfully.'})

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

@app.route('/managed-users/add', methods=['POST'])
def create_managed_user():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    username = (request.form.get('username') or '').strip()
    if not username:
        flash('Managed user name is required', 'danger')
        return redirect(url_for('admin'))

    existing_user = ManagedUser.query.filter_by(username=username).first()
    if existing_user:
        flash(f'Managed user {username} already exists', 'warning')
        return redirect(url_for('admin'))

    managed_user = ManagedUser(
        username=username,
        is_valid=False,
        system_ip='Unassigned',
    )
    db.session.add(managed_user)
    db.session.commit()

    flash(f'Managed user {username} created', 'success')
    return redirect(url_for('admin'))


@app.route('/managed-users/<int:user_id>/mappings/add', methods=['POST'])
def add_user_mapping(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    system_id = (request.form.get('system_id') or '').strip()
    linux_username = (request.form.get('linux_username') or '').strip()
    linux_uid_raw = (request.form.get('linux_uid') or '').strip()

    if not system_id or not linux_username:
        flash('Device and Linux username are required', 'danger')
        return redirect(url_for('admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash(f'Device {_device_display_label(system_id)} is not registered or approved', 'danger')
        return redirect(url_for('admin'))

    device_label = _device_display_label(system_id)
    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        flash(f'{user.username} is already linked to {device_label}', 'warning')
        return redirect(url_for('admin'))

    linux_uid = None
    if linux_uid_raw:
        try:
            linux_uid = int(linux_uid_raw)
        except ValueError:
            flash('Linux UID must be numeric', 'danger')
            return redirect(url_for('admin'))

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=linux_username,
        linux_uid=linux_uid,
        is_valid=False,
    )
    db.session.add(mapping)
    db.session.commit()

    flash(f'Mapping added: {user.username} -> {linux_username}@{device_label}', 'success')
    return redirect(url_for('admin'))


@app.route('/users/add', methods=['GET', 'POST'])
def add_user():
    """
    Backward-compatible endpoint that creates a managed user and one mapping.
    """
    if request.method == 'GET':
        return redirect(url_for('admin'))
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    username = (request.form.get('username') or '').strip()
    system_id = (request.form.get('system_id') or '').strip()

    if not username or not system_id:
        flash('Both username and device are required', 'danger')
        return redirect(url_for('admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash(f'Device {_device_display_label(system_id)} is not registered or approved', 'danger')
        return redirect(url_for('admin'))

    device_label = _device_display_label(system_id)
    user = ManagedUser.query.filter_by(username=username).first()
    if not user:
        user = ManagedUser(username=username, is_valid=False, system_ip='Unassigned')
        db.session.add(user)
        db.session.flush()

    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        db.session.rollback()
        flash(f'User {username} on {device_label} already exists', 'warning')
        return redirect(url_for('admin'))

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=username,
    )
    db.session.add(mapping)
    db.session.commit()
    flash(f'Managed user {username} and mapping added', 'success')
    return redirect(url_for('admin'))


@app.route('/users/validate/<int:user_id>')
def validate_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mappings = list(user.device_mappings)
    if not mappings:
        flash('No device mappings configured for this managed user', 'warning')
        return redirect(url_for('admin'))

    total_spent = 0
    total_valid = 0
    messages = []
    device_labels = _get_device_label_map()
    for mapping in mappings:
        agent_client = AgentClient(system_id=mapping.system_id)
        is_valid, message, config_dict = agent_client.validate_user(mapping.linux_username)
        mapping.last_checked = datetime.utcnow()
        mapping.is_valid = is_valid
        if is_valid and config_dict:
            mapping.last_config = json.dumps(config_dict)
            if config_dict.get("LINUX_UID") is not None:
                try:
                    mapping.linux_uid = int(config_dict.get("LINUX_UID"))
                except (TypeError, ValueError):
                    pass
            total_spent += coerce_time_spent_day(config_dict.get('TIME_SPENT_DAY', 0))
            total_valid += 1
        else:
            messages.append(f"{_mapping_display_label(mapping, device_labels)}: {message}")

    user.is_valid = total_valid > 0
    user.last_checked = datetime.utcnow()
    user.last_config = json.dumps({
        "TIME_SPENT_DAY": total_spent,
        "MAPPING_COUNT": len(mappings),
        "VALID_MAPPING_COUNT": total_valid,
    })

    today = date.today()
    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
    if usage:
        usage.time_spent = total_spent
    else:
        db.session.add(UserTimeUsage(user_id=user.id, date=today, time_spent=total_spent))

    db.session.commit()
    if total_valid:
        flash(f'Validated {total_valid}/{len(mappings)} mapping(s) for {user.username}', 'success')
    else:
        flash(f'User validation failed: {"; ".join(messages) if messages else "No mappings validated"}', 'danger')
    return redirect(url_for('admin'))


@app.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/validate')
def validate_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    agent_client = AgentClient(system_id=mapping.system_id)
    is_valid, message, config_dict = agent_client.validate_user(mapping.linux_username)

    mapping.last_checked = datetime.utcnow()
    mapping.is_valid = is_valid
    if is_valid and config_dict:
        mapping.last_config = json.dumps(config_dict)
        if config_dict.get("LINUX_UID") is not None:
            try:
                mapping.linux_uid = int(config_dict.get("LINUX_UID"))
            except (TypeError, ValueError):
                pass

    _refresh_managed_user_summary(user)
    db.session.commit()
    device_labels = _get_device_label_map()

    if is_valid:
        flash(f'Mapping validated: {_mapping_display_label(mapping, device_labels)}', 'success')
    else:
        flash(f'Mapping validation failed: {message}', 'danger')
    return redirect(url_for('admin'))


@app.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/delete', methods=['POST'])
def delete_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    mapping_label = _mapping_display_label(mapping)
    db.session.delete(mapping)
    db.session.flush()
    _refresh_managed_user_summary(user)
    db.session.commit()
    flash(f'Mapping removed: {mapping_label}', 'success')
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
    intervals = UserDailyTimeInterval.query.filter_by(user_id=user.id).order_by(
        UserDailyTimeInterval.day_of_week,
        UserDailyTimeInterval.sort_order,
        UserDailyTimeInterval.id,
    ).all()

    intervals_dict = {str(day): [] for day in range(1, 8)}
    for interval in intervals:
        if interval.is_enabled:
            intervals_dict[str(interval.day_of_week)].append(_serialize_interval(interval))

    return jsonify({
        'success': True,
        'intervals': intervals_dict,
        'username': user.username,
        'step_minutes': INTERVAL_STEP_MINUTES,
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
        
        intervals_data = data.get('intervals')
        if not isinstance(intervals_data, dict):
            return jsonify({'success': False, 'message': 'Intervals payload must be an object'}), 400

        replacement_map = {}
        for day_str, raw_entries in intervals_data.items():
            try:
                day_of_week = int(day_str)
            except (TypeError, ValueError):
                return jsonify({
                    'success': False,
                    'message': f'Invalid day value: {day_str}'
                }), 400

            try:
                replacement_map[day_of_week] = _build_intervals_for_day(day_of_week, raw_entries)
            except (ValueError, TypeError) as e:
                return jsonify({
                    'success': False,
                    'message': str(e)
                }), 400

        for day_of_week, new_intervals in replacement_map.items():
            existing_intervals = UserDailyTimeInterval.query.filter_by(
                user_id=user.id,
                day_of_week=day_of_week,
            ).all()
            for interval in existing_intervals:
                db.session.delete(interval)
            db.session.flush()

            persisted_intervals = new_intervals or [_build_disabled_interval_placeholder(day_of_week)]
            for interval in persisted_intervals:
                interval.user_id = user.id
                interval.mark_modified()
                db.session.add(interval)
        
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
    total_count = enabled_count
    
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
    
    mappings = list(user.device_mappings)
    if not mappings:
        return jsonify({'success': False, 'message': 'No device mappings configured for this user'}), 400

    online_mappings = [mapping for mapping in mappings if AgentConnectionManager.is_online(mapping.system_id)]
    device_labels = _get_device_label_map()
    if not online_mappings:
        user.pending_time_adjustment = seconds
        user.pending_time_operation = operation
        db.session.commit()
        return jsonify({
            'success': True,
            'message': f"All mapped devices are offline. Adjustment {operation}{seconds}s queued.",
            'username': user.username,
            'pending': True,
            'refresh': True
        })

    failures = []
    for mapping in online_mappings:
        agent_client = AgentClient(system_id=mapping.system_id)
        success, message = agent_client.modify_time_left(mapping.linux_username, operation, seconds)
        if not success:
            failures.append(f"{_mapping_display_label(mapping, device_labels)}: {message}")

    if failures:
        user.pending_time_adjustment = seconds
        user.pending_time_operation = operation
        db.session.commit()
        return jsonify({
            'success': True,
            'message': f"Applied to {len(online_mappings) - len(failures)}/{len(online_mappings)} online mapping(s). Remaining queued.",
            'details': failures,
            'username': user.username,
            'pending': True,
            'refresh': True
        })

    user.pending_time_adjustment = None
    user.pending_time_operation = None
    user.last_checked = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'success': True,
        'message': f"Adjustment applied to {len(online_mappings)} mapping(s).",
        'username': user.username,
        'refresh': True
    })

def run_schema_migrations():
    """Run lightweight SQLite migrations and backfill mapping table."""
    agent_device_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(agent_device)")).fetchall()
    }
    if agent_device_columns and 'system_hostname' not in agent_device_columns:
        db.session.execute(text("""
            ALTER TABLE agent_device
            ADD COLUMN system_hostname VARCHAR(255) NULL
        """))
        db.session.commit()

    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS managed_user_device_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            managed_user_id INTEGER NOT NULL,
            system_id VARCHAR(50) NOT NULL,
            linux_username VARCHAR(50) NOT NULL,
            linux_uid INTEGER NULL,
            is_valid BOOLEAN DEFAULT 0,
            last_checked DATETIME NULL,
            last_config TEXT NULL,
            date_added DATETIME NULL,
            last_modified DATETIME NULL,
            FOREIGN KEY(managed_user_id) REFERENCES managed_user(id),
            FOREIGN KEY(system_id) REFERENCES agent_device(system_id),
            UNIQUE(managed_user_id, system_id),
            UNIQUE(system_id, linux_username),
            UNIQUE(system_id, linux_uid)
        )
    """))
    db.session.commit()

    users = ManagedUser.query.filter(ManagedUser.system_id.isnot(None)).all()
    for user in users:
        if not user.system_id:
            continue
        existing = ManagedUserDeviceMap.query.filter_by(
            managed_user_id=user.id,
            system_id=user.system_id,
        ).first()
        if existing:
            continue

        linux_uid = None
        if user.last_config:
            try:
                parsed = json.loads(user.last_config)
                if parsed.get("LINUX_UID") is not None:
                    linux_uid = int(parsed.get("LINUX_UID"))
            except (TypeError, ValueError):
                linux_uid = None

        mapping = ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id=user.system_id,
            linux_username=user.username,
            linux_uid=linux_uid,
            is_valid=user.is_valid,
            last_checked=user.last_checked,
            last_config=user.last_config,
        )
        db.session.add(mapping)
    db.session.commit()

    interval_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(user_daily_time_interval)")).fetchall()
    }
    if interval_columns and 'sort_order' not in interval_columns:
        db.session.execute(text("""
            CREATE TABLE user_daily_time_interval_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                start_hour INTEGER NOT NULL,
                start_minute INTEGER DEFAULT 0,
                end_hour INTEGER NOT NULL,
                end_minute INTEGER DEFAULT 0,
                is_enabled BOOLEAN DEFAULT 1,
                is_synced BOOLEAN DEFAULT 0,
                last_synced DATETIME NULL,
                last_modified DATETIME NULL,
                FOREIGN KEY(user_id) REFERENCES managed_user(id),
                UNIQUE(user_id, day_of_week, sort_order)
            )
        """))
        db.session.execute(text("""
            INSERT INTO user_daily_time_interval_new (
                id,
                user_id,
                day_of_week,
                sort_order,
                start_hour,
                start_minute,
                end_hour,
                end_minute,
                is_enabled,
                is_synced,
                last_synced,
                last_modified
            )
            SELECT
                id,
                user_id,
                day_of_week,
                0,
                start_hour,
                start_minute,
                end_hour,
                end_minute,
                1,
                is_synced,
                last_synced,
                last_modified
            FROM user_daily_time_interval
            WHERE COALESCE(is_enabled, 1) = 1
        """))
        db.session.execute(text("DROP TABLE user_daily_time_interval"))
        db.session.execute(text("ALTER TABLE user_daily_time_interval_new RENAME TO user_daily_time_interval"))
        db.session.commit()


if not os.environ.get('TESTING'):
    with app.app_context():
        db.create_all()
        run_schema_migrations()
        print("Database tables verified")

        # Initialize admin password if it doesn't exist
        if not Settings.get_value('admin_password_hash', None) and not Settings.get_value('admin_password', None):
            Settings.set_admin_password('admin')
            print("Admin password initialized")

        # Start background tasks automatically
        task_manager.start()
        print("Background tasks started automatically")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
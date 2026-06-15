import os
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, send_from_directory, current_app, session
import dateutil.parser
from src.database import db, AgentDevice, ManagedUserDeviceMap, YoutubeHistory, ManagedUser

_LOGGER = logging.getLogger(__name__)

api_youtube_bp = Blueprint('api_youtube', __name__)

# Default hardcoded Extension ID generated from our private key.
# This can be customized if needed, but a stable default is required.
DEFAULT_EXTENSION_ID = "kpaecpjkfljdgbhmlndgfgjdbobmpeoc"

def _get_extension_id():
    """Dynamically read the generated extension ID from extension_id.txt or fallback."""
    try:
        id_path = os.path.join(current_app.static_folder, 'extensions', 'extension_id.txt')
        if os.path.exists(id_path):
            with open(id_path, 'r') as f:
                val = f.read().strip()
                if val:
                    return val
    except Exception:
        pass
    return DEFAULT_EXTENSION_ID

@api_youtube_bp.route('/api/youtube/log', methods=['POST'])
def log_youtube_history():
    """
    Endpoint for the Chrome Extension and Android Agent to upload YouTube watch logs.
    Protected by the Agent Device Secure Token.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        _LOGGER.warning("YouTube history upload rejected: Missing or invalid Authorization header.")
        return jsonify({'success': False, 'message': 'Missing or invalid authorization header'}), 401
    
    token = auth_header.split(' ')[1].strip()
    
    # Authenticate device
    device = AgentDevice.query.filter_by(secure_token=token).first()
    if not device:
        _LOGGER.warning("YouTube history upload rejected: Invalid agent device token.")
        return jsonify({'success': False, 'message': 'Invalid token'}), 401

    payload = request.get_json() or {}
    linux_username = payload.get('linux_username')
    logs = payload.get('logs', [])

    if not linux_username:
        return jsonify({'success': False, 'message': 'Missing linux_username'}), 400

    # Map the OS/device user to ManagedUser
    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=device.system_id,
        linux_username=linux_username
    ).first()

    if not mapping:
        _LOGGER.warning(
            "YouTube history upload for device %s rejected: No mapping found for username '%s'.",
            device.system_id,
            linux_username
        )
        return jsonify({'success': False, 'message': f'No user mapping for user {linux_username} on this device'}), 400

    managed_user_id = mapping.managed_user_id
    success_count = 0

    for entry in logs:
        video_id = entry.get('video_id')
        title = entry.get('title')
        if not video_id or not title:
            continue

        watched_at_str = entry.get('watched_at')
        try:
            watched_at = dateutil.parser.isoparse(watched_at_str)
            if watched_at.tzinfo is None:
                watched_at = watched_at.replace(tzinfo=timezone.utc)
            else:
                watched_at = watched_at.astimezone(timezone.utc)
        except Exception:
            watched_at = datetime.now(timezone.utc)

        record = YoutubeHistory(
            device_id=device.system_id,
            managed_user_id=managed_user_id,
            video_id=video_id[:20],
            title=title[:255],
            channel_name=entry.get('channel_name', '')[:255] if entry.get('channel_name') else None,
            channel_id=entry.get('channel_id', '')[:100] if entry.get('channel_id') else None,
            duration_seconds=int(entry.get('duration_seconds', 0)),
            watched_at=watched_at
        )
        db.session.add(record)
        success_count += 1

    try:
        db.session.commit()
        _LOGGER.info(
            "Successfully logged %d YouTube history entries for user %s (%d) on device %s.",
            success_count,
            linux_username,
            managed_user_id,
            device.system_id
        )
        return jsonify({'success': True, 'count': success_count})
    except Exception as e:
        db.session.rollback()
        _LOGGER.exception("Database error while committing YouTube history logs.")
        return jsonify({'success': False, 'message': 'Database error'}), 500


@api_youtube_bp.route('/api/extensions/update', methods=['GET'])
def get_extension_update_manifest():
    """
    Serves the Chrome Auto-Update XML manifest.
    Instructs Chrome where to download the CRX package.
    """
    extension_id = getattr(current_app, 'extension_id', None) or _get_extension_id()
    base_url = request.url_root.rstrip('/')
    
    xml_content = f"""<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='{extension_id}'>
    <updatecheck codebase='{base_url}/api/extensions/download' version='1.0.2' />
  </app>
</gupdate>"""

    response = current_app.response_class(xml_content, mimetype='application/xml')
    return response


@api_youtube_bp.route('/api/extensions/download', methods=['GET'])
def download_extension_crx():
    """
    Serves the packaged .crx extension file.
    """
    extensions_dir = os.path.join(current_app.static_folder, 'extensions')
    filename = 'youtube_monitor.crx'
    
    if not os.path.exists(os.path.join(extensions_dir, filename)):
        _LOGGER.error("Requested extension package not found at: %s", os.path.join(extensions_dir, filename))
        return jsonify({'success': False, 'message': 'Extension package not built/available'}), 404
        
    return send_from_directory(
        directory=extensions_dir,
        path=filename,
        mimetype='application/x-chrome-extension',
        as_attachment=True,
        download_name=filename
    )


@api_youtube_bp.route('/api/user/<int:user_id>/youtube', methods=['GET'])
def get_user_youtube_history(user_id):
    """
    Get YouTube watch history and analytics for a managed user.
    Only accessible by logged-in parents.
    """
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    # Verify user exists
    user = ManagedUser.query.get_or_404(user_id)

    # Query params
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    search = request.args.get('search', '').strip()
    category = request.args.get('category', 'all').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    # Base query for history list
    query = YoutubeHistory.query.filter_by(managed_user_id=user.id)

    # Date range filters
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            query = query.filter(YoutubeHistory.watched_at >= start_date)
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
            query = query.filter(YoutubeHistory.watched_at <= end_date)
        except ValueError:
            pass

    # Search filter
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            db.or_(
                YoutubeHistory.title.ilike(search_filter),
                YoutubeHistory.channel_name.ilike(search_filter)
            )
        )

    # Category filter
    if category != 'all':
        query = query.filter(YoutubeHistory.category == category)

    # Clone query for analytics calculation (before pagination)
    analytics_query = query

    # Calculate main history list with pagination
    paginated = query.order_by(db.desc(YoutubeHistory.watched_at)).paginate(page=page, per_page=per_page, error_out=False)
    history_list = [item.to_dict() for item in paginated.items]

    # Calculate categories analytics
    category_stats = db.session.query(
        YoutubeHistory.category,
        db.func.sum(YoutubeHistory.duration_seconds).label('total_duration'),
        db.func.count(YoutubeHistory.id).label('video_count')
    ).filter(YoutubeHistory.managed_user_id == user.id)

    # Apply same date filters to analytics query
    if start_date_str:
        category_stats = category_stats.filter(YoutubeHistory.watched_at >= start_date)
    if end_date_str:
        category_stats = category_stats.filter(YoutubeHistory.watched_at <= end_date)
    if search:
        category_stats = category_stats.filter(db.or_(YoutubeHistory.title.ilike(search_filter), YoutubeHistory.channel_name.ilike(search_filter)))

    category_results = category_stats.group_by(YoutubeHistory.category).all()
    categories_data = [
        {
            'category': row.category,
            'total_seconds': int(row.total_duration or 0),
            'count': row.video_count
        } for row in category_results
    ]

    # Calculate channel analytics (Top 10)
    channel_stats = db.session.query(
        YoutubeHistory.channel_name,
        db.func.sum(YoutubeHistory.duration_seconds).label('total_duration'),
        db.func.count(YoutubeHistory.id).label('video_count')
    ).filter(YoutubeHistory.managed_user_id == user.id)

    if start_date_str:
        channel_stats = channel_stats.filter(YoutubeHistory.watched_at >= start_date)
    if end_date_str:
        channel_stats = channel_stats.filter(YoutubeHistory.watched_at <= end_date)
    if search:
        channel_stats = channel_stats.filter(db.or_(YoutubeHistory.title.ilike(search_filter), YoutubeHistory.channel_name.ilike(search_filter)))

    channel_results = channel_stats.group_by(YoutubeHistory.channel_name).order_by(db.desc('total_duration')).limit(10).all()
    channels_data = [
        {
            'channel_name': row.channel_name or 'Unknown Channel',
            'total_seconds': int(row.total_duration or 0),
            'count': row.video_count
        } for row in channel_results
    ]

    # Get list of all distinct categories active in this query for filters
    all_categories_query = db.session.query(YoutubeHistory.category).filter_by(managed_user_id=user.id).distinct().all()
    distinct_categories = sorted([row[0] for row in all_categories_query if row[0]])

    total_seconds = sum(cat['total_seconds'] for cat in categories_data)
    total_videos = sum(cat['count'] for cat in categories_data)

    return jsonify({
        'success': True,
        'data': {
            'history': history_list,
            'pagination': {
                'page': paginated.page,
                'per_page': paginated.per_page,
                'total_items': paginated.total,
                'total_pages': paginated.pages,
                'has_next': paginated.has_next,
                'has_prev': paginated.has_prev,
            },
            'analytics': {
                'categories': categories_data,
                'channels': channels_data,
                'total_seconds': total_seconds,
                'total_videos': total_videos,
            },
            'distinct_categories': distinct_categories
        }
    })


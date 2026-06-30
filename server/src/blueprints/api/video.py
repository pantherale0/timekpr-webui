import os
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, send_from_directory, current_app, session
import dateutil.parser
from src.models import (
    db,
    AgentDevice,
    ManagedUserDeviceMap,
    VideoHistory,
    WebHistory,
    ManagedUser,
    AiPromptLog,
    AiSessionLog,
)

_LOGGER = logging.getLogger(__name__)

api_video_bp = Blueprint('api_video', __name__)

DEFAULT_EXTENSION_ID = "kpaecpjkfljdgbhmlndgfgjdbobmpeoc"
DEFAULT_EXTENSION_VERSION = "0.0.0"


def _get_extension_id():
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


def _get_extension_version():
    try:
        version_path = os.path.join(
            current_app.static_folder, 'extensions', 'extension_version.txt'
        )
        if os.path.exists(version_path):
            with open(version_path, 'r') as f:
                val = f.read().strip()
                if val:
                    return val
    except Exception:
        pass
    return DEFAULT_EXTENSION_VERSION


def _video_url(platform, video_id):
    if platform == VideoHistory.VIDEO_PLATFORM_TIKTOK:
        return f"https://www.tiktok.com/video/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


def _default_category_for_platform(platform):
    if platform == VideoHistory.VIDEO_PLATFORM_TIKTOK:
        return 'TikTok'
    return 'Unknown'


def _authenticate_video_log_request(forced_platform=None):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        _LOGGER.warning("Video history upload rejected: Missing or invalid Authorization header.")
        return None, (jsonify({'success': False, 'message': 'Missing or invalid authorization header'}), 401)

    token = auth_header.split(' ')[1].strip()
    device = AgentDevice.query.filter_by(secure_token=token).first()
    if not device:
        _LOGGER.warning("Video history upload rejected: Invalid agent device token.")
        return None, (jsonify({'success': False, 'message': 'Invalid token'}), 401)

    payload = request.get_json() or {}
    linux_username = payload.get('linux_username')
    logs = payload.get('logs', [])
    platform = forced_platform or (payload.get('platform') or VideoHistory.VIDEO_PLATFORM_YOUTUBE).strip().lower()

    if platform not in VideoHistory.SUPPORTED_PLATFORMS:
        return None, (jsonify({'success': False, 'message': f'Unsupported platform: {platform}'}), 400)

    if not linux_username:
        return None, (jsonify({'success': False, 'message': 'Missing linux_username'}), 400)

    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=device.system_id,
        linux_username=linux_username,
    ).first()

    if not mapping:
        _LOGGER.warning(
            "Video history upload for device %s rejected: No mapping found for username '%s'.",
            device.system_id,
            linux_username,
        )
        return None, (
            jsonify({
                'success': False,
                'message': f'No user mapping for user {linux_username} on this device',
            }),
            400,
        )

    return (device, mapping, logs, platform), None


def _persist_video_logs(device, mapping, logs, platform):
    success_count = 0
    default_category = _default_category_for_platform(platform)

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

        record = VideoHistory(
            device_id=device.system_id,
            managed_user_id=mapping.managed_user_id,
            platform=platform,
            video_id=str(video_id)[:25],
            title=title[:255],
            channel_name=entry.get('channel_name', '')[:255] if entry.get('channel_name') else None,
            channel_id=entry.get('channel_id', '')[:100] if entry.get('channel_id') else None,
            category=default_category,
            duration_seconds=int(entry.get('duration_seconds', 0)),
            watched_at=watched_at,
        )
        db.session.add(record)
        success_count += 1

    return success_count


@api_video_bp.route('/api/video/log', methods=['POST'])
def log_video_history():
    """Upload watch logs for a supported video platform."""
    auth_result, error_response = _authenticate_video_log_request()
    if error_response:
        return error_response

    device, mapping, logs, platform = auth_result
    success_count = _persist_video_logs(device, mapping, logs, platform)

    try:
        db.session.commit()
        _LOGGER.info(
            "Successfully logged %d %s history entries for user %s (%d) on device %s.",
            success_count,
            platform,
            mapping.linux_username,
            mapping.managed_user_id,
            device.system_id,
        )
        return jsonify({'success': True, 'count': success_count})
    except Exception:
        db.session.rollback()
        _LOGGER.exception("Database error while committing video history logs.")
        return jsonify({'success': False, 'message': 'Database error'}), 500


def _log_video_history_alias(forced_platform):
    auth_result, error_response = _authenticate_video_log_request(forced_platform=forced_platform)
    if error_response:
        return error_response

    device, mapping, logs, platform = auth_result
    success_count = _persist_video_logs(device, mapping, logs, platform)

    try:
        db.session.commit()
        _LOGGER.info(
            "Successfully logged %d %s history entries for user %s (%d) on device %s.",
            success_count,
            platform,
            mapping.linux_username,
            mapping.managed_user_id,
            device.system_id,
        )
        return jsonify({'success': True, 'count': success_count})
    except Exception:
        db.session.rollback()
        _LOGGER.exception("Database error while committing video history logs.")
        return jsonify({'success': False, 'message': 'Database error'}), 500


@api_video_bp.route('/api/youtube/log', methods=['POST'])
def log_youtube_history():
    """Backward-compatible alias for YouTube watch log ingestion."""
    return _log_video_history_alias(VideoHistory.VIDEO_PLATFORM_YOUTUBE)


@api_video_bp.route('/api/tiktok/log', methods=['POST'])
def log_tiktok_history():
    """Backward-compatible alias for TikTok watch log ingestion."""
    return _log_video_history_alias(VideoHistory.VIDEO_PLATFORM_TIKTOK)


@api_video_bp.route('/api/browser/log', methods=['POST'])
def log_web_history():
    """Upload web browsing history logs."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        _LOGGER.warning("Web history upload rejected: Missing or invalid Authorization header.")
        return jsonify({'success': False, 'message': 'Missing or invalid authorization header'}), 401

    token = auth_header.split(' ')[1].strip()
    device = AgentDevice.query.filter_by(secure_token=token).first()
    if not device:
        _LOGGER.warning("Web history upload rejected: Invalid agent device token.")
        return jsonify({'success': False, 'message': 'Invalid token'}), 401

    payload = request.get_json() or {}
    linux_username = payload.get('linux_username')
    logs = payload.get('logs', [])

    if not linux_username:
        return jsonify({'success': False, 'message': 'Missing linux_username'}), 400

    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=device.system_id,
        linux_username=linux_username,
    ).first()

    if not mapping:
        _LOGGER.warning(
            "Web history upload for device %s rejected: No mapping found for username '%s'.",
            device.system_id,
            linux_username,
        )
        return jsonify({'success': False, 'message': f'No user mapping for user {linux_username} on this device'}), 400

    managed_user_id = mapping.managed_user_id
    success_count = 0

    for entry in logs:
        url = entry.get('url')
        domain = entry.get('domain')
        if not url or not domain:
            continue

        title = entry.get('title')
        visited_at_str = entry.get('visited_at')
        try:
            visited_at = dateutil.parser.isoparse(visited_at_str)
            if visited_at.tzinfo is None:
                visited_at = visited_at.replace(tzinfo=timezone.utc)
            else:
                visited_at = visited_at.astimezone(timezone.utc)
        except Exception:
            visited_at = datetime.now(timezone.utc)

        record = WebHistory(
            device_id=device.system_id,
            managed_user_id=managed_user_id,
            url=url,
            title=title[:255] if title else None,
            domain=domain[:255],
            visited_at=visited_at,
        )
        db.session.add(record)
        success_count += 1

    try:
        db.session.commit()
        _LOGGER.info(
            "Successfully logged %d web history entries for user %s (%d) on device %s.",
            success_count,
            linux_username,
            managed_user_id,
            device.system_id,
        )
        return jsonify({'success': True, 'count': success_count})
    except Exception:
        db.session.rollback()
        _LOGGER.exception("Database error while committing web history logs.")
        return jsonify({'success': False, 'message': 'Database error'}), 500


@api_video_bp.route('/api/extensions/update', methods=['GET'])
def get_extension_update_manifest():
    extension_id = getattr(current_app, 'extension_id', None) or _get_extension_id()
    extension_version = getattr(current_app, 'extension_version', None) or _get_extension_version()
    base_url = request.url_root.rstrip('/')
    if request.headers.get('X-Forwarded-Proto', '').lower() == 'https':
        if base_url.startswith('http://'):
            base_url = base_url.replace('http://', 'https://', 1)

    xml_content = f"""<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='{extension_id}'>
    <updatecheck codebase='{base_url}/api/extensions/download' version='{extension_version}' />
  </app>
</gupdate>"""

    return current_app.response_class(xml_content, mimetype='application/xml')


@api_video_bp.route('/api/extensions/download', methods=['GET'])
def download_extension_crx():
    extensions_dir = os.path.join(current_app.static_folder, 'extensions')
    filename = 'youtube_monitor.crx'

    if not os.path.exists(os.path.join(extensions_dir, filename)):
        _LOGGER.error(
            "Requested extension package not found at: %s",
            os.path.join(extensions_dir, filename),
        )
        return jsonify({'success': False, 'message': 'Extension package not built/available'}), 404

    return send_from_directory(
        directory=extensions_dir,
        path=filename,
        mimetype='application/x-chrome-extension',
        as_attachment=True,
        download_name=filename,
    )


@api_video_bp.route('/api/user/<int:user_id>/youtube', methods=['GET'])
def get_user_youtube_history(user_id):
    """YouTube-only watch history (legacy endpoint)."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    search = request.args.get('search', '').strip()
    category = request.args.get('category', 'all').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    query = VideoHistory.query.filter_by(
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
    )

    start_date = None
    end_date = None
    search_filter = f"%{search}%" if search else None

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )
            query = query.filter(VideoHistory.watched_at >= start_date)
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
            )
            query = query.filter(VideoHistory.watched_at <= end_date)
        except ValueError:
            pass

    if search:
        query = query.filter(
            db.or_(
                VideoHistory.title.ilike(search_filter),
                VideoHistory.channel_name.ilike(search_filter),
            )
        )

    if category != 'all':
        query = query.filter(VideoHistory.category == category)

    paginated = query.order_by(db.desc(VideoHistory.watched_at)).paginate(
        page=page, per_page=per_page, error_out=False
    )
    history_list = [item.to_dict() for item in paginated.items]

    category_stats = db.session.query(
        VideoHistory.category,
        db.func.sum(VideoHistory.duration_seconds).label('total_duration'),
        db.func.count(VideoHistory.id).label('video_count'),
    ).filter(
        VideoHistory.managed_user_id == user.id,
        VideoHistory.platform == VideoHistory.VIDEO_PLATFORM_YOUTUBE,
    )

    if start_date:
        category_stats = category_stats.filter(VideoHistory.watched_at >= start_date)
    if end_date:
        category_stats = category_stats.filter(VideoHistory.watched_at <= end_date)
    if search:
        category_stats = category_stats.filter(
            db.or_(
                VideoHistory.title.ilike(search_filter),
                VideoHistory.channel_name.ilike(search_filter),
            )
        )

    category_results = category_stats.group_by(VideoHistory.category).all()
    categories_data = [
        {
            'category': row.category,
            'total_seconds': int(row.total_duration or 0),
            'count': row.video_count,
        }
        for row in category_results
    ]

    channel_stats = db.session.query(
        VideoHistory.channel_name,
        db.func.sum(VideoHistory.duration_seconds).label('total_duration'),
        db.func.count(VideoHistory.id).label('video_count'),
    ).filter(
        VideoHistory.managed_user_id == user.id,
        VideoHistory.platform == VideoHistory.VIDEO_PLATFORM_YOUTUBE,
    )

    if start_date:
        channel_stats = channel_stats.filter(VideoHistory.watched_at >= start_date)
    if end_date:
        channel_stats = channel_stats.filter(VideoHistory.watched_at <= end_date)
    if search:
        channel_stats = channel_stats.filter(
            db.or_(
                VideoHistory.title.ilike(search_filter),
                VideoHistory.channel_name.ilike(search_filter),
            )
        )

    channel_results = channel_stats.group_by(VideoHistory.channel_name).order_by(
        db.desc('total_duration')
    ).limit(10).all()
    channels_data = [
        {
            'channel_name': row.channel_name or 'Unknown Channel',
            'total_seconds': int(row.total_duration or 0),
            'count': row.video_count,
        }
        for row in channel_results
    ]

    all_categories_query = db.session.query(VideoHistory.category).filter_by(
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
    ).distinct().all()
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
            'distinct_categories': distinct_categories,
        },
    })


@api_video_bp.route('/api/user/<int:user_id>/history', methods=['GET'])
def get_user_combined_history(user_id):
    """Combined web, video, and AI prompt history."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    search = request.args.get('search', '').strip()
    activity_type = request.args.get('type', 'all').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    include_history = request.args.get('include_history', 'true').lower() != 'false'
    include_analytics = request.args.get('include_analytics', 'true').lower() != 'false'

    from sqlalchemy import literal_column, desc, or_, text

    video_query = db.session.query(
        VideoHistory.id.label('id'),
        VideoHistory.platform.label('activity_type'),
        VideoHistory.title.label('title'),
        VideoHistory.video_id.label('url_or_id'),
        VideoHistory.channel_name.label('domain_or_channel'),
        VideoHistory.category.label('category'),
        VideoHistory.duration_seconds.label('duration_seconds'),
        VideoHistory.watched_at.label('timestamp'),
        literal_column("'Allowed'").label('status'),
        db.null().label('prompt_text'),
    ).filter(VideoHistory.managed_user_id == user.id)

    web_query = db.session.query(
        WebHistory.id.label('id'),
        literal_column("'web'").label('activity_type'),
        WebHistory.title.label('title'),
        WebHistory.url.label('url_or_id'),
        WebHistory.domain.label('domain_or_channel'),
        literal_column("'Web Page'").label('category'),
        literal_column("0").label('duration_seconds'),
        WebHistory.visited_at.label('timestamp'),
        literal_column("'Allowed'").label('status'),
        db.null().label('prompt_text'),
    ).filter(WebHistory.managed_user_id == user.id)

    # Resolve device mapping IDs for AI logs
    device_map_ids = [m.id for m in user.device_mappings]

    if device_map_ids:
        ai_query = db.session.query(
            AiPromptLog.id.label('id'),
            literal_column("'ai_prompt'").label('activity_type'),
            AiPromptLog.title.label('title'),
            AiPromptLog.url.label('url_or_id'),
            AiPromptLog.domain.label('domain_or_channel'),
            AiPromptLog.service.label('category'),
            literal_column("0").label('duration_seconds'),
            AiPromptLog.logged_at.label('timestamp'),
            AiPromptLog.status.label('status'),
            AiPromptLog.prompt_text.label('prompt_text'),
        ).filter(AiPromptLog.device_map_id.in_(device_map_ids))
    else:
        # Dummy query returning no rows if no devices are mapped
        ai_query = db.session.query(
            AiPromptLog.id.label('id'),
            literal_column("'ai_prompt'").label('activity_type'),
            AiPromptLog.title.label('title'),
            AiPromptLog.url.label('url_or_id'),
            AiPromptLog.domain.label('domain_or_channel'),
            AiPromptLog.service.label('category'),
            literal_column("0").label('duration_seconds'),
            AiPromptLog.logged_at.label('timestamp'),
            AiPromptLog.status.label('status'),
            AiPromptLog.prompt_text.label('prompt_text'),
        ).filter(literal_column("1") == literal_column("0"))

    start_date = None
    end_date = None
    search_filter = f"%{search}%" if search else None

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )
            video_query = video_query.filter(VideoHistory.watched_at >= start_date)
            web_query = web_query.filter(WebHistory.visited_at >= start_date)
            ai_query = ai_query.filter(AiPromptLog.logged_at >= start_date)
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
            )
            video_query = video_query.filter(VideoHistory.watched_at <= end_date)
            web_query = web_query.filter(WebHistory.visited_at <= end_date)
            ai_query = ai_query.filter(AiPromptLog.logged_at <= end_date)
        except ValueError:
            pass

    if search:
        video_query = video_query.filter(
            or_(
                VideoHistory.title.ilike(search_filter),
                VideoHistory.channel_name.ilike(search_filter),
            )
        )
        web_query = web_query.filter(
            or_(
                WebHistory.title.ilike(search_filter),
                WebHistory.url.ilike(search_filter),
                WebHistory.domain.ilike(search_filter),
            )
        )
        ai_query = ai_query.filter(
            or_(
                AiPromptLog.title.ilike(search_filter),
                AiPromptLog.url.ilike(search_filter),
                AiPromptLog.domain.ilike(search_filter),
                AiPromptLog.prompt_text.ilike(search_filter),
            )
        )

    if activity_type in VideoHistory.SUPPORTED_PLATFORMS:
        video_query = video_query.filter(VideoHistory.platform == activity_type)
        union_stmt = video_query
    elif activity_type == 'web':
        union_stmt = web_query
    elif activity_type == 'ai':
        union_stmt = ai_query
    else:
        from sqlalchemy import union_all
        union_stmt = union_all(video_query, web_query, ai_query)

    history_list = []
    pagination_payload = {
        'page': page,
        'per_page': per_page,
        'total_items': 0,
        'total_pages': 0,
        'has_next': False,
        'has_prev': False,
    }

    if include_history:
        union_sub = union_stmt.subquery()
        paginated = db.session.query(union_sub).order_by(union_sub.c.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        for row in paginated.items:
            item_dict = {
                'id': row.id,
                'type': row.activity_type,
                'title': row.title or "Untitled Page",
                'timestamp': row.timestamp.isoformat() if row.timestamp else None,
                'status': row.status,
            }
            if row.activity_type in VideoHistory.SUPPORTED_PLATFORMS:
                item_dict.update({
                    'platform': row.activity_type,
                    'video_id': row.url_or_id,
                    'channel_name': row.domain_or_channel or "Unknown Channel",
                    'category': row.category or "Unknown",
                    'duration_seconds': int(row.duration_seconds or 0),
                    'url': _video_url(row.activity_type, row.url_or_id),
                })
            elif row.activity_type == 'ai_prompt':
                item_dict.update({
                    'url': row.url_or_id,
                    'domain': row.domain_or_channel or "Unknown Domain",
                    'category': row.category or "AI Service",
                    'prompt_text': row.prompt_text,
                })
            else:
                item_dict.update({
                    'url': row.url_or_id,
                    'domain': row.domain_or_channel or "Unknown Domain",
                    'category': 'Web Page',
                })
            history_list.append(item_dict)

        pagination_payload = {
            'page': paginated.page,
            'per_page': paginated.per_page,
            'total_items': paginated.total,
            'total_pages': paginated.pages,
            'has_next': paginated.has_next,
            'has_prev': paginated.has_prev,
        }

    analytics_payload = None
    if include_analytics:
        video_category_stats = db.session.query(
            VideoHistory.platform,
            VideoHistory.category,
            db.func.sum(VideoHistory.duration_seconds).label('total_duration'),
            db.func.count(VideoHistory.id).label('video_count'),
        ).filter(VideoHistory.managed_user_id == user.id)

        if start_date:
            video_category_stats = video_category_stats.filter(VideoHistory.watched_at >= start_date)
        if end_date:
            video_category_stats = video_category_stats.filter(VideoHistory.watched_at <= end_date)
        if search:
            video_category_stats = video_category_stats.filter(
                or_(
                    VideoHistory.title.ilike(search_filter),
                    VideoHistory.channel_name.ilike(search_filter),
                )
            )

        video_category_results = video_category_stats.group_by(
            VideoHistory.platform, VideoHistory.category
        ).all()
        video_categories_data = [
            {
                'platform': row.platform,
                'category': row.category,
                'total_seconds': int(row.total_duration or 0),
                'count': row.video_count,
            }
            for row in video_category_results
        ]

        video_creator_stats = db.session.query(
            VideoHistory.platform,
            VideoHistory.channel_name,
            db.func.sum(VideoHistory.duration_seconds).label('total_duration'),
            db.func.count(VideoHistory.id).label('video_count'),
        ).filter(VideoHistory.managed_user_id == user.id)

        if start_date:
            video_creator_stats = video_creator_stats.filter(VideoHistory.watched_at >= start_date)
        if end_date:
            video_creator_stats = video_creator_stats.filter(VideoHistory.watched_at <= end_date)
        if search:
            video_creator_stats = video_creator_stats.filter(
                or_(
                    VideoHistory.title.ilike(search_filter),
                    VideoHistory.channel_name.ilike(search_filter),
                )
            )

        video_creator_results = video_creator_stats.group_by(
            VideoHistory.platform, VideoHistory.channel_name
        ).order_by(desc('total_duration')).limit(10).all()
        video_creators_data = [
            {
                'platform': row.platform,
                'channel_name': row.channel_name or 'Unknown Channel',
                'total_seconds': int(row.total_duration or 0),
                'count': row.video_count,
            }
            for row in video_creator_results
        ]

        web_domain_stats = db.session.query(
            WebHistory.domain,
            db.func.count(WebHistory.id).label('visit_count'),
        ).filter(WebHistory.managed_user_id == user.id)

        if start_date:
            web_domain_stats = web_domain_stats.filter(WebHistory.visited_at >= start_date)
        if end_date:
            web_domain_stats = web_domain_stats.filter(WebHistory.visited_at <= end_date)
        if search:
            web_domain_stats = web_domain_stats.filter(
                or_(
                    WebHistory.title.ilike(search_filter),
                    WebHistory.url.ilike(search_filter),
                    WebHistory.domain.ilike(search_filter),
                )
            )

        web_domain_results = web_domain_stats.group_by(WebHistory.domain).order_by(
            desc('visit_count')
        ).limit(10).all()
        domains_data = [
            {
                'domain': row.domain or 'Unknown Domain',
                'count': row.visit_count,
            }
            for row in web_domain_results
        ]

        # AI Stats and Services
        ai_prompt_stats_results = []
        total_ai_seconds = 0
        if device_map_ids:
            ai_prompt_stats_query = db.session.query(
                AiPromptLog.service,
                db.func.count(AiPromptLog.id).label('prompt_count'),
            ).filter(AiPromptLog.device_map_id.in_(device_map_ids))

            if start_date:
                ai_prompt_stats_query = ai_prompt_stats_query.filter(AiPromptLog.logged_at >= start_date)
            if end_date:
                ai_prompt_stats_query = ai_prompt_stats_query.filter(AiPromptLog.logged_at <= end_date)
            if search:
                ai_prompt_stats_query = ai_prompt_stats_query.filter(
                    or_(
                        AiPromptLog.title.ilike(search_filter),
                        AiPromptLog.url.ilike(search_filter),
                        AiPromptLog.domain.ilike(search_filter),
                        AiPromptLog.prompt_text.ilike(search_filter),
                    )
                )
            ai_prompt_stats_results = ai_prompt_stats_query.group_by(AiPromptLog.service).all()

            ai_time_query = db.session.query(
                db.func.sum(AiSessionLog.duration_seconds).label('total_duration')
            ).filter(AiSessionLog.device_map_id.in_(device_map_ids))

            if start_date:
                ai_time_query = ai_time_query.filter(AiSessionLog.logged_at >= start_date)
            if end_date:
                ai_time_query = ai_time_query.filter(AiSessionLog.logged_at <= end_date)

            total_ai_seconds = ai_time_query.scalar() or 0

        total_video_seconds = sum(cat['total_seconds'] for cat in video_categories_data)
        total_video_count = sum(cat['count'] for cat in video_categories_data)
        total_web_visits = sum(dom['count'] for dom in domains_data)

        youtube_categories = [
            cat for cat in video_categories_data
            if cat['platform'] == VideoHistory.VIDEO_PLATFORM_YOUTUBE
        ]
        youtube_channels = [
            creator for creator in video_creators_data
            if creator['platform'] == VideoHistory.VIDEO_PLATFORM_YOUTUBE
        ]

        analytics_payload = {
            'video_categories': video_categories_data,
            'video_creators': video_creators_data,
            'web_domains': domains_data,
            'total_video_seconds': total_video_seconds,
            'total_video_count': total_video_count,
            'total_web_visits': total_web_visits,
            'youtube_categories': youtube_categories,
            'youtube_channels': youtube_channels,
            'total_youtube_seconds': sum(cat['total_seconds'] for cat in youtube_categories),
            'total_youtube_videos': sum(cat['count'] for cat in youtube_categories),
            'total_ai_prompts': sum(row.prompt_count for row in ai_prompt_stats_results),
            'total_ai_seconds': int(total_ai_seconds),
            'ai_services': [
                {
                    'service': row.service,
                    'count': row.prompt_count
                }
                for row in ai_prompt_stats_results
            ]
        }

    return jsonify({
        'success': True,
        'data': {
            'history': history_list,
            'pagination': pagination_payload,
            'analytics': analytics_payload,
        },
    })

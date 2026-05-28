import logging
from datetime import datetime, timezone
from flask import Blueprint, session, request, jsonify, flash, redirect, url_for, abort
from src.database import db, BlocklistSource, BlocklistDomain, ManagedUserBlocklistAssignment, ManagedUser
from src.blocklist_helper import (
    validate_external_source_url,
    parse_blocklist_text,
    compute_source_revision,
    normalize_domain,
)
from src.blocklists_manager import _build_user_blocklist_sync_status

_LOGGER = logging.getLogger(__name__)

api_blocklists_bp = Blueprint('api_blocklists', __name__)


@api_blocklists_bp.route('/blocklists/sources/add', methods=['POST'])
def create_blocklist_source():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    name = (request.form.get('name') or '').strip()
    source_type = (request.form.get('source_type') or BlocklistSource.TYPE_MANUAL).strip()
    source_url = (request.form.get('source_url') or '').strip()
    manual_domains_raw = request.form.get('manual_domains') or ''

    if not name:
        flash('Blocklist name is required', 'danger')
        return redirect(url_for('ui_dashboard.settings'))

    existing = BlocklistSource.query.filter_by(name=name).first()
    if existing:
        flash(f'Blocklist "{name}" already exists', 'warning')
        return redirect(url_for('ui_dashboard.settings'))

    if source_type not in {BlocklistSource.TYPE_MANUAL, BlocklistSource.TYPE_EXTERNAL_URL}:
        flash('Unsupported blocklist source type', 'danger')
        return redirect(url_for('ui_dashboard.settings'))

    validated_url = None
    domains = []
    try:
        if source_type == BlocklistSource.TYPE_EXTERNAL_URL:
            validated_url = validate_external_source_url(source_url)
        else:
            domains, _ = parse_blocklist_text(manual_domains_raw, strict=True)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('ui_dashboard.settings'))

    source = BlocklistSource(
        name=name,
        source_type=source_type,
        source_url=validated_url,
        is_enabled=True,
        content_revision=compute_source_revision(domains),
    )
    db.session.add(source)
    db.session.flush()

    for domain in domains:
        db.session.add(BlocklistDomain(source_id=source.id, domain=domain))

    db.session.commit()

    from app import task_manager
    if source.source_type == BlocklistSource.TYPE_EXTERNAL_URL:
        success, message = task_manager.refresh_external_blocklist_source(source.id)
        flash(message, 'success' if success else 'warning')
    else:
        task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
        flash(f'Blocklist "{source.name}" created with {len(domains)} domain(s)', 'success')

    return redirect(url_for('ui_dashboard.settings'))


@api_blocklists_bp.route('/blocklists/sources/<int:source_id>/delete', methods=['POST'])
def delete_blocklist_source(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source_row = db.session.query(
        BlocklistSource.id,
        BlocklistSource.name,
    ).filter_by(id=source_id).first()
    if source_row is None:
        abort(404)

    source_name = source_row.name
    ManagedUserBlocklistAssignment.query.filter_by(source_id=source_id).delete(
        synchronize_session=False
    )
    BlocklistDomain.query.filter_by(source_id=source_id).delete(
        synchronize_session=False
    )
    BlocklistSource.query.filter_by(id=source_id).delete(
        synchronize_session=False
    )
    db.session.commit()
    
    from app import task_manager
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Blocklist "{source_name}" deleted', 'success')
    return redirect(url_for('ui_dashboard.settings'))


@api_blocklists_bp.route('/blocklists/sources/<int:source_id>/refresh', methods=['POST'])
def refresh_blocklist_source(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    from app import task_manager
    success, message = task_manager.refresh_external_blocklist_source(source_id, force=True)
    flash(message, 'success' if success else 'warning')
    return redirect(url_for('ui_dashboard.settings'))


@api_blocklists_bp.route('/blocklists/sources/<int:source_id>/toggle', methods=['POST'])
def toggle_blocklist_source(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source = BlocklistSource.query.get_or_404(source_id)
    source.is_enabled = request.form.get('is_enabled') == 'on'
    source.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    
    from app import task_manager
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Blocklist "{source.name}" {"enabled" if source.is_enabled else "disabled"}', 'success')
    return redirect(url_for('ui_dashboard.settings'))


@api_blocklists_bp.route('/blocklists/sources/<int:source_id>/domains/add', methods=['POST'])
def add_blocklist_domain(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source = BlocklistSource.query.get_or_404(source_id)
    if source.source_type != BlocklistSource.TYPE_MANUAL:
        flash('Only manual blocklists support direct domain editing', 'warning')
        return redirect(url_for('ui_dashboard.settings'))

    raw_domain = request.form.get('domain')
    try:
        domain = normalize_domain(raw_domain)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('ui_dashboard.settings'))

    existing = BlocklistDomain.query.filter_by(source_id=source.id, domain=domain).first()
    if existing:
        flash(f'{domain} is already present in "{source.name}"', 'warning')
        return redirect(url_for('ui_dashboard.settings'))

    db.session.add(BlocklistDomain(source_id=source.id, domain=domain))
    source.content_revision = compute_source_revision(
        row.domain
        for row in BlocklistDomain.query.with_entities(BlocklistDomain.domain).filter_by(
            source_id=source.id
        )
    )
    source.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    
    from app import task_manager
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Added {domain} to "{source.name}"', 'success')
    return redirect(url_for('ui_dashboard.settings'))


@api_blocklists_bp.route('/blocklists/sources/<int:source_id>/domains/<int:domain_id>/delete', methods=['POST'])
def delete_blocklist_domain(source_id, domain_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source = BlocklistSource.query.get_or_404(source_id)
    domain = BlocklistDomain.query.filter_by(id=domain_id, source_id=source.id).first_or_404()
    domain_text = domain.domain
    db.session.delete(domain)
    db.session.flush()
    source.content_revision = compute_source_revision(
        row.domain
        for row in BlocklistDomain.query.with_entities(BlocklistDomain.domain).filter_by(
            source_id=source.id
        )
    )
    source.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    
    from app import task_manager
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Removed {domain_text} from "{source.name}"', 'success')
    return redirect(url_for('ui_dashboard.settings'))


@api_blocklists_bp.route('/managed-users/<int:user_id>/blocklists/update', methods=['POST'])
def update_user_blocklists(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    selected_ids = {
        int(raw_id)
        for raw_id in request.form.getlist('source_ids')
        if raw_id.strip().isdigit()
    }

    valid_sources = {
        source.id: source
        for source in BlocklistSource.query.filter(
            BlocklistSource.id.in_(selected_ids),
            BlocklistSource.is_enabled.is_(True),
        ).all()
    } if selected_ids else {}

    if selected_ids and len(valid_sources) != len(selected_ids):
        flash('One or more selected blocklists no longer exist', 'danger')
        return redirect(url_for('ui_dashboard.edit_user_profile', user_id=user.id))

    current_ids = {assignment.source_id for assignment in user.blocklist_assignments}
    for assignment in list(user.blocklist_assignments):
        if assignment.source_id not in selected_ids:
            db.session.delete(assignment)

    for source_id in sorted(selected_ids - current_ids):
        db.session.add(ManagedUserBlocklistAssignment(managed_user_id=user.id, source_id=source_id))

    db.session.commit()
    
    from app import task_manager
    task_manager.notify_domain_policy_hint(reason='blocklist_assignment_updated')
    flash(f'Updated blocklist assignments for {user.username}', 'success')
    return redirect(url_for('ui_dashboard.edit_user_profile', user_id=user.id))


@api_blocklists_bp.route('/api/user/<int:user_id>/blocklists/sync-status')
def get_blocklist_sync_status(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    status = _build_user_blocklist_sync_status(user)
    return jsonify({
        'success': True,
        'needs_sync': status['needs_sync'],
        'assigned_source_count': status['assigned_source_count'],
        'effective_domain_count': status['effective_domain_count'],
        'mapping_count': status['mapping_count'],
        'synced_mapping_count': status['synced_mapping_count'],
        'awaiting_uid_count': status['awaiting_uid_count'],
        'mappings': status['mappings'],
    })

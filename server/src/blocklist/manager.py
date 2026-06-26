import logging
from sqlalchemy import func
from src.models import db, BlocklistSource, BlocklistDomain, ManagedUserBlocklistAssignment
from src.common.helpers import _device_display_label
from src.blocklist.helper import build_source_state_map, summarize_mapping_blocklist_sync

_LOGGER = logging.getLogger(__name__)


def _serialize_blocklist_source(
    source,
    *,
    domain_count=0,
    assigned_user_count=0,
    preview_domains=None,
):
    payload = {
        'id': source.id,
        'name': source.name,
        'source_type': source.source_type,
        'source_url': source.source_url,
        'is_enabled': source.is_enabled,
        'is_marketplace': bool(getattr(source, 'is_marketplace', False)),
        'preset_id': getattr(source, 'preset_id', None),
        'domain_count': int(domain_count or 0),
        'assigned_user_count': int(assigned_user_count or 0),
        'last_sync_at': source.last_sync_at.strftime('%Y-%m-%d %H:%M') if source.last_sync_at else None,
        'last_sync_status': source.last_sync_status,
        'last_sync_error': source.last_sync_error,
    }
    if preview_domains is not None:
        payload['domains'] = preview_domains
    return payload


def _get_blocklist_sources(include_domains=False, enabled_only=False, preview_limit=25):
    domain_count_subquery = db.session.query(
        BlocklistDomain.source_id.label('source_id'),
        func.count(BlocklistDomain.id).label('domain_count'),
    ).group_by(BlocklistDomain.source_id).subquery()

    assignment_count_subquery = db.session.query(
        ManagedUserBlocklistAssignment.source_id.label('source_id'),
        func.count(ManagedUserBlocklistAssignment.id).label('assignment_count'),
    ).group_by(ManagedUserBlocklistAssignment.source_id).subquery()

    query = db.session.query(
        BlocklistSource,
        func.coalesce(domain_count_subquery.c.domain_count, 0).label('domain_count'),
        func.coalesce(assignment_count_subquery.c.assignment_count, 0).label('assignment_count'),
    ).outerjoin(
        domain_count_subquery,
        domain_count_subquery.c.source_id == BlocklistSource.id,
    ).outerjoin(
        assignment_count_subquery,
        assignment_count_subquery.c.source_id == BlocklistSource.id,
    )
    if enabled_only:
        query = query.filter(BlocklistSource.is_enabled.is_(True))

    source_rows = query.order_by(BlocklistSource.name.asc(), BlocklistSource.id.asc()).all()

    return [
        _serialize_blocklist_source(
            source,
            domain_count=domain_count,
            assigned_user_count=assignment_count,
            preview_domains=(
                [
                    {'id': d.id, 'domain': d.domain}
                    for d in BlocklistDomain.query.filter_by(source_id=source.id)
                    .order_by(BlocklistDomain.domain.asc())
                    .limit(preview_limit)
                    .all()
                ]
                if include_domains and source.source_type == BlocklistSource.TYPE_MANUAL
                else None
            ),
        )
        for source, domain_count, assignment_count in source_rows
    ]


def _get_user_assigned_blocklist_source_ids(user):
    return {
        assignment.source_id
        for assignment in user.blocklist_assignments
        if assignment.source and assignment.source.is_enabled
    }


def _clean_sync_error(raw_error):
    if not raw_error:
        return None
    err = str(raw_error).lower()
    if 'hash mismatch' in err or 'revision mismatch' in err:
        return 'The policy version on this device does not match the server. Ensure the device is online to update.'
    if 'sqlite' in err or 'database' in err or 'db error' in err:
        return 'A local database error occurred on the device. Restarting the Guardian service usually resolves this.'
    if 'websocket' in err or 'connection closed' in err or 'timeout' in err:
        return 'Unable to connect to the device. Please make sure it is powered on and connected to the internet.'
    return raw_error


def _build_user_blocklist_sync_status(user):
    assigned_source_ids = _get_user_assigned_blocklist_source_ids(user)
    active_sources = []
    if assigned_source_ids:
        active_sources = BlocklistSource.query.filter(
            BlocklistSource.id.in_(assigned_source_ids)
        ).all()
    source_state_map = build_source_state_map(active_sources)

    mappings = []
    for mapping in sorted(
        user.device_mappings,
        key=lambda item: (
            (_device_display_label(item.system_id) or '').lower(),
            (item.linux_username or '').lower(),
            item.id,
        ),
    ):
        summary = summarize_mapping_blocklist_sync(mapping, source_state_map, assigned_source_ids)
        mappings.append({
            'mapping_id': mapping.id,
            'system_id': mapping.system_id,
            'device_label': _device_display_label(mapping.system_id),
            'linux_username': mapping.linux_username,
            'linux_uid': mapping.linux_uid,
            'status': summary['status'],
            'needs_sync': summary['needs_sync'],
            'effective_domain_count': summary['effective_domain_count'],
            'last_synced': mapping.blocklist_last_synced.strftime('%Y-%m-%d %H:%M') if mapping.blocklist_last_synced else None,
            'last_error': _clean_sync_error(mapping.blocklist_last_error),
        })

    needs_sync = any(mapping['needs_sync'] for mapping in mappings)
    synced_count = sum(1 for mapping in mappings if mapping['status'] == 'synced')
    awaiting_uid_count = sum(1 for mapping in mappings if mapping['status'] == 'awaiting_uid')

    return {
        'assigned_source_ids': sorted(assigned_source_ids),
        'assigned_source_count': len(assigned_source_ids),
        'effective_domain_count': sum(
            int(state.get('domain_count') or 0)
            for state in source_state_map.values()
        ),
        'mapping_count': len(mappings),
        'synced_mapping_count': synced_count,
        'awaiting_uid_count': awaiting_uid_count,
        'needs_sync': needs_sync,
        'mappings': mappings,
    }

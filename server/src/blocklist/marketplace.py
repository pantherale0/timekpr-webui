import os
import json
import logging
import threading
from src.models import db, BlocklistSource, BlocklistDomain, ManagedUserBlocklistAssignment
from src.blocklist.helper import compute_source_revision

_LOGGER = logging.getLogger(__name__)


def load_marketplace_presets():
    """Load marketplace presets from the static JSON catalog."""
    presets_path = os.path.join(os.path.dirname(__file__), 'marketplace_presets.json')
    try:
        with open(presets_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        _LOGGER.error("Failed to load marketplace presets JSON: %s", exc)
        return []


def get_marketplace_presets_dict():
    """Get marketplace presets as a dictionary keyed by preset ID."""
    presets = load_marketplace_presets()
    return {preset['id']: preset for preset in presets}


def trigger_background_sync(source_id):
    """Spawn a background thread to refresh the external blocklist source (runs synchronously in testing)."""
    from app import app, task_manager
    if app.config.get('TESTING'):
        try:
            task_manager.refresh_external_blocklist_source(source_id, force=True)
        except Exception as exc:
            _LOGGER.error("Error in marketplace sync during test: %s", exc)
        return

    def run():
        with app.app_context():
            try:
                _LOGGER.info("Starting background sync for marketplace source ID %d", source_id)
                task_manager.refresh_external_blocklist_source(source_id, force=True)
            except Exception as exc:
                _LOGGER.error("Error in async marketplace sync for source %d: %s", source_id, exc)
    
    threading.Thread(target=run, daemon=True).start()


def sync_marketplace_subscriptions(user, selected_preset_ids):
    """
    Synchronize child profile marketplace blocklist subscriptions.
    Creates missing marketplace sources on-the-fly and handles clean-up of orphans.
    """
    presets_dict = get_marketplace_presets_dict()
    
    # 1. Fetch current marketplace sources from DB
    marketplace_sources = BlocklistSource.query.filter_by(is_marketplace=True).all()
    preset_to_source = {source.preset_id: source for source in marketplace_sources if source.preset_id}
    
    assigned_source_ids = {a.source_id for a in user.blocklist_assignments}
    
    # Keep track of sources that are actively subscribed/required for this user
    desired_source_ids = set()
    
    # 2. Add or find required marketplace sources
    for preset_id in selected_preset_ids:
        preset = presets_dict.get(preset_id)
        if not preset:
            continue
            
        source = preset_to_source.get(preset_id)
        if not source:
            # Create a new BlocklistSource for the preset
            source = BlocklistSource(
                name=preset['name'],
                source_type=preset['source_type'],
                source_url=preset.get('source_url'),
                is_marketplace=True,
                preset_id=preset_id,
                is_enabled=True,
            )
            db.session.add(source)
            db.session.flush() # Flush to populate source.id
            
            # Populate initial domains for manual sources
            if preset['source_type'] == BlocklistSource.TYPE_MANUAL:
                domains = preset.get('domains') or []
                for domain in domains:
                    db.session.add(BlocklistDomain(source_id=source.id, domain=domain))
                source.content_revision = compute_source_revision(domains)
            
            db.session.commit()
            
            # Update mappings
            preset_to_source[preset_id] = source
            
            # If external_url, kick off the background sync
            if preset['source_type'] == BlocklistSource.TYPE_EXTERNAL_URL:
                trigger_background_sync(source.id)
                
        desired_source_ids.add(source.id)
        
        # Assign to user if not already assigned
        if source.id not in assigned_source_ids:
            assignment = ManagedUserBlocklistAssignment(
                managed_user_id=user.id,
                source_id=source.id
            )
            db.session.add(assignment)
            
    # 3. Unsubscribe from marketplace sources no longer selected
    marketplace_source_ids = {s.id for s in marketplace_sources}
    for assignment in list(user.blocklist_assignments):
        if assignment.source_id in marketplace_source_ids and assignment.source_id not in desired_source_ids:
            db.session.delete(assignment)
            
    db.session.commit()
    
    # 4. Cleanup orphaned marketplace sources (no user assignments remaining)
    all_marketplace_sources = BlocklistSource.query.filter_by(is_marketplace=True).all()
    for source in all_marketplace_sources:
        active_assignments_count = ManagedUserBlocklistAssignment.query.filter_by(source_id=source.id).count()
        if active_assignments_count == 0:
            _LOGGER.info("Cleaning up orphaned marketplace source: %s", source.name)
            # Delete associated domains first
            BlocklistDomain.query.filter_by(source_id=source.id).delete(synchronize_session=False)
            db.session.delete(source)
            
    db.session.commit()
    
    # 5. Notify agents to sync policy
    try:
        from app import task_manager
        task_manager.notify_domain_policy_hint(reason='blocklist_assignment_updated')
    except Exception as exc:
        _LOGGER.error("Failed to notify policy sync hint: %s", exc)

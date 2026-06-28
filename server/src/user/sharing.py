from datetime import datetime, timezone
from src.models import db, ManagedUserShare, ManagedUserShareInvite

def process_pending_invite_redemption(parent_account_id, token_code):
    """
    Process the redemption of a child profile sharing invitation.
    Creates a ManagedUserShare entry and marks the invite as used.
    """
    invite = ManagedUserShareInvite.query.filter_by(invite_code=token_code).first()
    if not invite:
        return False, "Invitation code not found."

    if invite.expires_at:
        expires_at = invite.expires_at.replace(tzinfo=timezone.utc) if invite.expires_at.tzinfo is None else invite.expires_at
        if expires_at < datetime.now(timezone.utc):
            return False, "Invitation has expired."

    if invite.used_count >= invite.max_uses:
        return False, "Invitation has already been fully redeemed."

    # Verify if the user already has a share mapping for this child profile
    existing_share = ManagedUserShare.query.filter_by(
        parent_account_id=parent_account_id,
        managed_user_id=invite.managed_user_id
    ).first()

    if existing_share:
        # Update permissions scopes
        existing_share.permissions_json = invite.permissions_json
        existing_share.shared_at = datetime.now(timezone.utc)
    else:
        # Create a new share record
        new_share = ManagedUserShare(
            parent_account_id=parent_account_id,
            managed_user_id=invite.managed_user_id,
            permissions_json=invite.permissions_json,
        )
        db.session.add(new_share)

    # Increment used count
    invite.used_count += 1
    db.session.commit()
    return True, "Invitation redeemed successfully."

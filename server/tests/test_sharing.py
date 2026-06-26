from datetime import datetime, timedelta, timezone
import pytest
from werkzeug.exceptions import Forbidden

from src.models import (
    db,
    Household,
    ParentAccount,
    HouseholdParentMembership,
    HouseholdInvite,
    ManagedUser,
    AgentDevice,
    ManagedUserShare,
    ManagedUserShareInvite,
)
from src.common.helpers import (
    parent_has_access_to_child,
    parent_has_access_to_device,
    check_parent_child_access,
    check_parent_device_access,
)
from src.common.tasks import BackgroundTaskManager


def test_household_isolation(app, db_session):
    """Test that parents in different households are isolated.

    Parent A in Household A can access Child A and Device A,
    but NOT Child B or Device B from Household B.
    """
    # Create Household A
    hh_a = Household(name="Household A", enrollment_token="token-a")
    db_session.add(hh_a)
    db_session.flush()

    parent_a = ParentAccount(email="parent_a@test.com", name="Parent A")
    db_session.add(parent_a)
    db_session.flush()

    membership_a = HouseholdParentMembership(
        household_id=hh_a.id,
        parent_account_id=parent_a.id,
        permissions_json={"is_owner": True}
    )
    db_session.add(membership_a)

    child_a = ManagedUser(username="child_a", household_id=hh_a.id, system_ip="Unassigned", is_valid=True)
    db_session.add(child_a)

    device_a = AgentDevice(system_id="device-a", status="approved", secure_token="token", household_id=hh_a.id)
    db_session.add(device_a)

    # Create Household B
    hh_b = Household(name="Household B", enrollment_token="token-b")
    db_session.add(hh_b)
    db_session.flush()

    parent_b = ParentAccount(email="parent_b@test.com", name="Parent B")
    db_session.add(parent_b)
    db_session.flush()

    membership_b = HouseholdParentMembership(
        household_id=hh_b.id,
        parent_account_id=parent_b.id,
        permissions_json={"is_owner": True}
    )
    db_session.add(membership_b)

    child_b = ManagedUser(username="child_b", household_id=hh_b.id, system_ip="Unassigned", is_valid=True)
    db_session.add(child_b)

    device_b = AgentDevice(system_id="device-b", status="approved", secure_token="token", household_id=hh_b.id)
    db_session.add(device_b)

    db_session.commit()

    # 1. Assert direct python functions parent_has_access_to_child & parent_has_access_to_device
    assert parent_has_access_to_child(parent_a.id, child_a.id) is True
    assert parent_has_access_to_child(parent_a.id, child_b.id) is False
    assert parent_has_access_to_child(parent_b.id, child_b.id) is True
    assert parent_has_access_to_child(parent_b.id, child_a.id) is False

    assert parent_has_access_to_device(parent_a.id, device_a.system_id) is True
    assert parent_has_access_to_device(parent_a.id, device_b.system_id) is False
    assert parent_has_access_to_device(parent_b.id, device_b.system_id) is True
    assert parent_has_access_to_device(parent_b.id, device_a.system_id) is False

    # 2. Assert via request/session context check helpers
    with app.test_request_context():
        from flask import session
        
        # Parent A context
        session['logged_in'] = True
        session['parent_account_id'] = parent_a.id

        # Parent A can access child_a and device_a
        check_parent_child_access(child_a.id)
        check_parent_device_access(device_a.system_id)

        # Parent A gets 403 trying to access child_b or device_b
        with pytest.raises(Forbidden):
            check_parent_child_access(child_b.id)
        with pytest.raises(Forbidden):
            check_parent_device_access(device_b.system_id)

        # Parent B context
        session['parent_account_id'] = parent_b.id

        # Parent B can access child_b and device_b
        check_parent_child_access(child_b.id)
        check_parent_device_access(device_b.system_id)

        # Parent B gets 403 trying to access child_a or device_a
        with pytest.raises(Forbidden):
            check_parent_child_access(child_a.id)
        with pytest.raises(Forbidden):
            check_parent_device_access(device_a.system_id)


def test_individual_child_share_without_required_perm(app, db_session):
    """Test individual child sharing without required permission parameter.

    A parent from Household B can access a child in Household A if shared,
    with default permission check passing.
    """
    hh_a = Household(name="Household A", enrollment_token="token-a")
    db_session.add(hh_a)
    db_session.flush()

    parent_a = ParentAccount(email="parent_a@test.com")
    parent_b = ParentAccount(email="parent_b@test.com")
    db_session.add_all([parent_a, parent_b])
    db_session.flush()

    membership_a = HouseholdParentMembership(
        household_id=hh_a.id,
        parent_account_id=parent_a.id,
        permissions_json={"is_owner": True}
    )
    db_session.add(membership_a)

    child_a = ManagedUser(username="child_a", household_id=hh_a.id, system_ip="Unassigned", is_valid=True)
    db_session.add(child_a)
    db_session.commit()

    # Initially, Parent B has no access to Child A
    assert parent_has_access_to_child(parent_b.id, child_a.id) is False

    # Share Child A with Parent B (empty permissions_json)
    share = ManagedUserShare(
        parent_account_id=parent_b.id,
        managed_user_id=child_a.id,
        permissions_json={}
    )
    db_session.add(share)
    db_session.commit()

    # Parent B now has basic access to Child A
    assert parent_has_access_to_child(parent_b.id, child_a.id) is True

    # Test via request/session helper
    with app.test_request_context():
        from flask import session
        session['logged_in'] = True
        session['parent_account_id'] = parent_b.id

        # No 403 raised since basic access exists
        check_parent_child_access(child_a.id)


def test_individual_child_share_with_required_perms(app, db_session):
    """Test that sharing respects granular permission flags in permissions_json."""
    hh_a = Household(name="Household A", enrollment_token="token-a")
    db_session.add(hh_a)
    db_session.flush()

    parent_a = ParentAccount(email="parent_a@test.com")
    parent_b = ParentAccount(email="parent_b@test.com")
    db_session.add_all([parent_a, parent_b])
    db_session.flush()

    membership_a = HouseholdParentMembership(
        household_id=hh_a.id,
        parent_account_id=parent_a.id,
        permissions_json={"is_owner": True}
    )
    db_session.add(membership_a)

    child_a = ManagedUser(username="child_a", household_id=hh_a.id, system_ip="Unassigned", is_valid=True)
    db_session.add(child_a)
    db_session.commit()

    # Share Child A with Parent B with specific view-only capability
    share = ManagedUserShare(
        parent_account_id=parent_b.id,
        managed_user_id=child_a.id,
        permissions_json={"can_view_usage": True, "can_modify_limits": False}
    )
    db_session.add(share)
    db_session.commit()

    # 1. Direct function checks
    assert parent_has_access_to_child(parent_b.id, child_a.id, required_perm="can_view_usage") is True
    assert parent_has_access_to_child(parent_b.id, child_a.id, required_perm="can_modify_limits") is False
    assert parent_has_access_to_child(parent_b.id, child_a.id, required_perm="nonexistent") is False

    # 2. Context checks
    with app.test_request_context():
        from flask import session
        session['logged_in'] = True
        session['parent_account_id'] = parent_b.id

        # Access check with views-only allowed
        check_parent_child_access(child_a.id, required_perm="can_view_usage")

        # Modifying limits should abort with 403
        with pytest.raises(Forbidden):
            check_parent_child_access(child_a.id, required_perm="can_modify_limits")

    # Update sharing capability to allow modifying limits
    share.permissions_json = {"can_view_usage": True, "can_modify_limits": True}
    db_session.commit()

    assert parent_has_access_to_child(parent_b.id, child_a.id, required_perm="can_modify_limits") is True
    with app.test_request_context():
        from flask import session
        session['logged_in'] = True
        session['parent_account_id'] = parent_b.id
        
        # Should now pass without raising 403
        check_parent_child_access(child_a.id, required_perm="can_modify_limits")


def test_household_membership_permissions(app, db_session):
    """Test that household memberships verify roles like owner vs restricted parent."""
    hh_a = Household(name="Household A", enrollment_token="token-a")
    db_session.add(hh_a)
    db_session.flush()

    # Owner Parent A
    parent_a = ParentAccount(email="owner@test.com")
    # Restricted Parent B
    parent_b = ParentAccount(email="restricted@test.com")
    db_session.add_all([parent_a, parent_b])
    db_session.flush()

    membership_a = HouseholdParentMembership(
        household_id=hh_a.id,
        parent_account_id=parent_a.id,
        permissions_json={"is_owner": True}
    )
    # Restricted membership
    membership_b = HouseholdParentMembership(
        household_id=hh_a.id,
        parent_account_id=parent_b.id,
        permissions_json={"can_view_usage": True, "can_modify_limits": False}
    )
    db_session.add_all([membership_a, membership_b])

    child_a = ManagedUser(username="child_a", household_id=hh_a.id, system_ip="Unassigned", is_valid=True)
    db_session.add(child_a)
    db_session.commit()

    # Owner has access regardless of required perm because is_owner = True
    assert parent_has_access_to_child(parent_a.id, child_a.id, required_perm="can_modify_limits") is True
    assert parent_has_access_to_child(parent_a.id, child_a.id, required_perm="any_nonexistent_perm") is True

    # Restricted member requires the specific permission flag
    assert parent_has_access_to_child(parent_b.id, child_a.id, required_perm="can_view_usage") is True
    assert parent_has_access_to_child(parent_b.id, child_a.id, required_perm="can_modify_limits") is False
    assert parent_has_access_to_child(parent_b.id, child_a.id, required_perm=None) is True # Structural membership access


def test_invite_token_pruning(app, db_session):
    """Test that expired household and child share invites are pruned correctly."""
    hh_a = Household(name="Household A", enrollment_token="token-a")
    db_session.add(hh_a)
    db_session.flush()

    user_a = ManagedUser(username="child_a", household_id=hh_a.id, system_ip="Unassigned", is_valid=True)
    db_session.add(user_a)
    db_session.flush()

    parent_a = ParentAccount(email="parent_a@test.com")
    db_session.add(parent_a)
    db_session.flush()

    now = datetime.now(timezone.utc)

    # Active (not expired) invites
    active_hh_invite = HouseholdInvite(
        household_id=hh_a.id,
        invite_code="active-hh",
        created_by_id=parent_a.id,
        permissions_json={},
        expires_at=now + timedelta(hours=1),
        max_uses=1
    )
    active_share_invite = ManagedUserShareInvite(
        managed_user_id=user_a.id,
        invite_code="active-share",
        permissions_json={},
        created_by_id=parent_a.id,
        expires_at=now + timedelta(hours=1),
        max_uses=1
    )

    # Expired invites
    expired_hh_invite = HouseholdInvite(
        household_id=hh_a.id,
        invite_code="expired-hh",
        created_by_id=parent_a.id,
        permissions_json={},
        expires_at=now - timedelta(hours=1),
        max_uses=1
    )
    expired_share_invite = ManagedUserShareInvite(
        managed_user_id=user_a.id,
        invite_code="expired-share",
        permissions_json={},
        created_by_id=parent_a.id,
        expires_at=now - timedelta(hours=1),
        max_uses=1
    )

    db_session.add_all([
        active_hh_invite, active_share_invite,
        expired_hh_invite, expired_share_invite
    ])
    db_session.commit()

    # Before pruning, all 4 exist
    assert HouseholdInvite.query.count() == 2
    assert ManagedUserShareInvite.query.count() == 2

    # Instantiate task manager and prune
    manager = BackgroundTaskManager(app)
    manager._prune_expired_invites()

    # After pruning, only active ones should remain
    assert HouseholdInvite.query.count() == 1
    assert ManagedUserShareInvite.query.count() == 1

    remaining_hh = HouseholdInvite.query.first()
    assert remaining_hh.invite_code == "active-hh"

    remaining_share = ManagedUserShareInvite.query.first()
    assert remaining_share.invite_code == "active-share"

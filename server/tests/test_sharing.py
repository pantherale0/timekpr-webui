import pytest
from datetime import datetime, timedelta, timezone
from flask import session
from src.models import db, ParentAccount, ManagedUser, ManagedUserShare, ManagedUserShareInvite

def test_generate_and_redeem_sharing_invite(app, client):
    # Setup test DB entities
    parent = ParentAccount(email="primary@local")
    db.session.add(parent)
    db.session.flush()

    child = ManagedUser(username="test_child", system_id="test_system", system_ip="127.0.0.1", household_id=None)
    db.session.add(child)
    db.session.flush()

    # Share ownership/access so parent can manage policies (needed to generate invite)
    share = ManagedUserShare(
        parent_account_id=parent.id,
        managed_user_id=child.id,
        permissions_json={"can_manage_policies": True}
    )
    db.session.add(share)
    db.session.commit()

    # 1. Generate sharing invite link
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['parent_account_id'] = parent.id

    resp = client.post(f"/api/profiles/{child.id}/generate-invite", json={
        "can_view_screentime": True,
        "can_manage_screentime": True,
        "can_view_monitoring": False,
        "can_manage_policies": False
    })
    assert resp.status_code == 200
    data = resp.json
    assert data['success'] is True
    redeem_url = data['redeem_url']
    token = redeem_url.split('/')[-1]

    # Verify database state
    invite = ManagedUserShareInvite.query.filter_by(invite_code=token).first()
    assert invite is not None
    assert invite.managed_user_id == child.id
    assert invite.permissions_json['can_manage_screentime'] is True
    assert invite.permissions_json['can_view_monitoring'] is False

    # 2. Redeem invite link (unauthenticated)
    client.post("/logout")  # Ensure we are logged out
    with client.session_transaction() as sess:
        sess.clear()

    resp = client.get(redeem_url)
    assert resp.status_code == 302
    assert resp.location == "/" or "login" in resp.location

    # Check cached token in session
    with client.session_transaction() as sess:
        assert sess['pending_invite_token'] == token

    # 3. Simulate parent login and verify immediate redemption
    second_parent = ParentAccount(email="coparent@local")
    db.session.add(second_parent)
    db.session.commit()

    # Authenticate second_parent and call callback or manual login pathway
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['parent_account_id'] = second_parent.id

    # Hit the OIDC callback or trigger a request that processes login session
    # Let's hit the redeem URL again while logged in to trigger immediate redemption
    resp = client.get(redeem_url)
    assert resp.status_code == 302
    assert "dashboard" in resp.location

    # Verify share was created
    new_share = ManagedUserShare.query.filter_by(
        parent_account_id=second_parent.id,
        managed_user_id=child.id
    ).first()
    assert new_share is not None
    assert new_share.permissions_json['can_manage_screentime'] is True
    assert new_share.permissions_json['can_view_monitoring'] is False

"""Integration tests for site registration approval and online account audit endpoints."""

from datetime import datetime, timezone

import pytest

from src.database import (
    AgentDevice,
    ApprovalRequest,
    ManagedUser,
    ManagedUserDeviceMap,
    MappingApprovalSettings,
    PolicyApprovalGrant,
    UserOnlineAccount,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def reg_fixture(db_session):
    """Set up a device, user and mapping for registration tests."""
    device = AgentDevice(
        system_id="sys-reg-test",
        status="approved",
        secure_token="reg-secret-token",
    )
    user = ManagedUser(username="reg-child", system_ip="Unassigned", is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id="sys-reg-test",
        linux_username="reg-child",
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.flush()

    settings = MappingApprovalSettings(
        device_map_id=mapping.id,
        registration_approval_enabled=True,
    )
    db_session.add(settings)
    db_session.commit()

    return {"device": device, "user": user, "mapping": mapping, "settings": settings}


@pytest.fixture
def auth_client(client):
    """Return a test client with an active admin session."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client


# ---------------------------------------------------------------------------
# /api/registration/check
# ---------------------------------------------------------------------------

AGENT_HEADERS = {"Authorization": "Bearer reg-secret-token"}


class TestCheckRegistration:
    def test_missing_domain_returns_400(self, client, reg_fixture):
        resp = client.post(
            "/api/registration/check",
            json={"linux_username": "reg-child"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 400
        assert resp.get_json()["success"] is False

    def test_invalid_token_returns_401(self, client, reg_fixture):
        resp = client.post(
            "/api/registration/check",
            json={"linux_username": "reg-child", "domain": "example.com"},
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status_code == 401

    def test_feature_disabled_allows_registration(self, client, reg_fixture, db_session):
        reg_fixture["settings"].registration_approval_enabled = False
        db_session.commit()

        resp = client.post(
            "/api/registration/check",
            json={"linux_username": "reg-child", "domain": "example.com"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["allowed"] is True

    def test_no_grant_no_request_blocks(self, client, reg_fixture):
        resp = client.post(
            "/api/registration/check",
            json={"linux_username": "reg-child", "domain": "signup.example.com"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["allowed"] is False
        assert data["pending"] is False

    def test_active_grant_allows(self, client, reg_fixture, db_session):
        grant = PolicyApprovalGrant(
            device_map_id=reg_fixture["mapping"].id,
            grant_type="registration",
            target_kind="domain",
            target_value="signup.example.com",
            display_label="signup.example.com",
            status="active",
        )
        db_session.add(grant)
        db_session.commit()

        resp = client.post(
            "/api/registration/check",
            json={"linux_username": "reg-child", "domain": "signup.example.com"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.get_json()["allowed"] is True

    def test_pending_request_shows_pending(self, client, reg_fixture, db_session):
        pending = ApprovalRequest(
            device_map_id=reg_fixture["mapping"].id,
            request_type="registration",
            target_kind="domain",
            target_value="signup.example.com",
            display_label="signup.example.com",
            status="pending",
            requested_at=datetime.now(timezone.utc),
        )
        db_session.add(pending)
        db_session.commit()

        resp = client.post(
            "/api/registration/check",
            json={"linux_username": "reg-child", "domain": "signup.example.com"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["allowed"] is False
        assert data["pending"] is True


# ---------------------------------------------------------------------------
# /api/registration/request
# ---------------------------------------------------------------------------

class TestRequestRegistration:
    def test_creates_approval_request(self, client, reg_fixture, db_session):
        resp = client.post(
            "/api/registration/request",
            json={"linux_username": "reg-child", "domain": "newsite.example.com"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        row = ApprovalRequest.query.filter_by(
            device_map_id=reg_fixture["mapping"].id,
            request_type="registration",
            target_value="newsite.example.com",
        ).first()
        assert row is not None
        assert row.status == "pending"

    def test_duplicate_request_updates_timestamp(self, client, reg_fixture, db_session):
        # First request
        client.post(
            "/api/registration/request",
            json={"linux_username": "reg-child", "domain": "dup.example.com"},
            headers=AGENT_HEADERS,
        )
        original_count = ApprovalRequest.query.filter_by(
            request_type="registration", target_value="dup.example.com"
        ).count()

        # Second request — should NOT create a second row
        client.post(
            "/api/registration/request",
            json={"linux_username": "reg-child", "domain": "dup.example.com"},
            headers=AGENT_HEADERS,
        )
        new_count = ApprovalRequest.query.filter_by(
            request_type="registration", target_value="dup.example.com"
        ).count()

        assert original_count == new_count == 1

    def test_missing_domain_returns_400(self, client, reg_fixture):
        resp = client.post(
            "/api/registration/request",
            json={"linux_username": "reg-child"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/registration/log-login
# ---------------------------------------------------------------------------

class TestLogLogin:
    def test_creates_online_account_record(self, client, reg_fixture, db_session):
        resp = client.post(
            "/api/registration/log-login",
            json={
                "linux_username": "reg-child",
                "domain": "service.example.com",
                "username": "child@example.com",
            },
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        account = UserOnlineAccount.query.filter_by(
            managed_user_id=reg_fixture["user"].id,
            domain="service.example.com",
            username="child@example.com",
        ).first()
        assert account is not None

    def test_duplicate_login_updates_last_seen(self, client, reg_fixture, db_session):
        payload = {
            "linux_username": "reg-child",
            "domain": "service.example.com",
            "username": "child@example.com",
        }
        client.post("/api/registration/log-login", json=payload, headers=AGENT_HEADERS)
        client.post("/api/registration/log-login", json=payload, headers=AGENT_HEADERS)

        count = UserOnlineAccount.query.filter_by(
            managed_user_id=reg_fixture["user"].id,
            domain="service.example.com",
            username="child@example.com",
        ).count()
        assert count == 1

    def test_missing_username_returns_400(self, client, reg_fixture):
        resp = client.post(
            "/api/registration/log-login",
            json={"linux_username": "reg-child", "domain": "service.example.com"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 400

    def test_missing_domain_returns_400(self, client, reg_fixture):
        resp = client.post(
            "/api/registration/log-login",
            json={"linux_username": "reg-child", "username": "child@example.com"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/user/<id>/online-accounts
# ---------------------------------------------------------------------------

class TestOnlineAccountsAPI:
    def test_requires_authentication(self, client, reg_fixture):
        resp = client.get(f"/api/user/{reg_fixture['user'].id}/online-accounts")
        assert resp.status_code == 401

    def test_returns_empty_list_when_no_accounts(self, auth_client, reg_fixture):
        resp = auth_client.get(f"/api/user/{reg_fixture['user'].id}/online-accounts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["accounts"] == []

    def test_returns_logged_accounts(self, auth_client, client, reg_fixture, db_session):
        # Create an account record via the log-login endpoint first
        client.post(
            "/api/registration/log-login",
            json={
                "linux_username": "reg-child",
                "domain": "accounts.example.com",
                "username": "the-child",
            },
            headers=AGENT_HEADERS,
        )

        resp = auth_client.get(f"/api/user/{reg_fixture['user'].id}/online-accounts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["accounts"]) == 1
        assert data["accounts"][0]["domain"] == "accounts.example.com"
        assert data["accounts"][0]["username"] == "the-child"

"""Tests for SPA shell and fragment routes."""

import pytest

from src.models import Settings


@pytest.fixture
def auth_client(client):
    Settings.set_admin_password('admin')
    client.post('/', data={'username': 'admin', 'password': 'admin'})
    return client


def test_spa_shell_requires_auth(client):
    response = client.get('/dashboard')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/')


def test_spa_shell_renders_for_authenticated_user(auth_client):
    response = auth_client.get('/dashboard')
    assert response.status_code == 200
    assert b'id="spa-main"' in response.data
    assert b'spa-router.js' in response.data
    assert b'Family Home' in response.data
    assert b'manifest.webmanifest' in response.data
    assert b'id="createProfileModal"' in response.data
    assert b'guardian-wizard.js' in response.data

    html = response.data.decode('utf-8')
    onboarding_marker = html.find('function openOnboardingWizard')
    script_close_after_onboarding = html.find('</script>', onboarding_marker)
    modal_pos = html.find('id="createProfileModal"')
    assert script_close_after_onboarding != -1
    assert modal_pos > script_close_after_onboarding


def test_spa_fragment_requires_auth(client):
    response = client.get(
        '/ui/fragment/dashboard',
        headers={'X-Guardian-SPA': 'fragment'},
    )
    assert response.status_code == 401


def test_spa_fragment_returns_html_partial(auth_client):
    response = auth_client.get(
        '/ui/fragment/dashboard',
        headers={'X-Guardian-SPA': 'fragment'},
    )
    assert response.status_code == 200
    assert b'<!-- spa-title:' in response.data
    assert b'Family Home' in response.data
    assert b'spa-router.js' not in response.data


def test_spa_fragment_unknown_route(auth_client):
    response = auth_client.get(
        '/ui/fragment/not-a-real-page',
        headers={'X-Guardian-SPA': 'fragment'},
    )
    assert response.status_code == 404


def test_spa_fragment_settings(auth_client):
    response = auth_client.get(
        '/ui/fragment/settings',
        headers={'X-Guardian-SPA': 'fragment'},
    )
    assert response.status_code == 200
    assert b'Household Settings' in response.data


def test_spa_fragment_admin_settings(auth_client):
    response = auth_client.get(
        '/ui/fragment/admin/settings',
        headers={'X-Guardian-SPA': 'fragment'},
    )
    assert response.status_code == 200
    assert b'System Settings' in response.data


def test_spa_fragment_user_profile(auth_client, db_session):
    from src.models import ManagedUser

    user = ManagedUser(username='spa-test-child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()

    response = auth_client.get(
        f'/ui/fragment/admin/users/{user.id}',
        headers={'X-Guardian-SPA': 'fragment'},
    )
    assert response.status_code == 200
    assert b'admin-user-edit-tabs' in response.data
    assert b'admin-user-edit-sub-rail' in response.data
    assert b'id="browsing-tab"' in response.data
    assert b'id="computer-tab"' in response.data
    assert b'admin-user-edit.js' in response.data
    assert b'guardian-autosave.js' in auth_client.get('/dashboard').data


def test_service_worker_route(auth_client):
    response = auth_client.get('/sw.js')
    assert response.status_code == 200
    assert b'CACHE_NAME' in response.data


def test_manifest_served(auth_client):
    response = auth_client.get('/static/manifest.webmanifest')
    assert response.status_code == 200
    assert b'"display": "standalone"' in response.data


def test_spa_fragment_weekly_schedule_nav_regions(auth_client, db_session):
    from src.models import ManagedUser, UserWeeklySchedule

    user = ManagedUser(username='routine-nav-user', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(UserWeeklySchedule(user_id=user.id))
    db_session.commit()

    response = auth_client.get(
        f'/ui/fragment/weekly-schedule/{user.id}',
        headers={'X-Guardian-SPA': 'fragment'},
    )
    assert response.status_code == 200
    assert b'spa-region:top_nav_center' in response.data
    assert b'id="routine-sync-status"' in response.data
    assert b'save-routine-btn' in response.data
    assert b'routine-blueprint-root' in response.data


def test_admin_approvals_spa_shell(auth_client):
    response = auth_client.get('/admin/approvals')
    assert response.status_code == 200
    assert b'id="spa-main"' in response.data
    assert b'Family Dialogue' in response.data

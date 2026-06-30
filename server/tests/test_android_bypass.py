import pytest

from src.policy.android_bypass import (
    ANDROID_BYPASS_TOOL_PACKAGES,
    apply_android_bypass_app_blocks,
    bypass_packages_for_maturity,
)


@pytest.fixture
def child_user(db_session):
    from src.models import ManagedUser

    user = ManagedUser(username='bypass_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()
    return user


def test_bypass_packages_for_maturity():
    assert bypass_packages_for_maturity('low') == []
    assert bypass_packages_for_maturity('medium') == list(ANDROID_BYPASS_TOOL_PACKAGES)
    assert bypass_packages_for_maturity('high') == list(ANDROID_BYPASS_TOOL_PACKAGES)


def test_apply_android_bypass_app_blocks_creates_policy(app, db_session, child_user):
    with app.app_context():
        written = apply_android_bypass_app_blocks(child_user, 'high')
        db_session.commit()

    assert written == len(ANDROID_BYPASS_TOOL_PACKAGES)

    from src.models import AppPolicy, AppPolicyRule, ManagedUserAppPolicyAssignment

    policy = AppPolicy.query.filter_by(
        name=f'Anti-bypass tools ({child_user.username})',
        platform=AppPolicy.PLATFORM_ANDROID,
    ).first()
    assert policy is not None
    assert AppPolicyRule.query.filter_by(policy_id=policy.id).count() == len(ANDROID_BYPASS_TOOL_PACKAGES)
    assert ManagedUserAppPolicyAssignment.query.filter_by(
        managed_user_id=child_user.id,
        policy_id=policy.id,
    ).first() is not None


def test_apply_android_bypass_app_blocks_clears_on_low(app, db_session, child_user):
    with app.app_context():
        apply_android_bypass_app_blocks(child_user, 'high')
        db_session.commit()
        apply_android_bypass_app_blocks(child_user, 'low')
        db_session.commit()

    from src.models import AppPolicy

    policy = AppPolicy.query.filter_by(
        name=f'Anti-bypass tools ({child_user.username})',
        platform=AppPolicy.PLATFORM_ANDROID,
    ).first()
    assert policy is None

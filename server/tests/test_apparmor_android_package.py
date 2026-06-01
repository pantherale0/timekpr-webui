"""Tests for Android package match rules."""

import pytest

from src.apparmor_manager import _validate_apparmor_rule_target
from src.database import AppArmorRule


def test_validate_android_package_match_type():
    package = _validate_apparmor_rule_target(
        AppArmorRule.MATCH_TYPE_PACKAGE,
        'com.android.chrome',
        'android',
    )
    assert package == 'com.android.chrome'


def test_validate_android_prefixed_executable_path():
    package = _validate_apparmor_rule_target(
        AppArmorRule.MATCH_TYPE_EXECUTABLE,
        '/android/package/com.discord',
        'android',
    )
    assert package == 'com.discord'

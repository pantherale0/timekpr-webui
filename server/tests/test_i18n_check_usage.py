"""Tests for scripts/i18n/check_usage.py."""

from __future__ import annotations

import importlib.util
import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_I18N = Path(__file__).resolve().parents[2] / 'scripts' / 'i18n'
sys.path.insert(0, str(SCRIPTS_I18N))
lib = importlib.import_module('lib')
check_usage = importlib.util.module_from_spec(
    spec := importlib.util.spec_from_file_location('check_usage', SCRIPTS_I18N / 'check_usage.py')
)
assert spec.loader is not None
sys.modules['check_usage'] = check_usage
spec.loader.exec_module(check_usage)


def test_missing_key_detected_in_python(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    server = repo / 'server' / 'src'
    server.mkdir(parents=True)
    (server / 'views.py').write_text(
        "from src.i18n.catalog import flash_t\nflash_t('flash.auth.missing_example', 'danger')\n",
        encoding='utf-8',
    )

    i18n_root = repo / 'i18n' / 'en'
    i18n_root.mkdir(parents=True)
    (i18n_root / 'server.yaml').write_text(
        'meta:\n  locale: en\n  label: English\nflash:\n  auth:\n    login_success: ok\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(check_usage, 'REPO_ROOT', repo)
    monkeypatch.setattr(lib, 'I18N_ROOT', repo / 'i18n')
    monkeypatch.setattr(lib, 'REPO_ROOT', repo)

    report = check_usage.run_check()
    assert any(ref.catalog_key == 'flash.auth.missing_example' for ref in report.missing_keys)


def test_api_message_key_is_prefixed(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    server = repo / 'server' / 'src'
    server.mkdir(parents=True)
    (server / 'api.py').write_text(
        "from src.i18n.catalog import api_message\napi_message('not_authenticated')\n",
        encoding='utf-8',
    )

    i18n_root = repo / 'i18n' / 'en'
    i18n_root.mkdir(parents=True)
    (i18n_root / 'server.yaml').write_text(
        'meta:\n  locale: en\n  label: English\napi:\n  not_authenticated: Sign in required\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(check_usage, 'REPO_ROOT', repo)
    monkeypatch.setattr(lib, 'I18N_ROOT', repo / 'i18n')
    monkeypatch.setattr(lib, 'REPO_ROOT', repo)

    report = check_usage.run_check()
    assert report.missing_keys == []


def test_hardcoded_template_warning(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    templates = repo / 'server' / 'templates'
    templates.mkdir(parents=True)
    (templates / 'page.html').write_text(
        '<h1>Hardcoded Heading For Families</h1>\n',
        encoding='utf-8',
    )

    i18n_root = repo / 'i18n' / 'en'
    i18n_root.mkdir(parents=True)
    (i18n_root / 'server.yaml').write_text(
        'meta:\n  locale: en\n  label: English\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(check_usage, 'REPO_ROOT', repo)
    monkeypatch.setattr(lib, 'I18N_ROOT', repo / 'i18n')
    monkeypatch.setattr(lib, 'REPO_ROOT', repo)

    report = check_usage.run_check(changed_files={'server/templates/page.html'})
    assert any(item.text == 'Hardcoded Heading For Families' for item in report.hardcoded)


def test_dynamic_template_key_suffix_is_ignored(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    templates = repo / 'server' / 'templates'
    templates.mkdir(parents=True)
    (templates / 'page.html').write_text(
        "{{ t('pages.device_detail.hardware_status_' ~ status) }}\n",
        encoding='utf-8',
    )

    i18n_root = repo / 'i18n' / 'en'
    i18n_root.mkdir(parents=True)
    (i18n_root / 'server.yaml').write_text(
        'meta:\n  locale: en\n  label: English\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(check_usage, 'REPO_ROOT', repo)
    monkeypatch.setattr(lib, 'I18N_ROOT', repo / 'i18n')
    monkeypatch.setattr(lib, 'REPO_ROOT', repo)

    report = check_usage.run_check()
    assert report.missing_keys == []


def test_android_missing_string_key(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    kotlin = repo / 'android-agent' / 'app' / 'src' / 'main' / 'java' / 'com' / 'example'
    kotlin.mkdir(parents=True)
    (kotlin / 'MainActivity.kt').write_text(
        'val title = getString(R.string.missing_android_copy)\n',
        encoding='utf-8',
    )

    i18n_root = repo / 'i18n' / 'en'
    i18n_root.mkdir(parents=True)
    (i18n_root / 'agent.yaml').write_text(
        'meta:\n  locale: en\n  label: English\nstrings:\n  app_name: Guardian Agent\n',
        encoding='utf-8',
    )
    (i18n_root / 'server.yaml').write_text(
        'meta:\n  locale: en\n  label: English\n',
        encoding='utf-8',
    )
    (i18n_root / 'extension.yaml').write_text(
        'meta:\n  locale: en\n  label: English\nmessages: {}\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(check_usage, 'REPO_ROOT', repo)
    monkeypatch.setattr(lib, 'I18N_ROOT', repo / 'i18n')
    monkeypatch.setattr(lib, 'REPO_ROOT', repo)

    report = check_usage.run_check()
    assert any(ref.catalog_key == 'missing_android_copy' for ref in report.missing_keys)


def test_android_hardcoded_layout_warning(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    layout = repo / 'android-agent' / 'app' / 'src' / 'main' / 'res' / 'layout'
    layout.mkdir(parents=True)
    (layout / 'activity_example.xml').write_text(
        '<TextView android:text="Hardcoded Android Heading" />\n',
        encoding='utf-8',
    )

    i18n_root = repo / 'i18n' / 'en'
    i18n_root.mkdir(parents=True)
    (i18n_root / 'agent.yaml').write_text(
        'meta:\n  locale: en\n  label: English\nstrings:\n  app_name: Guardian Agent\n',
        encoding='utf-8',
    )
    (i18n_root / 'server.yaml').write_text(
        'meta:\n  locale: en\n  label: English\n',
        encoding='utf-8',
    )
    (i18n_root / 'extension.yaml').write_text(
        'meta:\n  locale: en\n  label: English\nmessages: {}\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(check_usage, 'REPO_ROOT', repo)
    monkeypatch.setattr(lib, 'I18N_ROOT', repo / 'i18n')
    monkeypatch.setattr(lib, 'REPO_ROOT', repo)

    report = check_usage.run_check(
        changed_files={'android-agent/app/src/main/res/layout/activity_example.xml'},
    )
    assert any(item.text == 'Hardcoded Android Heading' for item in report.hardcoded)


def test_diff_mode_ignores_unchanged_files(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    server = repo / 'server' / 'src'
    server.mkdir(parents=True)
    (server / 'changed.py').write_text(
        "from src.i18n.catalog import flash_t\nflash_t('flash.auth.missing_in_diff', 'danger')\n",
        encoding='utf-8',
    )
    (server / 'unchanged.py').write_text(
        "from src.i18n.catalog import flash_t\nflash_t('flash.auth.missing_elsewhere', 'danger')\n",
        encoding='utf-8',
    )

    i18n_root = repo / 'i18n' / 'en'
    i18n_root.mkdir(parents=True)
    (i18n_root / 'server.yaml').write_text(
        'meta:\n  locale: en\n  label: English\nflash:\n  auth:\n    login_success: ok\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(check_usage, 'REPO_ROOT', repo)
    monkeypatch.setattr(lib, 'I18N_ROOT', repo / 'i18n')
    monkeypatch.setattr(lib, 'REPO_ROOT', repo)

    full_report = check_usage.run_check()
    assert any(ref.catalog_key == 'flash.auth.missing_in_diff' for ref in full_report.missing_keys)
    assert any(ref.catalog_key == 'flash.auth.missing_elsewhere' for ref in full_report.missing_keys)

    diff_report = check_usage.run_check(changed_files={'server/src/changed.py'})
    assert diff_report.diff_mode is True
    missing = {ref.catalog_key for ref in diff_report.missing_keys}
    assert 'flash.auth.missing_in_diff' in missing
    assert 'flash.auth.missing_elsewhere' not in missing


def test_manage_cli_check_usage():
    manage_path = Path(__file__).resolve().parents[2] / 'scripts' / 'i18n' / 'manage.py'
    spec = importlib.util.spec_from_file_location('i18n_manage', manage_path)
    manage = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(manage)
    assert manage.main(['check-usage', '--no-warn-hardcoded']) == 0

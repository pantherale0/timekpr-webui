import logging
import os
import struct
import zipfile
import tempfile
import hashlib
from urllib.parse import urljoin

import requests
from flask import Blueprint, jsonify, request, session

from src.policy.android import (
    build_policy_summary,
    get_or_create_policy,
    upsert_policy,
)
from src.models import AgentDevice
from src.common.url_safety import validate_safe_outbound_url

_LOGGER = logging.getLogger(__name__)

api_android_device_policy_bp = Blueprint('api_android_device_policy', __name__)


def _require_auth():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    return None


def _get_device_or_404(system_id):
    device = AgentDevice.query.get(system_id)
    if device is None:
        return None, (jsonify({'success': False, 'message': 'Device not found'}), 404)
    return device, None


@api_android_device_policy_bp.route('/api/devices/<system_id>/android-device-policy', methods=['GET'])
def get_android_device_policy(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_device_or_404(system_id)
    if error_response is not None:
        return error_response

    try:
        policy = get_or_create_policy(device)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'policy': build_policy_summary(policy, device),
    })


@api_android_device_policy_bp.route('/api/devices/<system_id>/android-device-policy', methods=['PUT'])
def update_android_device_policy(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_device_or_404(system_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'success': False, 'message': 'Request body must be a JSON object'}), 400

    try:
        policy = upsert_policy(device, body)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    summary = build_policy_summary(policy, device)
    message = 'Device policy saved'
    if not policy.is_synced:
        message = f'Device policy saved; sync pending ({policy.last_sync_error or "agent offline"})'

    return jsonify({
        'success': True,
        'message': message,
        'policy': summary,
    })


@api_android_device_policy_bp.route('/api/mappings/<int:mapping_id>/android-device-policy', methods=['GET'])
def get_android_device_policy_legacy(mapping_id):
    from src.models import ManagedUserDeviceMap
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response
    mapping = ManagedUserDeviceMap.query.get(mapping_id)
    if mapping is None or not mapping.device:
        return jsonify({'success': False, 'message': 'Mapping or device not found'}), 404
    return get_android_device_policy(mapping.system_id)


@api_android_device_policy_bp.route('/api/mappings/<int:mapping_id>/android-device-policy', methods=['PUT'])
def update_android_device_policy_legacy(mapping_id):
    from src.models import ManagedUserDeviceMap
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response
    mapping = ManagedUserDeviceMap.query.get(mapping_id)
    if mapping is None or not mapping.device:
        return jsonify({'success': False, 'message': 'Mapping or device not found'}), 404
    return update_android_device_policy(mapping.system_id)


def parse_axml_package_name(data: bytes) -> str | None:
    try:
        if len(data) < 8:
            raise ValueError("AXML too short")
        magic, size = struct.unpack('<II', data[0:8])
        if magic != 0x00080003:
            raise ValueError("Not a valid binary AndroidManifest.xml")
            
        offset = 8
        if offset + 8 > len(data):
            raise ValueError("AXML missing string pool header")
        chunk_type, chunk_size = struct.unpack('<II', data[offset:offset+8])
        if chunk_type != 0x001C0001:
            raise ValueError("String pool chunk not found")
            
        if offset + 28 > len(data):
            raise ValueError("AXML missing string pool metadata")
        string_count, style_count, flags, string_offset, style_offset = struct.unpack(
            '<IIIII', data[offset+8:offset+28]
        )
        
        is_utf8 = (flags & 256) != 0
        
        offsets_start = offset + 28
        if offsets_start + string_count * 4 > len(data):
            raise ValueError("AXML offsets out of bounds")
        offsets = []
        for i in range(string_count):
            off = struct.unpack('<I', data[offsets_start + i*4:offsets_start + i*4 + 4])[0]
            offsets.append(off)
            
        strings_start = offset + string_offset
        strings = []
        for i in range(string_count):
            start = strings_start + offsets[i]
            if start >= len(data):
                strings.append("")
                continue
            if is_utf8:
                val = data[start]
                if val & 0x80:
                    start += 2
                else:
                    start += 1
                if start >= len(data):
                    strings.append("")
                    continue
                length = data[start]
                if length & 0x80:
                    start += 2
                else:
                    start += 1
                if start + length > len(data):
                    strings.append("")
                    continue
                string_data = data[start:start+length].decode('utf-8', errors='ignore')
                strings.append(string_data)
            else:
                if start + 2 > len(data):
                    strings.append("")
                    continue
                length = struct.unpack('<H', data[start:start+2])[0]
                if length & 0x8000:
                    start += 4
                else:
                    start += 2
                if start + length * 2 > len(data):
                    strings.append("")
                    continue
                string_data = data[start:start+length*2].decode('utf-16le', errors='ignore')
                strings.append(string_data)

        offset += chunk_size
        if offset < len(data):
            if offset + 8 <= len(data):
                chunk_type, chunk_size = struct.unpack('<II', data[offset:offset+8])
                if chunk_type == 0x00080180:
                    offset += chunk_size
                
        while offset < len(data):
            if offset + 8 > len(data):
                break
            c_type, c_size = struct.unpack('<II', data[offset:offset+8])
            if c_type == 0x00100102: # Start Tag
                if offset + 24 > len(data):
                    break
                name_idx = struct.unpack('<i', data[offset+20:offset+24])[0]
                tag_name = strings[name_idx] if 0 <= name_idx < len(strings) else ""
                if tag_name == "manifest":
                    if offset + 30 > len(data):
                        break
                    attr_count = struct.unpack('<H', data[offset+28:offset+30])[0]
                    for a in range(attr_count):
                        a_start = offset + 36 + a * 20
                        if a_start + 12 > len(data):
                            break
                        a_ns, a_name, a_raw_val = struct.unpack('<iii', data[a_start:a_start+12])
                        attr_name = strings[a_name] if 0 <= a_name < len(strings) else ""
                        if attr_name == "package":
                            return strings[a_raw_val] if 0 <= a_raw_val < len(strings) else None
            if c_size <= 0:
                break
            offset += c_size
    except (IndexError, struct.error, UnicodeDecodeError) as e:
        raise ValueError(f"AXML parsing failed: {str(e)}")
    return None


@api_android_device_policy_bp.route('/api/devices/<system_id>/validate-apk-url', methods=['POST'])
def validate_apk_url(system_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    device, error_response = _get_device_or_404(system_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or 'apk_url' not in body:
        return jsonify({'success': False, 'message': 'apk_url is required'}), 400

    apk_url = body['apk_url']
    if not isinstance(apk_url, str):
        return jsonify({'success': False, 'message': 'apk_url must be a string'}), 400

    try:
        validate_safe_outbound_url(apk_url)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    # Max size: 100MB
    MAX_APK_SIZE = 100 * 1024 * 1024
    
    # Follow redirects manually to validate each target URL against safety rules
    current_url = apk_url
    hops = 0
    max_hops = 5
    response = None

    try:
        while hops < max_hops:
            try:
                response = requests.get(current_url, stream=True, timeout=15, allow_redirects=False)
            except requests.RequestException as e:
                return jsonify({'success': False, 'message': f'Failed to request APK URL: {str(e)}'}), 400

            if response.status_code in (301, 302, 303, 307, 308):
                redirect_url = response.headers.get('Location')
                if not redirect_url:
                    break
                current_url = urljoin(current_url, redirect_url)
                try:
                    validate_safe_outbound_url(current_url)
                except ValueError as exc:
                    return jsonify({'success': False, 'message': f'Redirect blocked: {str(exc)}'}), 400
                hops += 1
            else:
                break
        else:
            return jsonify({'success': False, 'message': 'Too many redirects during validation'}), 400

        if response.status_code != 200:
            return jsonify({'success': False, 'message': f'Server returned HTTP status {response.status_code}'}), 400

        # Check content-length header
        cl_str = response.headers.get('Content-Length')
        if cl_str:
            try:
                cl = int(cl_str)
                if cl > MAX_APK_SIZE:
                    return jsonify({'success': False, 'message': 'APK exceeds the maximum limit of 100MB'}), 400
            except ValueError:
                pass

        sha256 = hashlib.sha256()
        total_downloaded = 0

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        total_downloaded += len(chunk)
                        if total_downloaded > MAX_APK_SIZE:
                            return jsonify({'success': False, 'message': 'APK exceeds the maximum limit of 100MB'}), 400
                        f.write(chunk)
                        sha256.update(chunk)

            # Extract the package name
            with zipfile.ZipFile(tmp_path) as z:
                try:
                    manifest_data = z.read("AndroidManifest.xml")
                except KeyError:
                    return jsonify({'success': False, 'message': 'Invalid APK: AndroidManifest.xml not found'}), 400

                package_name = parse_axml_package_name(manifest_data)
                if not package_name:
                    return jsonify({'success': False, 'message': 'Could not extract package name from AndroidManifest.xml'}), 400

            sha256_hash = sha256.hexdigest()
            return jsonify({
                'success': True,
                'package_name': package_name,
                'sha256_checksum': sha256_hash,
            })
        except zipfile.BadZipFile:
            return jsonify({'success': False, 'message': 'Invalid APK: not a valid ZIP file'}), 400
        except ValueError as exc:
            return jsonify({'success': False, 'message': f'Invalid AXML or APK: {str(exc)}'}), 400
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except Exception as exc:
        _LOGGER.exception("Failed to validate APK URL")
        return jsonify({'success': False, 'message': f'Validation failed: {str(exc)}'}), 400


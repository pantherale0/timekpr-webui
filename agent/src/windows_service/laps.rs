//! Local built-in Administrator password rotation and server escrow.

#[cfg(target_os = "windows")]
use crate::bios_management::password::{generate_supervisor_password, zeroize_string};
#[cfg(target_os = "windows")]
use chrono::Utc;
#[cfg(target_os = "windows")]
use serde::{Deserialize, Serialize};
#[cfg(target_os = "windows")]
use std::ffi::OsStr;
#[cfg(target_os = "windows")]
use std::fs;
#[cfg(target_os = "windows")]
use std::os::windows::ffi::OsStrExt;
#[cfg(target_os = "windows")]
use std::path::PathBuf;
#[cfg(target_os = "windows")]
use uuid::Uuid;
#[cfg(target_os = "windows")]
use windows_sys::Win32::Foundation::CloseHandle;
#[cfg(target_os = "windows")]
use windows_sys::Win32::NetworkManagement::NetManagement::{
    NetApiBufferFree, NetUserChangePassword, NetUserGetInfo, NetUserSetInfo,
};
#[cfg(target_os = "windows")]
use windows_sys::Win32::Security::{
    LogonUserW, LOGON32_LOGON_NETWORK, LOGON32_PROVIDER_DEFAULT,
};

#[cfg(target_os = "windows")]
const ADMIN_USERNAME: &str = "Administrator";
#[cfg(target_os = "windows")]
const LAPS_STATE_PATH: &str = r"C:\ProgramData\Guardian\laps_state.json";
#[cfg(target_os = "windows")]
const UF_ACCOUNTDISABLE: u32 = 0x0002;
#[cfg(target_os = "windows")]
const UF_PASSWD_NOTREQD: u32 = 0x0020;
#[cfg(target_os = "windows")]
const UF_NORMAL_ACCOUNT: u32 = 0x0200;

#[cfg(target_os = "windows")]
#[repr(C)]
struct USER_INFO_1 {
    usri1_name: *mut u16,
    usri1_password: *mut u16,
    usri1_password_age: u32,
    usri1_priv: u32,
    usri1_home_dir: *mut u16,
    usri1_comment: *mut u16,
    usri1_flags: u32,
    usri1_script_path: *mut u16,
}

#[cfg(target_os = "windows")]
#[repr(C)]
struct USER_INFO_1003 {
    usri1003_name: *mut u16,
    usri1003_password: *mut u16,
    usri1003_password_age: u32,
    usri1003_priv: u32,
    usri1003_home_dir: *mut u16,
    usri1003_comment: *mut u16,
    usri1003_flags: u32,
    usri1003_script_path: *mut u16,
}

#[cfg(target_os = "windows")]
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct LapsState {
    #[serde(default)]
    managed: bool,
    #[serde(default)]
    rotation_id: String,
    #[serde(default)]
    last_rotated_at: String,
}

#[derive(Debug, Clone)]
pub struct LapsEscrowPayload {
    pub rotation_id: String,
    pub occurred_at: String,
    pub password: String,
}

#[cfg(target_os = "windows")]
fn wide_string(value: &str) -> Vec<u16> {
    OsStr::new(value).encode_wide().chain(std::iter::once(0)).collect()
}

#[cfg(target_os = "windows")]
fn laps_state_path() -> PathBuf {
    PathBuf::from(LAPS_STATE_PATH)
}

#[cfg(target_os = "windows")]
fn load_laps_state() -> LapsState {
    let path = laps_state_path();
    if let Ok(raw) = fs::read_to_string(&path) {
        serde_json::from_str(&raw).unwrap_or_default()
    } else {
        LapsState::default()
    }
}

#[cfg(target_os = "windows")]
fn save_laps_state(state: &LapsState) -> Result<(), String> {
    let path = laps_state_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create {}: {}", parent.display(), e))?;
    }
    let json = serde_json::to_string_pretty(state)
        .map_err(|e| format!("failed to serialize LAPS state: {}", e))?;
    fs::write(&path, json).map_err(|e| format!("failed to write {}: {}", path.display(), e))
}

#[cfg(target_os = "windows")]
fn administrator_account_enabled() -> Result<bool, String> {
    unsafe {
        let username = wide_string(ADMIN_USERNAME);
        let mut buffer = std::ptr::null_mut();
        let status = NetUserGetInfo(std::ptr::null(), username.as_ptr(), 1, &mut buffer);
        if status != 0 || buffer.is_null() {
            return Err(format!("NetUserGetInfo failed for Administrator (status={status})"));
        }
        let info = &*(buffer as *const USER_INFO_1);
        let enabled = info.usri1_flags & UF_ACCOUNTDISABLE == 0;
        NetApiBufferFree(buffer as *const _);
        Ok(enabled)
    }
}

#[cfg(target_os = "windows")]
fn administrator_allows_blank_password() -> bool {
    unsafe {
        let username = wide_string(ADMIN_USERNAME);
        let mut buffer = std::ptr::null_mut();
        if NetUserGetInfo(std::ptr::null(), username.as_ptr(), 1, &mut buffer) != 0
            || buffer.is_null()
        {
            return false;
        }
        let info = &*(buffer as *const USER_INFO_1);
        let blank_allowed = info.usri1_flags & UF_PASSWD_NOTREQD != 0;
        NetApiBufferFree(buffer as *const _);
        blank_allowed
    }
}

#[cfg(target_os = "windows")]
fn administrator_accepts_blank_password() -> bool {
    unsafe {
        let username = wide_string(ADMIN_USERNAME);
        let password = wide_string("");
        let mut token = 0isize;
        let ok = LogonUserW(
            username.as_ptr(),
            std::ptr::null(),
            password.as_ptr(),
            LOGON32_LOGON_NETWORK,
            LOGON32_PROVIDER_DEFAULT,
            &mut token,
        );
        if ok != 0 && token != 0 {
            CloseHandle(token);
            return true;
        }
        false
    }
}

#[cfg(target_os = "windows")]
fn should_rotate_administrator() -> Result<bool, String> {
    if !administrator_account_enabled()? {
        return Ok(false);
    }
    let state = load_laps_state();
    if !state.managed {
        return Ok(true);
    }
    if administrator_allows_blank_password() || administrator_accepts_blank_password() {
        return Ok(true);
    }
    Ok(false)
}

#[cfg(target_os = "windows")]
fn set_administrator_password(new_password: &str) -> Result<(), String> {
    unsafe {
        let mut username = wide_string(ADMIN_USERNAME);
        let mut password = wide_string(new_password);
        let mut info = USER_INFO_1003 {
            usri1003_name: username.as_mut_ptr(),
            usri1003_password: password.as_mut_ptr(),
            usri1003_password_age: 0,
            usri1003_priv: 1,
            usri1003_home_dir: std::ptr::null_mut(),
            usri1003_comment: std::ptr::null_mut(),
            usri1003_flags: UF_NORMAL_ACCOUNT,
            usri1003_script_path: std::ptr::null_mut(),
        };
        let status = NetUserSetInfo(
            std::ptr::null(),
            username.as_ptr(),
            1003,
            &mut info as *mut _ as *mut u8,
            std::ptr::null_mut(),
        );
        if status == 0 {
            return Ok(());
        }

        let old_password = wide_string("");
        let change_status = NetUserChangePassword(
            std::ptr::null(),
            username.as_ptr(),
            old_password.as_ptr(),
            password.as_ptr(),
        );
        if change_status != 0 {
            return Err(format!(
                "failed to set Administrator password (set={status}, change={change_status})"
            ));
        }
        Ok(())
    }
}

pub fn audit_and_rotate() -> Result<Option<LapsEscrowPayload>, String> {
    #[cfg(target_os = "windows")]
    {
        if crate::windows_service::boot_mode::is_safe_mode_boot() {
            return Ok(None);
        }
        if !should_rotate_administrator()? {
            return Ok(None);
        }

        let mut password = generate_supervisor_password(20);
        set_administrator_password(&password)?;

        let rotation_id = Uuid::new_v4().to_string();
        let occurred_at = Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
        let payload = LapsEscrowPayload {
            rotation_id: rotation_id.clone(),
            occurred_at: occurred_at.clone(),
            password: password.clone(),
        };

        let state = LapsState {
            managed: true,
            rotation_id,
            last_rotated_at: occurred_at,
        };
        save_laps_state(&state)?;
        zeroize_string(&mut password);
        Ok(Some(payload))
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok(None)
    }
}

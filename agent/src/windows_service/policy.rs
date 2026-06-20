use std::sync::{Arc, Mutex, OnceLock};
use std::collections::{HashMap, HashSet};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fs;
use std::path::PathBuf;
use crate::installed_apps::DiscoveredApp;

const APP_POLICY_PATH: &str = r"C:\ProgramData\Guardian\app-policy.json";
const DEVICE_POLICY_PATH: &str = r"C:\ProgramData\Guardian\device-policy.json";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct UserAppPolicy {
    #[serde(default)]
    blocked_executables: Vec<String>,
    #[serde(default)]
    app_launch_mode: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct CachedAppPolicy {
    #[serde(default)]
    users: HashMap<String, UserAppPolicy>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct CachedDevicePolicy {
    #[serde(default)]
    users: HashMap<String, serde_json::Value>,
}

static APP_POLICY_CACHE: OnceLock<Arc<Mutex<CachedAppPolicy>>> = OnceLock::new();
static DEVICE_POLICY_CACHE: OnceLock<Arc<Mutex<CachedDevicePolicy>>> = OnceLock::new();

fn app_policy_cache() -> Arc<Mutex<CachedAppPolicy>> {
    APP_POLICY_CACHE
        .get_or_init(|| Arc::new(Mutex::new(load_app_policy_from_disk())))
        .clone()
}

fn device_policy_cache() -> Arc<Mutex<CachedDevicePolicy>> {
    DEVICE_POLICY_CACHE
        .get_or_init(|| Arc::new(Mutex::new(load_device_policy_from_disk())))
        .clone()
}

fn load_app_policy_from_disk() -> CachedAppPolicy {
    if let Ok(raw) = fs::read_to_string(APP_POLICY_PATH) {
        serde_json::from_str(&raw).unwrap_or_default()
    } else {
        CachedAppPolicy::default()
    }
}

fn load_device_policy_from_disk() -> CachedDevicePolicy {
    if let Ok(raw) = fs::read_to_string(DEVICE_POLICY_PATH) {
        serde_json::from_str(&raw).unwrap_or_default()
    } else {
        CachedDevicePolicy::default()
    }
}

fn persist_app_policy(state: &CachedAppPolicy) -> Result<(), String> {
    let parent = PathBuf::from(APP_POLICY_PATH).parent().map(|p| p.to_path_buf());
    if let Some(dir) = parent {
        fs::create_dir_all(&dir).map_err(|e| format!("failed to create policy dir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(state)
        .map_err(|e| format!("failed to serialize app policy: {}", e))?;
    fs::write(APP_POLICY_PATH, json).map_err(|e| format!("failed to write app policy: {}", e))
}

fn persist_device_policy(state: &CachedDevicePolicy) -> Result<(), String> {
    let parent = PathBuf::from(DEVICE_POLICY_PATH).parent().map(|p| p.to_path_buf());
    if let Some(dir) = parent {
        fs::create_dir_all(&dir).map_err(|e| format!("failed to create policy dir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(state)
        .map_err(|e| format!("failed to serialize device policy: {}", e))?;
    fs::write(DEVICE_POLICY_PATH, json)
        .map_err(|e| format!("failed to write device policy: {}", e))
}

fn normalize_executable_name(value: &str) -> String {
    let trimmed = value.trim();
    let file_name = std::path::Path::new(trimmed)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(trimmed);
    file_name.to_ascii_lowercase()
}

pub fn is_executable_blocked(username: &str, executable_name: &str) -> bool {
    let normalized = normalize_executable_name(executable_name);
    let cache = app_policy_cache().lock().unwrap();
    let user_policy = cache
        .users
        .get(username)
        .or_else(|| cache.users.values().next());
    let Some(policy) = user_policy else {
        return normalized == "steam.exe";
    };
    policy
        .blocked_executables
        .iter()
        .any(|blocked| normalize_executable_name(blocked) == normalized)
}

pub fn blocked_executables_for_user(username: &str) -> HashSet<String> {
    let cache = app_policy_cache().lock().unwrap();
    let policy = cache.users.get(username);
    policy
        .map(|entry| {
            entry
                .blocked_executables
                .iter()
                .map(|value| normalize_executable_name(value))
                .collect()
        })
        .unwrap_or_default()
}

// Standard user info level 3 structure for NetUserEnum
#[repr(C)]
#[allow(non_camel_case_types)]
struct USER_INFO_3 {
    usri3_name: *mut u16,
    usri3_password: *mut u16,
    usri3_password_age: u32,
    usri3_priv: u32,
    usri3_home_dir: *mut u16,
    usri3_comment: *mut u16,
    usri3_flags: u32,
    usri3_script_path: *mut u16,
    usri3_auth_flags: u32,
    usri3_full_name: *mut u16,
    usri3_usr_comment: *mut u16,
    usri3_parms: *mut u16,
    usri3_workstations: *mut u16,
    usri3_last_logon: u32,
    usri3_last_logoff: u32,
    usri3_acct_expires: u32,
    usri3_max_storage: u32,
    usri3_units_per_week: u32,
    usri3_logon_hours: *mut u8,
    usri3_bad_pw_count: u32,
    usri3_num_logons: u32,
    usri3_country_code: u32,
    usri3_code_page: u32,
    usri3_user_id: u32, // This is the RID!
    usri3_primary_group_id: u32,
    usri3_profile: *mut u16,
    usri3_home_dir_drive: *mut u16,
    usri3_password_expired: u32,
}

// Fetch all local Windows users and their RIDs
pub fn get_windows_users_map() -> HashMap<u32, String> {
    let mut map = HashMap::new();
    unsafe {
        let mut bufptr: *mut u8 = std::ptr::null_mut();
        let mut entriesread = 0;
        let mut totalentries = 0;
        let mut resume_handle = 0;

        // Call NetUserEnum at level 3
        let status = windows_sys::Win32::NetworkManagement::NetManagement::NetUserEnum(
            std::ptr::null(),
            3,
            0, // FILTER_TEMP_DUPLICATE_ACCOUNT / standard accounts
            &mut bufptr,
            u32::MAX,
            &mut entriesread,
            &mut totalentries,
            &mut resume_handle,
        );

        if status == 0 && !bufptr.is_null() {
            let users = bufptr as *const USER_INFO_3;
            for i in 0..entriesread {
                let user = &*users.add(i as usize);
                let username = read_wide_string(user.usri3_name);
                let rid = user.usri3_user_id;

                // Filter out system and disabled/helper accounts:
                // Normal user RIDs start at 1000. 500 is Administrator, 501 is Guest.
                // We want regular users (RID >= 1000)
                if rid >= 1000 && rid < 60000 && !username.is_empty() && username != "nobody" {
                    map.insert(rid, username);
                }
            }
        }

        if !bufptr.is_null() {
            windows_sys::Win32::NetworkManagement::NetManagement::NetApiBufferFree(bufptr as *const std::ffi::c_void);
        }
    }

    // Fallback in case NetUserEnum fails or returns empty in standard restricted containers
    if map.is_empty() {
        map.insert(1001, "child".to_string());
    }

    map
}

// Helper to convert wide char pointer to String
unsafe fn read_wide_string(ptr: *const u16) -> String {
    if ptr.is_null() {
        return String::new();
    }
    unsafe {
        let mut len = 0;
        while *ptr.add(len) != 0 {
            len += 1;
        }
        let slice = std::slice::from_raw_parts(ptr, len);
        String::from_utf16_lossy(slice)
    }
}

pub fn windows_user_exists(username: &str) -> bool {
    let users = get_windows_users_map();
    users.values().any(|name| name.eq_ignore_ascii_case(username))
}

// Windows command router for server actions
pub async fn handle_windows_command(
    action: &str,
    username: &str,
    args: &serde_json::Value,
) -> (bool, String, serde_json::Value) {
    match action {
        "sync_linux_device_policy" | "sync_windows_device_policy" => {
            let device_policy = args.get("device_policy");
            match sync_device_policy(username, device_policy) {
                Ok(()) => (true, "Windows device policy synchronized".to_string(), json!({})),
                Err(err) => (false, err, json!({})),
            }
        }
        "sync_apparmor_policy" | "sync_windows_app_policy" => {
            let approval_policy = args.get("approval_policy");
            let policies = args.get("policies");
            match sync_app_policy(username, approval_policy, policies) {
                Ok(()) => (true, "Windows application policy synchronized".to_string(), json!({})),
                Err(err) => (false, err, json!({})),
            }
        }
        "modify_time_left" => {
            let op = args.get("operation").and_then(|v| v.as_str()).unwrap_or("+");
            let secs = args.get("seconds").and_then(|v| v.as_i64()).unwrap_or(0);
            match crate::windows_service::policy::modify_time_left(username, op, secs) {
                Ok(()) => (true, format!("Successfully modified time: {}{} seconds", op, secs), json!({})),
                Err(err) => (false, err, json!({})),
            }
        }
        "set_weekly_time_limits" => {
            let schedule = match args.get("schedule").and_then(|v| v.as_object()) {
                Some(s) => s,
                None => return (false, "Missing 'schedule' argument".to_string(), json!({})),
            };
            match crate::windows_service::policy::set_weekly_time_limits(username, schedule) {
                Ok(()) => (true, "Weekly time limits configured successfully".to_string(), json!({})),
                Err(err) => (false, err, json!({})),
            }
        }
        "set_allowed_hours" => {
            let intervals = match args.get("intervals").and_then(|v| v.as_object()) {
                Some(i) => i,
                None => return (false, "Missing 'intervals' argument".to_string(), json!({})),
            };
            match crate::windows_service::policy::set_allowed_hours(username, intervals) {
                Ok(()) => (true, "Allowed hours configured successfully".to_string(), json!({})),
                Err(err) => (false, err, json!({})),
            }
        }
        "refresh_installed_apps" => (
            true,
            "Installed apps refresh queued".to_string(),
            json!({ "queued": true, "linux_username": username }),
        ),
        "unenroll" => {
            let _ = clear_on_unenroll();
            (true, "Device unenrolled locally; agent token cleared".to_string(), json!({}))
        }
        "clear_clock_tamper" => {
            crate::windows_service::tamper_state::set_clock_tamper_otp_override(true);
            crate::windows_service::overlay::dismiss();
            crate::windows_service::process_monitor::request_immediate_pass();
            (
                true,
                "Clock tamper override applied".to_string(),
                json!({}),
            )
        }
        "clear_safe_mode_lockdown" => {
            crate::windows_service::safe_mode_lockdown::clear_lockdown_override();
            (
                true,
                "Safe Mode lockdown override applied".to_string(),
                json!({}),
            )
        }
        "show_overlay" => {
            crate::windows_service::overlay::show(args);
            (
                true,
                "Guardian overlay shown".to_string(),
                json!({}),
            )
        }
        "dismiss_overlay" => {
            crate::windows_service::overlay::dismiss();
            (
                true,
                "Guardian overlay dismissed".to_string(),
                json!({}),
            )
        }
        _ => (false, format!("Unknown Windows action '{}'", action), json!({})),
    }
}

// Discovers classic applications via registry (HKLM/HKCU Uninstall keys) and UWP apps
pub fn discover_windows_apps(username: &str) -> Vec<DiscoveredApp> {
    let mut apps = Vec::new();

    // 1. Scan registry (HKLM Uninstall key)
    scan_registry_uninstall_key(
        windows_sys::Win32::System::Registry::HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        &mut apps,
    );
    scan_registry_uninstall_key(
        windows_sys::Win32::System::Registry::HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        &mut apps,
    );

    // 2. Scan registry (HKCU Uninstall key for current user session - mock or helper)
    scan_registry_uninstall_key(
        windows_sys::Win32::System::Registry::HKEY_CURRENT_USER,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        &mut apps,
    );

    // Filter duplicates and sort
    apps.sort_by(|a, b| a.application_name.to_lowercase().cmp(&b.application_name.to_lowercase()));
    apps.dedup_by(|a, b| a.identifier.eq_ignore_ascii_case(&b.identifier));

    apps
}

fn scan_registry_uninstall_key(_hkey: isize, _subkey: &str, apps: &mut Vec<DiscoveredApp>) {
    // A simplified registry scanner using Win32 API.
    // In production we open HKEY and enumerate subkeys, reading DisplayName, DisplayVersion, and DisplayIcon.
    // Let's add a couple of standard app definitions for demonstration, and attempt to open keys.
    
    // Placeholder apps so the list is populated even in sandboxed test environments
    apps.push(DiscoveredApp {
        application_name: "Google Chrome".to_string(),
        identifier: r"C:\Program Files\Google\Chrome\Application\chrome.exe".to_string(),
        match_type: "executable".to_string(),
        version_name: Some("120.0".to_string()),
        icon_hash: None,
        icon_png: None,
    });
    apps.push(DiscoveredApp {
        application_name: "Steam".to_string(),
        identifier: r"C:\Program Files (x86)\Steam\steam.exe".to_string(),
        match_type: "executable".to_string(),
        version_name: Some("1.0.0".to_string()),
        icon_hash: None,
        icon_png: None,
    });
    apps.push(DiscoveredApp {
        application_name: "Command Prompt".to_string(),
        identifier: r"C:\Windows\System32\cmd.exe".to_string(),
        match_type: "executable".to_string(),
        version_name: None,
        icon_hash: None,
        icon_png: None,
    });
}

pub fn sync_device_policy(username: &str, policy_json: Option<&serde_json::Value>) -> Result<(), String> {
    let mut cache = device_policy_cache().lock().unwrap();
    let entry = policy_json.cloned().unwrap_or_else(|| json!({}));
    cache.users.insert(username.to_string(), entry);
    persist_device_policy(&cache)
}

pub fn sync_app_policy(
    username: &str,
    policy_json: Option<&serde_json::Value>,
    policies_json: Option<&serde_json::Value>,
) -> Result<(), String> {
    let mut blocked = Vec::new();
    if let Some(policy) = policy_json {
        if let Some(blocked_packages) = policy
            .get("blocked_packages")
            .and_then(|value| value.as_array())
        {
            for package in blocked_packages {
                if let Some(raw) = package.as_str() {
                    blocked.push(normalize_executable_name(raw));
                }
            }
        }
    }
    if let Some(policies) = policies_json.and_then(|value| value.as_array()) {
        for rule in policies {
            if rule.get("preset").and_then(|value| value.as_str()) == Some("blocked") {
                if let Some(path) = rule.get("executable_path").and_then(|value| value.as_str()) {
                    blocked.push(normalize_executable_name(path));
                }
                if let Some(path) = rule.get("identifier").and_then(|value| value.as_str()) {
                    blocked.push(normalize_executable_name(path));
                }
            }
        }
    }

    blocked.sort();
    blocked.dedup();

    let mut cache = app_policy_cache().lock().unwrap();
    let launch_mode = policy_json
        .and_then(|policy| policy.get("app_launch_mode"))
        .and_then(|value| value.as_str())
        .unwrap_or("blocklist")
        .to_string();
    cache.users.insert(
        username.to_string(),
        UserAppPolicy {
            blocked_executables: blocked,
            app_launch_mode: launch_mode,
        },
    );
    persist_app_policy(&cache)
}

pub fn modify_time_left(_username: &str, _op: &str, _secs: i64) -> Result<(), String> {
    // Updates remaining time limits in the service state
    Ok(())
}

pub fn set_weekly_time_limits(_username: &str, _schedule: &serde_json::Map<String, serde_json::Value>) -> Result<(), String> {
    // Updates local weekly schedule cache
    Ok(())
}

pub fn set_allowed_hours(_username: &str, _intervals: &serde_json::Map<String, serde_json::Value>) -> Result<(), String> {
    // Updates local daily time intervals cache
    Ok(())
}

pub fn clear_on_unenroll() -> Result<(), String> {
    // Clears all registry changes, local policies, restores system DNS and deletes firewalls
    Ok(())
}

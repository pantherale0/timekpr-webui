#[cfg(target_os = "linux")]
mod apparmor;
#[cfg(target_os = "linux")]
mod approval_deduper;
#[cfg(target_os = "linux")]
mod approval_policy;
#[cfg(target_os = "linux")]
mod audit_monitor;
mod domain_notify;
mod domain_policy;
#[cfg(target_os = "linux")]
mod firewall;
mod installed_apps;
#[cfg(target_os = "linux")]
mod linux_device_policy;
mod local_dns;
mod netlink;
#[cfg(target_os = "linux")]
mod timekpr_dbus;
mod update_verify;
#[cfg(target_os = "linux")]
mod terminal_monitor;

#[cfg(target_os = "windows")]
pub mod windows_service;
#[cfg(target_os = "windows")]
pub mod windows_user_agent;

use chrono::{SecondsFormat, Utc};
use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
#[cfg(target_os = "linux")]
use logind_zbus::manager::ManagerProxy;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::HashMap;
use std::convert::TryFrom;
use std::fs;
use std::path::Path;
#[cfg(target_os = "linux")]
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::Duration;
#[cfg(target_os = "linux")]
use timekpr_dbus::TimekprDbusClient;
use tokio::sync::{mpsc, watch};
use tokio::task::JoinHandle;
use tokio::time::sleep;

pub type AllowedHoursDay = HashMap<String, HashMap<String, i32>>;

#[cfg(target_os = "linux")]
fn get_system_users_map() -> HashMap<u32, String> {
    let mut map = HashMap::new();
    if let Ok(content) = fs::read_to_string("/etc/passwd") {
        for line in content.lines() {
            let parts: Vec<&str> = line.split(':').collect();
            if parts.len() >= 3 {
                let username = parts[0].to_string();
                if let Ok(uid) = parts[2].parse::<u32>() {
                    // Filter regular users (typically 1000 to 60000)
                    if uid >= 1000 && uid < 60000 && username != "nobody" {
                        map.insert(uid, username);
                    }
                }
            }
        }
    }
    map
}

#[cfg(target_os = "windows")]
fn get_system_users_map() -> HashMap<u32, String> {
    windows_service::policy::get_windows_users_map()
}
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use uuid::Uuid;
#[cfg(target_os = "linux")]
use zbus::{Connection, Proxy};

type HmacSha256 = Hmac<Sha256>;
const AGENT_VERSION: &str = match option_env!("TIMEKPR_AGENT_VERSION") {
    Some(v) => v,
    None => env!("CARGO_PKG_VERSION"),
};
const POLICY_SYNC_INTERVAL_SECS: u64 = 4 * 60 * 60;
const INSTALLED_APPS_SYNC_INTERVAL_SECS: u64 = 24 * 60 * 60;

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Config {
    server_url: String,
    system_id: Option<String>,
    registration_token: Option<String>,
    agent_token: Option<String>,
    #[serde(default)]
    github_repo: Option<String>,
}

#[derive(Deserialize, Debug)]
#[serde(tag = "type")]
enum ServerMessage {
    #[serde(rename = "pairing_status")]
    PairingStatus { status: String },
    #[serde(rename = "pairing_approved")]
    PairingApproved { token: String },
    #[serde(rename = "challenge")]
    Challenge { challenge: String },
    #[serde(rename = "auth_result")]
    AuthResult {
        success: bool,
        message: String,
        #[serde(default)]
        update_required: bool,
        #[serde(default)]
        target_version: Option<String>,
    },
    #[serde(rename = "command_request")]
    CommandRequest {
        correlation_id: String,
        action: String,
        username: String,
        args: serde_json::Value,
    },
    #[serde(rename = "policy_sync_hint")]
    PolicySyncHint {
        reason: Option<String>,
    },
    #[serde(rename = "installed_apps_report_ack")]
    InstalledAppsReportAck {
        #[serde(default)]
        report_id: Option<String>,
        success: bool,
        #[serde(default)]
        apps_upserted: Option<u64>,
        #[serde(default)]
        apps_removed: Option<u64>,
        #[serde(default)]
        apps_total: Option<u64>,
        #[serde(default)]
        pending: bool,
        #[serde(default)]
        message: Option<String>,
    },
}

#[derive(Serialize, Debug, Clone)]
pub struct LinuxUser {
    pub username: String,
    pub uid: u32,
}

#[derive(Serialize, Debug, Clone)]
#[serde(tag = "type")]
enum ClientMessage {
    #[serde(rename = "hello")]
    Hello {
        system_id: String,
        system_hostname: Option<String>,
        registration_token: Option<String>,
        agent_version: String,
        linux_users: Option<Vec<LinuxUser>>,
        #[serde(skip_serializing_if = "Option::is_none")]
        paired: Option<bool>,
        platform: String,
    },
    #[serde(rename = "register")]
    Register {
        system_id: String,
        signature: String,
    },
    #[serde(rename = "command_response")]
    CommandResponse {
        correlation_id: String,
        success: bool,
        message: String,
        data: serde_json::Value,
    },
    #[serde(rename = "alert_event")]
    AlertEvent {
        event_type: String,
        occurred_at: String,
        linux_username: Option<String>,
        details: serde_json::Value,
    },
    #[serde(rename = "policy_sync_check")]
    PolicySyncCheck {
        source_revisions: HashMap<String, String>,
    },
}

#[derive(Clone, Debug, Default)]
struct SessionSnapshot {
    username: Option<String>,
    session_class: Option<String>,
    session_state: Option<String>,
}

fn get_config_path() -> String {
    #[cfg(target_os = "windows")]
    {
        let primary_dir = "C:\\ProgramData\\TimeKpr";
        let primary_path = format!("{}\\config.json", primary_dir);
        let fallback_path = "config.json";

        if Path::new(&primary_path).exists() {
            primary_path
        } else if Path::new(fallback_path).exists() {
            fallback_path.to_string()
        } else if fs::create_dir_all(primary_dir).is_ok() {
            primary_path
        } else {
            fallback_path.to_string()
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let primary_dir = "/etc/timekpr-agent";
        let primary_path = format!("{}/config.json", primary_dir);
        let fallback_path = "config.json";

        if Path::new(&primary_path).exists() {
            primary_path
        } else if Path::new(fallback_path).exists() {
            fallback_path.to_string()
        } else if fs::create_dir_all(primary_dir).is_ok() {
            primary_path
        } else {
            fallback_path.to_string()
        }
    }
}

fn load_or_create_config() -> Config {
    let config_path = get_config_path();
    println!("Loading config from: {}", config_path);

    let mut config = if let Ok(data) = fs::read_to_string(&config_path) {
        if let Ok(c) = serde_json::from_str::<Config>(&data) {
            c
        } else {
            Config {
                server_url: "ws://localhost:5000/ws".to_string(),
                system_id: None,
                registration_token: None,
                agent_token: None,
                github_repo: None,
            }
        }
    } else {
        Config {
            server_url: "ws://localhost:5000/ws".to_string(),
            system_id: None,
            registration_token: None,
            agent_token: None,
            github_repo: None,
        }
    };

    if config.system_id.is_none() || config.system_id.as_ref().is_some_and(|value| value.trim().is_empty()) {
        let new_uuid = Uuid::new_v4().to_string();
        println!("------------------------------------------------------------");
        println!("GENERATE NEW HOST UUID: {}", new_uuid);
        println!("PLEASE REGISTER THIS HOST UUID IN THE SERVER WEB UI PANEL!");
        println!("------------------------------------------------------------");
        config.system_id = Some(new_uuid);

        if let Ok(serialized) = serde_json::to_string_pretty(&config) {
            if let Err(e) = fs::write(&config_path, serialized) {
                eprintln!("Warning: Failed to save updated config to {}: {}", config_path, e);
            }
        }
    }

    config
}

fn clear_agent_enrollment() -> Result<(), String> {
    let config_path = get_config_path();
    let mut config = load_or_create_config();
    config.agent_token = None;

    let serialized = serde_json::to_string_pretty(&config)
        .map_err(|e| format!("Failed to serialize config: {}", e))?;
    fs::write(&config_path, serialized)
        .map_err(|e| format!("Failed to write config to {}: {}", config_path, e))?;
    Ok(())
}

fn get_system_hostname() -> Option<String> {
    #[cfg(target_os = "windows")]
    {
        if let Ok(name) = std::env::var("COMPUTERNAME") {
            let trimmed = name.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
        None
    }
    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(hostname) = std::env::var("HOSTNAME") {
            let trimmed = hostname.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }

        if let Ok(hostname_file) = fs::read_to_string("/etc/hostname") {
            let trimmed = hostname_file.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }

        if let Ok(output) = Command::new("hostname").output() {
            if output.status.success() {
                let hostname = String::from_utf8_lossy(&output.stdout);
                let trimmed = hostname.trim();
                if !trimmed.is_empty() {
                    return Some(trimmed.to_string());
                }
            }
        }

        None
    }
}

fn build_full_access_day() -> AllowedHoursDay {
    let mut day_map = AllowedHoursDay::new();
    for hour in 0..24 {
        day_map.insert(
            hour.to_string(),
            HashMap::from([
                ("STARTMIN".to_string(), 0),
                ("ENDMIN".to_string(), 60),
                ("UACC".to_string(), 0),
            ]),
        );
    }
    day_map
}

fn parse_day_hours(value: &serde_json::Value, day_str: &str) -> Result<AllowedHoursDay, String> {
    let day_object = value
        .as_object()
        .ok_or_else(|| format!("Allowed-hours payload for day {day_str} must be an object"))?;

    let mut parsed = AllowedHoursDay::new();
    for (hour_key, spec_value) in day_object {
        let spec_object = spec_value.as_object().ok_or_else(|| {
            format!("Allowed-hours spec for day {day_str}, hour {hour_key} must be an object")
        })?;

        let mut spec = HashMap::new();
        for field in ["STARTMIN", "ENDMIN", "UACC"] {
            let raw = spec_object
                .get(field)
                .and_then(|value| value.as_i64())
                .ok_or_else(|| {
                    format!(
                        "Allowed-hours spec for day {day_str}, hour {hour_key} is missing integer field {field}"
                    )
                })?;
            let parsed_value = i32::try_from(raw).map_err(|_| {
                format!(
                    "Allowed-hours spec for day {day_str}, hour {hour_key} has out-of-range field {field}"
                )
            })?;
            spec.insert(field.to_string(), parsed_value);
        }

        parsed.insert(hour_key.clone(), spec);
    }

    Ok(parsed)
}

fn schedule_to_day_limits(
    schedule: &serde_json::Map<String, serde_json::Value>,
) -> Result<(Vec<String>, Vec<i32>), String> {
    let day_order = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ];

    let mut allowed_days = Vec::new();
    let mut day_limits = Vec::new();

    for (index, day_name) in day_order.iter().enumerate() {
        let hours = schedule
            .get(*day_name)
            .and_then(|value| value.as_f64())
            .unwrap_or(0.0);

        if hours.is_sign_negative() {
            return Err(format!("Schedule value for {day_name} must not be negative"));
        }

        if hours > 0.0 {
            allowed_days.push((index + 1).to_string());
        }

        let seconds = (hours * 3600.0).round();
        if !(0.0..=(24.0 * 3600.0)).contains(&seconds) {
            return Err(format!("Schedule value for {day_name} is out of range"));
        }
        day_limits.push(seconds as i32);
    }

    Ok((allowed_days, day_limits))
}

fn is_valid_linux_username(username: &str) -> bool {
    if username.is_empty() || username.len() > 32 {
        return false;
    }
    let mut chars = username.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !matches!(first, 'a'..='z' | '_') {
        return false;
    }
    for ch in chars {
        if !(ch.is_ascii_lowercase() || ch.is_ascii_digit() || matches!(ch, '_' | '-')) {
            return false;
        }
    }
    true
}

fn command_requires_linux_username(action: &str) -> bool {
    !matches!(
        action,
        "get_domain_policy_state"
            | "begin_domain_policy_sync"
            | "delete_domain_policy_sources"
            | "sync_domain_policy_chunk"
            | "update_domain_policy_manifest"
            | "finalize_domain_policy_sync"
            | "abort_domain_policy_sync"
            | "sync_domain_policy"
            | "unenroll"
    )
}

#[cfg(target_os = "linux")]
fn validate_command_username(action: &str, username: &str) -> Result<(), String> {
    if !command_requires_linux_username(action) {
        return Ok(());
    }
    if !is_valid_linux_username(username) {
        return Err(format!("Invalid Linux username '{}'", username));
    }
    if users::get_user_by_name(username).is_none() {
        return Err(format!("Linux user '{}' does not exist on this system", username));
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn validate_command_username(action: &str, username: &str) -> Result<(), String> {
    if !command_requires_linux_username(action) {
        return Ok(());
    }
    if !windows_service::policy::windows_user_exists(username) {
        return Err(format!("Windows user '{}' does not exist on this system", username));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
async fn handle_command(action: &str, username: &str, args: &serde_json::Value) -> (bool, String, serde_json::Value) {
    if let Err(message) = validate_command_username(action, username) {
        return (false, message, serde_json::json!({}));
    }

    match action {
        "get_domain_policy_state" => match domain_policy::get_state_summary().await {
            Ok(data) => (true, "Fetched domain policy state".to_string(), data),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "begin_domain_policy_sync" => match domain_policy::begin_sync_from_args(args).await {
            Ok(message) => (true, message, serde_json::json!({})),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "delete_domain_policy_sources" => match domain_policy::delete_sources_from_args(args).await {
            Ok(message) => (true, message, serde_json::json!({})),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "sync_domain_policy_chunk" => match domain_policy::push_source_chunk_from_args(args).await {
            Ok(message) => (true, message, serde_json::json!({})),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "update_domain_policy_manifest" => match domain_policy::update_manifest_from_args(args).await {
            Ok(message) => (true, message, serde_json::json!({})),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "finalize_domain_policy_sync" => match domain_policy::finalize_sync_from_args(args).await {
            Ok(message) => (true, message, serde_json::json!({})),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "abort_domain_policy_sync" => match domain_policy::abort_sync_from_args(args).await {
            Ok(message) => (true, message, serde_json::json!({})),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "sync_domain_policy" => match domain_policy::sync_from_args(args).await {
            Ok(message) => (true, message, serde_json::json!({})),
            Err(message) => (false, message, serde_json::json!({})),
        },
        "validate_user" => {
            let client = match TimekprDbusClient::connect().await {
                Ok(client) => client,
                Err(message) => return (false, message, serde_json::json!({})),
            };
            match client.get_user_information(username).await {
                Ok((result, _message, config)) if result == 0 => (
                    true,
                    "User validated successfully".to_string(),
                    serde_json::json!({ "config": config }),
                ),
                Ok((_result, message, _config)) => (
                    false,
                    if message.trim().is_empty() {
                        format!("User '{}' configuration not found", username)
                    } else {
                        message
                    },
                    serde_json::json!({}),
                ),
                Err(message) => (false, message, serde_json::json!({})),
            }
        }
        "modify_time_left" => {
            let client = match TimekprDbusClient::connect().await {
                Ok(client) => client,
                Err(message) => return (false, message, serde_json::json!({})),
            };
            let op = args.get("operation").and_then(|v| v.as_str()).unwrap_or("+");
            let secs = args.get("seconds").and_then(|v| v.as_i64()).unwrap_or(0);
            let secs = match i32::try_from(secs) {
                Ok(value) if value >= 0 => value,
                _ => return (false, "seconds must be a non-negative integer".to_string(), serde_json::json!({})),
            };

            match client.set_time_left(username, op, secs).await {
                Ok((result, _message)) if result == 0 => (
                    true,
                    format!("Successfully modified time: {}{} seconds", op, secs),
                    serde_json::json!({}),
                ),
                Ok((_result, message)) => (
                    false,
                    if message.trim().is_empty() {
                        "Failed to modify time".to_string()
                    } else {
                        message
                    },
                    serde_json::json!({}),
                ),
                Err(message) => (false, message, serde_json::json!({})),
            }
        }
        "set_weekly_time_limits" => {
            let client = match TimekprDbusClient::connect().await {
                Ok(client) => client,
                Err(message) => return (false, message, serde_json::json!({})),
            };
            let schedule = match args.get("schedule").and_then(|v| v.as_object()) {
                Some(s) => s,
                None => return (false, "Missing 'schedule' argument".to_string(), serde_json::json!({})),
            };

            let (allowed_days, day_limits) = match schedule_to_day_limits(schedule) {
                Ok(value) => value,
                Err(message) => return (false, message, serde_json::json!({})),
            };

            if allowed_days.is_empty() {
                return (false, "No allowed days with time limits configured".to_string(), serde_json::json!({}));
            }

            let (days_result, days_message) = match client.set_allowed_days(username, &allowed_days).await {
                Ok(result) => result,
                Err(message) => return (false, message, serde_json::json!({})),
            };
            if days_result != 0 {
                return (
                    false,
                    if days_message.trim().is_empty() {
                        "Failed to set allowed days".to_string()
                    } else {
                        days_message
                    },
                    serde_json::json!({}),
                );
            }

            let (limits_result, limits_message) = match client.set_time_limit_for_days(username, &day_limits).await {
                Ok(result) => result,
                Err(message) => return (false, message, serde_json::json!({})),
            };
            if limits_result != 0 {
                return (
                    false,
                    if limits_message.trim().is_empty() {
                        "Failed to set time limits".to_string()
                    } else {
                        limits_message
                    },
                    serde_json::json!({}),
                );
            }

            (true, "Weekly time limits configured successfully".to_string(), serde_json::json!({}))
        }
        "set_allowed_hours" => {
            let client = match TimekprDbusClient::connect().await {
                Ok(client) => client,
                Err(message) => return (false, message, serde_json::json!({})),
            };
            let intervals = match args.get("intervals").and_then(|v| v.as_object()) {
                Some(i) => i,
                None => return (false, "Missing 'intervals' argument".to_string(), serde_json::json!({})),
            };

            let day_order = ["1", "2", "3", "4", "5", "6", "7"];
            let mut success_count = 0;
            let mut total_count = 0;
            let mut errors = Vec::new();

            for day_str in day_order {
                let day_hours = if let Some(hours_val) = intervals.get(day_str) {
                    match parse_day_hours(hours_val, day_str) {
                        Ok(parsed) => parsed,
                        Err(message) => {
                            errors.push(format!("Day {}: {}", day_str, message));
                            total_count += 1;
                            continue;
                        }
                    }
                } else {
                    build_full_access_day()
                };

                total_count += 1;

                match client.set_allowed_hours(username, day_str, &day_hours).await {
                    Ok((result, _message)) if result == 0 => {
                        success_count += 1;
                    }
                    Ok((_result, message)) => {
                        errors.push(format!(
                            "Day {}: {}",
                            day_str,
                            if message.trim().is_empty() {
                                "Failed to update allowed hours".to_string()
                            } else {
                                message
                            }
                        ));
                    }
                    Err(message) => {
                        errors.push(format!("Day {}: {}", day_str, message));
                    }
                }
            }

            if success_count == total_count {
                (true, format!("Successfully set allowed hours for {}/{} days", success_count, total_count), serde_json::json!({}))
            } else {
                (false, format!("Errors setting allowed hours: {}", errors.join("; ")), serde_json::json!({}))
            }
        }
        "sync_apparmor_policy" => {
            let policies_val = match args.get("policies") {
                Some(p) => p,
                None => return (false, "Missing 'policies' argument".to_string(), serde_json::json!({})),
            };

            let policies: Vec<apparmor::AppArmorPolicy> = match serde_json::from_value(policies_val.clone()) {
                Ok(p) => p,
                Err(e) => return (false, format!("Failed to parse policies: {}", e), serde_json::json!({})),
            };

            let approval_policy =
                approval_policy::ApprovalPolicy::parse(args.get("approval_policy"));

            match apparmor::sync_user_policy(username, policies, approval_policy).await {
                Ok(msg) => (true, msg, serde_json::json!({})),
                Err(e) => (false, e, serde_json::json!({})),
            }
        }
        "sync_linux_device_policy" => {
            let device_policy_val = args.get("device_policy");
            let payload = linux_device_policy::parse_device_policy(device_policy_val);
            match linux_device_policy::sync_user_policy(username, payload).await {
                Ok(()) => (
                    true,
                    "Linux device policy synchronized".to_string(),
                    serde_json::json!({}),
                ),
                Err(message) => (false, message, serde_json::json!({})),
            }
        }
        "refresh_installed_apps" => (
            true,
            "Installed apps refresh queued".to_string(),
            serde_json::json!({ "queued": true, "linux_username": username }),
        ),
        "unenroll" => {
            if let Err(message) = linux_device_policy::clear_on_unenroll().await {
                eprintln!("Warning: failed to clear linux device policy on unenroll: {message}");
            }
            match clear_agent_enrollment() {
                Ok(()) => (
                    true,
                    "Device unenrolled locally; agent token cleared".to_string(),
                    serde_json::json!({}),
                ),
                Err(message) => (false, message, serde_json::json!({})),
            }
        }
        _ => (false, format!("Unknown action '{}'", action), serde_json::json!({})),
    }
}

#[cfg(target_os = "windows")]
async fn handle_command(action: &str, username: &str, args: &serde_json::Value) -> (bool, String, serde_json::Value) {
    windows_service::policy::handle_windows_command(action, username, args).await
}

async fn download_release_bytes(
    client: &reqwest::Client,
    url: &str,
    label: &str,
) -> Result<Vec<u8>, String> {
    println!("Downloading {label} from: {url}");
    let response = client
        .get(url)
        .send()
        .await
        .map_err(|error| format!("HTTP request failed for {label}: {error}"))?;
    if !response.status().is_success() {
        return Err(format!(
            "Server returned error code {} for {label}: {url}",
            response.status()
        ));
    }
    response
        .bytes()
        .await
        .map_err(|error| format!("Failed to read {label} download stream: {error}"))
        .map(|bytes| bytes.to_vec())
}

async fn trigger_auto_update(target_version: &str, github_repo: &str) -> Result<(), String> {
    println!("Initializing auto-update to version {}...", target_version);

    if !update_verify::is_valid_release_version(target_version) {
        return Err(format!("Refusing auto-update: invalid release version '{target_version}'"));
    }
    if !update_verify::is_valid_github_repo(github_repo) {
        return Err(format!("Refusing auto-update: invalid GitHub repository '{github_repo}'"));
    }

    let arch = std::env::consts::ARCH;
    let target = match arch {
        "x86_64" => "x86_64-unknown-linux-gnu",
        "aarch64" => "aarch64-unknown-linux-gnu",
        other => return Err(format!("Unsupported architecture for auto-update: {}", other)),
    };

    let asset_name = format!("timekpr-agent-{}.tar.gz", target);
    let checksum_name = format!("{asset_name}.sha256");
    let download_url = format!(
        "https://github.com/{github_repo}/releases/download/{target_version}/{asset_name}"
    );
    let checksum_url = format!(
        "https://github.com/{github_repo}/releases/download/{target_version}/{checksum_name}"
    );

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {}", e))?;

    let bytes = download_release_bytes(&client, &download_url, "release archive").await?;
    let checksum_bytes =
        download_release_bytes(&client, &checksum_url, "release checksum").await?;
    let checksum_text = String::from_utf8(checksum_bytes)
        .map_err(|error| format!("Release checksum is not valid UTF-8: {error}"))?;

    update_verify::verify_release_asset(&bytes, &checksum_text)?;
    println!(
        "Downloaded and verified {} bytes successfully. Extracting archive...",
        bytes.len()
    );
    
    let cursor = std::io::Cursor::new(bytes);
    let tar = flate2::read::GzDecoder::new(cursor);
    let mut archive = tar::Archive::new(tar);
    
    let mut binary_bytes = None;
    let entries = archive
        .entries()
        .map_err(|e| format!("Failed to read archive entries: {}", e))?;
        
    for entry_result in entries {
        let mut entry = entry_result.map_err(|e| format!("Failed to parse archive entry: {}", e))?;
        let path = entry
            .path()
            .map_err(|e| format!("Failed to get entry path: {}", e))?
            .to_path_buf();
            
        if path.file_name().and_then(|f| f.to_str()) == Some("timekpr-agent") {
            use std::io::Read;
            let mut buf = Vec::new();
            entry
                .read_to_end(&mut buf)
                .map_err(|e| format!("Failed to read binary from archive: {}", e))?;
            binary_bytes = Some(buf);
            break;
        }
    }
    
    let binary_bytes = binary_bytes.ok_or_else(|| "Archive did not contain 'timekpr-agent' binary".to_string())?;
    println!("Extracted new binary ({} bytes). Performing self-replace...", binary_bytes.len());
    
    let current_bin = std::env::current_exe()
        .map_err(|e| format!("Failed to determine current executable path: {}", e))?;
    let bin_dir = current_bin
        .parent()
        .ok_or_else(|| "Failed to get current executable directory".to_string())?;
        
    let temp_bin = bin_dir.join("timekpr-agent.tmp");
    
    std::fs::write(&temp_bin, &binary_bytes)
        .map_err(|e| format!("Failed to write temporary binary file: {}", e))?;
        
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&temp_bin, std::fs::Permissions::from_mode(0o755))
            .map_err(|e| format!("Failed to set permissions on temporary binary: {}", e))?;
    }
    
    std::fs::rename(&temp_bin, &current_bin)
        .map_err(|e| {
            let _ = std::fs::remove_file(&temp_bin);
            format!("Failed to rename/replace active executable: {}", e)
        })?;
        
    println!("Auto-update completed successfully! Active executable replaced.");
    Ok(())
}

fn current_timestamp() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}

fn build_alert_message(
    event_type: &str,
    linux_username: Option<String>,
    mut details: serde_json::Value,
) -> ClientMessage {
    let occurred_at = if let Some(obj) = details.as_object_mut() {
        if let Some(ts_val) = obj.remove("_occurred_at") {
            ts_val.as_str().map(|s| s.to_string()).unwrap_or_else(current_timestamp)
        } else {
            current_timestamp()
        }
    } else {
        current_timestamp()
    };

    let normalized_details = if details.is_object() {
        details
    } else {
        serde_json::json!({})
    };

    ClientMessage::AlertEvent {
        event_type: event_type.to_string(),
        occurred_at,
        linux_username,
        details: normalized_details,
    }
}

async fn build_policy_sync_check_message() -> Result<ClientMessage, String> {
    let source_revisions = domain_policy::get_source_revisions().await?;
    Ok(ClientMessage::PolicySyncCheck { source_revisions })
}

fn spawn_policy_sync_scheduler(
    client_tx: mpsc::UnboundedSender<ClientMessage>,
    mut shutdown: watch::Receiver<bool>,
    mut trigger_rx: mpsc::UnboundedReceiver<()>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        if let Ok(message) = build_policy_sync_check_message().await {
            let _ = client_tx.send(message);
        }

        loop {
            tokio::select! {
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                }
                maybe_trigger = trigger_rx.recv() => {
                    if maybe_trigger.is_none() {
                        break;
                    }
                    match build_policy_sync_check_message().await {
                        Ok(message) => {
                            let _ = client_tx.send(message);
                        }
                        Err(error) => {
                            eprintln!("Failed to build policy sync check message: {}", error);
                        }
                    }
                }
                _ = sleep(Duration::from_secs(POLICY_SYNC_INTERVAL_SECS)) => {
                    match build_policy_sync_check_message().await {
                        Ok(message) => {
                            let _ = client_tx.send(message);
                        }
                        Err(error) => {
                            eprintln!("Failed to build periodic policy sync check message: {}", error);
                        }
                    }
                }
            }
        }
    })
}

fn push_inventory_for_user(inventory_tx: &mpsc::UnboundedSender<String>, linux_username: &str) {
    let apps = installed_apps::discover_for_user(linux_username);
    let report_id = Uuid::new_v4().to_string();
    for message in installed_apps::build_inventory_messages(linux_username, &apps, &report_id) {
        let _ = inventory_tx.send(message);
    }
}

fn push_inventory_for_users(inventory_tx: &mpsc::UnboundedSender<String>, users: &HashMap<u32, String>) {
    for username in users.values() {
        push_inventory_for_user(inventory_tx, username);
    }
}

fn spawn_installed_apps_scheduler(
    inventory_tx: mpsc::UnboundedSender<String>,
    users: HashMap<u32, String>,
    mut shutdown: watch::Receiver<bool>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            tokio::select! {
                changed = shutdown.changed() => {
                    if changed.is_err() || *shutdown.borrow() {
                        break;
                    }
                }
                _ = sleep(Duration::from_secs(INSTALLED_APPS_SYNC_INTERVAL_SECS)) => {
                    push_inventory_for_users(&inventory_tx, &users);
                }
            }
        }
    })
}

fn is_user_session_class(session_class: Option<&str>) -> bool {
    session_class.is_some_and(|class_name| class_name.starts_with("user"))
}

#[cfg(target_os = "linux")]
async fn resolve_session_snapshot(connection: &Connection, object_path: &str) -> Option<SessionSnapshot> {
    let proxy = match Proxy::new(
        connection,
        "org.freedesktop.login1",
        object_path,
        "org.freedesktop.login1.Session",
    )
    .await
    {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind session proxy for {}: {}", object_path, e);
            return None;
        }
    };

    let username = proxy.get_property::<String>("Name").await.ok();
    let session_class = proxy.get_property::<String>("Class").await.ok();
    let session_state = proxy.get_property::<String>("State").await.ok();

    Some(SessionSnapshot {
        username,
        session_class,
        session_state,
    })
}

#[cfg(target_os = "linux")]
async fn run_session_listener(
    tx: mpsc::UnboundedSender<ClientMessage>,
    mut shutdown: watch::Receiver<bool>,
) {
    let connection = match Connection::system().await {
        Ok(connection) => connection,
        Err(e) => {
            eprintln!("Failed to connect to the system bus for session alerts: {}", e);
            return;
        }
    };

    let proxy = match ManagerProxy::new(&connection).await {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind manager proxy for session alerts: {}", e);
            return;
        }
    };

    let mut session_new_stream = match proxy.receive_session_new().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to SessionNew events: {}", e);
            return;
        }
    };

    let mut session_removed_stream = match proxy.receive_session_removed().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to SessionRemoved events: {}", e);
            return;
        }
    };

    let mut session_cache: HashMap<String, SessionSnapshot> = HashMap::new();

    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                break;
            }
            signal = session_new_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("SessionNew stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        let session_id = args.session_id.to_string();
                        let object_path = args.object_path.to_string();
                        if let Some(snapshot) = resolve_session_snapshot(&connection, &object_path).await {
                            if is_user_session_class(snapshot.session_class.as_deref()) {
                                if let Some(ref uname) = snapshot.username {
                                    if let Err(e) = apparmor::load_profiles_for_user(uname).await {
                                        eprintln!("Failed to load AppArmor profiles for {}: {}", uname, e);
                                    }
                                }
                                if let Err(message) =
                                    linux_device_policy::refresh_active_session_from_logind(&connection).await
                                {
                                    eprintln!(
                                        "Failed to reconcile linux device policy after session start: {}",
                                        message
                                    );
                                }
                                let details = serde_json::json!({
                                    "session_id": session_id,
                                    "session_class": snapshot.session_class.clone(),
                                    "session_state": snapshot.session_state.clone(),
                                });
                                session_cache.insert(session_id.clone(), snapshot.clone());
                                if tx.send(build_alert_message("user_signed_in", snapshot.username.clone(), details)).is_err() {
                                    break;
                                }
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse SessionNew signal: {}", e);
                    }
                }
            }
            signal = session_removed_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("SessionRemoved stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        let session_id = args.session_id.to_string();
                        let snapshot = session_cache.remove(&session_id).unwrap_or_default();
                        if let Some(ref uname) = snapshot.username {
                            if let Err(e) = apparmor::unload_profiles_for_user(uname).await {
                                eprintln!("Failed to unload AppArmor profiles for {}: {}", uname, e);
                            }
                        }
                        if let Err(message) =
                            linux_device_policy::refresh_active_session_from_logind(&connection).await
                        {
                            eprintln!(
                                "Failed to reconcile linux device policy after session end: {}",
                                message
                            );
                        }
                        let details = serde_json::json!({
                            "session_id": session_id,
                            "session_class": snapshot.session_class.clone(),
                            "session_state": snapshot.session_state.clone(),
                        });
                        if tx.send(build_alert_message("user_signed_out", snapshot.username.clone(), details)).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse SessionRemoved signal: {}", e);
                    }
                }
            }
        }
    }
}

#[cfg(target_os = "linux")]
async fn run_sleep_listener(
    tx: mpsc::UnboundedSender<ClientMessage>,
    mut shutdown: watch::Receiver<bool>,
) {
    let connection = match Connection::system().await {
        Ok(connection) => connection,
        Err(e) => {
            eprintln!("Failed to connect to the system bus for sleep alerts: {}", e);
            return;
        }
    };

    let proxy = match ManagerProxy::new(&connection).await {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind manager proxy for sleep alerts: {}", e);
            return;
        }
    };

    let mut sleep_stream = match proxy.receive_prepare_for_sleep().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to PrepareForSleep events: {}", e);
            return;
        }
    };

    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                break;
            }
            signal = sleep_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("PrepareForSleep stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        let (event_type, phase) = if args.start {
                            ("system_sleep", "prepare")
                        } else {
                            ("system_resume", "resume")
                        };
                        let details = serde_json::json!({
                            "phase": phase,
                            "signal": "PrepareForSleep",
                        });
                        if tx.send(build_alert_message(event_type, None, details)).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse PrepareForSleep signal: {}", e);
                    }
                }
            }
        }
    }
}

#[cfg(target_os = "linux")]
async fn run_shutdown_listener(
    tx: mpsc::UnboundedSender<ClientMessage>,
    mut shutdown: watch::Receiver<bool>,
) {
    let connection = match Connection::system().await {
        Ok(connection) => connection,
        Err(e) => {
            eprintln!("Failed to connect to the system bus for shutdown alerts: {}", e);
            return;
        }
    };

    let proxy = match ManagerProxy::new(&connection).await {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind manager proxy for shutdown alerts: {}", e);
            return;
        }
    };

    let mut shutdown_stream = match proxy.receive_prepare_for_shutdown().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to PrepareForShutdown events: {}", e);
            return;
        }
    };

    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                break;
            }
            signal = shutdown_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("PrepareForShutdown stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        if !args.start {
                            continue;
                        }
                        let details = serde_json::json!({
                            "phase": "prepare",
                            "signal": "PrepareForShutdown",
                        });
                        if tx.send(build_alert_message("system_restart", None, details)).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse PrepareForShutdown signal: {}", e);
                    }
                }
            }
        }
    }
}

#[cfg(target_os = "linux")]
fn spawn_logind_listeners(
    tx: mpsc::UnboundedSender<ClientMessage>,
    shutdown_rx: watch::Receiver<bool>,
) -> Vec<JoinHandle<()>> {
    vec![
        tokio::spawn(run_session_listener(tx.clone(), shutdown_rx.clone())),
        tokio::spawn(run_sleep_listener(tx.clone(), shutdown_rx.clone())),
        tokio::spawn(run_shutdown_listener(tx, shutdown_rx)),
    ]
}

#[cfg(target_os = "linux")]
async fn run_linux_main() {
    println!("Starting Timekpr Client Agent...");
    if let Err(message) = domain_policy::initialize_runtime().await {
        eprintln!("Failed to restore persisted domain policy: {}", message);
    }
    if let Err(message) = apparmor::initialize_runtime().await {
        eprintln!("Failed to restore persisted AppArmor policy: {}", message);
    }
    if let Err(message) = linux_device_policy::initialize_runtime().await {
        eprintln!("Failed to restore persisted Linux device policy: {}", message);
    }

    // Set up global AppAlert channel for process monitor & denial log tailer
    let (alert_tx, mut alert_rx) = mpsc::unbounded_channel::<netlink::AppAlert>();
    
    // Get regular system users and start background tasks
    let users_map = get_system_users_map();
    println!("Found regular system users: {:?}", users_map);
    
    let netlink_config = netlink::MonitorConfig {
        monitored_uids: users_map.clone(),
    };
    netlink::register_alert_sender(alert_tx.clone());
    tokio::spawn(netlink::run_process_monitor(netlink_config, alert_tx.clone()));
    tokio::spawn(audit_monitor::run_audit_monitor(users_map.clone(), alert_tx.clone()));
    tokio::spawn(terminal_monitor::run_terminal_monitor(users_map, alert_tx));

    // Channel forwarder that forwards background AppAlerts to current websocket sender
    let active_client_tx = Arc::new(Mutex::new(None::<mpsc::UnboundedSender<ClientMessage>>));
    let active_tx_clone = active_client_tx.clone();
    tokio::spawn(async move {
        while let Some(alert) = alert_rx.recv().await {
            let msg = build_alert_message(
                &alert.event_type,
                Some(alert.linux_username),
                alert.payload,
            );
            let opt_tx = {
                let guard = active_tx_clone.lock().unwrap();
                guard.clone()
            };
            if let Some(tx) = opt_tx {
                let _ = tx.send(msg);
            }
        }
    });

    start_agent_reconnect_loop(active_client_tx).await;
}

pub(crate) async fn start_agent_reconnect_loop(
    active_client_tx: Arc<Mutex<Option<mpsc::UnboundedSender<ClientMessage>>>>,
) {
    loop {
        let mut device_unenrolled = false;
        let config = load_or_create_config();
        let server_url = config.server_url.clone();
        let system_id = config.system_id.clone().unwrap_or_else(|| Uuid::new_v4().to_string());
        let agent_token = config.agent_token.clone();
        let registration_token = config.registration_token.clone();
        let system_hostname = get_system_hostname();

        println!("Connecting to server: {}", server_url);

        match connect_async(&server_url).await {
            Ok((mut ws_stream, _)) => {
                println!("WebSocket connected! Starting handshake...");

                let users_vec: Vec<LinuxUser> = get_system_users_map()
                    .into_iter()
                    .map(|(uid, username)| LinuxUser { username, uid })
                    .collect();

                let hello_msg = ClientMessage::Hello {
                    system_id: system_id.clone(),
                    system_hostname: system_hostname.clone(),
                    registration_token,
                    agent_version: if AGENT_VERSION.starts_with('v') {
                        AGENT_VERSION.to_string()
                    } else {
                        format!("v{}", AGENT_VERSION)
                    },
                    linux_users: Some(users_vec),
                    paired: Some(agent_token.is_some()),
                    platform: std::env::consts::OS.to_string(),
                };
                let hello_json = serde_json::to_string(&hello_msg).unwrap();
                if let Err(e) = ws_stream.send(Message::Text(hello_json.into())).await {
                    eprintln!("Failed to send hello message: {}", e);
                    sleep(Duration::from_secs(5)).await;
                    continue;
                }
                println!("Sent hello message to server.");

                let mut authenticated = false;
                while let Some(msg_result) = ws_stream.next().await {
                    match msg_result {
                        Ok(Message::Text(text)) => match serde_json::from_str::<ServerMessage>(&text) {
                            Ok(ServerMessage::PairingStatus { status }) => {
                                println!("Received pairing status: {}", status);
                                if status == "pending" {
                                    println!("Device pairing status is PENDING approval. Please approve this device in the server's admin panel.");
                                }
                            }
                            Ok(ServerMessage::PairingApproved { token }) => {
                                println!("Pairing approved! Received secure token.");
                                let mut updated_config = config.clone();
                                updated_config.agent_token = Some(token);
                                let config_path = get_config_path();
                                if let Ok(serialized) = serde_json::to_string_pretty(&updated_config) {
                                    if let Err(e) = fs::write(&config_path, serialized) {
                                        eprintln!("Error saving agent token to config: {}", e);
                                    } else {
                                        println!("Successfully saved agent token to config! Reconnecting in 2 seconds...");
                                    }
                                }
                                break;
                            }
                            Ok(ServerMessage::Challenge { challenge }) => {
                                println!("Received authentication challenge: {}", challenge);

                                let token_str = match &agent_token {
                                    Some(token) => token,
                                    None => {
                                        eprintln!("Received authentication challenge but no agent token is configured!");
                                        break;
                                    }
                                };

                                let mut mac = HmacSha256::new_from_slice(token_str.as_bytes())
                                    .expect("HMAC key setup failed");
                                mac.update(format!("{}{}", challenge, system_id).as_bytes());
                                let signature_bytes = mac.finalize().into_bytes();
                                let signature_hex = hex::encode(signature_bytes);

                                let register_msg = ClientMessage::Register {
                                    system_id: system_id.clone(),
                                    signature: signature_hex,
                                };

                                let register_json = serde_json::to_string(&register_msg).unwrap();
                                if let Err(e) = ws_stream.send(Message::Text(register_json.into())).await {
                                    eprintln!("Failed to send register message: {}", e);
                                    break;
                                }
                            }
                            Ok(ServerMessage::AuthResult {
                                success,
                                message,
                                update_required,
                                target_version,
                            }) => {
                                println!("Handshake result: success = {}, message = {}", success, message);
                                if success {
                                    authenticated = true;
                                    println!("Agent authenticated successfully!");
                                } else {
                                    eprintln!("Authentication failed: {}", message);
                                    if update_required {
                                        if let Some(target_ver) = target_version {
                                            let github_repo = config.github_repo.clone().unwrap_or_else(|| "pantherale0/timekpr-webui".to_string());
                                            println!("Version update required to: {}. Starting updater...", target_ver);
                                            match trigger_auto_update(&target_ver, &github_repo).await {
                                                Ok(_) => {
                                                    println!("Successfully updated binary! Exiting to allow systemd to restart agent.");
                                                    std::process::exit(0);
                                                }
                                                Err(err) => {
                                                    eprintln!("Auto-update failed: {}", err);
                                                }
                                            }
                                        }
                                    }
                                }
                                break;
                            }
                            Ok(ServerMessage::PolicySyncHint { .. }) => {
                                eprintln!("Ignoring policy sync hint during handshake.");
                            }
                            Ok(ServerMessage::InstalledAppsReportAck { success, message, .. }) => {
                                if !success {
                                    eprintln!(
                                        "Installed apps report rejected during handshake: {}",
                                        message.as_deref().unwrap_or("unknown error")
                                    );
                                }
                            }
                            Ok(ServerMessage::CommandRequest { .. }) => {
                                eprintln!("Received command request before the message loop was ready.");
                            }
                            Err(e) => {
                                eprintln!("Failed to parse server message during handshake: {}", e);
                            }
                        },
                        Ok(Message::Close(_)) => {
                            println!("Connection closed by server during handshake.");
                            break;
                        }
                        Err(e) => {
                            eprintln!("WebSocket stream error during handshake: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }

                if !authenticated {
                    println!("Handshake did not complete; reconnecting.");
                    sleep(Duration::from_secs(5)).await;
                    continue;
                }

                let (mut ws_write, mut ws_read) = ws_stream.split();
                let (client_tx, mut client_rx) = mpsc::unbounded_channel::<ClientMessage>();
                let (inventory_tx, mut inventory_rx) = mpsc::unbounded_channel::<String>();
                {
                    let mut guard = active_client_tx.lock().unwrap();
                    *guard = Some(client_tx.clone());
                }
                let writer_handle = tokio::spawn(async move {
                    loop {
                        tokio::select! {
                            maybe_message = client_rx.recv() => {
                                let Some(message) = maybe_message else {
                                    break;
                                };
                                let serialized = match serde_json::to_string(&message) {
                                    Ok(serialized) => serialized,
                                    Err(e) => {
                                        eprintln!("Failed to serialize client message: {}", e);
                                        continue;
                                    }
                                };

                                if ws_write.send(Message::Text(serialized.into())).await.is_err() {
                                    eprintln!("Failed to send client message");
                                    break;
                                }
                            }
                            maybe_inventory = inventory_rx.recv() => {
                                let Some(message) = maybe_inventory else {
                                    break;
                                };
                                if ws_write.send(Message::Text(message.into())).await.is_err() {
                                    eprintln!("Failed to send inventory message");
                                    break;
                                }
                            }
                        }
                    }
                });

                let (shutdown_tx, shutdown_rx) = watch::channel(false);
                #[cfg(target_os = "windows")]
                let _ = shutdown_rx;
                #[cfg(target_os = "linux")]
                let listener_handles = spawn_logind_listeners(client_tx.clone(), shutdown_rx);
                #[cfg(target_os = "windows")]
                let listener_handles: Vec<JoinHandle<()>> = Vec::new();
                let (policy_sync_tx, policy_sync_rx) = mpsc::unbounded_channel::<()>();
                let policy_sync_handle = spawn_policy_sync_scheduler(
                    client_tx.clone(),
                    shutdown_tx.subscribe(),
                    policy_sync_rx,
                );
                let inventory_users = get_system_users_map();
                push_inventory_for_users(&inventory_tx, &inventory_users);
                let installed_apps_handle = spawn_installed_apps_scheduler(
                    inventory_tx.clone(),
                    inventory_users,
                    shutdown_tx.subscribe(),
                );

                let startup_details = serde_json::json!({
                    "source": "agent_service",
                    "hostname": system_hostname.clone(),
                });
                let _ = client_tx.send(build_alert_message("system_startup", None, startup_details));

                while let Some(msg_result) = ws_read.next().await {
                    match msg_result {
                        Ok(Message::Text(text)) => match serde_json::from_str::<ServerMessage>(&text) {
                            Ok(ServerMessage::CommandRequest { correlation_id, action, username, args }) => {
                                println!(
                                    "Received command: {} for user {} (correlation ID: {})",
                                    action,
                                    username,
                                    correlation_id
                                );

                                let (success, message, data) = handle_command(&action, &username, &args).await;
                                let response = ClientMessage::CommandResponse {
                                    correlation_id,
                                    success,
                                    message,
                                    data,
                                };

                                if client_tx.send(response).is_err() {
                                    eprintln!("Writer channel closed while sending command response");
                                    break;
                                }

                                if action == "refresh_installed_apps" && success {
                                    push_inventory_for_user(&inventory_tx, &username);
                                }

                                if action == "unenroll" && success {
                                    device_unenrolled = true;
                                    let _ = shutdown_tx.send(true);
                                    break;
                                }
                            }
                            Ok(ServerMessage::PolicySyncHint { reason }) => {
                                println!(
                                    "Received policy sync hint{}",
                                    reason
                                        .as_deref()
                                        .map(|value| format!(": {}", value))
                                        .unwrap_or_default()
                                );
                                let _ = policy_sync_tx.send(());
                            }
                            Ok(ServerMessage::InstalledAppsReportAck { success, message, .. }) => {
                                if !success {
                                    eprintln!(
                                        "Installed apps report rejected: {}",
                                        message.as_deref().unwrap_or("unknown error")
                                    );
                                }
                            }
                            Ok(other) => {
                                eprintln!("Ignoring unexpected server message after authentication: {:?}", other);
                            }
                            Err(e) => {
                                eprintln!("Failed to parse server message: {}", e);
                            }
                        },
                        Ok(Message::Close(_)) => {
                            println!("Connection closed by server.");
                            break;
                        }
                        Err(e) => {
                            eprintln!("WebSocket stream error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }

                {
                    let mut guard = active_client_tx.lock().unwrap();
                    *guard = None;
                }

                let _ = shutdown_tx.send(true);
                drop(client_tx);
                drop(policy_sync_tx);

                for handle in listener_handles {
                    let _ = handle.await;
                }
                let _ = policy_sync_handle.await;
                let _ = installed_apps_handle.await;
                let _ = writer_handle.await;
            }
            Err(e) => {
                eprintln!("Connection failed: {}. Retrying...", e);
            }
        }

        if device_unenrolled {
            println!("Device unenrolled; stopping agent reconnect loop.");
            return;
        }

        println!("Reconnecting in 5 seconds...");
        sleep(Duration::from_secs(5)).await;
    }
}

#[cfg(target_os = "linux")]
#[tokio::main]
async fn main() {
    run_linux_main().await;
}

#[cfg(target_os = "windows")]
#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|arg| arg == "--user-agent") {
        windows_user_agent::run_user_agent().await;
        return;
    }

    windows_service::run_service().await;
}

#[cfg(test)]
mod tests {
    use super::{
        build_alert_message, is_user_session_class, parse_day_hours, schedule_to_day_limits,
        ClientMessage, ServerMessage,
    };

    #[test]
    fn alert_messages_use_expected_shape() {
        let message = build_alert_message(
            "system_startup",
            Some("alice".to_string()),
            serde_json::json!({"source": "test"}),
        );

        match message {
            ClientMessage::AlertEvent {
                event_type,
                occurred_at,
                linux_username,
                details,
            } => {
                assert_eq!(event_type, "system_startup");
                assert_eq!(linux_username.as_deref(), Some("alice"));
                assert!(occurred_at.ends_with('Z'));
                assert_eq!(details["source"], "test");
            }
            other => panic!("unexpected message: {other:?}"),
        }
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn user_session_class_filter_matches_systemd_user_sessions() {
        assert!(is_user_session_class(Some("user")));
        assert!(is_user_session_class(Some("user-light")));
        assert!(!is_user_session_class(Some("greeter")));
        assert!(!is_user_session_class(None));
    }

    #[test]
    fn schedule_conversion_preserves_all_days() {
        let schedule = serde_json::json!({
            "monday": 2.0,
            "tuesday": 0.0,
            "wednesday": 1.5,
            "thursday": 0.0,
            "friday": 0.0,
            "saturday": 0.0,
            "sunday": 0.25
        });

        let (allowed_days, day_limits) = schedule_to_day_limits(schedule.as_object().unwrap()).unwrap();
        assert_eq!(allowed_days, vec!["1", "3", "7"]);
        assert_eq!(day_limits, vec![7200, 0, 5400, 0, 0, 0, 900]);
    }

    #[test]
    fn installed_apps_report_ack_deserializes() {
        let success_ack = r#"{"type":"installed_apps_report_ack","report_id":"abc","success":true,"apps_upserted":12,"apps_removed":1,"apps_total":12,"pending":false}"#;
        match serde_json::from_str::<ServerMessage>(success_ack).unwrap() {
            ServerMessage::InstalledAppsReportAck {
                report_id,
                success,
                apps_upserted,
                apps_removed,
                apps_total,
                pending,
                message,
            } => {
                assert_eq!(report_id.as_deref(), Some("abc"));
                assert!(success);
                assert_eq!(apps_upserted, Some(12));
                assert_eq!(apps_removed, Some(1));
                assert_eq!(apps_total, Some(12));
                assert!(!pending);
                assert!(message.is_none());
            }
            other => panic!("unexpected message: {other:?}"),
        }

        let failure_ack = r#"{"type":"installed_apps_report_ack","report_id":"abc","success":false,"message":"bad payload"}"#;
        match serde_json::from_str::<ServerMessage>(failure_ack).unwrap() {
            ServerMessage::InstalledAppsReportAck { success, message, .. } => {
                assert!(!success);
                assert_eq!(message.as_deref(), Some("bad payload"));
            }
            other => panic!("unexpected message: {other:?}"),
        }
    }

    #[test]
    fn day_hours_parser_requires_expected_integer_fields() {
        let payload = serde_json::json!({
            "9": {"STARTMIN": 30, "ENDMIN": 60, "UACC": 0},
            "10": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0}
        });

        let parsed = parse_day_hours(&payload, "1").unwrap();
        assert_eq!(parsed["9"]["STARTMIN"], 30);
        assert_eq!(parsed["9"]["ENDMIN"], 60);
        assert_eq!(parsed["10"]["UACC"], 0);
    }
}

#[cfg(target_os = "linux")]
use std::io::Cursor;
#[cfg(target_os = "linux")]
use std::path::Path;
#[cfg(target_os = "linux")]
use std::process::Command;
#[cfg(target_os = "linux")]
use std::sync::{Arc, Mutex};

use base64::Engine;
use chrono::Utc;
use serde::Serialize;
use sha2::{Digest, Sha256};
use uuid::Uuid;

#[cfg(target_os = "linux")]
use image::codecs::jpeg::JpegEncoder;
#[cfg(target_os = "linux")]
use image::{imageops::FilterType, ImageReader};

const MAX_CAPTURE_WIDTH: u32 = 1280;
const JPEG_QUALITY: u8 = 75;

#[derive(Clone, Debug)]
pub struct RecallPolicy {
    pub enabled: bool,
    pub interval_seconds: u64,
}

impl Default for RecallPolicy {
    fn default() -> Self {
        Self {
            enabled: false,
            interval_seconds: 300,
        }
    }
}

pub type SharedRecallPolicy = Arc<Mutex<RecallPolicy>>;

pub fn new_shared_recall_policy() -> SharedRecallPolicy {
    Arc::new(Mutex::new(RecallPolicy::default()))
}

pub fn apply_recall_policy(shared: &SharedRecallPolicy, payload: &serde_json::Value) -> Result<(), String> {
    let policy_value = payload
        .get("recall_policy")
        .ok_or_else(|| "Missing recall_policy argument".to_string())?;

    let enabled = policy_value
        .get("enabled")
        .and_then(|value| value.as_bool())
        .unwrap_or(false);
    let interval_seconds = policy_value
        .get("intervalSeconds")
        .and_then(|value| value.as_u64())
        .unwrap_or(300)
        .clamp(60, 3600);

    let mut guard = shared
        .lock()
        .map_err(|_| "Recall policy lock poisoned".to_string())?;
    guard.enabled = enabled;
    guard.interval_seconds = interval_seconds;
    Ok(())
}

#[derive(Debug)]
pub struct CapturedScreenshot {
    pub linux_username: String,
    pub jpeg_bytes: Vec<u8>,
    pub width: u32,
    pub height: u32,
    pub active_window_title: Option<String>,
}

#[derive(Serialize)]
struct ScreenshotReportMessage<'a> {
    #[serde(rename = "type")]
    message_type: &'static str,
    screenshot_id: String,
    linux_username: &'a str,
    captured_at: String,
    mime_type: &'static str,
    width: u32,
    height: u32,
    content_hash: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    active_window_title: Option<&'a str>,
    data_base64: String,
}

pub fn build_screenshot_report(capture: &CapturedScreenshot) -> Result<String, String> {
    let content_hash = hex::encode(Sha256::digest(&capture.jpeg_bytes));
    let payload = ScreenshotReportMessage {
        message_type: "screenshot_report",
        screenshot_id: Uuid::new_v4().to_string(),
        linux_username: &capture.linux_username,
        captured_at: Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string(),
        mime_type: "image/jpeg",
        width: capture.width,
        height: capture.height,
        content_hash,
        active_window_title: capture.active_window_title.as_deref(),
        data_base64: base64::engine::general_purpose::STANDARD.encode(&capture.jpeg_bytes),
    };
    serde_json::to_string(&payload).map_err(|error| format!("Failed to serialize screenshot report: {error}"))
}

#[cfg(target_os = "linux")]
fn uid_for_username(username: &str) -> Option<u32> {
    users::get_user_by_name(username).map(|user| user.uid())
}

#[cfg(target_os = "linux")]
fn run_as_user(username: &str, uid: u32, env_pairs: &[(&str, &str)], program: &str, args: &[&str]) -> Option<std::process::Output> {
    let runtime_dir = format!("/run/user/{uid}");
    if !Path::new(&runtime_dir).exists() {
        return None;
    }

    let mut command = Command::new("runuser");
    command.arg("-u").arg(username).arg("--").arg("env");
    command.arg(format!("XDG_RUNTIME_DIR={runtime_dir}"));
    for (key, value) in env_pairs {
        command.arg(format!("{key}={value}"));
    }
    command.arg(program);
    command.args(args);
    command.output().ok()
}

#[cfg(target_os = "linux")]
fn capture_raw_image(username: &str, uid: u32) -> Option<Vec<u8>> {
    for wayland_display in ["wayland-0", "wayland-1"] {
        if let Some(output) = run_as_user(
            username,
            uid,
            &[("WAYLAND_DISPLAY", wayland_display)],
            "grim",
            &["-"],
        ) {
            if output.status.success() && !output.stdout.is_empty() {
                return Some(output.stdout);
            }
        }
    }

    for display in [":0", ":1"] {
        if let Some(output) = run_as_user(username, uid, &[("DISPLAY", display)], "grim", &["-"]) {
            if output.status.success() && !output.stdout.is_empty() {
                return Some(output.stdout);
            }
        }
        if let Some(output) = run_as_user(username, uid, &[("DISPLAY", display)], "scrot", &["-p", "-"]) {
            if output.status.success() && !output.stdout.is_empty() {
                return Some(output.stdout);
            }
        }
        if let Some(output) = run_as_user(
            username,
            uid,
            &[("DISPLAY", display)],
            "import",
            &["-window", "root", "png:-"],
        ) {
            if output.status.success() && !output.stdout.is_empty() {
                return Some(output.stdout);
            }
        }
    }

    None
}

#[cfg(target_os = "linux")]
fn active_window_title(username: &str, uid: u32) -> Option<String> {
    for display in [":0", ":1"] {
        let output = run_as_user(
            username,
            uid,
            &[("DISPLAY", display)],
            "xdotool",
            &["getactivewindow", "getwindowname"],
        )?;
        if !output.status.success() {
            continue;
        }
        let title = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if !title.is_empty() {
            return Some(title);
        }
    }
    None
}

#[cfg(target_os = "linux")]
fn encode_jpeg(image_bytes: &[u8]) -> Option<(Vec<u8>, u32, u32)> {
    let image = ImageReader::new(Cursor::new(image_bytes))
        .with_guessed_format()
        .ok()?
        .decode()
        .ok()?;

    let (width, height) = (image.width(), image.height());
    let resized = if width > MAX_CAPTURE_WIDTH {
        let new_height = ((height as f64) * (MAX_CAPTURE_WIDTH as f64) / (width as f64)).round() as u32;
        image::imageops::resize(&image, MAX_CAPTURE_WIDTH, new_height.max(1), FilterType::Triangle)
    } else {
        image.to_rgba8()
    };

    let mut buffer = Vec::new();
    let mut encoder = JpegEncoder::new_with_quality(&mut buffer, JPEG_QUALITY);
    encoder.encode_image(&resized).ok()?;
    Some((buffer, resized.width(), resized.height()))
}

#[cfg(target_os = "linux")]
fn discover_active_usernames() -> Vec<String> {
    let output = match Command::new("loginctl").args(["list-sessions", "--no-legend"]).output() {
        Ok(output) if output.status.success() => output,
        _ => return Vec::new(),
    };

    let mut usernames = Vec::new();
    let session_text = String::from_utf8_lossy(&output.stdout);
    for line in session_text.lines() {
        let mut parts = line.split_whitespace();
        let session_id = match parts.next() {
            Some(value) => value,
            None => continue,
        };
        let username = match parts.nth(2) {
            Some(value) => value.to_string(),
            None => continue,
        };
        if username == "root" || usernames.iter().any(|existing| existing == &username) {
            continue;
        }
        let detail = Command::new("loginctl")
            .args(["show-session", session_id, "-p", "Type", "-p", "State", "-p", "Class"])
            .output();
        if let Ok(detail) = detail {
            let detail_text = String::from_utf8_lossy(&detail.stdout).to_lowercase();
            let is_graphical = detail_text.contains("type=x11")
                || detail_text.contains("type=wayland")
                || detail_text.contains("class=user");
            let is_active = detail_text.contains("state=active") || detail_text.contains("state=online");
            if is_graphical && is_active {
                usernames.push(username);
            }
        }
    }
    usernames
}

#[cfg(target_os = "linux")]
fn capture_for_username(username: &str) -> Option<CapturedScreenshot> {
    let uid = uid_for_username(username)?;
    let raw = capture_raw_image(username, uid)?;
    let (jpeg_bytes, width, height) = encode_jpeg(&raw)?;
    Some(CapturedScreenshot {
        linux_username: username.to_string(),
        jpeg_bytes,
        width,
        height,
        active_window_title: active_window_title(username, uid),
    })
}

#[cfg(target_os = "linux")]
pub fn capture_screenshots(linux_username: Option<&str>) -> Vec<CapturedScreenshot> {
    let targets = if let Some(username) = linux_username {
        vec![username.to_string()]
    } else {
        discover_active_usernames()
    };

    let mut captures = Vec::new();
    for username in targets {
        if let Some(capture) = capture_for_username(&username) {
            captures.push(capture);
        }
    }
    captures
}

#[cfg(not(target_os = "linux"))]
pub fn capture_screenshots(_linux_username: Option<&str>) -> Vec<CapturedScreenshot> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apply_recall_policy_updates_shared_state() {
        let shared = new_shared_recall_policy();
        let payload = serde_json::json!({
            "recall_policy": {
                "enabled": true,
                "intervalSeconds": 120
            }
        });
        apply_recall_policy(&shared, &payload).expect("policy should apply");
        let guard = shared.lock().unwrap();
        assert!(guard.enabled);
        assert_eq!(guard.interval_seconds, 120);
    }
}

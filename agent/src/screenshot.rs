use std::io::Cursor;
use std::sync::{Arc, Mutex};

use base64::Engine;
use chrono::Utc;
use image::codecs::jpeg::JpegEncoder;
use image::{imageops::FilterType, ImageBuffer, ImageReader, Rgba};
use serde::Serialize;
use sha2::{Digest, Sha256};
use uuid::Uuid;

#[cfg(target_os = "linux")]
use std::path::Path;
#[cfg(target_os = "linux")]
use std::process::Command;

const MAX_CAPTURE_WIDTH: u32 = 1280;
const JPEG_QUALITY: u8 = 75;

#[derive(Clone, Debug)]
pub struct ScreenshotPolicy {
    pub enabled: bool,
    pub interval_seconds: u64,
}

impl Default for ScreenshotPolicy {
    fn default() -> Self {
        Self {
            enabled: false,
            interval_seconds: 300,
        }
    }
}

pub type SharedScreenshotPolicy = Arc<Mutex<ScreenshotPolicy>>;

pub fn new_shared_screenshot_policy() -> SharedScreenshotPolicy {
    Arc::new(Mutex::new(ScreenshotPolicy::default()))
}

pub fn apply_screenshot_policy(shared: &SharedScreenshotPolicy, payload: &serde_json::Value) -> Result<(), String> {
    let policy_value = payload
        .get("screenshot_policy")
        .ok_or_else(|| "Missing screenshot_policy argument".to_string())?;

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
        .map_err(|_| "Screenshot policy lock poisoned".to_string())?;
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

fn encode_rgba_buffer(rgba: ImageBuffer<Rgba<u8>, Vec<u8>>) -> Option<(Vec<u8>, u32, u32)> {
    let (width, height) = (rgba.width(), rgba.height());
    let resized = if width > MAX_CAPTURE_WIDTH {
        let new_height = ((height as f64) * (MAX_CAPTURE_WIDTH as f64) / (width as f64)).round() as u32;
        image::imageops::resize(&rgba, MAX_CAPTURE_WIDTH, new_height.max(1), FilterType::Triangle)
    } else {
        rgba
    };

    let mut buffer = Vec::new();
    let mut encoder = JpegEncoder::new_with_quality(&mut buffer, JPEG_QUALITY);
    encoder.encode_image(&resized).ok()?;
    Some((buffer, resized.width(), resized.height()))
}

fn encode_image_bytes(image_bytes: &[u8]) -> Option<(Vec<u8>, u32, u32)> {
    let image = ImageReader::new(Cursor::new(image_bytes))
        .with_guessed_format()
        .ok()?
        .decode()
        .ok()?;
    encode_rgba_buffer(image.to_rgba8())
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
    let (jpeg_bytes, width, height) = encode_image_bytes(&raw)?;
    Some(CapturedScreenshot {
        linux_username: username.to_string(),
        jpeg_bytes,
        width,
        height,
        active_window_title: active_window_title(username, uid),
    })
}

#[cfg(target_os = "windows")]
mod win32 {
    use super::{encode_rgba_buffer, CapturedScreenshot};
    use image::{ImageBuffer, Rgba};
    use std::mem::size_of;
    use std::ptr::null_mut;
    use windows_sys::Win32::Foundation::{CloseHandle, HWND, HANDLE};
    use windows_sys::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC, GetDIBits,
        ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, RGBQUAD,
        SRCCOPY,
    };
    use windows_sys::Win32::Security::{ImpersonateLoggedOnUser, RevertToSelf};
    use windows_sys::Win32::System::RemoteDesktop::{
        WTSActive, WTSConnected, WTSFreeMemory, WTSEnumerateSessionsW, WTSQuerySessionInformationW,
        WTSQueryUserToken, WTSUserName, WTS_CURRENT_SERVER_HANDLE, WTS_SESSION_INFOW,
    };
    use windows_sys::Win32::System::StationsAndDesktops::{
        CloseDesktop, OpenInputDesktop, SetThreadDesktop, DESKTOP_READOBJECTS,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetDesktopWindow, GetForegroundWindow, GetSystemMetrics, GetWindowTextW, SM_CXSCREEN,
        SM_CYSCREEN,
    };

    fn handle_is_valid(handle: HANDLE) -> bool {
        handle != 0
    }

    unsafe fn read_wide_string(ptr: *const u16) -> String {
        if ptr.is_null() {
            return String::new();
        }
        let mut len = 0;
        while unsafe { *ptr.add(len) } != 0 {
            len += 1;
        }
        let slice = unsafe { std::slice::from_raw_parts(ptr, len) };
        String::from_utf16_lossy(slice)
    }

    fn session_username(session_id: u32) -> Option<String> {
        unsafe {
            let mut buffer: *mut u16 = null_mut();
            let mut bytes_returned = 0;
            let success = WTSQuerySessionInformationW(
                WTS_CURRENT_SERVER_HANDLE,
                session_id,
                WTSUserName,
                &mut buffer,
                &mut bytes_returned,
            );
            if success == 0 || buffer.is_null() {
                return None;
            }
            let username = read_wide_string(buffer);
            WTSFreeMemory(buffer as *mut _);
            if username.is_empty() {
                None
            } else {
                Some(username)
            }
        }
    }

    fn session_id_for_username(target: &str) -> Option<u32> {
        unsafe {
            let mut session_info: *mut WTS_SESSION_INFOW = null_mut();
            let mut count = 0;
            if WTSEnumerateSessionsW(WTS_CURRENT_SERVER_HANDLE, 0, 1, &mut session_info, &mut count) == 0
            {
                return None;
            }

            let mut matched = None;
            for index in 0..count {
                let session = &*session_info.add(index as usize);
                if session.State != WTSActive && session.State != WTSConnected {
                    continue;
                }
                if let Some(username) = session_username(session.SessionId) {
                    if username.eq_ignore_ascii_case(target) {
                        matched = Some(session.SessionId);
                        break;
                    }
                }
            }

            if !session_info.is_null() {
                WTSFreeMemory(session_info as *mut _);
            }
            matched
        }
    }

    unsafe fn active_window_title() -> Option<String> {
        let hwnd: HWND = GetForegroundWindow();
        if !handle_is_valid(hwnd) {
            return None;
        }
        let mut buffer = [0u16; 512];
        let length = GetWindowTextW(hwnd, buffer.as_mut_ptr(), buffer.len() as i32);
        if length <= 0 {
            return None;
        }
        let title = String::from_utf16_lossy(&buffer[..length as usize]);
        if title.is_empty() { None } else { Some(title) }
    }

    unsafe fn capture_desktop_bitmap() -> Option<(Vec<u8>, u32, u32)> {
        let desktop = OpenInputDesktop(0, 0, DESKTOP_READOBJECTS);
        if !handle_is_valid(desktop) {
            return None;
        }
        if SetThreadDesktop(desktop) == 0 {
            CloseDesktop(desktop);
            return None;
        }

        let hwnd = GetDesktopWindow();
        let screen_dc = GetDC(hwnd);
        if !handle_is_valid(screen_dc) {
            CloseDesktop(desktop);
            return None;
        }

        let width = GetSystemMetrics(SM_CXSCREEN).max(1) as u32;
        let height = GetSystemMetrics(SM_CYSCREEN).max(1) as u32;
        let mem_dc = CreateCompatibleDC(screen_dc);
        if !handle_is_valid(mem_dc) {
            ReleaseDC(hwnd, screen_dc);
            CloseDesktop(desktop);
            return None;
        }

        let bitmap = CreateCompatibleBitmap(screen_dc, width as i32, height as i32);
        if !handle_is_valid(bitmap) {
            DeleteDC(mem_dc);
            ReleaseDC(hwnd, screen_dc);
            CloseDesktop(desktop);
            return None;
        }

        let old_bitmap = SelectObject(mem_dc, bitmap);
        BitBlt(
            mem_dc,
            0,
            0,
            width as i32,
            height as i32,
            screen_dc,
            0,
            0,
            SRCCOPY,
        );

        let mut bitmap_info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: width as i32,
                biHeight: -(height as i32),
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB,
                biSizeImage: 0,
                biXPelsPerMeter: 0,
                biYPelsPerMeter: 0,
                biClrUsed: 0,
                biClrImportant: 0,
            },
            bmiColors: [RGBQUAD {
                rgbBlue: 0,
                rgbGreen: 0,
                rgbRed: 0,
                rgbReserved: 0,
            }],
        };

        let mut pixels = vec![0u8; (width * height * 4) as usize];
        let copied = GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            pixels.as_mut_ptr() as *mut _,
            &mut bitmap_info,
            DIB_RGB_COLORS,
        );

        SelectObject(mem_dc, old_bitmap);
        DeleteObject(bitmap);
        DeleteDC(mem_dc);
        ReleaseDC(hwnd, screen_dc);
        CloseDesktop(desktop);

        if copied == 0 {
            return None;
        }

        let mut rgba = Vec::with_capacity(pixels.len());
        for chunk in pixels.chunks_exact(4) {
            rgba.extend_from_slice(&[chunk[2], chunk[1], chunk[0], chunk[3]]);
        }

        let image = ImageBuffer::<Rgba<u8>, Vec<u8>>::from_raw(width, height, rgba)?;
        encode_rgba_buffer(image)
    }

    fn capture_for_session(session_id: u32, username: &str) -> Option<CapturedScreenshot> {
        unsafe {
            let mut token: HANDLE = 0;
            if WTSQueryUserToken(session_id, &mut token) == 0 {
                return None;
            }

            if ImpersonateLoggedOnUser(token) == 0 {
                CloseHandle(token);
                return None;
            }

            let capture_result = (|| {
                let (jpeg_bytes, width, height) = capture_desktop_bitmap()?;
                let active_window_title = active_window_title();
                Some(CapturedScreenshot {
                    linux_username: username.to_string(),
                    jpeg_bytes,
                    width,
                    height,
                    active_window_title,
                })
            })();

            RevertToSelf();
            CloseHandle(token);
            capture_result
        }
    }

    pub fn capture_for_username(username: &str) -> Option<CapturedScreenshot> {
        let session_id = session_id_for_username(username)?;
        capture_for_session(session_id, username)
    }

    pub fn capture_screenshots(target_username: Option<&str>) -> Vec<CapturedScreenshot> {
        if let Some(username) = target_username {
            return capture_for_username(username).into_iter().collect();
        }

        let mut captures = Vec::new();
        unsafe {
            let mut session_info: *mut WTS_SESSION_INFOW = null_mut();
            let mut count = 0;
            if WTSEnumerateSessionsW(WTS_CURRENT_SERVER_HANDLE, 0, 1, &mut session_info, &mut count) == 0
            {
                return captures;
            }

            for index in 0..count {
                let session = &*session_info.add(index as usize);
                if session.State != WTSActive && session.State != WTSConnected {
                    continue;
                }
                let Some(username) = session_username(session.SessionId) else {
                    continue;
                };
                if captures
                    .iter()
                    .any(|capture| capture.linux_username.eq_ignore_ascii_case(&username))
                {
                    continue;
                }
                if let Some(capture) = capture_for_session(session.SessionId, &username) {
                    captures.push(capture);
                }
            }

            if !session_info.is_null() {
                WTSFreeMemory(session_info as *mut _);
            }
        }
        captures
    }
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

#[cfg(target_os = "windows")]
pub fn capture_screenshots(linux_username: Option<&str>) -> Vec<CapturedScreenshot> {
    win32::capture_screenshots(linux_username)
}

#[cfg(not(any(target_os = "linux", target_os = "windows")))]
pub fn capture_screenshots(_linux_username: Option<&str>) -> Vec<CapturedScreenshot> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apply_screenshot_policy_updates_shared_state() {
        let shared = new_shared_screenshot_policy();
        let payload = serde_json::json!({
            "screenshot_policy": {
                "enabled": true,
                "intervalSeconds": 120
            }
        });
        apply_screenshot_policy(&shared, &payload).expect("policy should apply");
        let guard = shared.lock().unwrap();
        assert!(guard.enabled);
        assert_eq!(guard.interval_seconds, 120);
    }
}

/// Guardian Space overlay launcher for Linux.
///
/// When the server sends a `show_overlay` command, this module spawns the
/// `guardian-overlay-helper` child process which opens `blockedv2.html` in a
/// kiosk-style browser window for the managed user.  A static mutex keeps
/// track of the child so `dismiss_overlay` can kill it cleanly.
///
/// The helper binary uses CEF (Chromium Embedded Framework) for a kiosk overlay.
/// Build with: `cargo build --bin guardian-overlay-helper --features cef-overlay`
use std::process::{Child, Command};
use std::sync::Mutex;

static OVERLAY_CHILD: Mutex<Option<Child>> = Mutex::new(None);

/// Simple percent-encoder for URL query parameter values.
fn url_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for byte in s.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char);
            }
            b' ' => out.push_str("%20"),
            b => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
}

/// Show the Guardian Space overlay for the given managed user.
///
/// Reads `reason`, `overlay_age_tier`, `overlay_parent_note`, and
/// `device_name` from `args` (all optional with safe defaults) and spawns
/// `guardian-overlay-helper <url> <username>`.
pub fn show(args: &serde_json::Value, username: &str) -> Result<String, String> {
    let reason = args
        .get("reason")
        .and_then(|v| v.as_str())
        .unwrap_or("sleep");
    let age_tier = args
        .get("overlay_age_tier")
        .or_else(|| args.get("age_tier"))
        .and_then(|v| v.as_str())
        .unwrap_or("eight12");
    let parent_note = args
        .get("overlay_parent_note")
        .or_else(|| args.get("parent_note"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let device_name = args
        .get("device_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let lang = args
        .get("lang")
        .or_else(|| args.get("locale"))
        .and_then(|v| v.as_str())
        .unwrap_or("en");

    let url = format!(
        "file:///usr/share/guardian-agent/blockedv2.html?reason={}&age={}&device={}&note={}&lang={}",
        url_encode(reason),
        url_encode(age_tier),
        url_encode(device_name),
        url_encode(parent_note),
        url_encode(lang),
    );

    // Kill any existing overlay before launching a new one
    dismiss();

    let child = Command::new("guardian-overlay-helper")
        .arg(&url)
        .arg(username)
        .spawn()
        .map_err(|e| format!("Failed to spawn guardian-overlay-helper: {}", e))?;

    let mut guard = OVERLAY_CHILD.lock().unwrap();
    *guard = Some(child);

    Ok(format!(
        "Guardian Space overlay shown for {} (reason={})",
        username, reason
    ))
}

/// Dismiss the active Guardian Space overlay by terminating the helper process.
pub fn dismiss() {
    let mut guard = OVERLAY_CHILD.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

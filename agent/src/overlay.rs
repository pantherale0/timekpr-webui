/// Guardian Space overlay launcher for Linux.
///
/// When the server sends a `show_overlay` command, this module spawns the
/// `guardian-overlay-helper` child process which opens `blockedv2.html` in a
/// kiosk-style browser window for the managed user.  A static mutex keeps
/// track of the child so `dismiss_overlay` can kill it cleanly.
///
/// The helper binary uses CEF (Chromium Embedded Framework) for a kiosk overlay.
/// Build with: `cargo build --bin guardian-overlay-helper --features cef-overlay`
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

const HELPER_NAME: &str = "guardian-overlay-helper";

/// Locate the `guardian-overlay-helper` binary.
///
/// Preference order:
/// 1. Same directory as the running `guardian-agent` executable (the standard
///    install layout where both binaries sit in `/usr/local/bin`).
/// 2. Bare name — resolved via `$PATH` as a fallback for non-standard setups.
///
/// Returns `None` when the companion binary is definitely not present so the
/// caller can emit a clear "not installed" message instead of an OS error.
fn find_helper() -> Option<PathBuf> {
    // Try sibling of the running executable first.
    if let Ok(mut exe) = std::env::current_exe() {
        exe.pop();
        exe.push(HELPER_NAME);
        if exe.is_file() {
            return Some(exe);
        }
    }

    // Fall back to PATH lookup.
    if let Ok(path_var) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path_var) {
            let candidate = dir.join(HELPER_NAME);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }

    None
}

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

    let helper_path = find_helper().ok_or_else(|| {
        format!(
            "{HELPER_NAME} is not installed. \
             Install the CEF overlay helper alongside the guardian-agent binary \
             (e.g. /usr/local/bin/{HELPER_NAME}) or build it with: \
             cargo build --release --bin {HELPER_NAME} --features cef-overlay"
        )
    })?;

    let child = Command::new(&helper_path)
        .arg(&url)
        .arg(username)
        .spawn()
        .map_err(|e| format!("Failed to spawn {}: {}", helper_path.display(), e))?;

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

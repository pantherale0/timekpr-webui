//! Windows overlay control via IPC to the user-session agent (WebView2 / Edge app mode).

use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};

static OVERLAY_VISIBLE: AtomicBool = AtomicBool::new(false);

pub fn show(args: &Value) {
    let reason = args
        .get("reason")
        .and_then(|v| v.as_str())
        .unwrap_or("clock_tamper");
    let username = args
        .get("linux_username")
        .and_then(|v| v.as_str())
        .unwrap_or("child");
    let device_name = args
        .get("device_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
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

    let payload = serde_json::json!({
        "type": "overlay",
        "action": "show",
        "reason": reason,
        "linux_username": username,
        "device_name": device_name,
        "age_tier": age_tier,
        "parent_note": parent_note,
    });
    crate::windows_service::ipc::broadcast_json(&payload);
    OVERLAY_VISIBLE.store(true, Ordering::SeqCst);
}

pub fn dismiss() {
    if !OVERLAY_VISIBLE.swap(false, Ordering::SeqCst) {
        return;
    }
    let payload = serde_json::json!({
        "type": "overlay",
        "action": "dismiss",
    });
    crate::windows_service::ipc::broadcast_json(&payload);
}

pub fn is_visible() -> bool {
    OVERLAY_VISIBLE.load(Ordering::SeqCst)
}

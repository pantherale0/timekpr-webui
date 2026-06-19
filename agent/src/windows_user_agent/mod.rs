#[cfg(target_os = "windows")]
use std::process::{Child, Command};
#[cfg(target_os = "windows")]
use std::sync::Mutex;
#[cfg(target_os = "windows")]
use tokio::io::AsyncBufReadExt;
#[cfg(target_os = "windows")]
use tokio::net::windows::named_pipe::ClientOptions;

#[cfg(target_os = "windows")]
const OVERLAY_HTML_PATH: &str = r"C:\Program Files\Guardian\blockedv2.html";

#[cfg(target_os = "windows")]
static OVERLAY_CHILD: Mutex<Option<Child>> = Mutex::new(None);

#[cfg(target_os = "windows")]
pub async fn run_user_agent() {
    println!("Starting TimeKpr User Session Agent...");
    let pipe_name = r"\\.\pipe\timekpr_ipc";

    loop {
        println!("UserAgent: Connecting to Named Pipe service...");
        match ClientOptions::new().open(pipe_name) {
            Ok(client) => {
                println!("UserAgent: Connected to service!");
                let reader = tokio::io::BufReader::new(client);
                let mut lines = reader.lines();

                while let Ok(Some(line)) = lines.next_line().await {
                    if let Ok(payload) = serde_json::from_str::<serde_json::Value>(&line) {
                        match payload["type"].as_str() {
                            Some("toast") => {
                                let fallback_title = crate::i18n::t("notification_fallback_title");
                                let title = payload["title"].as_str().unwrap_or(&fallback_title);
                                let message = payload["message"].as_str().unwrap_or("");
                                show_toast_notification(title, message);
                            }
                            Some("overlay") => match payload["action"].as_str() {
                                Some("show") => show_overlay(&payload),
                                Some("dismiss") => dismiss_overlay(),
                                _ => {}
                            },
                            _ => {}
                        }
                    }
                }
            }
            Err(e) => {
                eprintln!("UserAgent: Failed to connect to Named Pipe: {}. Retrying...", e);
            }
        }

        tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
    }
}

#[cfg(target_os = "windows")]
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

#[cfg(target_os = "windows")]
fn overlay_file_url(payload: &serde_json::Value) -> String {
    let reason = payload
        .get("reason")
        .and_then(|v| v.as_str())
        .unwrap_or("clock_tamper");
    let age_tier = payload
        .get("age_tier")
        .and_then(|v| v.as_str())
        .unwrap_or("eight12");
    let device_name = payload
        .get("device_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let parent_note = payload
        .get("parent_note")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let lang = payload
        .get("lang")
        .and_then(|v| v.as_str())
        .unwrap_or("en");

    let path = OVERLAY_HTML_PATH.replace('\\', "/");
    let encoded_path = url_encode(&path);
    format!(
        "file:///{encoded_path}?reason={}&age={}&device={}&note={}&lang={}",
        url_encode(reason),
        url_encode(age_tier),
        url_encode(device_name),
        url_encode(parent_note),
        url_encode(lang),
    )
}

#[cfg(target_os = "windows")]
fn find_edge_executable() -> Option<String> {
    let candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ];
    for candidate in candidates {
        if std::path::Path::new(candidate).is_file() {
            return Some(candidate.to_string());
        }
    }
    None
}

#[cfg(target_os = "windows")]
fn show_overlay(payload: &serde_json::Value) {
    dismiss_overlay();

    let edge = match find_edge_executable() {
        Some(path) => path,
        None => {
            eprintln!("UserAgent: Microsoft Edge not found; overlay unavailable");
            return;
        }
    };

    let url = overlay_file_url(payload);
    println!("UserAgent: Showing overlay at {}", url);

    match Command::new(&edge)
        .args([
            &format!("--app={url}"),
            "--kiosk",
            "--edge-kiosk-type=fullscreen",
            "--no-first-run",
            "--disable-features=msEdgeSidebarV2",
        ])
        .spawn()
    {
        Ok(child) => {
            let mut guard = OVERLAY_CHILD.lock().unwrap();
            *guard = Some(child);
        }
        Err(error) => {
            eprintln!("UserAgent: Failed to launch overlay browser: {}", error);
        }
    }
}

#[cfg(target_os = "windows")]
fn dismiss_overlay() {
    let mut guard = OVERLAY_CHILD.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg(target_os = "windows")]
fn show_toast_notification(title: &str, message: &str) {
    println!("Showing Toast: {} - {}", title, message);

    let clean_title = title.replace('\'', "\"");
    let clean_message = message.replace('\'', "\"");

    let ps_script = format!(
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; \
         $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); \
         $toastXml = [xml]$template.GetXml(); \
         $toastXml.toast.visual.binding.text[0].AppendChild($toastXml.CreateTextNode('{}')) | Out-Null; \
         $toastXml.toast.visual.binding.text[1].AppendChild($toastXml.CreateTextNode('{}')) | Out-Null; \
         $xml = New-Object Windows.Data.Xml.Dom.XmlDocument; \
         $xml.LoadXml($toastXml.OuterXml); \
         $toast = [Windows.UI.Notifications.ToastNotification]::new($xml); \
         [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('TimeKpr').Show($toast)",
        clean_title, clean_message
    );

    let _ = Command::new("powershell")
        .args(["-Command", &ps_script])
        .spawn();
}

#[cfg(not(target_os = "windows"))]
pub async fn run_user_agent() {}

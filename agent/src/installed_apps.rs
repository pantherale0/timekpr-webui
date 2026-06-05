use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use base64::Engine;
use serde::Serialize;
use sha2::{Digest, Sha256};

pub const CHUNK_SIZE: usize = 100;
pub const MATCH_TYPE_EXECUTABLE: &str = "executable";
const ICON_MAX_BYTES: usize = 32 * 1024;

#[derive(Debug, Clone, Serialize)]
pub struct DiscoveredApp {
    pub application_name: String,
    pub identifier: String,
    pub match_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub icon_hash: Option<String>,
    #[serde(skip)]
    pub icon_png: Option<Vec<u8>>,
}

#[derive(Serialize)]
struct InstalledAppsReportMessage<'a> {
    #[serde(rename = "type")]
    message_type: &'static str,
    report_id: &'a str,
    linux_username: &'a str,
    chunk_index: usize,
    chunk_total: usize,
    is_final: bool,
    reported_at: String,
    apps: Vec<DiscoveredAppPayload<'a>>,
}

#[derive(Serialize)]
struct DiscoveredAppPayload<'a> {
    application_name: &'a str,
    identifier: &'a str,
    match_type: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    version_name: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    icon_hash: Option<&'a str>,
}

#[derive(Serialize)]
struct AppIconReportMessage<'a> {
    #[serde(rename = "type")]
    message_type: &'static str,
    content_hash: &'a str,
    mime_type: &'a str,
    data_base64: String,
}

pub fn discover_for_user(linux_username: &str) -> Vec<DiscoveredApp> {
    let mut desktop_dirs = vec![
        PathBuf::from("/usr/share/applications"),
        PathBuf::from("/usr/local/share/applications"),
        PathBuf::from("/var/lib/flatpak/exports/share/applications"),
        PathBuf::from("/var/lib/snapd/desktop/applications"),
    ];

    if let Some(home) = users::get_user_by_name(linux_username).and_then(|user| user.home_dir().map(Path::to_path_buf)) {
        desktop_dirs.push(home.join(".local/share/applications"));
        desktop_dirs.push(home.join(".local/share/flatpak/exports/share/applications"));
    }

    let mut by_identifier: HashMap<String, DiscoveredApp> = HashMap::new();
    for dir in desktop_dirs {
        if !dir.is_dir() {
            continue;
        }
        let entries = match fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|ext| ext.to_str()) != Some("desktop") {
                continue;
            }
            if let Some(app) = parse_desktop_file(&path) {
                by_identifier.insert(app.identifier.clone(), app);
            }
        }
    }

    let mut apps: Vec<DiscoveredApp> = by_identifier.into_values().collect();
    apps.sort_by(|left, right| left.application_name.to_lowercase().cmp(&right.application_name.to_lowercase()));
    apps
}

pub fn parse_desktop_file(path: &Path) -> Option<DiscoveredApp> {
    let contents = fs::read_to_string(path).ok()?;
    let mut in_desktop_entry = false;
    let mut name: Option<String> = None;
    let mut exec: Option<String> = None;
    let mut icon: Option<String> = None;
    let mut version: Option<String> = None;
    let mut hidden = false;
    let mut no_display = false;

    for line in contents.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if trimmed == "[Desktop Entry]" {
            in_desktop_entry = true;
            continue;
        }
        if trimmed.starts_with('[') {
            if in_desktop_entry {
                break;
            }
            continue;
        }
        if !in_desktop_entry {
            continue;
        }

        let Some((key, value)) = trimmed.split_once('=') else {
            continue;
        };
        let key = key.trim();
        let value = value.trim();
        match key {
            "Name" if name.is_none() => name = Some(value.to_string()),
            "Exec" if exec.is_none() => exec = Some(value.to_string()),
            "Icon" if icon.is_none() => icon = Some(value.to_string()),
            "Version" if version.is_none() => version = Some(value.to_string()),
            "Hidden" => hidden = parse_bool(value),
            "NoDisplay" => no_display = parse_bool(value),
            _ => {}
        }
    }

    if hidden || no_display {
        return None;
    }

    let application_name = name?;
    let exec_line = exec?;
    let identifier = normalize_exec_to_path(&exec_line)?;
    let icon_png = icon.as_deref().and_then(resolve_icon_png);
    let icon_hash = icon_png.as_ref().map(|bytes| sha256_hex(bytes));

    Some(DiscoveredApp {
        application_name,
        identifier,
        match_type: MATCH_TYPE_EXECUTABLE.to_string(),
        version_name: version,
        icon_hash,
        icon_png,
    })
}

fn parse_bool(value: &str) -> bool {
    matches!(value.trim().to_ascii_lowercase().as_str(), "true" | "1" | "yes")
}

pub fn normalize_exec_to_path(exec_line: &str) -> Option<String> {
    for token in exec_line.split_whitespace() {
        if token.starts_with('/') && !token.contains('%') {
            return Some(token.to_string());
        }
    }
    None
}

fn resolve_icon_png(icon_name: &str) -> Option<Vec<u8>> {
    let trimmed = icon_name.trim();
    if trimmed.is_empty() {
        return None;
    }

    let candidates = if trimmed.starts_with('/') {
        vec![PathBuf::from(trimmed)]
    } else {
        vec![
            PathBuf::from(format!("/usr/share/pixmaps/{}.png", trimmed)),
            PathBuf::from(format!("/usr/share/pixmaps/{}.svg", trimmed)),
            PathBuf::from(format!("/usr/share/icons/hicolor/48x48/apps/{}.png", trimmed)),
            PathBuf::from(format!("/usr/share/icons/hicolor/64x64/apps/{}.png", trimmed)),
            PathBuf::from(format!("/usr/share/icons/Adwaita/48x48/apps/{}.png", trimmed)),
        ]
    };

    for candidate in candidates {
        if candidate.extension().and_then(|ext| ext.to_str()) != Some("png") {
            continue;
        }
        if !candidate.is_file() {
            continue;
        }
        let bytes = fs::read(&candidate).ok()?;
        if bytes.len() > ICON_MAX_BYTES {
            continue;
        }
        return Some(bytes);
    }
    None
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    hex::encode(digest)
}

pub fn build_inventory_messages(linux_username: &str, apps: &[DiscoveredApp], report_id: &str) -> Vec<String> {
    let mut messages = Vec::new();
    let mut sent_hashes = HashSet::new();
    let chunks: Vec<&[DiscoveredApp]> = if apps.is_empty() {
        vec![&[]]
    } else {
        apps.chunks(CHUNK_SIZE).collect()
    };

    for chunk in chunks.iter() {
        for app in chunk.iter() {
            if let (Some(hash), Some(bytes)) = (&app.icon_hash, &app.icon_png) {
                if sent_hashes.insert(hash.clone()) {
                    let payload = AppIconReportMessage {
                        message_type: "app_icon_report",
                        content_hash: hash,
                        mime_type: "image/png",
                        data_base64: base64::engine::general_purpose::STANDARD.encode(bytes),
                    };
                    if let Ok(serialized) = serde_json::to_string(&payload) {
                        messages.push(serialized);
                    }
                }
            }
        }
    }

    let reported_at = chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string();
    for (index, chunk) in chunks.iter().enumerate() {
        let payload = InstalledAppsReportMessage {
            message_type: "installed_apps_report",
            report_id,
            linux_username,
            chunk_index: index,
            chunk_total: chunks.len(),
            is_final: index + 1 == chunks.len(),
            reported_at: reported_at.clone(),
            apps: chunk
                .iter()
                .map(|app| DiscoveredAppPayload {
                    application_name: &app.application_name,
                    identifier: &app.identifier,
                    match_type: &app.match_type,
                    version_name: app.version_name.as_deref(),
                    icon_hash: app.icon_hash.as_deref(),
                })
                .collect(),
        };
        if let Ok(serialized) = serde_json::to_string(&payload) {
            messages.push(serialized);
        }
    }

    messages
}
mod tests {
    use super::*;

    #[test]
    fn normalize_exec_strips_field_codes() {
        assert_eq!(
            normalize_exec_to_path("/usr/bin/firefox %u"),
            Some("/usr/bin/firefox".to_string())
        );
        assert_eq!(
            normalize_exec_to_path("env VAR=1 /opt/app/bin/demo --flag"),
            Some("/opt/app/bin/demo".to_string())
        );
        assert_eq!(normalize_exec_to_path("firefox %u"), None);
    }

    #[test]
    fn parse_desktop_file_skips_hidden_entries() {
        let path = std::env::temp_dir().join(format!("timekpr-hidden-{}.desktop", std::process::id()));
        fs::write(
            &path,
            "[Desktop Entry]\nName=Hidden App\nExec=/usr/bin/hidden\nHidden=true\n",
        )
        .unwrap();
        assert!(parse_desktop_file(&path).is_none());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn parse_desktop_file_extracts_metadata() {
        let path = std::env::temp_dir().join(format!("timekpr-demo-{}.desktop", std::process::id()));
        fs::write(
            &path,
            "[Desktop Entry]\nName=Demo App\nExec=/usr/bin/demo\nVersion=1.2.3\nIcon=demo\n",
        )
        .unwrap();
        let app = parse_desktop_file(&path).unwrap();
        assert_eq!(app.application_name, "Demo App");
        assert_eq!(app.identifier, "/usr/bin/demo");
        assert_eq!(app.version_name.as_deref(), Some("1.2.3"));
        let _ = fs::remove_file(path);
    }
}

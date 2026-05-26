use serde_json::json;
use std::collections::HashMap;
use std::process::Stdio;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::mpsc;

use crate::netlink::AppAlert;

/// Tail the system journal for AppArmor DENIED messages and forward them as
/// `app_blocked` alert events through the provided channel.
///
/// This spawns `journalctl --follow` filtering for AppArmor messages. Each
/// DENIED line is parsed to extract the blocked executable and the profile that
/// denied it.
pub async fn run_audit_monitor(
    uid_map: HashMap<u32, String>,
    alert_tx: mpsc::UnboundedSender<AppAlert>,
) {
    if uid_map.is_empty() {
        println!("audit_monitor: no monitored UIDs, audit monitor idle");
        return;
    }

    println!(
        "audit_monitor: starting AppArmor denial monitor for UIDs {:?}",
        uid_map.keys().collect::<Vec<_>>()
    );

    match run_monitor_inner(uid_map, alert_tx).await {
        Ok(()) => println!("audit_monitor: exited normally"),
        Err(e) => eprintln!("audit_monitor: error: {}", e),
    }
}

async fn run_monitor_inner(
    uid_map: HashMap<u32, String>,
    alert_tx: mpsc::UnboundedSender<AppAlert>,
) -> Result<(), String> {
    // Build a reverse lookup from username → linux_username for log matching
    let username_set: std::collections::HashSet<String> = uid_map.values().cloned().collect();

    let mut child = Command::new("journalctl")
        .args([
            "--follow",
            "--no-pager",
            "-t",
            "audit",
            "--output=short",
            "--since=now",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to spawn journalctl: {}", e))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "failed to capture journalctl stdout".to_string())?;

    let mut reader = BufReader::new(stdout).lines();

    println!("audit_monitor: tailing journal for AppArmor denials");

    while let Ok(Some(line)) = reader.next_line().await {
        if !line.contains("apparmor=\"DENIED\"") && !line.contains("apparmor=DENIED") {
            continue;
        }

        // Parse the denial line for useful fields
        let profile = extract_field(&line, "profile=");
        let operation = extract_field(&line, "operation=");
        let name = extract_field(&line, "name=");
        let comm = extract_field(&line, "comm=");

        // Try to identify which monitored user this applies to.
        // Timekpr profiles follow the naming convention: timekpr-<username>-<app>
        let linux_username = if let Some(ref prof) = profile {
            if prof.starts_with("timekpr-") {
                let parts: Vec<&str> = prof.splitn(3, '-').collect();
                if parts.len() >= 2 {
                    let uname = parts[1].to_string();
                    if username_set.contains(&uname) {
                        Some(uname)
                    } else {
                        None
                    }
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };

        let username = match linux_username {
            Some(u) => u,
            None => continue, // Not a Timekpr-managed denial
        };

        println!(
            "audit_monitor: AppArmor DENIED for user={} profile={:?} comm={:?} name={:?}",
            username, profile, comm, name
        );

        let _ = alert_tx.send(AppAlert {
            event_type: "app_blocked".to_string(),
            linux_username: username,
            payload: json!({
                "details": {
                    "profile": profile.unwrap_or_default(),
                    "operation": operation.unwrap_or_default(),
                    "blocked_path": name.unwrap_or_default(),
                    "comm": comm.unwrap_or_default(),
                    "raw_line": &line,
                }
            }),
        });
    }

    Ok(())
}

/// Extract a key=value or key="value" field from an audit log line.
fn extract_field(line: &str, key: &str) -> Option<String> {
    let start = line.find(key)?;
    let value_start = start + key.len();
    let rest = &line[value_start..];

    if rest.starts_with('"') {
        // Quoted value
        let end = rest[1..].find('"').map(|i| i + 1)?;
        Some(rest[1..end].to_string())
    } else {
        // Unquoted value – ends at space or end of line
        let end = rest.find(' ').unwrap_or(rest.len());
        let value = rest[..end].trim_matches('"').to_string();
        if value.is_empty() {
            None
        } else {
            Some(value)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_quoted_field() {
        let line = r#"audit: apparmor="DENIED" operation="open" profile="timekpr-alice-steam" name="/etc/passwd" comm="steam""#;
        assert_eq!(
            extract_field(line, "profile="),
            Some("timekpr-alice-steam".to_string())
        );
        assert_eq!(
            extract_field(line, "operation="),
            Some("open".to_string())
        );
        assert_eq!(
            extract_field(line, "name="),
            Some("/etc/passwd".to_string())
        );
        assert_eq!(
            extract_field(line, "comm="),
            Some("steam".to_string())
        );
    }

    #[test]
    fn extract_unquoted_field() {
        let line = "apparmor=DENIED operation=exec profile=timekpr-bob-discord";
        assert_eq!(
            extract_field(line, "apparmor="),
            Some("DENIED".to_string())
        );
        assert_eq!(
            extract_field(line, "profile="),
            Some("timekpr-bob-discord".to_string())
        );
    }

    #[test]
    fn extract_missing_field() {
        let line = "some random log line without fields";
        assert_eq!(extract_field(line, "profile="), None);
    }
}

//! BCD store monitoring and boot-configuration tamper interception.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const BASELINE_PATH: &str = r"C:\ProgramData\Guardian\bcd_baseline.json";
const ALERT_DEBOUNCE: Duration = Duration::from_secs(300);

static AUDIT_IN_PROGRESS: AtomicBool = AtomicBool::new(false);
static LAST_ALERT: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct BcdBaselineState {
    #[serde(default)]
    entries: HashMap<String, String>,
}

fn baseline_path() -> PathBuf {
    PathBuf::from(BASELINE_PATH)
}

pub fn mark_audit_in_progress(active: bool) {
    AUDIT_IN_PROGRESS.store(active, Ordering::SeqCst);
}

pub fn is_guardian_bcd_audit_active() -> bool {
    AUDIT_IN_PROGRESS.load(Ordering::SeqCst)
}

pub fn parse_bcd_safeboot_flags(output: &str) -> Vec<String> {
    let mut flags = Vec::new();
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let lower = trimmed.to_ascii_lowercase();
        if lower.starts_with("safeboot") {
            if let Some((_, value)) = trimmed.split_once(char::is_whitespace) {
                flags.push(format!("safeboot:{}", value.trim().to_ascii_lowercase()));
            } else {
                flags.push("safeboot".to_string());
            }
        } else if lower.contains("safebootalternateshell") {
            flags.push("safebootalternateshell".to_string());
        } else if lower.contains("safebootnetwork") {
            flags.push("safebootnetwork".to_string());
        }
    }
    flags.sort();
    flags.dedup();
    flags
}

fn hash_bcd_output(output: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(output.as_bytes());
    hex::encode(hasher.finalize())
}

fn load_baseline() -> BcdBaselineState {
    let path = baseline_path();
    if let Ok(raw) = fs::read_to_string(&path) {
        serde_json::from_str(&raw).unwrap_or_default()
    } else {
        BcdBaselineState::default()
    }
}

fn save_baseline(state: &BcdBaselineState) -> Result<(), String> {
    let path = baseline_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create {}: {}", parent.display(), e))?;
    }
    let json = serde_json::to_string_pretty(state)
        .map_err(|e| format!("failed to serialize BCD baseline: {}", e))?;
    fs::write(&path, json).map_err(|e| format!("failed to write {}: {}", path.display(), e))
}

fn run_bcdedit_enum(entry: &str) -> Result<String, String> {
    mark_audit_in_progress(true);
    let result = (|| {
        let output = Command::new("bcdedit")
            .args(["/enum", entry])
            .output()
            .map_err(|e| format!("failed to run bcdedit: {}", e))?;
        if !output.status.success() {
            return Err(format!(
                "bcdedit /enum {} failed with status {}",
                entry,
                output.status
            ));
        }
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    })();
    mark_audit_in_progress(false);
    result
}

fn should_emit_alert() -> bool {
    let lock = LAST_ALERT.get_or_init(|| Mutex::new(None));
    let mut guard = lock.lock().unwrap();
    let now = Instant::now();
    if guard
        .map(|last| now.duration_since(last) >= ALERT_DEBOUNCE)
        .unwrap_or(true)
    {
        *guard = Some(now);
        true
    } else {
        false
    }
}

pub fn emit_boot_config_tamper_alert(details: serde_json::Value) {
    if !should_emit_alert() {
        return;
    }
    crate::netlink::send_app_alert("boot_config_tamper", "system", details);
}

fn audit_entry(entry: &str, baseline: &mut BcdBaselineState) -> Result<(), String> {
    let output = run_bcdedit_enum(entry)?;
    let flags = parse_bcd_safeboot_flags(&output);
    let current_hash = hash_bcd_output(&output);

    if flags.is_empty() {
        if let Some(previous_hash) = baseline.entries.get(entry) {
            if previous_hash != &current_hash {
                emit_boot_config_tamper_alert(serde_json::json!({
                    "source": "bcdedit_enum",
                    "entry_id": entry,
                    "detected_flags": [],
                    "baseline_hash": previous_hash,
                    "current_hash": current_hash,
                    "reason": "bcd_hash_drift",
                }));
            }
        } else {
            baseline.entries.insert(entry.to_string(), current_hash);
            save_baseline(baseline)?;
        }
        return Ok(());
    }

    emit_boot_config_tamper_alert(serde_json::json!({
        "source": "bcdedit_enum",
        "entry_id": entry,
        "detected_flags": flags,
        "baseline_hash": baseline.entries.get(entry).cloned().unwrap_or_default(),
        "current_hash": current_hash,
    }));
    Ok(())
}

pub fn audit_bcd_store_once() -> Result<(), String> {
    let mut baseline = load_baseline();
    audit_entry("{current}", &mut baseline)?;
    audit_entry("{default}", &mut baseline)?;
    save_baseline(&baseline)
}

pub fn is_boot_tool_process(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "bcdedit.exe" | "msconfig.exe" | "reagentc.exe" | "bootim.exe"
    )
}

pub fn should_intercept_boot_tool(name: &str, command_line: Option<&str>) -> bool {
    if is_guardian_bcd_audit_active() {
        return false;
    }
    if !is_boot_tool_process(name) {
        return false;
    }
    if let Some(line) = command_line {
        let lower = line.to_ascii_lowercase();
        return lower.contains("safeboot")
            || lower.contains("/set")
            || lower.contains("{default}")
            || lower.contains("{current}");
    }
    true
}

pub fn emit_process_intercept_alert(process_name: &str, command_line: Option<&str>) {
    let truncated = command_line
        .map(|line| line.chars().take(256).collect::<String>())
        .unwrap_or_default();
    emit_boot_config_tamper_alert(serde_json::json!({
        "source": "process_intercept",
        "process_name": process_name,
        "command_line": truncated,
    }));
}

pub fn terminate_process_tree(target_pid: u32, processes: &[(u32, u32, String)]) {
    let mut children: HashMap<u32, Vec<u32>> = HashMap::new();
    for (pid, ppid, _) in processes {
        children.entry(*ppid).or_default().push(*pid);
    }

    let mut stack = vec![target_pid];
    let mut visited = HashSet::new();
    while let Some(pid) = stack.pop() {
        if !visited.insert(pid) {
            continue;
        }
        if let Some(kids) = children.get(&pid) {
            for child in kids {
                stack.push(*child);
            }
        }
        if pid == target_pid {
            continue;
        }
        unsafe {
            use windows_sys::Win32::Foundation::CloseHandle;
            use windows_sys::Win32::System::Threading::{
                OpenProcess, TerminateProcess, PROCESS_TERMINATE,
            };
            let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
            if handle != 0 {
                TerminateProcess(handle, 1);
                CloseHandle(handle);
            }
        }
    }

    unsafe {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};
        let handle = OpenProcess(PROCESS_TERMINATE, 0, target_pid);
        if handle != 0 {
            TerminateProcess(handle, 1);
            CloseHandle(handle);
        }
    }
}

pub async fn start_bcd_monitor() {
    println!("Starting BCD integrity monitor...");
    loop {
        if crate::windows_service::boot_mode::is_safe_mode_boot() {
            tokio::time::sleep(Duration::from_secs(60)).await;
            continue;
        }
        if let Err(err) = audit_bcd_store_once() {
            eprintln!("BCD integrity audit failed: {}", err);
        }
        tokio::time::sleep(Duration::from_secs(60)).await;
    }
}

#[cfg(not(target_os = "windows"))]
pub async fn start_bcd_monitor() {}

#[cfg(test)]
mod tests {
    use super::parse_bcd_safeboot_flags;

    #[test]
    fn detects_safeboot_flags_in_bcd_output() {
        let sample = r#"
identifier              {default}
safeboot                minimal
"#;
        let flags = parse_bcd_safeboot_flags(sample);
        assert!(flags.iter().any(|flag| flag.contains("safeboot")));
        assert!(flags.iter().any(|flag| flag.contains("minimal")));
    }
}

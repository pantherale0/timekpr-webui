#[cfg(target_os = "windows")]
use std::collections::HashSet;
#[cfg(target_os = "windows")]
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(target_os = "windows")]
use std::time::Duration;
#[cfg(target_os = "windows")]
use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32First, Process32Next, PROCESSENTRY32, TH32CS_SNAPPROCESS,
};
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};

#[cfg(target_os = "windows")]
static IMMEDIATE_PASS_REQUESTED: AtomicBool = AtomicBool::new(false);

#[cfg(target_os = "windows")]
pub fn request_immediate_pass() {
    IMMEDIATE_PASS_REQUESTED.store(true, Ordering::SeqCst);
}

#[cfg(target_os = "windows")]
fn take_immediate_pass() -> bool {
    IMMEDIATE_PASS_REQUESTED.swap(false, Ordering::SeqCst)
}

#[cfg(target_os = "windows")]
fn resolve_active_username(
    rid: u32,
    users_map: &std::collections::HashMap<u32, String>,
) -> String {
    users_map
        .get(&rid)
        .cloned()
        .unwrap_or_else(|| "unknown".to_string())
}

#[cfg(target_os = "windows")]
struct ProcessRecord {
    pid: u32,
    parent_pid: u32,
    name: String,
}

#[cfg(target_os = "windows")]
fn snapshot_processes() -> Vec<ProcessRecord> {
    let mut processes = Vec::new();
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return processes;
        }

        let mut entry: PROCESSENTRY32 = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32>() as u32;
        if Process32First(snapshot, &mut entry) != 0 {
            loop {
                let process_name = read_u8_string(&entry.szExeFile);
                if !process_name.is_empty() {
                    processes.push(ProcessRecord {
                        pid: entry.th32ProcessID,
                        parent_pid: entry.th32ParentProcessID,
                        name: process_name,
                    });
                }
                if Process32Next(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
    }
    processes
}

#[cfg(target_os = "windows")]
pub async fn start_process_monitor() {
    println!("Starting Windows Process Monitor...");
    let users_map = crate::windows_service::policy::get_windows_users_map();
    let mut blocked_notified: HashSet<String> = HashSet::new();
    let mut last_reconciled_user: Option<String> = None;
    let mut force_reconcile = true;

    loop {
        let tamper_active = crate::windows_service::tamper_state::is_clock_tamper_active();
        let safe_mode_lockdown =
            crate::windows_service::safe_mode_lockdown::is_safe_mode_lockdown_active();
        let poll_ms = if tamper_active || safe_mode_lockdown {
            100
        } else {
            500
        };

        let active_username = crate::windows_service::dns_proxy::get_active_session_username();
        if active_username != last_reconciled_user || force_reconcile {
            let _ = crate::extension_policy::run_reconcile(active_username.as_deref(), None);
            last_reconciled_user = active_username.clone();
            force_reconcile = false;
        }

        let active_rid = crate::windows_service::dns_proxy::get_active_session_user_rid();
        let processes = snapshot_processes();

        for process in &processes {
            if crate::windows_service::bcd_integrity::should_intercept_boot_tool(
                &process.name,
                None,
            ) {
                crate::windows_service::bcd_integrity::emit_process_intercept_alert(
                    &process.name,
                    None,
                );
                let tree: Vec<(u32, u32, String)> = processes
                    .iter()
                    .map(|entry| (entry.pid, entry.parent_pid, entry.name.clone()))
                    .collect();
                crate::windows_service::bcd_integrity::terminate_process_tree(
                    process.pid,
                    &tree,
                );
            }
        }

        if let Some(rid) = active_rid {
            let is_locked_out = check_user_lockout_status(rid) || safe_mode_lockdown;
            let alert_username = resolve_active_username(rid, &users_map);

            for process in &processes {
                let should_block = if is_locked_out {
                    is_non_essential_app(&process.name, tamper_active || safe_mode_lockdown)
                        || is_cached_policy_blocked(&alert_username, &process.name)
                } else {
                    is_app_explicitly_blocked(rid, &alert_username, &process.name)
                };

                if should_block {
                    if blocked_notified.insert(process.name.clone()) {
                        println!(
                            "ProcessMonitor: Terminating blocked application '{}' (PID: {})",
                            process.name, process.pid
                        );

                        crate::netlink::send_app_alert(
                            "app_blocked",
                            &alert_username,
                            serde_json::json!({
                                "reason": if safe_mode_lockdown {
                                    "safe_mode_lockdown"
                                } else if is_locked_out {
                                    "limit_exceeded"
                                } else {
                                    "not_approved"
                                },
                                "application_name": &process.name,
                                "pid": process.pid,
                                "disposition": "DENIED"
                            }),
                        );

                        if !safe_mode_lockdown {
                            crate::windows_service::ipc::broadcast_toast_notification(
                                &crate::i18n::t("app_blocked_title"),
                                &crate::i18n::t_fmt("app_blocked_body", &[("app", &process.name)]),
                            );
                        }
                    }

                    unsafe {
                        let handle = OpenProcess(PROCESS_TERMINATE, 0, process.pid);
                        if handle != 0 {
                            TerminateProcess(handle, 1);
                            CloseHandle(handle);
                        }
                    }
                }
            }
        }

        if take_immediate_pass() {
            continue;
        }
        tokio::time::sleep(Duration::from_millis(poll_ms)).await;
    }
}

#[cfg(target_os = "windows")]
fn check_user_lockout_status(_rid: u32) -> bool {
    crate::windows_service::tamper_state::is_clock_tamper_active()
}

#[cfg(target_os = "windows")]
fn is_cached_policy_blocked(username: &str, name: &str) -> bool {
    crate::windows_service::policy::is_executable_blocked(username, name)
}

#[cfg(target_os = "windows")]
fn is_app_explicitly_blocked(_rid: u32, username: &str, name: &str) -> bool {
    is_cached_policy_blocked(username, name)
}

#[cfg(target_os = "windows")]
fn is_tamper_launcher(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "cmd.exe"
            | "powershell.exe"
            | "pwsh.exe"
            | "wt.exe"
            | "bash.exe"
            | "wscript.exe"
            | "cscript.exe"
            | "regedit.exe"
            | "msconfig.exe"
            | "bcdedit.exe"
            | "taskmgr.exe"
    )
}

#[cfg(target_os = "windows")]
fn is_non_essential_app(name: &str, tamper_active: bool) -> bool {
    if tamper_active && is_tamper_launcher(name) {
        return true;
    }
    let lowercase_name = name.to_lowercase();
    let essentials = [
        "dwm.exe",
        "ctfmon.exe",
        "taskhostw.exe",
        "taskhost.exe",
        "conhost.exe",
        "logonui.exe",
        "timekpr-agent.exe",
        "guardian-agent.exe",
        "svchost.exe",
        "lsass.exe",
        "csrss.exe",
        "winlogon.exe",
        "services.exe",
        "smss.exe",
        "fontdrvhost.exe",
        "sihost.exe",
    ];
    !essentials.iter().any(|ess| lowercase_name == *ess)
}

#[cfg(target_os = "windows")]
fn read_u8_string(array: &[u8]) -> String {
    let len = array.iter().position(|&c| c == 0).unwrap_or(array.len());
    String::from_utf8_lossy(&array[..len]).into_owned()
}

#[cfg(not(target_os = "windows"))]
pub async fn start_process_monitor() {}

#[cfg(not(target_os = "windows"))]
pub fn request_immediate_pass() {}

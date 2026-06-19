#[cfg(target_os = "windows")]
use std::collections::HashSet;
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
pub async fn start_process_monitor() {
    println!("Starting Windows Process Monitor...");
    let mut blocked_notified: HashSet<String> = HashSet::new();
    let mut last_reconciled_user: Option<String> = None;
    let mut force_reconcile = true;

    loop {
        let active_username = crate::windows_service::dns_proxy::get_active_session_username();
        if active_username != last_reconciled_user || force_reconcile {
            let _ = crate::extension_policy::run_reconcile(active_username.as_deref(), None);
            last_reconciled_user = active_username;
            force_reconcile = false;
        }

        let active_rid = crate::windows_service::dns_proxy::get_active_session_user_rid();
        
        if let Some(rid) = active_rid {
            // Check if active user has exceeded time limits or if we have blocked apps
            let is_locked_out = check_user_lockout_status(rid);
            
            // Collect processes using ToolHelp snapshot
            let mut processes = Vec::new();
            unsafe {
                let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
                if snapshot != INVALID_HANDLE_VALUE {
                    let mut entry: PROCESSENTRY32 = std::mem::zeroed();
                    entry.dwSize = std::mem::size_of::<PROCESSENTRY32>() as u32;

                    if Process32First(snapshot, &mut entry) != 0 {
                        loop {
                            let process_name = read_u8_string(&entry.szExeFile);
                            if !process_name.is_empty() {
                                processes.push((entry.th32ProcessID, process_name));
                            }

                            if Process32Next(snapshot, &mut entry) == 0 {
                                break;
                            }
                        }
                    }
                    CloseHandle(snapshot);
                }
            }

            // Enforce policy
            for (pid, name) in processes {
                let should_block = if is_locked_out {
                    // Lockout mode: terminate everything except system essential processes
                    is_non_essential_app(&name)
                } else {
                    // Regular mode: check if this specific application is blocked
                    is_app_explicitly_blocked(rid, &name)
                };

                if should_block {
                    if blocked_notified.insert(name.clone()) {
                        println!("ProcessMonitor: Terminating blocked application '{}' (PID: {})", name, pid);
                        
                        // Send server alert
                        crate::netlink::send_app_alert(
                            "app_blocked",
                            "child", // username placeholder
                            serde_json::json!({
                                "reason": if is_locked_out { "limit_exceeded" } else { "not_approved" },
                                "application_name": &name,
                                "pid": pid,
                                "disposition": "DENIED"
                            })
                        );

                        // Notify user-agent helper via Named Pipe IPC
                        crate::windows_service::ipc::broadcast_toast_notification(
                            &crate::i18n::t("app_blocked_title"),
                            &crate::i18n::t_fmt("app_blocked_body", &[("app", &name)]),
                        );
                    }

                    // Kill the process
                    unsafe {
                        let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
                        if handle != 0 {
                            TerminateProcess(handle, 1);
                            CloseHandle(handle);
                        }
                    }
                }
            }
        }

        // Clean notified cache periodically or let it run
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

#[cfg(target_os = "windows")]
fn check_user_lockout_status(_rid: u32) -> bool {
    // Queries local service state to see if the child's time limit is exceeded
    // For demo/design verification, returns false (will hook to real timer)
    false
}

#[cfg(target_os = "windows")]
fn is_app_explicitly_blocked(_rid: u32, _name: &str) -> bool {
    // Queries local synced policies (e.g. Steam or other blocked executables)
    // For demo/design verification:
    _name.eq_ignore_ascii_case("steam.exe")
}

#[cfg(target_os = "windows")]
fn is_non_essential_app(name: &str) -> bool {
    let lowercase_name = name.to_lowercase();
    let essentials = [
        "explorer.exe",
        "dwm.exe",
        "ctfmon.exe",
        "taskhostw.exe",
        "conhost.exe",
        "logonui.exe",
        "timekpr-agent.exe",
        "svchost.exe",
        "lsass.exe",
        "csrss.exe",
        "winlogon.exe",
        "services.exe",
    ];
    !essentials.iter().any(|ess| lowercase_name == *ess)
}

#[cfg(target_os = "windows")]
fn read_u8_string(array: &[u8]) -> String {
    let len = array.iter().position(|&c| c == 0).unwrap_or(array.len());
    String::from_utf8_lossy(&array[..len]).into_owned()
}

#[cfg(not(target_os = "windows"))]
pub async fn start_process_monitor() {
    // No-op for Linux compilation compatibility
}

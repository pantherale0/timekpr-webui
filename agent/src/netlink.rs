use serde_json::json;
use std::collections::{HashMap, HashSet};
use std::mem;
use std::sync::{Mutex, OnceLock};
use tokio::sync::mpsc;
use tokio::time::Instant;

#[cfg(target_os = "linux")]
use std::os::unix::io::{AsRawFd, RawFd};
#[cfg(target_os = "linux")]
use crate::apparmor;
#[cfg(target_os = "linux")]
use crate::approval_deduper;
#[cfg(target_os = "linux")]
use crate::linux_device_policy;

/// Alert payload ready to be forwarded to the server.
#[derive(Debug, Clone)]
pub struct AppAlert {
    pub event_type: String,
    pub linux_username: String,
    pub payload: serde_json::Value,
}

static ALERT_SENDER: OnceLock<Mutex<Option<mpsc::UnboundedSender<AppAlert>>>> = OnceLock::new();

pub fn register_alert_sender(tx: mpsc::UnboundedSender<AppAlert>) {
    let slot = ALERT_SENDER.get_or_init(|| Mutex::new(None));
    let mut guard = slot.lock().expect("alert sender mutex poisoned");
    *guard = Some(tx);
}

pub fn send_app_alert(event_type: &str, linux_username: &str, payload: serde_json::Value) {
    let Some(slot) = ALERT_SENDER.get() else {
        return;
    };
    let guard = slot.lock().expect("alert sender mutex poisoned");
    if let Some(tx) = guard.as_ref() {
        let _ = tx.send(AppAlert {
            event_type: event_type.to_string(),
            linux_username: linux_username.to_string(),
            payload,
        });
    }
}

/// Configuration for which usernames (and their UIDs) to monitor.
#[cfg(target_os = "linux")]
#[derive(Clone)]
pub struct MonitorConfig {
    /// Maps UID → linux username for users we care about.
    pub monitored_uids: HashMap<u32, String>,
}

/// Background task: open a Netlink process connector socket and relay exec/exit
/// events for monitored UIDs to the main event loop via `alert_tx`.
#[cfg(target_os = "linux")]
pub async fn run_process_monitor(
    config: MonitorConfig,
    alert_tx: mpsc::UnboundedSender<AppAlert>,
) {
    if config.monitored_uids.is_empty() {
        println!("netlink: no monitored UIDs configured, process monitor idle");
        return;
    }

    println!(
        "netlink: starting process monitor for UIDs {:?}",
        config.monitored_uids.keys().collect::<Vec<_>>()
    );

    match run_monitor_inner(config, alert_tx).await {
        Ok(()) => println!("netlink: process monitor exited normally"),
        Err(e) => eprintln!("netlink: process monitor error: {}", e),
    }
}

// ── Netlink / CN_PROC constants ─────────────────────────────────────────
const NETLINK_CONNECTOR: i32 = 11;
const CN_IDX_PROC: u32 = 1;
const CN_VAL_PROC: u32 = 1;
const PROC_CN_MCAST_LISTEN: u32 = 1;
const PROC_EVENT_EXEC: u32 = 0x0000_0002;
const PROC_EVENT_EXIT: u32 = 0x8000_0000;

#[repr(C)]
#[derive(Default)]
struct SockaddrNl {
    nl_family: u16,
    nl_pad: u16,
    nl_pid: u32,
    nl_groups: u32,
}

#[repr(C)]
#[derive(Default)]
struct NlmsghdrConnMsg {
    nlmsg_len: u32,
    nlmsg_type: u16,
    nlmsg_flags: u16,
    nlmsg_seq: u32,
    nlmsg_pid: u32,
    // cn_msg header
    cn_id_idx: u32,
    cn_id_val: u32,
    cn_seq: u32,
    cn_ack: u32,
    cn_len: u16,
    cn_flags: u16,
    // proc_event header
    what: u32,
}

#[repr(C)]
struct ProcEventExec {
    cpu: u32,
    timestamp_ns: u64,
    process_pid: u32,
    process_tgid: u32,
}

#[repr(C)]
struct ProcEventExit {
    cpu: u32,
    timestamp_ns: u64,
    process_pid: u32,
    process_tgid: u32,
    exit_code: u32,
    exit_signal: u32,
}

#[repr(C)]
#[derive(Default)]
struct CnMsgSubscribe {
    // nlmsghdr
    nlmsg_len: u32,
    nlmsg_type: u16,
    nlmsg_flags: u16,
    nlmsg_seq: u32,
    nlmsg_pid: u32,
    // cn_msg
    cn_id_idx: u32,
    cn_id_val: u32,
    cn_seq: u32,
    cn_ack: u32,
    cn_len: u16,
    cn_flags: u16,
    // subscribe data
    mode: u32,
}

struct RawSocket {
    fd: RawFd,
}

impl RawSocket {
    fn new() -> Result<Self, String> {
        unsafe {
            let fd = libc::socket(
                libc::AF_NETLINK,
                libc::SOCK_DGRAM | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
                NETLINK_CONNECTOR,
            );
            if fd < 0 {
                return Err(format!(
                    "failed to create netlink socket: {}",
                    std::io::Error::last_os_error()
                ));
            }
            Ok(Self { fd })
        }
    }

    fn bind(&self) -> Result<(), String> {
        let addr = SockaddrNl {
            nl_family: libc::AF_NETLINK as u16,
            nl_groups: CN_IDX_PROC,
            nl_pid: std::process::id(),
            ..Default::default()
        };
        unsafe {
            let rc = libc::bind(
                self.fd,
                &addr as *const _ as *const libc::sockaddr,
                mem::size_of::<SockaddrNl>() as libc::socklen_t,
            );
            if rc < 0 {
                return Err(format!(
                    "failed to bind netlink socket: {}",
                    std::io::Error::last_os_error()
                ));
            }
        }
        Ok(())
    }

    fn subscribe(&self) -> Result<(), String> {
        let msg = CnMsgSubscribe {
            nlmsg_len: mem::size_of::<CnMsgSubscribe>() as u32,
            nlmsg_type: libc::NLMSG_DONE as u16,
            nlmsg_flags: 0,
            nlmsg_seq: 0,
            nlmsg_pid: std::process::id(),
            cn_id_idx: CN_IDX_PROC,
            cn_id_val: CN_VAL_PROC,
            cn_seq: 0,
            cn_ack: 0,
            cn_len: mem::size_of::<u32>() as u16,
            cn_flags: 0,
            mode: PROC_CN_MCAST_LISTEN,
        };
        unsafe {
            let sent = libc::send(
                self.fd,
                &msg as *const _ as *const libc::c_void,
                mem::size_of::<CnMsgSubscribe>(),
                0,
            );
            if sent < 0 {
                return Err(format!(
                    "failed to subscribe to proc events: {}",
                    std::io::Error::last_os_error()
                ));
            }
        }
        Ok(())
    }
}

impl AsRawFd for RawSocket {
    fn as_raw_fd(&self) -> RawFd {
        self.fd
    }
}

impl Drop for RawSocket {
    fn drop(&mut self) {
        unsafe {
            libc::close(self.fd);
        }
    }
}

/// Get the UID that owns a given PID.
fn pid_to_uid(pid: u32) -> Option<u32> {
    let status_path = format!("/proc/{}/status", pid);
    let content = std::fs::read_to_string(status_path).ok()?;
    for line in content.lines() {
        if line.starts_with("Uid:") {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 2 {
                return parts[1].parse().ok();
            }
        }
    }
    None
}

/// Read the executable name from /proc/<pid>/comm.
fn pid_to_comm(pid: u32) -> Option<String> {
    std::fs::read_to_string(format!("/proc/{}/comm", pid))
        .ok()
        .map(|s| s.trim().to_string())
}

/// Read the full executable path from /proc/<pid>/exe.
fn pid_to_exe(pid: u32) -> Option<String> {
    std::fs::read_link(format!("/proc/{}/exe", pid))
        .ok()
        .map(|p| p.to_string_lossy().to_string())
}

/// Read the process command line as argv entries.
fn pid_to_cmdline(pid: u32) -> Option<Vec<String>> {
    let raw = std::fs::read(format!("/proc/{}/cmdline", pid)).ok()?;
    if raw.is_empty() {
        return None;
    }
    let argv: Vec<String> = raw
        .split(|byte| *byte == 0)
        .filter(|part| !part.is_empty())
        .map(|part| String::from_utf8_lossy(part).to_string())
        .collect();
    if argv.is_empty() {
        None
    } else {
        Some(argv)
    }
}

/// Read the current working directory for relative interpreter targets.
fn pid_to_cwd(pid: u32) -> Option<String> {
    std::fs::read_link(format!("/proc/{}/cwd", pid))
        .ok()
        .map(|p| p.to_string_lossy().to_string())
}

fn kill_pid(pid: u32) {
    unsafe {
        libc::kill(pid as i32, libc::SIGKILL);
    }
}

/// Active process tracking entry.
struct TrackedProcess {
    pid: u32,
    comm: String,
    exe_path: String,
    username: String,
    started: Instant,
}

async fn run_monitor_inner(
    config: MonitorConfig,
    alert_tx: mpsc::UnboundedSender<AppAlert>,
) -> Result<(), String> {
    let sock = RawSocket::new()?;
    sock.bind()?;
    sock.subscribe()?;

    let async_fd = tokio::io::unix::AsyncFd::new(sock)
        .map_err(|e| format!("failed to wrap socket in AsyncFd: {}", e))?;

    println!("netlink: proc connector subscribed, listening for events");

    // Track running processes: PID → TrackedProcess
    let mut tracked: HashMap<u32, TrackedProcess> = HashMap::new();

    let mut last_cleanup = Instant::now();
    let cleanup_interval = std::time::Duration::from_secs(300);

    let mut buf = vec![0u8; 4096];

    loop {
        let mut guard = async_fd.readable().await
            .map_err(|e| format!("async fd error: {}", e))?;

        let n = unsafe {
            libc::recv(
                async_fd.as_raw_fd(),
                buf.as_mut_ptr() as *mut libc::c_void,
                buf.len(),
                0,
            )
        };

        if n < 0 {
            let err = std::io::Error::last_os_error();
            if err.kind() == std::io::ErrorKind::WouldBlock {
                guard.clear_ready();
                continue;
            }
            if err.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            eprintln!("netlink: recv error: {}", err);
            continue;
        }

        let n = n as usize;
        if n < mem::size_of::<NlmsghdrConnMsg>() {
            continue;
        }

        // Parse the header to determine event type
        let hdr: NlmsghdrConnMsg =
            unsafe { std::ptr::read_unaligned(buf.as_ptr() as *const NlmsghdrConnMsg) };
        let header_size = mem::size_of::<NlmsghdrConnMsg>();
        let nlmsg_len = hdr.nlmsg_len as usize;
        if nlmsg_len < header_size || nlmsg_len > n {
            eprintln!(
                "netlink: dropping malformed frame (nlmsg_len={}, recv_len={})",
                nlmsg_len, n
            );
            continue;
        }

        match hdr.what {
            PROC_EVENT_EXEC => {
                if n < mem::size_of::<NlmsghdrConnMsg>() + mem::size_of::<ProcEventExec>() {
                    continue;
                }
                let event: ProcEventExec = unsafe {
                    std::ptr::read_unaligned(
                        buf.as_ptr().add(mem::size_of::<NlmsghdrConnMsg>()) as *const _,
                    )
                };

                let pid = event.process_tgid;
                let username = pid_to_uid(pid)
                    .and_then(|uid| config.monitored_uids.get(&uid).cloned());
                let comm = pid_to_comm(pid)
                    .filter(|value| !value.is_empty())
                    .unwrap_or_else(|| format!("pid-{}", pid));
                let exe_path = pid_to_exe(pid).unwrap_or_default();
                let argv = pid_to_cmdline(pid).unwrap_or_default();
                let cwd = pid_to_cwd(pid);
                let transient = username.is_none() || exe_path.is_empty();

                // 1. Approval overlay enforcement (allowlist/blocklist)
                if let Some(ref username) = username {
                    if !exe_path.is_empty() {
                        if let Some(_blocked_path) =
                            apparmor::check_approval_launch_block(username, &exe_path).await
                        {
                            let has_overlay = apparmor::approval_policy_for_user(username)
                                .await
                                .is_some();
                            if has_overlay
                                && approval_deduper::should_emit("app_launch", &exe_path)
                            {
                                let _ = alert_tx.send(AppAlert {
                                    event_type: "access_requested".to_string(),
                                    linux_username: username.clone(),
                                    payload: json!({
                                        "request_type": "app_launch",
                                        "target_kind": "executable",
                                        "target_value": &exe_path,
                                        "display_label": &comm,
                                    }),
                                });
                            }
                            let _ = alert_tx.send(AppAlert {
                                event_type: "app_blocked".to_string(),
                                linux_username: username.clone(),
                                payload: json!({
                                    "reason": "not_approved",
                                    "application_name": &comm,
                                    "executable_path": &exe_path,
                                    "target_kind": "executable",
                                    "pid": pid,
                                    "enforcement_source": "approval_overlay",
                                    "disposition": "DENIED",
                                }),
                            });
                            kill_pid(pid);
                            continue;
                        }
                    }
                }

                // 1b. Linux device policy terminal blocking
                if let Some(ref username) = username {
                    if !exe_path.is_empty()
                        && linux_device_policy::check_terminal_exec_block(username, &exe_path).await
                    {
                        let _ = alert_tx.send(AppAlert {
                            event_type: "app_blocked".to_string(),
                            linux_username: username.clone(),
                            payload: json!({
                                "reason": "terminal_disabled",
                                "application_name": &comm,
                                "executable_path": &exe_path,
                                "target_kind": "executable",
                                "pid": pid,
                                "enforcement_source": "linux_device_policy",
                                "disposition": "DENIED",
                            }),
                        });
                        kill_pid(pid);
                        continue;
                    }
                }

                // 2. AppArmor / static rule enforcement
                if let Some(decision) = if let Some(ref username) = username {
                    if exe_path.is_empty() {
                        None
                    } else {
                        apparmor::evaluate_exec_event(username, &exe_path, &argv, cwd.as_deref())
                            .await
                    }
                } else {
                    None
                } {
                    let blocked = decision.preset == "blocked";
                    let _ = alert_tx.send(AppAlert {
                        event_type: "app_blocked".to_string(),
                        linux_username: username
                            .clone()
                            .unwrap_or_else(|| "unknown".to_string()),
                        payload: json!({
                            "application_name": &comm,
                            "executable_path": &exe_path,
                            "pid": pid,
                            "path_rule": decision.rule_name,
                            "rule_target": decision.rule_target,
                            "matched_path": decision.matched_path,
                            "matched_via": decision.matched_via,
                            "enforcement_source": "exec_monitor",
                            "disposition": if blocked { "DENIED" } else { "ALLOWED" },
                        }),
                    });
                    if blocked {
                        kill_pid(pid);
                        continue;
                    }
                }

                // 2. Alert & Tracking for Monitored Processes
                if let Some(username) = username {
                    // Only send "app_launched" for processes with a concrete executable path
                    // to avoid spamming the server with transient pid-0/unknown alerts.
                    if !exe_path.is_empty() {
                        let _ = alert_tx.send(AppAlert {
                            event_type: "app_launched".to_string(),
                            linux_username: username.clone(),
                            payload: json!({
                                "application_name": &comm,
                                "executable_path": &exe_path,
                                "pid": pid,
                                "transient": transient,
                            }),
                        });

                        tracked.insert(
                            pid,
                            TrackedProcess {
                                pid,
                                comm,
                                exe_path,
                                username,
                                started: Instant::now(),
                            },
                        );
                    }
                }
            }
            PROC_EVENT_EXIT => {
                if n < mem::size_of::<NlmsghdrConnMsg>() + mem::size_of::<ProcEventExit>() {
                    continue;
                }
                let event: ProcEventExit = unsafe {
                    std::ptr::read_unaligned(
                        buf.as_ptr().add(mem::size_of::<NlmsghdrConnMsg>()) as *const _,
                    )
                };

                let pid = event.process_tgid;
                if let Some(process) = tracked.remove(&pid) {
                    let duration = process.started.elapsed();
                    let duration_secs = duration.as_secs();

                    // Only report usage for processes that ran >1 second
                    if duration_secs >= 1 && !process.exe_path.is_empty() {
                        let now = chrono::Utc::now();
                        let start_time = now - chrono::Duration::seconds(duration_secs as i64);
                        let _ = alert_tx.send(AppAlert {
                            event_type: "app_usage".to_string(),
                            linux_username: process.username.clone(),
                            payload: json!({
                                "application_name": &process.comm,
                                "executable_path": &process.exe_path,
                                "pid": process.pid,
                                "duration_seconds": duration_secs,
                                "start_time": start_time.to_rfc3339(),
                                "end_time": now.to_rfc3339(),
                            }),
                        });
                    }
                }
            }
            _ => {}
        }

        // Periodic cleanup of stale process tracking entries to prevent memory leaks (e.g. from missed exits)
        if last_cleanup.elapsed() >= cleanup_interval {
            last_cleanup = Instant::now();
            let before = tracked.len();
            let candidate_pids: Vec<u32> = tracked.keys().copied().collect();
            let dead_pids = tokio::task::spawn_blocking(move || {
                let mut dead = Vec::new();
                for pid in candidate_pids {
                    if !std::path::Path::new(&format!("/proc/{}", pid)).exists() {
                        dead.push(pid);
                    }
                }
                dead
            })
            .await
            .unwrap_or_else(|_| Vec::new());
            if !dead_pids.is_empty() {
                let dead_pid_set: HashSet<u32> = dead_pids.into_iter().collect();
                tracked.retain(|pid, _| !dead_pid_set.contains(pid));
            }
            let cleaned = before - tracked.len();
            if cleaned > 0 {
                println!(
                    "netlink: cleaned up {} stale tracked processes from memory",
                    cleaned
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pid_to_uid_reads_own_process() {
        // Reading our own UID should succeed
        let uid = pid_to_uid(std::process::id());
        assert!(uid.is_some());
    }

    #[test]
    fn pid_to_comm_reads_own_process() {
        let comm = pid_to_comm(std::process::id());
        assert!(comm.is_some());
        // The test binary name should be non-empty
        assert!(!comm.unwrap().is_empty());
    }

    #[test]
    fn pid_to_exe_reads_own_process() {
        let exe = pid_to_exe(std::process::id());
        assert!(exe.is_some());
    }

    #[test]
    fn pid_to_cmdline_reads_own_process() {
        let argv = pid_to_cmdline(std::process::id());
        assert!(argv.is_some());
        assert!(!argv.unwrap().is_empty());
    }
}

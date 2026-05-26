use serde_json::json;
use std::collections::HashMap;
use std::mem;
use std::os::unix::io::{AsRawFd, RawFd};
use tokio::sync::mpsc;
use tokio::time::Instant;

/// Alert payload ready to be forwarded to the server.
#[derive(Debug, Clone)]
pub struct AppAlert {
    pub event_type: String,
    pub linux_username: String,
    pub payload: serde_json::Value,
}

/// Configuration for which usernames (and their UIDs) to monitor.
#[derive(Clone)]
pub struct MonitorConfig {
    /// Maps UID → linux username for users we care about.
    pub monitored_uids: HashMap<u32, String>,
}

/// Background task: open a Netlink process connector socket and relay exec/exit
/// events for monitored UIDs to the main event loop via `alert_tx`.
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
                let uid = match pid_to_uid(pid) {
                    Some(uid) => uid,
                    None => continue,
                };

                let username = match config.monitored_uids.get(&uid) {
                    Some(name) => name.clone(),
                    None => continue,
                };

                let comm = pid_to_comm(pid).unwrap_or_default();
                let exe_path = pid_to_exe(pid).unwrap_or_default();

                if comm.is_empty() || exe_path.is_empty() {
                    continue;
                }

                // Send app_launched alert
                let _ = alert_tx.send(AppAlert {
                    event_type: "app_launched".to_string(),
                    linux_username: username.clone(),
                    payload: json!({
                        "details": {
                            "application_name": &comm,
                            "executable_path": &exe_path,
                            "pid": pid,
                        }
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
                    if duration_secs >= 1 {
                        let now = chrono::Utc::now();
                        let start_time = now - chrono::Duration::seconds(duration_secs as i64);
                        let _ = alert_tx.send(AppAlert {
                            event_type: "app_usage".to_string(),
                            linux_username: process.username.clone(),
                            payload: json!({
                                "details": {
                                    "application_name": &process.comm,
                                    "executable_path": &process.exe_path,
                                    "pid": process.pid,
                                    "duration_seconds": duration_secs,
                                    "start_time": start_time.to_rfc3339(),
                                    "end_time": now.to_rfc3339(),
                                }
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
            tracked.retain(|&pid, _| {
                std::path::Path::new(&format!("/proc/{}", pid)).exists()
            });
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
}

//! Linux clock integrity monitor: periodic checks, overlay lockdown, alerts.

#[cfg(target_os = "linux")]
use guardian_agent::clock_integrity::{
    apply_tick, fetch_tick_inputs, ClockIntegrityState, DetectionSource, PersistedClockIntegrity,
    TickStatus,
};
#[cfg(target_os = "linux")]
use serde_json::json;
#[cfg(target_os = "linux")]
use std::fs;
#[cfg(target_os = "linux")]
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::sync::{Arc, Mutex};
#[cfg(target_os = "linux")]
use futures_util::StreamExt;
#[cfg(target_os = "linux")]
use std::time::Duration;
#[cfg(target_os = "linux")]
use tokio::sync::mpsc;
#[cfg(target_os = "linux")]
use tokio::time::sleep;

#[cfg(target_os = "linux")]
const STATE_PATH: &str = "/var/lib/guardian-agent/clock_integrity.json";
#[cfg(target_os = "linux")]
const CHECK_INTERVAL_SECS: u64 = 60;

#[cfg(target_os = "linux")]
fn state_path() -> PathBuf {
    PathBuf::from(STATE_PATH)
}

#[cfg(target_os = "linux")]
fn load_state() -> ClockIntegrityState {
    let path = state_path();
    if let Ok(raw) = fs::read_to_string(&path) {
        ClockIntegrityState::from_json(&raw)
            .unwrap_or_else(|_| ClockIntegrityState::new(PersistedClockIntegrity::default()))
    } else {
        ClockIntegrityState::new(PersistedClockIntegrity::default())
    }
}

#[cfg(target_os = "linux")]
fn save_state(state: &ClockIntegrityState) -> Result<(), String> {
    let path = state_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create {}: {}", parent.display(), e))?;
    }
    let json = state.to_json()?;
    fs::write(&path, json).map_err(|e| format!("failed to write {}: {}", path.display(), e))
}

#[cfg(target_os = "linux")]
fn primary_managed_username(users_map: &std::collections::HashMap<u32, String>) -> Option<String> {
    users_map.values().next().cloned()
}

#[cfg(target_os = "linux")]
fn apply_lockdown(username: &str) {
    let args = json!({
        "reason": "clock_tamper",
        "device_name": std::fs::read_to_string("/etc/hostname")
            .unwrap_or_default()
            .trim()
            .to_string(),
    });
    if let Err(error) = crate::overlay::show(&args, username) {
        eprintln!("Failed to show clock tamper overlay: {}", error);
    }
}

#[cfg(target_os = "linux")]
fn clear_lockdown() {
    crate::overlay::dismiss();
}

#[cfg(target_os = "linux")]
fn emit_tamper_alert(username: &str, outcome: &guardian_agent::clock_integrity::TickOutcome) {
    let detection_source = outcome
        .detection_source
        .as_ref()
        .map(DetectionSource::as_str)
        .unwrap_or("boottime");
    let details = json!({
        "skew_seconds": outcome.skew_seconds,
        "detection_source": detection_source,
        "expected_wall_ms": outcome.expected_wall_ms,
    });
    crate::netlink::send_app_alert("clock_tamper", username, details);
}

#[cfg(target_os = "linux")]
async fn run_tick(
    state: &Arc<Mutex<ClockIntegrityState>>,
    users_map: &std::collections::HashMap<u32, String>,
) {
    let (wall_ms, boottime_ms, ntp_ms) = match fetch_tick_inputs().await {
        Ok(inputs) => inputs,
        Err(error) => {
            eprintln!("clock integrity: {}", error);
            return;
        }
    };

    let outcome = {
        let mut guard = match state.lock() {
            Ok(guard) => guard,
            Err(_) => return,
        };
        let outcome = apply_tick(&mut guard, wall_ms, boottime_ms, ntp_ms);
        if let Err(error) = save_state(&guard) {
            eprintln!("clock integrity: {}", error);
        }
        outcome
    };

    let username = primary_managed_username(users_map).unwrap_or_else(|| "unknown".to_string());

    match outcome.status {
        TickStatus::TamperDetected => {
            emit_tamper_alert(&username, &outcome);
            apply_lockdown(&username);
        }
        TickStatus::TamperCleared => {
            clear_lockdown();
        }
        TickStatus::Ok => {
            if outcome.tamper_active {
                apply_lockdown(&username);
            }
        }
    }
}

#[cfg(target_os = "linux")]
pub struct ClockIntegrityMonitor {
    pub(crate) state: Arc<Mutex<ClockIntegrityState>>,
    pub(crate) users_map: std::collections::HashMap<u32, String>,
}

#[cfg(target_os = "linux")]
pub fn spawn_logind_resume_listener(resume_tx: mpsc::UnboundedSender<()>) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let connection = match zbus::Connection::system().await {
            Ok(connection) => connection,
            Err(error) => {
                eprintln!("clock integrity resume listener: bus connect failed: {}", error);
                return;
            }
        };
        let proxy = match logind_zbus::manager::ManagerProxy::new(&connection).await {
            Ok(proxy) => proxy,
            Err(error) => {
                eprintln!("clock integrity resume listener: proxy failed: {}", error);
                return;
            }
        };
        let mut sleep_stream = match proxy.receive_prepare_for_sleep().await {
            Ok(stream) => stream,
            Err(error) => {
                eprintln!("clock integrity resume listener: subscribe failed: {}", error);
                return;
            }
        };
        while let Some(signal) = sleep_stream.next().await {
            let Ok(args) = signal.args() else {
                continue;
            };
            if !args.start {
                let _ = resume_tx.send(());
            }
        }
    })
}

#[cfg(target_os = "linux")]
pub fn spawn_periodic_monitor(monitor: Arc<ClockIntegrityMonitor>) -> tokio::task::JoinHandle<()> {
    let state = monitor.state.clone();
    let users_map = monitor.users_map.clone();
    tokio::spawn(async move {
        loop {
            run_tick(&state, &users_map).await;
            sleep(Duration::from_secs(CHECK_INTERVAL_SECS)).await;
        }
    })
}

#[cfg(target_os = "linux")]
impl ClockIntegrityMonitor {
    pub fn new(users_map: std::collections::HashMap<u32, String>) -> Self {
        let state = load_state();
        if state.tamper_active() {
            if let Some(username) = primary_managed_username(&users_map) {
                apply_lockdown(&username);
            }
        }
        Self {
            state: Arc::new(Mutex::new(state)),
            users_map,
        }
    }

    pub fn on_resume(&self) {
        let state = self.state.clone();
        let users_map = self.users_map.clone();
        tokio::spawn(async move {
            run_tick(&state, &users_map).await;
        });
    }
}

#[cfg(target_os = "linux")]
pub fn spawn_resume_hook(
    monitor: Arc<ClockIntegrityMonitor>,
    mut resume_rx: mpsc::UnboundedReceiver<()>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        while resume_rx.recv().await.is_some() {
            monitor.on_resume();
        }
    })
}

//! Windows clock integrity monitor: periodic checks, process lockout, alerts, toast.

use super::tamper_state::{clock_integrity_state_handle, save_state};
use guardian_agent::clock_integrity::{apply_tick, fetch_tick_inputs, DetectionSource, TickStatus};
use serde_json::json;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc;
use tokio::time::sleep;

const CHECK_INTERVAL_SECS: u64 = 60;

pub const PBT_APMRESUMEAUTOMATIC: u32 = 0x12;
pub const PBT_APMRESUMESUSPEND: u32 = 0x7;

fn primary_managed_username(users_map: &HashMap<u32, String>) -> Option<String> {
    let active_rid = crate::windows_service::dns_proxy::get_active_session_user_rid();
    if let Some(rid) = active_rid {
        if let Some(name) = users_map.get(&rid) {
            return Some(name.clone());
        }
    }
    users_map.values().next().cloned()
}

fn active_username(users_map: &HashMap<u32, String>) -> String {
    primary_managed_username(users_map).unwrap_or_else(|| "unknown".to_string())
}

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

fn show_tamper_toast() {
    crate::windows_service::ipc::broadcast_toast_notification(
        &crate::i18n::t("clock_tamper_title"),
        &crate::i18n::t("clock_tamper_body"),
    );
}

fn show_overlay(username: &str) {
    let args = json!({
        "reason": "clock_tamper",
        "device_name": std::env::var("COMPUTERNAME").unwrap_or_default(),
        "linux_username": username,
    });
    crate::windows_service::overlay::show(&args);
}

fn dismiss_overlay() {
    crate::windows_service::overlay::dismiss();
}

async fn run_tick(users_map: &HashMap<u32, String>) {
    let (wall_ms, boottime_ms, ntp_ms) = match fetch_tick_inputs().await {
        Ok(inputs) => inputs,
        Err(error) => {
            eprintln!("clock integrity: {}", error);
            return;
        }
    };

    let outcome = {
        let state_handle = clock_integrity_state_handle();
        let mut guard = match state_handle.lock() {
            Ok(guard) => guard,
            Err(_) => return,
        };
        let outcome = apply_tick(&mut guard, wall_ms, boottime_ms, ntp_ms);
        if let Err(error) = save_state(&guard) {
            eprintln!("clock integrity: {}", error);
        }
        outcome
    };

    let username = active_username(users_map);

    match outcome.status {
        TickStatus::TamperDetected => {
            emit_tamper_alert(&username, &outcome);
            show_tamper_toast();
            show_overlay(&username);
            super::process_monitor::request_immediate_pass();
        }
        TickStatus::TamperCleared => {
            dismiss_overlay();
            crate::windows_service::ipc::broadcast_toast_notification(
                &crate::i18n::t("clock_tamper_cleared_title"),
                &crate::i18n::t("clock_tamper_cleared_body"),
            );
        }
        TickStatus::Ok => {
            if outcome.tamper_active {
                show_overlay(&username);
                super::process_monitor::request_immediate_pass();
            }
        }
    }
}

pub struct ClockIntegrityMonitor {
    users_map: HashMap<u32, String>,
}

impl ClockIntegrityMonitor {
    fn new(users_map: HashMap<u32, String>) -> Self {
        if tamper_state::is_clock_tamper_active() {
            if let Some(username) = primary_managed_username(&users_map) {
                show_tamper_toast();
                show_overlay(&username);
                super::process_monitor::request_immediate_pass();
            }
        }
        Self { users_map }
    }

    pub fn on_resume(self: &Arc<Self>) {
        let users_map = self.users_map.clone();
        tokio::spawn(async move {
            run_tick(&users_map).await;
        });
    }
}

pub fn spawn_periodic_monitor(monitor: Arc<ClockIntegrityMonitor>) -> tokio::task::JoinHandle<()> {
    let users_map = monitor.users_map.clone();
    tokio::spawn(async move {
        loop {
            run_tick(&users_map).await;
            sleep(Duration::from_secs(CHECK_INTERVAL_SECS)).await;
        }
    })
}

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

pub fn start(users_map: HashMap<u32, String>) -> Arc<ClockIntegrityMonitor> {
    Arc::new(ClockIntegrityMonitor::new(users_map))
}
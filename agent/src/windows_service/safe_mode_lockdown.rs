//! Safe Mode lockdown state persisted under ProgramData.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};

const STATE_PATH: &str = r"C:\ProgramData\Guardian\safe_mode_lockdown.json";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct PersistedSafeModeLockdown {
    #[serde(default)]
    lockdown_active: bool,
    #[serde(default)]
    override_applied: bool,
}

fn state_path() -> PathBuf {
    PathBuf::from(STATE_PATH)
}

fn load_state() -> PersistedSafeModeLockdown {
    let path = state_path();
    if let Ok(raw) = fs::read_to_string(&path) {
        serde_json::from_str(&raw).unwrap_or_default()
    } else {
        PersistedSafeModeLockdown::default()
    }
}

fn save_state(state: &PersistedSafeModeLockdown) -> Result<(), String> {
    let path = state_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create {}: {}", parent.display(), e))?;
    }
    let json = serde_json::to_string_pretty(state)
        .map_err(|e| format!("failed to serialize safe mode lockdown state: {}", e))?;
    fs::write(&path, json).map_err(|e| format!("failed to write {}: {}", path.display(), e))
}

static STATE: OnceLock<Arc<Mutex<PersistedSafeModeLockdown>>> = OnceLock::new();

fn state_handle() -> Arc<Mutex<PersistedSafeModeLockdown>> {
    STATE
        .get_or_init(|| Arc::new(Mutex::new(load_state())))
        .clone()
}

pub fn on_safe_mode_service_start() {
    let mut state = state_handle().lock().unwrap();
    if !state.override_applied {
        state.lockdown_active = true;
        let _ = save_state(&state);
        println!("Safe Mode lockdown state activated.");
    }
}

pub fn on_normal_boot_service_start() {
    let mut state = state_handle().lock().unwrap();
    state.lockdown_active = false;
    state.override_applied = false;
    let _ = save_state(&state);
}

pub fn is_safe_mode_lockdown_active() -> bool {
    if !crate::windows_service::boot_mode::is_safe_mode_boot() {
        return false;
    }
    state_handle()
        .lock()
        .map(|guard| guard.lockdown_active && !guard.override_applied)
        .unwrap_or(false)
}

pub fn clear_lockdown_override() {
    let mut state = state_handle().lock().unwrap();
    state.lockdown_active = false;
    state.override_applied = true;
    let _ = save_state(&state);
    crate::windows_service::process_monitor::request_immediate_pass();
}

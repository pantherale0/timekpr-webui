//! Shared clock tamper state for the Windows service.

use guardian_agent::clock_integrity::{ClockIntegrityState, PersistedClockIntegrity};
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};

const STATE_PATH: &str = r"C:\ProgramData\Guardian\clock_integrity.json";

fn state_path() -> PathBuf {
    PathBuf::from(STATE_PATH)
}

fn load_state() -> ClockIntegrityState {
    let path = state_path();
    if let Ok(raw) = fs::read_to_string(&path) {
        ClockIntegrityState::from_json(&raw)
            .unwrap_or_else(|_| ClockIntegrityState::new(PersistedClockIntegrity::default()))
    } else {
        ClockIntegrityState::new(PersistedClockIntegrity::default())
    }
}

pub fn save_state(state: &ClockIntegrityState) -> Result<(), String> {
    let path = state_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create {}: {}", parent.display(), e))?;
    }
    let json = state.to_json()?;
    fs::write(&path, json).map_err(|e| format!("failed to write {}: {}", path.display(), e))
}

static STATE: OnceLock<Arc<Mutex<ClockIntegrityState>>> = OnceLock::new();

pub fn clock_integrity_state_handle() -> Arc<Mutex<ClockIntegrityState>> {
    STATE
        .get_or_init(|| Arc::new(Mutex::new(load_state())))
        .clone()
}

pub fn is_clock_tamper_active() -> bool {
    clock_integrity_state_handle()
        .lock()
        .map(|guard| guard.tamper_active())
        .unwrap_or(false)
}

pub fn set_clock_tamper_otp_override(active: bool) {
    if let Ok(mut guard) = clock_integrity_state_handle().lock() {
        guard.set_otp_override(active);
        let _ = save_state(&guard);
    }
}

pub fn reload_state_from_disk() {
    if let Ok(mut guard) = clock_integrity_state_handle().lock() {
        *guard = load_state();
    }
}

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};

use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use zbus::Connection;

mod bluetooth;
mod exec;
mod polkit;
mod session;

const STATE_DIR_PRIMARY: &str = "/var/lib/guardian-agent";
const STATE_DIR_FALLBACK: &str = "/etc/guardian-agent";
const STATE_FILENAME: &str = "linux-device-policy.json";

static LINUX_DEVICE_POLICY_RUNTIME: OnceLock<Arc<Mutex<LinuxDevicePolicyRuntime>>> = OnceLock::new();

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PolkitPolicy {
    #[serde(default, rename = "installSoftwareDisabled")]
    pub install_software_disabled: bool,
    #[serde(default, rename = "uninstallSoftwareDisabled")]
    pub uninstall_software_disabled: bool,
    #[serde(default, rename = "mountRemovableMediaDisabled")]
    pub mount_removable_media_disabled: bool,
    #[serde(default, rename = "modifyAccountsDisabled")]
    pub modify_accounts_disabled: bool,
    #[serde(default, rename = "systemPowerActionsDisabled")]
    pub system_power_actions_disabled: bool,
    #[serde(default, rename = "pkexecElevationDisabled")]
    pub pkexec_elevation_disabled: bool,
    #[serde(default, rename = "flatpakInstallDisabled")]
    pub flatpak_install_disabled: bool,
    #[serde(default, rename = "snapInstallDisabled")]
    pub snap_install_disabled: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConnectivityPolicy {
    #[serde(default, rename = "bluetoothDisabled")]
    pub bluetooth_disabled: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecPolicy {
    #[serde(default, rename = "terminalAccessDisabled")]
    pub terminal_access_disabled: bool,
}

pub use crate::extension_policy::ChromePolicy;

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DevicePolicyPayload {
    #[serde(default)]
    pub polkit: PolkitPolicy,
    #[serde(default)]
    pub connectivity: ConnectivityPolicy,
    #[serde(default)]
    pub exec: ExecPolicy,
    #[serde(default)]
    pub chrome: ChromePolicy,
    #[serde(default, rename = "supportMessage")]
    pub support_message: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
struct PersistedLinuxDevicePolicyState {
    users: HashMap<String, DevicePolicyPayload>,
}

struct LinuxDevicePolicyRuntime {
    state_path: PathBuf,
    current_state: PersistedLinuxDevicePolicyState,
    active_session_username: Option<String>,
    enforced_username: Option<String>,
    restored: bool,
}

pub async fn initialize_runtime() -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()?;
    drop(guard);

    if let Ok(connection) = Connection::system().await {
        refresh_active_session_from_logind(&connection).await?;
    }
    Ok(())
}

pub async fn sync_user_policy(
    username: &str,
    payload: DevicePolicyPayload,
) -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()?;
    guard.update_user_policy(username, payload)
}

pub async fn refresh_active_session_from_logind(connection: &Connection) -> Result<(), String> {
    let active_username = session::query_primary_seat_active_username(connection).await?;
    set_active_session_username(active_username).await
}

pub async fn set_active_session_username(active_username: Option<String>) -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()?;
    guard.active_session_username = active_username
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    guard.reconcile_enforcement()
}

pub async fn clear_on_unenroll() -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()?;
    guard.current_state.users.clear();
    guard.active_session_username = None;
    guard.enforced_username = None;
    guard.persist()?;
    guard.reconcile_enforcement()
}

pub async fn check_terminal_exec_block(username: &str, exe_path: &str) -> bool {
    let runtime = get_runtime();
    let guard = runtime.lock().await;
    terminal_exec_block_applies(
        guard.enforced_username.as_deref(),
        username,
        guard.current_state.users.get(username),
        exe_path,
    )
}

fn active_managed_user<'a>(
    active_session_username: Option<&'a str>,
    users: &'a HashMap<String, DevicePolicyPayload>,
) -> Option<&'a str> {
    let active = active_session_username.filter(|value| !value.is_empty())?;
    users.contains_key(active).then_some(active)
}

fn terminal_exec_block_applies(
    enforced_username: Option<&str>,
    username: &str,
    policy: Option<&DevicePolicyPayload>,
    exe_path: &str,
) -> bool {
    if enforced_username != Some(username) {
        return false;
    }
    let Some(policy) = policy else {
        return false;
    };
    if !policy.exec.terminal_access_disabled {
        return false;
    }
    exec::is_terminal_executable(exe_path)
}

fn get_runtime() -> Arc<Mutex<LinuxDevicePolicyRuntime>> {
    LINUX_DEVICE_POLICY_RUNTIME
        .get_or_init(|| Arc::new(Mutex::new(LinuxDevicePolicyRuntime::new())))
        .clone()
}

impl LinuxDevicePolicyRuntime {
    fn new() -> Self {
        Self {
            state_path: state_path(),
            current_state: PersistedLinuxDevicePolicyState::default(),
            active_session_username: None,
            enforced_username: None,
            restored: false,
        }
    }

    fn ensure_restored(&mut self) -> Result<(), String> {
        if self.restored {
            return Ok(());
        }
        if self.state_path.exists() {
            let raw = fs::read_to_string(&self.state_path)
                .map_err(|e| format!("failed to read linux device policy state: {e}"))?;
            self.current_state = serde_json::from_str(&raw)
                .map_err(|e| format!("failed to parse linux device policy state: {e}"))?;
        }
        self.restored = true;
        Ok(())
    }

    fn update_user_policy(&mut self, username: &str, payload: DevicePolicyPayload) -> Result<(), String> {
        let normalized_username = username.trim();
        if normalized_username.is_empty() {
            return Err("username must not be empty".to_string());
        }

        self.current_state
            .users
            .insert(normalized_username.to_string(), payload);
        self.persist()?;
        self.reconcile_enforcement()
    }

    fn reconcile_enforcement(&mut self) -> Result<(), String> {
        polkit::remove_all_managed_rules()?;
        bluetooth::reconcile(false)?;

        let Some(active_user) = active_managed_user(
            self.active_session_username.as_deref(),
            &self.current_state.users,
        ) else {
            self.enforced_username = None;
            let _ = crate::extension_policy::run_reconcile(None, None);
            return Ok(());
        };

        let Some(payload) = self.current_state.users.get(active_user).cloned() else {
            self.enforced_username = None;
            let _ = crate::extension_policy::run_reconcile(None, None);
            return Ok(());
        };

        polkit::reconcile(active_user, &payload)?;
        bluetooth::reconcile(payload.connectivity.bluetooth_disabled)?;
        self.enforced_username = Some(active_user.to_string());

        let _ = crate::extension_policy::run_reconcile(Some(active_user), Some(&payload.chrome));
        Ok(())
    }

    fn persist(&self) -> Result<(), String> {
        let serialized = serde_json::to_string_pretty(&self.current_state)
            .map_err(|e| format!("failed to serialize linux device policy state: {e}"))?;
        if let Some(parent) = self.state_path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("failed to create linux device policy state dir: {e}"))?;
        }
        let temp_path = self.state_path.with_extension("json.tmp");
        fs::write(&temp_path, &serialized)
            .map_err(|e| format!("failed to write linux device policy state: {e}"))?;
        fs::rename(&temp_path, &self.state_path)
            .map_err(|e| format!("failed to finalize linux device policy state: {e}"))?;
        Ok(())
    }
}

fn state_path() -> PathBuf {
    let primary = PathBuf::from(STATE_DIR_PRIMARY);
    if fs::create_dir_all(&primary).is_ok() {
        return primary.join(STATE_FILENAME);
    }
    let fallback = PathBuf::from(STATE_DIR_FALLBACK);
    let _ = fs::create_dir_all(&fallback);
    fallback.join(STATE_FILENAME)
}

pub fn parse_device_policy(value: Option<&serde_json::Value>) -> DevicePolicyPayload {
    value
        .and_then(|raw| serde_json::from_value(raw.clone()).ok())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_device_policy_defaults() {
        let payload = parse_device_policy(None);
        assert!(!payload.polkit.install_software_disabled);
        assert!(!payload.exec.terminal_access_disabled);
    }

    #[test]
    fn terminal_block_requires_enforced_session_user() {
        let policy = DevicePolicyPayload {
            exec: ExecPolicy {
                terminal_access_disabled: true,
            },
            ..DevicePolicyPayload::default()
        };
        assert!(terminal_exec_block_applies(
            Some("child"),
            "child",
            Some(&policy),
            "/usr/bin/bash",
        ));
        assert!(!terminal_exec_block_applies(
            Some("child"),
            "parent",
            Some(&policy),
            "/usr/bin/bash",
        ));
    }

    #[test]
    fn active_managed_user_requires_catalog_entry() {
        let mut users = HashMap::new();
        users.insert(
            "child".to_string(),
            DevicePolicyPayload {
                polkit: PolkitPolicy {
                    install_software_disabled: true,
                    ..PolkitPolicy::default()
                },
                ..DevicePolicyPayload::default()
            },
        );
        assert_eq!(
            active_managed_user(Some("child"), &users),
            Some("child")
        );
        assert_eq!(active_managed_user(None, &users), None);
        assert_eq!(active_managed_user(Some("parent"), &users), None);
    }
}

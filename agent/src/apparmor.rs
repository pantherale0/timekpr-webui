use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, OnceLock};
use tokio::sync::Mutex;

const PROFILE_DIR: &str = "/etc/apparmor.d";
const STATE_DIR_PRIMARY: &str = "/var/lib/timekpr-agent";
const STATE_DIR_FALLBACK: &str = "/etc/timekpr-agent";
const STATE_FILENAME: &str = "apparmor-policy.json";

static APPARMOR_RUNTIME: OnceLock<Arc<Mutex<AppArmorRuntime>>> = OnceLock::new();

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq)]
pub struct AppArmorPolicy {
    pub application_name: String,
    pub executable_path: String,
    pub preset: String, // "allowed", "no_internet", "blocked", "complain"
}

#[derive(Deserialize, Serialize, Clone, Debug, Default, PartialEq, Eq)]
struct PersistedAppArmorState {
    /// Per-linux-username list of active policies
    users: HashMap<String, Vec<AppArmorPolicy>>,
}

struct AppArmorRuntime {
    state_path: PathBuf,
    current_state: PersistedAppArmorState,
    restored: bool,
    /// Track which users currently have loaded profiles
    loaded_users: std::collections::HashSet<String>,
}

pub async fn initialize_runtime() -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()
}

pub async fn sync_user_policy(
    username: &str,
    policies: Vec<AppArmorPolicy>,
) -> Result<String, String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()?;
    guard.update_user_policy(username, policies)
}

pub async fn load_profiles_for_user(username: &str) -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()?;
    guard.load_user_profiles(username)
}

pub async fn unload_profiles_for_user(username: &str) -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored()?;
    guard.unload_user_profiles(username)
}

pub async fn get_monitored_executables() -> HashMap<String, Vec<AppArmorPolicy>> {
    let runtime = get_runtime();
    let guard = runtime.lock().await;
    guard.current_state.users.clone()
}

pub fn is_apparmor_available() -> bool {
    // Check if AppArmor is enabled in the kernel
    if let Ok(content) = fs::read_to_string("/sys/module/apparmor/parameters/enabled") {
        if content.trim() == "Y" {
            return true;
        }
    }
    false
}

fn get_runtime() -> Arc<Mutex<AppArmorRuntime>> {
    APPARMOR_RUNTIME
        .get_or_init(|| Arc::new(Mutex::new(AppArmorRuntime::new())))
        .clone()
}

impl AppArmorRuntime {
    fn new() -> Self {
        Self {
            state_path: state_path(),
            current_state: PersistedAppArmorState::default(),
            restored: false,
            loaded_users: std::collections::HashSet::new(),
        }
    }

    fn ensure_restored(&mut self) -> Result<(), String> {
        if self.restored {
            return Ok(());
        }
        self.restored = true;

        if self.state_path.exists() {
            let raw = fs::read_to_string(&self.state_path)
                .map_err(|e| format!("failed to read apparmor state: {}", e))?;
            let state: PersistedAppArmorState = serde_json::from_str(&raw)
                .map_err(|e| format!("failed to parse apparmor state: {}", e))?;
            let (sanitized_state, removed_rules) = sanitize_persisted_state(state);
            self.current_state = sanitized_state;
            if removed_rules > 0 {
                eprintln!(
                    "Removed {} unsafe AppArmor rule(s) from persisted state",
                    removed_rules
                );
                self.persist()?;
            }
        }
        Ok(())
    }

    fn update_user_policy(
        &mut self,
        username: &str,
        policies: Vec<AppArmorPolicy>,
    ) -> Result<String, String> {
        let mut restrictive = Vec::new();
        for policy in policies
            .into_iter()
            .filter(|p| p.preset == "no_internet" || p.preset == "blocked" || p.preset == "complain")
        {
            validate_executable_path(&policy.executable_path).map_err(|err| {
                format!(
                    "refusing unsafe AppArmor path for {}: {}",
                    policy.application_name, err
                )
            })?;
            restrictive.push(policy);
        }

        if restrictive.is_empty() {
            self.current_state.users.remove(username);
        } else {
            self.current_state
                .users
                .insert(username.to_string(), restrictive);
        }

        self.persist()?;

        // If user profiles are currently loaded, refresh them
        if self.loaded_users.contains(username) {
            self.unload_user_profiles(username)?;
            self.load_user_profiles(username)?;
        }

        let count = self
            .current_state
            .users
            .get(username)
            .map_or(0, |v| v.len());
        Ok(format!(
            "AppArmor policy updated for {}: {} restrictive rule(s)",
            username, count
        ))
    }

    fn load_user_profiles(&mut self, username: &str) -> Result<(), String> {
        if !is_apparmor_available() {
            eprintln!("AppArmor is not available on this system; skipping profile load for {}", username);
            return Ok(());
        }

        let policies = match self.current_state.users.get(username) {
            Some(policies) if !policies.is_empty() => policies.clone(),
            _ => {
                self.loaded_users.insert(username.to_string());
                return Ok(());
            }
        };

        let profile_dir = PathBuf::from(PROFILE_DIR);
        let _ = fs::create_dir_all(&profile_dir);

        for policy in &policies {
            if let Err(err) = validate_executable_path(&policy.executable_path) {
                eprintln!(
                    "Skipping unsafe AppArmor path for {} ({}): {}",
                    policy.application_name,
                    policy.executable_path,
                    err
                );
                continue;
            }
            let profile_name = make_profile_name(username, &policy.application_name);
            let profile_content = generate_profile(&profile_name, &policy.executable_path, &policy.preset);
            let profile_path = profile_dir.join(&profile_name);

            fs::write(&profile_path, &profile_content)
                .map_err(|e| format!("failed to write profile {}: {}", profile_name, e))?;

            let output = Command::new("apparmor_parser")
                .args(["-r", &profile_path.to_string_lossy()])
                .output()
                .map_err(|e| format!("failed to run apparmor_parser -r: {}", e))?;

            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                eprintln!(
                    "apparmor_parser -r failed for {}: {}",
                    profile_name,
                    stderr.trim()
                );
            } else {
                println!("Loaded AppArmor profile: {}", profile_name);
            }
        }

        self.loaded_users.insert(username.to_string());
        Ok(())
    }

    fn unload_user_profiles(&mut self, username: &str) -> Result<(), String> {
        if !is_apparmor_available() {
            self.loaded_users.remove(username);
            return Ok(());
        }

        // Unload ALL profiles for this user (look at both the persisted state
        // and any profiles on disk that match the naming convention).
        let profile_dir = PathBuf::from(PROFILE_DIR);
        let prefix = format!("timekpr-{}-", username);

        if let Ok(entries) = fs::read_dir(&profile_dir) {
            for entry in entries.flatten() {
                let file_name = entry.file_name().to_string_lossy().to_string();
                if file_name.starts_with(&prefix) {
                    let profile_path = entry.path();
                    let output = Command::new("apparmor_parser")
                        .args(["-R", &profile_path.to_string_lossy()])
                        .output();

                    match output {
                        Ok(result) if result.status.success() => {
                            println!("Unloaded AppArmor profile: {}", file_name);
                        }
                        Ok(result) => {
                            let stderr = String::from_utf8_lossy(&result.stderr);
                            eprintln!(
                                "apparmor_parser -R failed for {}: {}",
                                file_name,
                                stderr.trim()
                            );
                        }
                        Err(e) => {
                            eprintln!("Failed to run apparmor_parser -R for {}: {}", file_name, e);
                        }
                    }

                    // Remove the profile file
                    let _ = fs::remove_file(&profile_path);
                }
            }
        }

        self.loaded_users.remove(username);
        Ok(())
    }

    fn persist(&self) -> Result<(), String> {
        let serialized = serde_json::to_string_pretty(&self.current_state)
            .map_err(|e| format!("failed to serialize apparmor state: {}", e))?;
        let temp_path = self.state_path.with_extension("json.tmp");
        fs::write(&temp_path, &serialized)
            .map_err(|e| format!("failed to write apparmor state: {}", e))?;
        fs::rename(&temp_path, &self.state_path)
            .map_err(|e| format!("failed to finalize apparmor state: {}", e))?;
        Ok(())
    }
}

fn sanitize_persisted_state(mut state: PersistedAppArmorState) -> (PersistedAppArmorState, usize) {
    let mut removed_rules = 0;
    state.users.retain(|username, policies| {
        policies.retain(|policy| {
            if let Err(err) = validate_executable_path(&policy.executable_path) {
                eprintln!(
                    "Dropping unsafe persisted AppArmor path for {} ({}): {}",
                    username, policy.executable_path, err
                );
                removed_rules += 1;
                false
            } else {
                true
            }
        });
        !policies.is_empty()
    });
    (state, removed_rules)
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

fn make_profile_name(username: &str, app_name: &str) -> String {
    let sanitized_app: String = app_name
        .chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .collect();
    format!("timekpr-{}-{}", username, sanitized_app.to_lowercase())
}

fn validate_executable_path(executable_path: &str) -> Result<(), String> {
    let normalized = executable_path.trim();
    if normalized.is_empty() {
        return Err("path is empty".to_string());
    }
    if !Path::new(normalized).is_absolute() {
        return Err("path must be absolute".to_string());
    }
    if normalized.ends_with('/') {
        return Err("path must reference a file, not a directory".to_string());
    }
    if normalized.chars().any(|c| matches!(c, '*' | '?' | '[' | ']' | '{' | '}' | '"')) {
        return Err("path must not contain glob or attachment metacharacters".to_string());
    }
    if normalized.chars().any(char::is_whitespace) {
        return Err("paths containing whitespace are not supported".to_string());
    }
    Ok(())
}

fn generate_profile(profile_name: &str, executable_path: &str, preset: &str) -> String {
    match preset {
        "complain" => format!(
            r#"# Timekpr managed profile – REPORT ONLY
profile {profile_name} {executable_path} flags=(default_allow) {{
  # Browsers generate far too much log traffic under sparse complain-mode
  # profiles. Use default_allow and audit only selected activity so the app
  # remains unrestricted without overwhelming the desktop with audit spam.
  audit network inet,
  audit network inet6,
  audit network netlink,
}}
"#,
            profile_name = profile_name,
            executable_path = executable_path,
        ),
        "blocked" => format!(
            r#"# Timekpr managed profile – BLOCK execution
profile {profile_name} {executable_path} {{
  # Deny everything
  deny /** rwlkx,
  deny network,
  deny capability,
}}
"#,
            profile_name = profile_name,
            executable_path = executable_path,
        ),
        "no_internet" => format!(
            r#"# Timekpr managed profile – NO INTERNET
profile {profile_name} {executable_path} {{
  # Allow standard file access
  #include <abstractions/base>
  #include <abstractions/fonts>
  #include <abstractions/X>

  # Allow reading own binary and libraries
  {executable_path} mr,
  /usr/lib/** mr,
  /lib/** mr,
  /usr/share/** r,
  /etc/** r,
  /tmp/** rwk,
  /run/** rw,

  owner @{{HOME}}/** rw,
  owner /proc/** r,

  # DENY all network access
  deny network inet,
  deny network inet6,
  deny network netlink,
}}
"#,
            profile_name = profile_name,
            executable_path = executable_path,
        ),
        _ => String::new(), // "allowed" generates no profile
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_name_sanitizes_special_characters() {
        assert_eq!(
            make_profile_name("alice", "Google Chrome"),
            "timekpr-alice-google_chrome"
        );
    }

    #[test]
    fn blocked_profile_denies_everything() {
        let profile = generate_profile("timekpr-alice-steam", "/usr/bin/steam", "blocked");
        assert!(profile.contains("deny /** rwlkx,"));
        assert!(profile.contains("deny network,"));
    }

    #[test]
    fn no_internet_profile_denies_network() {
        let profile = generate_profile("timekpr-bob-firefox", "/usr/bin/firefox", "no_internet");
        assert!(profile.contains("deny network inet,"));
        assert!(profile.contains("deny network inet6,"));
        assert!(!profile.contains("deny /** rwlkx,"));
    }

    #[test]
    fn complain_profile_denies_everything_with_complain_flag() {
        let profile = generate_profile("timekpr-alice-steam", "/usr/bin/steam", "complain");
        assert!(profile.contains("flags=(default_allow)"));
        assert!(profile.contains("audit network inet,"));
        assert!(profile.contains("audit network inet6,"));
        assert!(profile.contains("audit network netlink,"));
        assert!(!profile.contains("deny /** rwlkx,"));
        assert!(!profile.contains("deny network,"));
    }

    #[test]
    fn allowed_preset_generates_empty_profile() {
        let profile = generate_profile("timekpr-bob-vlc", "/usr/bin/vlc", "allowed");
        assert!(profile.is_empty());
    }

    #[test]
    fn executable_path_validation_accepts_concrete_absolute_paths() {
        assert!(validate_executable_path("/usr/bin/google-chrome").is_ok());
    }

    #[test]
    fn executable_path_validation_rejects_glob_patterns() {
        let error = validate_executable_path("/usr/bin/**").unwrap_err();
        assert!(error.contains("glob"));
    }

    #[test]
    fn state_roundtrip_serialization() {
        let mut state = PersistedAppArmorState::default();
        state.users.insert(
            "alice".to_string(),
            vec![AppArmorPolicy {
                application_name: "Steam".to_string(),
                executable_path: "/usr/bin/steam".to_string(),
                preset: "blocked".to_string(),
            }],
        );

        let json = serde_json::to_string_pretty(&state).unwrap();
        let restored: PersistedAppArmorState = serde_json::from_str(&json).unwrap();
        assert_eq!(state, restored);
    }
}

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, OnceLock};
use tokio::sync::Mutex;
use users::get_user_by_name;
use users::os::unix::UserExt;

const PROFILE_DIR: &str = "/etc/apparmor.d";
const STATE_DIR_PRIMARY: &str = "/var/lib/timekpr-agent";
const STATE_DIR_FALLBACK: &str = "/etc/timekpr-agent";
const STATE_FILENAME: &str = "apparmor-policy.json";
const MATCH_TYPE_EXECUTABLE: &str = "executable";
const MATCH_TYPE_PATH_PATTERN: &str = "path_pattern";
const GLOBAL_PROFILE_PREFIX: &str = "timekpr-global-exec";
const PATH_PATTERN_SUFFIX: &str = "/**";
const SCRIPT_INTERPRETERS: &[&str] = &[
    "bash",
    "sh",
    "zsh",
    "fish",
    "python",
    "python3",
    "node",
    "ruby",
    "perl",
    "php",
];
const GLOBAL_EXEC_ATTACHMENTS: [(&str, &str); 4] = [
    ("usrbin", "/usr/bin/**"),
    ("bin", "/bin/**"),
    ("usrsbin", "/usr/sbin/**"),
    ("sbin", "/sbin/**"),
];

static APPARMOR_RUNTIME: OnceLock<Arc<Mutex<AppArmorRuntime>>> = OnceLock::new();

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq)]
pub struct AppArmorPolicy {
    pub application_name: String,
    pub executable_path: String,
    #[serde(default = "default_match_type")]
    pub match_type: String,
    pub preset: String, // "allowed", "no_internet", "blocked", "complain"
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExecDecision {
    pub preset: String,
    pub rule_name: String,
    pub rule_target: String,
    pub matched_path: String,
    pub matched_via: String,
}

#[derive(Clone, Debug)]
struct ResolvedPathRule {
    application_name: String,
    pattern: String,
    expanded_pattern: String,
    preset: String,
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

pub async fn evaluate_exec_event(
    username: &str,
    exe_path: &str,
    argv: &[String],
    cwd: Option<&str>,
) -> Option<ExecDecision> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    if guard.ensure_restored().is_err() {
        return None;
    }
    guard.evaluate_exec_event(username, exe_path, argv, cwd)
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

fn default_match_type() -> String {
    MATCH_TYPE_EXECUTABLE.to_string()
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
        for policy in policies.into_iter().filter(|p| {
            p.preset == "no_internet" || p.preset == "blocked" || p.preset == "complain"
        }) {
            restrictive.push(
                sanitize_policy(policy)
                    .map_err(|err| format!("refusing unsafe AppArmor rule: {}", err))?,
            );
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
            self.unload_exact_profiles_for_user(username)?;
            self.load_exact_profiles_for_user(username)?;
            self.refresh_global_exec_profiles()?;
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
            eprintln!(
                "AppArmor is not available on this system; skipping profile load for {}",
                username
            );
            self.loaded_users.insert(username.to_string());
            return Ok(());
        }

        self.loaded_users.insert(username.to_string());
        self.load_exact_profiles_for_user(username)?;
        self.refresh_global_exec_profiles()?;
        Ok(())
    }

    fn unload_user_profiles(&mut self, username: &str) -> Result<(), String> {
        if !is_apparmor_available() {
            self.loaded_users.remove(username);
            return Ok(());
        }

        self.unload_exact_profiles_for_user(username)?;
        self.loaded_users.remove(username);
        self.refresh_global_exec_profiles()?;
        Ok(())
    }

    fn load_exact_profiles_for_user(&self, username: &str) -> Result<(), String> {
        let policies = self
            .current_state
            .users
            .get(username)
            .cloned()
            .unwrap_or_default();
        let exact_policies: Vec<AppArmorPolicy> = policies
            .into_iter()
            .filter(|policy| {
                policy.match_type == MATCH_TYPE_EXECUTABLE && policy.preset != "complain"
            })
            .collect();
        if exact_policies.is_empty() {
            return Ok(());
        }

        let path_rule_lines = self.render_blocked_path_exec_lines_for_user(username);
        for policy in &exact_policies {
            let profile_name = make_profile_name(username, &policy.application_name);
            let profile_content = generate_profile(
                &profile_name,
                &policy.executable_path,
                &policy.preset,
                &path_rule_lines,
            );
            write_and_load_profile(&profile_name, &profile_content)?;
        }
        Ok(())
    }

    fn unload_exact_profiles_for_user(&self, username: &str) -> Result<(), String> {
        unload_profiles_with_prefix(&format!("timekpr-{}-", username))
    }

    fn refresh_global_exec_profiles(&self) -> Result<(), String> {
        unload_profiles_with_prefix(GLOBAL_PROFILE_PREFIX)?;

        let enforced_rules: Vec<ResolvedPathRule> = self
            .collect_active_path_rules()
            .into_iter()
            .filter(|rule| rule.preset == "blocked")
            .collect();
        if enforced_rules.is_empty() {
            return Ok(());
        }

        for (suffix, attachment) in GLOBAL_EXEC_ATTACHMENTS {
            let profile_name = format!("{}-{}", GLOBAL_PROFILE_PREFIX, suffix);
            let profile_content =
                generate_global_exec_profile(&profile_name, attachment, &enforced_rules);
            write_and_load_profile(&profile_name, &profile_content)?;
        }
        Ok(())
    }

    fn collect_active_path_rules(&self) -> Vec<ResolvedPathRule> {
        let mut rules = Vec::new();
        for username in &self.loaded_users {
            rules.extend(self.collect_path_rules_for_user(username));
        }
        rules
    }

    fn collect_path_rules_for_user(&self, username: &str) -> Vec<ResolvedPathRule> {
        self.current_state
            .users
            .get(username)
            .into_iter()
            .flatten()
            .filter(|policy| policy.match_type == MATCH_TYPE_PATH_PATTERN)
            .filter_map(|policy| {
                expand_path_pattern(username, &policy.executable_path)
                    .map(|expanded_pattern| ResolvedPathRule {
                        application_name: policy.application_name.clone(),
                        pattern: policy.executable_path.clone(),
                        expanded_pattern,
                        preset: policy.preset.clone(),
                    })
                    .map_err(|err| {
                        eprintln!(
                            "Skipping invalid path rule for {} ({}): {}",
                            username, policy.executable_path, err
                        );
                    })
                    .ok()
            })
            .collect()
    }

    fn render_blocked_path_exec_lines_for_user(&self, username: &str) -> String {
        let lines: Vec<String> = self
            .collect_path_rules_for_user(username)
            .into_iter()
            .filter(|rule| rule.preset == "blocked")
            .map(|rule| format!("  deny {} x,\n", rule.expanded_pattern))
            .collect();
        lines.join("")
    }

    fn evaluate_exec_event(
        &self,
        username: &str,
        exe_path: &str,
        argv: &[String],
        cwd: Option<&str>,
    ) -> Option<ExecDecision> {
        let mut path_rules = self.collect_path_rules_for_user(username);
        path_rules.sort_by(|left, right| right.expanded_pattern.len().cmp(&left.expanded_pattern.len()));

        let normalized_exe_path = normalize_runtime_path(exe_path);
        if let Some(rule) = path_rules
            .iter()
            .find(|rule| path_pattern_matches(&rule.expanded_pattern, &normalized_exe_path))
        {
            return Some(ExecDecision {
                preset: rule.preset.clone(),
                rule_name: rule.application_name.clone(),
                rule_target: rule.pattern.clone(),
                matched_path: normalized_exe_path,
                matched_via: "direct_exec".to_string(),
            });
        }

        let normalized_policy_exe = normalized_executable_match_path(exe_path);
        if let Some(policy) = self
            .current_state
            .users
            .get(username)
            .into_iter()
            .flatten()
            .find(|policy| {
                policy.match_type == MATCH_TYPE_EXECUTABLE
                    && policy.preset == "complain"
                    && normalized_executable_match_path(&policy.executable_path) == normalized_policy_exe
            })
        {
            return Some(ExecDecision {
                preset: policy.preset.clone(),
                rule_name: policy.application_name.clone(),
                rule_target: policy.executable_path.clone(),
                matched_path: normalized_exe_path,
                matched_via: "exact_exec_rule".to_string(),
            });
        }

        if !is_script_interpreter(exe_path) {
            return None;
        }

        let candidate_path = resolve_interpreter_target(argv, cwd)?;
        path_rules
            .iter()
            .find(|rule| path_pattern_matches(&rule.expanded_pattern, &candidate_path))
            .map(|rule| ExecDecision {
                preset: rule.preset.clone(),
                rule_name: rule.application_name.clone(),
                rule_target: rule.pattern.clone(),
                matched_path: candidate_path,
                matched_via: "interpreter_arg".to_string(),
            })
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
            if let Err(err) = validate_policy(policy) {
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

fn normalized_match_type(match_type: &str) -> &str {
    if match_type == MATCH_TYPE_PATH_PATTERN {
        MATCH_TYPE_PATH_PATTERN
    } else {
        MATCH_TYPE_EXECUTABLE
    }
}

fn validate_policy(policy: &AppArmorPolicy) -> Result<(), String> {
    match normalized_match_type(&policy.match_type) {
        MATCH_TYPE_EXECUTABLE => validate_executable_path(&policy.executable_path),
        MATCH_TYPE_PATH_PATTERN => {
            if policy.preset == "no_internet" {
                return Err("path rules do not support the no_internet preset".to_string());
            }
            let _ = normalize_path_pattern(&policy.executable_path)?;
            Ok(())
        }
        _ => Err("unsupported match type".to_string()),
    }
}

fn sanitize_policy(mut policy: AppArmorPolicy) -> Result<AppArmorPolicy, String> {
    policy.match_type = normalized_match_type(&policy.match_type).to_string();
    if policy.match_type == MATCH_TYPE_PATH_PATTERN {
        policy.executable_path = normalize_path_pattern(&policy.executable_path)?;
    } else {
        validate_executable_path(&policy.executable_path)?;
    }
    validate_policy(&policy)?;
    Ok(policy)
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

fn normalize_path_pattern(path_pattern: &str) -> Result<String, String> {
    let mut normalized = path_pattern.trim().to_string();
    if normalized.is_empty() {
        return Err("path pattern is empty".to_string());
    }
    if normalized.starts_with("/home/$USER/") {
        normalized = format!("$HOME/{}", &normalized["/home/$USER/".len()..]);
    }
    if !normalized.starts_with("$HOME/") {
        return Err("path patterns must stay under $HOME/ or /home/$USER/".to_string());
    }
    if !normalized.ends_with(PATH_PATTERN_SUFFIX) {
        return Err("path patterns must end with /**".to_string());
    }
    let base = &normalized[..normalized.len() - PATH_PATTERN_SUFFIX.len()];
    if base.is_empty()
        || base.contains('*')
        || base.contains('?')
        || base.contains('[')
        || base.contains(']')
        || base.contains('{')
        || base.contains('}')
        || base.contains('"')
    {
        return Err("only a trailing /** glob is supported for path rules".to_string());
    }
    if normalized.contains("/./")
        || normalized.contains("/../")
        || normalized.ends_with("/.")
        || normalized.ends_with("/..")
        || normalized.chars().any(char::is_whitespace)
    {
        return Err("path patterns must not contain relative segments or whitespace".to_string());
    }
    Ok(normalized)
}

fn expand_path_pattern(username: &str, path_pattern: &str) -> Result<String, String> {
    let normalized = normalize_path_pattern(path_pattern)?;
    let home_dir = get_user_by_name(username)
        .map(|user| user.home_dir().to_path_buf())
        .ok_or_else(|| format!("failed to resolve home directory for {}", username))?;
    let home_str = home_dir.to_string_lossy().trim_end_matches('/').to_string();
    Ok(format!("{}{}", home_str, &normalized["$HOME".len()..]))
}

fn path_pattern_matches(expanded_pattern: &str, candidate_path: &str) -> bool {
    let Some(root) = expanded_pattern.strip_suffix(PATH_PATTERN_SUFFIX) else {
        return false;
    };
    candidate_path == root
        || candidate_path
            .strip_prefix(root)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

fn normalize_runtime_path(path: &str) -> String {
    fs::canonicalize(path)
        .unwrap_or_else(|_| PathBuf::from(path))
        .to_string_lossy()
        .to_string()
}

fn normalized_executable_match_path(path: &str) -> String {
    normalize_runtime_path(path)
}

fn normalize_candidate_path(candidate: &str, cwd: Option<&str>) -> Option<String> {
    let candidate_path = PathBuf::from(candidate);
    let resolved = if candidate_path.is_absolute() {
        candidate_path
    } else if let Some(cwd) = cwd {
        PathBuf::from(cwd).join(candidate_path)
    } else {
        return None;
    };
    Some(normalize_runtime_path(&resolved.to_string_lossy()))
}

fn resolve_interpreter_target(argv: &[String], cwd: Option<&str>) -> Option<String> {
    let mut args = argv.iter().skip(1);
    while let Some(arg) = args.next() {
        if arg == "-c" || arg == "--command" {
            return None;
        }
        if arg.starts_with('-') {
            continue;
        }
        return normalize_candidate_path(arg, cwd);
    }
    None
}

fn is_script_interpreter(exe_path: &str) -> bool {
    let name = Path::new(exe_path)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    SCRIPT_INTERPRETERS
        .iter()
        .any(|candidate| name == *candidate || name.starts_with(&format!("{}.", candidate)))
}

fn write_and_load_profile(profile_name: &str, profile_content: &str) -> Result<(), String> {
    let profile_dir = PathBuf::from(PROFILE_DIR);
    let _ = fs::create_dir_all(&profile_dir);
    let profile_path = profile_dir.join(profile_name);
    fs::write(&profile_path, profile_content)
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
    Ok(())
}

fn unload_profiles_with_prefix(prefix: &str) -> Result<(), String> {
    let profile_dir = PathBuf::from(PROFILE_DIR);
    if let Ok(entries) = fs::read_dir(&profile_dir) {
        for entry in entries.flatten() {
            let file_name = entry.file_name().to_string_lossy().to_string();
            if file_name.starts_with(prefix) {
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
                        return Err(format!("failed to run apparmor_parser -R for {}: {}", file_name, e));
                    }
                }

                let _ = fs::remove_file(&profile_path);
            }
        }
    }
    Ok(())
}

fn generate_path_exec_lines(path_rules: &[ResolvedPathRule]) -> String {
    path_rules
        .iter()
        .filter(|rule| rule.preset == "blocked")
        .map(|rule| format!("  deny {} x,\n", rule.expanded_pattern))
        .collect()
}

fn generate_global_exec_profile(
    profile_name: &str,
    attachment_path: &str,
    path_rules: &[ResolvedPathRule],
) -> String {
    format!(
        r#"# Timekpr managed global path-exec baseline
profile {profile_name} {attachment_path} flags=(default_allow) {{
  #include <abstractions/base>
{path_rule_lines}}}
"#,
        profile_name = profile_name,
        attachment_path = attachment_path,
        path_rule_lines = generate_path_exec_lines(path_rules),
    )
}

fn generate_profile(
    profile_name: &str,
    executable_path: &str,
    preset: &str,
    blocked_path_rule_lines: &str,
) -> String {
    match preset {
        "complain" => String::new(),
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

{blocked_path_rule_lines}  # DENY all network access
  # DENY all network access
  deny network inet,
  deny network inet6,
  deny network netlink,
}}
"#,
            profile_name = profile_name,
            executable_path = executable_path,
            blocked_path_rule_lines = blocked_path_rule_lines,
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
        let profile = generate_profile("timekpr-alice-steam", "/usr/bin/steam", "blocked", "");
        assert!(profile.contains("deny /** rwlkx,"));
        assert!(profile.contains("deny network,"));
    }

    #[test]
    fn no_internet_profile_denies_network() {
        let profile = generate_profile("timekpr-bob-firefox", "/usr/bin/firefox", "no_internet", "");
        assert!(profile.contains("deny network inet,"));
        assert!(profile.contains("deny network inet6,"));
        assert!(!profile.contains("deny /** rwlkx,"));
    }

    #[test]
    fn complain_profile_generates_no_apparmor_profile() {
        let profile = generate_profile("timekpr-alice-steam", "/usr/bin/steam", "complain", "");
        assert!(profile.is_empty());
    }

    #[test]
    fn allowed_preset_generates_empty_profile() {
        let profile = generate_profile("timekpr-bob-vlc", "/usr/bin/vlc", "allowed", "");
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
    fn path_pattern_validation_accepts_home_subtrees() {
        assert_eq!(
            normalize_path_pattern("/home/$USER/Downloads/**").unwrap(),
            "$HOME/Downloads/**"
        );
    }

    #[test]
    fn path_pattern_matches_direct_child_paths() {
        assert!(path_pattern_matches(
            "/home/alice/Downloads/**",
            "/home/alice/Downloads/game.AppImage"
        ));
        assert!(!path_pattern_matches(
            "/home/alice/Downloads/**",
            "/home/alice/Documents/note.txt"
        ));
    }

    #[test]
    fn state_roundtrip_serialization() {
        let mut state = PersistedAppArmorState::default();
        state.users.insert(
            "alice".to_string(),
            vec![AppArmorPolicy {
                application_name: "Steam".to_string(),
                executable_path: "/usr/bin/steam".to_string(),
                match_type: MATCH_TYPE_EXECUTABLE.to_string(),
                preset: "blocked".to_string(),
            }],
        );

        let json = serde_json::to_string_pretty(&state).unwrap();
        let restored: PersistedAppArmorState = serde_json::from_str(&json).unwrap();
        assert_eq!(state, restored);
    }

    #[test]
    fn exact_exec_report_only_is_decided_by_exec_monitor() {
        let mut runtime = AppArmorRuntime::new();
        runtime.current_state.users.insert(
            "alice".to_string(),
            vec![AppArmorPolicy {
                application_name: "Steam".to_string(),
                executable_path: "/usr/bin/steam".to_string(),
                match_type: MATCH_TYPE_EXECUTABLE.to_string(),
                preset: "complain".to_string(),
            }],
        );

        let decision = runtime.evaluate_exec_event("alice", "/usr/bin/steam", &[], None);
        assert!(decision.is_some());
        let decision = decision.unwrap();
        assert_eq!(decision.preset, "complain");
        assert_eq!(decision.matched_via, "exact_exec_rule");
        assert_eq!(decision.rule_target, "/usr/bin/steam");
    }
}

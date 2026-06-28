#[cfg(target_os = "linux")]
use std::collections::HashMap;
#[cfg(target_os = "linux")]
use std::path::Path;
#[cfg(target_os = "linux")]
use std::sync::{Mutex, OnceLock};

#[cfg(target_os = "linux")]
use crate::apparmor;
#[cfg(target_os = "linux")]
use crate::installed_apps::{self, ReportableIndex};
#[cfg(target_os = "linux")]
use users::os::unix::UserExt;

#[cfg(target_os = "linux")]
const SYSTEM_BINARY_PREFIXES: &[&str] = &[
    "/usr/bin/",
    "/bin/",
    "/usr/sbin/",
    "/sbin/",
    "/usr/libexec/",
    "/usr/lib/",
    "/lib/",
    "/usr/lib64/",
    "/lib64/",
];

#[cfg(target_os = "linux")]
const PACKAGED_APP_PREFIXES: &[&str] = &[
    "/opt/",
    "/snap/",
    "/var/lib/flatpak/",
    "/var/lib/snapd/",
];

#[cfg(target_os = "linux")]
const UTILITY_BASENAMES: &[&str] = &[
    "sleep", "lsusb", "lsblk", "lspci", "grep", "awk", "sed", "cat", "echo", "printf", "test",
    "[", "cut", "sort", "uniq", "head", "tail", "wc", "date", "env", "id", "uname", "which",
    "type", "true", "false", "readlink", "dirname", "basename", "seq", "tr", "xargs", "find",
    "stat", "file", "du", "df", "mount", "ps", "pgrep", "pkill", "kill", "killall", "nice",
    "nohup", "timeout", "watch", "tee", "mktemp", "touch", "mkdir", "rm", "mv", "cp", "ln",
    "chmod", "chown", "ls", "pwd", "cd", "sh", "bash", "zsh", "dash", "fish",
];

#[cfg(target_os = "linux")]
static USER_INDEXES: OnceLock<Mutex<HashMap<String, ReportableIndex>>> = OnceLock::new();

#[cfg(target_os = "linux")]
fn user_indexes() -> &'static Mutex<HashMap<String, ReportableIndex>> {
    USER_INDEXES.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(target_os = "linux")]
pub fn refresh_user_index(username: &str) {
    let index = installed_apps::build_reportable_index(username);
    let mut guard = user_indexes()
        .lock()
        .expect("reporting filter index mutex poisoned");
    guard.insert(username.to_string(), index);
}

#[cfg(target_os = "linux")]
pub fn refresh_user_indexes(usernames: impl IntoIterator<Item = impl AsRef<str>>) {
    for username in usernames {
        refresh_user_index(username.as_ref());
    }
}

#[cfg(target_os = "linux")]
pub fn is_reportable_executable(username: &str, exe_path: &str) -> bool {
    if exe_path.is_empty() {
        return false;
    }

    let index = user_indexes()
        .lock()
        .expect("reporting filter index mutex poisoned")
        .get(username)
        .cloned()
        .unwrap_or_default();

    decide_reportable(username, exe_path, &index)
}

#[cfg(target_os = "linux")]
fn decide_reportable(username: &str, exe_path: &str, index: &ReportableIndex) -> bool {
    if is_under_user_home(username, exe_path) {
        return true;
    }

    if is_packaged_app_path(exe_path) {
        return true;
    }

    let basename = path_basename(exe_path);

    if index.absolute_paths.contains(exe_path) {
        return true;
    }

    if index.exec_basenames.contains(&basename) {
        return true;
    }

    if apparmor::executable_matches_configured_policy(username, exe_path) {
        return true;
    }

    if is_utility_basename(&basename) {
        return false;
    }

    if is_system_binary_path(exe_path) {
        return false;
    }

    true
}

#[cfg(target_os = "linux")]
fn is_under_user_home(username: &str, exe_path: &str) -> bool {
    let Some(home) = users::get_user_by_name(username).map(|user| user.home_dir().to_path_buf()) else {
        return false;
    };
    let home_prefix = format!("{}/", home.to_string_lossy().trim_end_matches('/'));
    exe_path.starts_with(&home_prefix) || exe_path == home.to_string_lossy()
}

#[cfg(target_os = "linux")]
fn is_packaged_app_path(exe_path: &str) -> bool {
    PACKAGED_APP_PREFIXES
        .iter()
        .any(|prefix| exe_path.starts_with(prefix))
}

#[cfg(target_os = "linux")]
fn is_system_binary_path(exe_path: &str) -> bool {
    SYSTEM_BINARY_PREFIXES
        .iter()
        .any(|prefix| exe_path.starts_with(prefix))
}

#[cfg(target_os = "linux")]
fn is_utility_basename(basename: &str) -> bool {
    UTILITY_BASENAMES
        .iter()
        .any(|candidate| *candidate == basename)
}

#[cfg(target_os = "linux")]
fn path_basename(exe_path: &str) -> String {
    Path::new(exe_path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(exe_path)
        .to_string()
}

#[cfg(not(target_os = "linux"))]
pub fn refresh_user_index(_username: &str) {}

#[cfg(not(target_os = "linux"))]
pub fn refresh_user_indexes(_usernames: impl IntoIterator<Item = impl AsRef<str>>) {}

#[cfg(not(target_os = "linux"))]
pub fn is_reportable_executable(_username: &str, _exe_path: &str) -> bool {
    true
}

#[cfg(target_os = "linux")]
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filters_system_utilities_without_inventory() {
        let index = ReportableIndex::default();
        assert!(!decide_reportable(
            "alice",
            "/usr/bin/sleep",
            &index
        ));
        assert!(!decide_reportable(
            "alice",
            "/usr/bin/lsusb",
            &index
        ));
    }

    #[test]
    fn reports_inventory_basename_match_for_system_path() {
        let mut index = ReportableIndex::default();
        index.exec_basenames.insert("firefox".to_string());
        assert!(decide_reportable(
            "alice",
            "/usr/bin/firefox",
            &index
        ));
    }

    #[test]
    fn reports_executables_under_user_home() {
        let Some(user) = users::get_current_username() else {
            return;
        };
        let username = user.to_string_lossy();
        let username = username.as_ref();
        let Some(user_record) = users::get_user_by_name(username) else {
            return;
        };
        let exe = format!(
            "{}/.local/bin/mygame",
            user_record.home_dir().display()
        );
        let index = ReportableIndex::default();
        assert!(decide_reportable(&username, &exe, &index));
    }

    #[test]
    fn filters_system_python_without_policy_or_inventory() {
        let index = ReportableIndex::default();
        assert!(!decide_reportable(
            "alice",
            "/usr/bin/python3",
            &index
        ));
    }

    #[test]
    fn reports_custom_opt_installations() {
        let index = ReportableIndex::default();
        assert!(decide_reportable(
            "alice",
            "/opt/steam/steam",
            &index
        ));
    }

    #[test]
    fn filters_utility_basenames_on_non_packaged_paths() {
        let index = ReportableIndex::default();
        assert!(!decide_reportable(
            "alice",
            "/custom/tools/bin/sleep",
            &index
        ));
    }
}

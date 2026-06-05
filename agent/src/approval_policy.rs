use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use users::get_user_by_name;
use users::os::unix::UserExt;

const MODE_ALLOWLIST: &str = "allowlist";
const MODE_BLOCKLIST: &str = "blocklist";

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ApprovalPolicy {
    pub app_launch_mode: String,
    #[serde(default)]
    pub approved_packages: Vec<String>,
    #[serde(default)]
    pub blocked_packages: Vec<String>,
}

impl ApprovalPolicy {
    pub fn parse(value: Option<&serde_json::Value>) -> Option<Self> {
        let object = value?.as_object()?;
        let mode = object
            .get("app_launch_mode")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        if mode != MODE_ALLOWLIST && mode != MODE_BLOCKLIST {
            return None;
        }

        Some(Self {
            app_launch_mode: mode,
            approved_packages: parse_string_array(object.get("approved_packages")),
            blocked_packages: parse_string_array(object.get("blocked_packages")),
        })
    }

    pub fn approved_set(&self) -> HashSet<String> {
        self.approved_packages
            .iter()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .collect()
    }

    pub fn blocked_set(&self) -> HashSet<String> {
        self.blocked_packages
            .iter()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .collect()
    }

    pub fn effective_blocked(
        rules_blocked: &HashSet<String>,
        approval: Option<&ApprovalPolicy>,
    ) -> HashSet<String> {
        let Some(approval) = approval else {
            return rules_blocked.clone();
        };
        let approved = approval.approved_set();
        approval
            .blocked_set()
            .into_iter()
            .filter(|identifier| !approved.contains(identifier))
            .collect()
    }

    pub fn executable_matches_blocked(
        &self,
        exe_path: &str,
        username: &str,
        effective_blocked: &HashSet<String>,
    ) -> bool {
        let approved = self.approved_set();
        if effective_blocked.is_empty() {
            return false;
        }
        let blocked_match = effective_blocked
            .iter()
            .any(|identifier| executable_matches_identifier(exe_path, identifier, username));
        if !blocked_match {
            return false;
        }
        !approved
            .iter()
            .any(|identifier| executable_matches_identifier(exe_path, identifier, username))
    }
}

const PATH_PATTERN_SUFFIX: &str = "/**";

pub fn executable_matches_identifier(exe_path: &str, identifier: &str, username: &str) -> bool {
    let normalized_exe = normalize_runtime_path(exe_path);
    let normalized_identifier = identifier.trim();
    if normalized_identifier.is_empty() {
        return false;
    }
    if normalized_identifier.ends_with(PATH_PATTERN_SUFFIX) {
        let expanded = expand_path_pattern(username, normalized_identifier).unwrap_or_default();
        if expanded.is_empty() {
            return false;
        }
        return path_pattern_matches(&expanded, &normalized_exe);
    }
    normalize_runtime_path(normalized_identifier) == normalized_exe
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

fn expand_path_pattern(username: &str, path_pattern: &str) -> Result<String, String> {
    let normalized = normalize_path_pattern(path_pattern)?;
    let home_dir = get_user_by_name(username)
        .map(|user| user.home_dir().to_path_buf())
        .ok_or_else(|| format!("failed to resolve home directory for {}", username))?;
    let home_str = home_dir.to_string_lossy().trim_end_matches('/').to_string();
    Ok(format!("{}{}", home_str, &normalized["$HOME".len()..]))
}

fn normalize_path_pattern(path_pattern: &str) -> Result<String, String> {
    let mut normalized = path_pattern.trim().to_string();
    if normalized.starts_with("/home/$USER/") {
        normalized = format!("$HOME/{}", &normalized["/home/$USER/".len()..]);
    }
    if !normalized.starts_with("$HOME/") {
        return Err("path patterns must stay under $HOME/".to_string());
    }
    if !normalized.ends_with(PATH_PATTERN_SUFFIX) {
        return Err("path patterns must end with /**".to_string());
    }
    Ok(normalized)
}

fn parse_string_array(value: Option<&serde_json::Value>) -> Vec<String> {
    let Some(array) = value.and_then(|value| value.as_array()) else {
        return Vec::new();
    };
    array
        .iter()
        .filter_map(|entry| entry.as_str())
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
        .map(str::to_string)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn effective_blocked_subtracts_approved() {
        let approval = ApprovalPolicy {
            app_launch_mode: MODE_ALLOWLIST.to_string(),
            approved_packages: vec!["/usr/bin/firefox".to_string()],
            blocked_packages: vec![
                "/usr/bin/firefox".to_string(),
                "/usr/bin/steam".to_string(),
            ],
        };
        let effective = ApprovalPolicy::effective_blocked(&HashSet::new(), Some(&approval));
        assert!(!effective.contains("/usr/bin/firefox"));
        assert!(effective.contains("/usr/bin/steam"));
    }

    #[test]
    fn executable_identifier_matches_exact_path() {
        let temp_dir = std::env::temp_dir();
        let exe = temp_dir.join("timekpr-test-exe");
        let _ = std::fs::write(&exe, b"");
        let path = exe.to_string_lossy().to_string();
        assert!(executable_matches_identifier(&path, &path, "user"));
    }

    #[test]
    fn open_mode_uses_rules_blocked_only() {
        let rules = HashSet::from(["/usr/bin/steam".to_string()]);
        let effective = ApprovalPolicy::effective_blocked(&rules, None);
        assert_eq!(effective, rules);
    }
}

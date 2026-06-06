use std::fs;
use std::path::PathBuf;

use super::DevicePolicyPayload;

const POLKIT_RULES_DIR: &str = "/etc/polkit-1/rules.d";
const RULE_PREFIX: &str = "50-timekpr-";

pub fn reconcile(username: &str, payload: &DevicePolicyPayload) -> Result<(), String> {
    remove_all_managed_rules()?;
    let rules_path = rule_path_for_user(username);
    if !any_polkit_restrictions(payload) {
        return Ok(());
    }

    fs::create_dir_all(POLKIT_RULES_DIR)
        .map_err(|e| format!("failed to create polkit rules directory: {e}"))?;
    let content = render_rules(username, payload);
    let temp_path = rules_path.with_extension("rules.tmp");
    fs::write(&temp_path, content)
        .map_err(|e| format!("failed to write polkit rules for {username}: {e}"))?;
    fs::rename(&temp_path, &rules_path)
        .map_err(|e| format!("failed to finalize polkit rules for {username}: {e}"))?;
    Ok(())
}

pub fn remove_all_managed_rules() -> Result<(), String> {
    let dir = PathBuf::from(POLKIT_RULES_DIR);
    if !dir.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(&dir).map_err(|e| format!("failed to read polkit rules dir: {e}"))? {
        let entry = entry.map_err(|e| format!("failed to read polkit rules entry: {e}"))?;
        let file_name = entry.file_name();
        let Some(name) = file_name.to_str() else {
            continue;
        };
        if name.starts_with(RULE_PREFIX) && name.ends_with(".rules") {
            fs::remove_file(entry.path())
                .map_err(|e| format!("failed to remove polkit rules file {name}: {e}"))?;
        }
    }
    Ok(())
}

fn any_polkit_restrictions(payload: &DevicePolicyPayload) -> bool {
    payload.polkit.install_software_disabled
        || payload.polkit.uninstall_software_disabled
        || payload.polkit.mount_removable_media_disabled
        || payload.polkit.modify_accounts_disabled
        || payload.polkit.system_power_actions_disabled
        || payload.polkit.pkexec_elevation_disabled
        || payload.polkit.flatpak_install_disabled
        || payload.polkit.snap_install_disabled
}

fn rule_path_for_user(username: &str) -> PathBuf {
    let sanitized: String = username
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    PathBuf::from(POLKIT_RULES_DIR).join(format!("{RULE_PREFIX}{sanitized}.rules"))
}

fn render_rules(username: &str, payload: &DevicePolicyPayload) -> String {
    let escaped_user = username.replace('\\', "\\\\").replace('"', "\\\"");
    let mut checks = Vec::new();

    if payload.polkit.install_software_disabled {
        checks.push(
            "action.id.indexOf(\"org.freedesktop.packagekit.\") === 0 ||
      action.id.indexOf(\"com.ubuntu.softwarecenter.\") === 0".to_string(),
        );
    }
    if payload.polkit.uninstall_software_disabled {
        checks.push(
            "(action.id.indexOf(\"org.freedesktop.packagekit.\") === 0 &&
       (action.id.indexOf(\"remove\") !== -1 || action.id.indexOf(\"uninstall\") !== -1))".to_string(),
        );
    }
    if payload.polkit.mount_removable_media_disabled {
        checks.push("action.id.indexOf(\"org.freedesktop.udisks2.\") === 0".to_string());
    }
    if payload.polkit.modify_accounts_disabled {
        checks.push(
            "action.id.indexOf(\"org.freedesktop.accounts.\") === 0 ||
      action.id.indexOf(\"org.freedesktop.Accounts.\") === 0".to_string(),
        );
    }
    if payload.polkit.system_power_actions_disabled {
        checks.push(
            "action.id === \"org.freedesktop.login1.reboot\" ||
      action.id === \"org.freedesktop.login1.power-off\" ||
      action.id === \"org.freedesktop.login1.suspend\" ||
      action.id === \"org.freedesktop.login1.hibernate\"".to_string(),
        );
    }
    if payload.polkit.pkexec_elevation_disabled {
        checks.push("action.id === \"org.freedesktop.policykit.exec\"".to_string());
    }
    if payload.polkit.flatpak_install_disabled {
        checks.push("action.id.indexOf(\"org.freedesktop.Flatpak.\") === 0".to_string());
    }
    if payload.polkit.snap_install_disabled {
        checks.push("action.id.indexOf(\"io.snapcraft.\") === 0".to_string());
    }

    let combined = checks.join(" ||\n      ");
    format!(
        r#"// TimeKpr managed polkit rules for user "{escaped_user}"
polkit.addRule(function(action, subject) {{
  if (subject.user !== "{escaped_user}") {{
    return polkit.Result.NOT_HANDLED;
  }}
  if ({combined}) {{
    return polkit.Result.NO;
  }}
}});
"#
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::linux_device_policy::{DevicePolicyPayload, PolkitPolicy};

    #[test]
    fn render_rules_includes_packagekit_when_install_disabled() {
        let payload = DevicePolicyPayload {
            polkit: PolkitPolicy {
                install_software_disabled: true,
                ..PolkitPolicy::default()
            },
            ..DevicePolicyPayload::default()
        };
        let rendered = render_rules("child", &payload);
        assert!(rendered.contains("org.freedesktop.packagekit."));
        assert!(rendered.contains("subject.user !== \"child\""));
        assert!(rendered.contains("polkit.Result.NO"));
    }

    #[test]
    fn any_polkit_restrictions_false_by_default() {
        assert!(!any_polkit_restrictions(&DevicePolicyPayload::default()));
    }
}

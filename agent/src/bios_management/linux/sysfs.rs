use std::fs;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::super::baseline::{find_attribute_name, ADMIN_AUTH_ROLES, SECURE_BOOT_ALIASES, USB_BOOT_ALIASES};
use super::super::compliance::{evaluate_secure_boot, evaluate_usb_boot, HardwareReceipt};

#[derive(Debug, Clone)]
pub struct FirmwareInterface {
    pub oem: String,
    pub interface: String,
    pub root: PathBuf,
}

pub fn resolve_interface(oem: &str) -> Option<FirmwareInterface> {
    let candidates = match oem {
        "dell" => vec![(
            "dell-wmi-sysman",
            PathBuf::from("/sys/class/firmware-attributes/dell-wmi-sysman"),
        )],
        "lenovo" => vec![(
            "thinklmi",
            PathBuf::from("/sys/class/firmware-attributes/thinklmi"),
        )],
        "hp" => vec![
            (
                "hp-bioscfg",
                PathBuf::from("/sys/class/firmware-attributes/hp-bioscfg"),
            ),
            (
                "hp-bioscfg-platform",
                PathBuf::from("/sys/devices/platform/hp-bioscfg"),
            ),
        ],
        _ => return None,
    };

    for (interface, root) in candidates {
        if root.is_dir() {
            return Some(FirmwareInterface {
                oem: oem.to_string(),
                interface: interface.to_string(),
                root,
            });
        }
    }
    None
}

pub fn ensure_kernel_module(oem: &str) -> Result<(), String> {
    if oem != "lenovo" {
        return Ok(());
    }
    if resolve_interface(oem).is_some() {
        return Ok(());
    }
    let status = std::process::Command::new("modprobe")
        .arg("think-lmi")
        .status()
        .map_err(|error| format!("Failed to load think-lmi kernel module: {error}"))?;
    if !status.success() {
        return Err("think-lmi kernel module is not available on this system".to_string());
    }
    Ok(())
}

pub fn list_attributes(root: &Path) -> Vec<String> {
    let attributes_dir = root.join("attributes");
    let Ok(entries) = fs::read_dir(&attributes_dir) else {
        return Vec::new();
    };
    let mut names = Vec::new();
    for entry in entries.flatten() {
        let file_type = entry.file_type().ok();
        if file_type.map(|kind| kind.is_dir()).unwrap_or(false) {
            if let Some(name) = entry.file_name().to_str() {
                names.push(name.to_string());
            }
        }
    }
    names.sort();
    names
}

pub fn read_file(path: &Path) -> Result<String, String> {
    fs::read_to_string(path)
        .map(|value| value.trim().trim_matches('\0').to_string())
        .map_err(|error| format!("Failed to read {}: {error}", path.display()))
}

pub fn write_file(path: &Path, value: &str) -> Result<(), String> {
    fs::write(path, format!("{value}\n")).map_err(|error| format!("Failed to write {}: {error}", path.display()))
}

pub fn read_attribute_value(root: &Path, attribute: &str) -> Result<String, String> {
    read_file(&root.join("attributes").join(attribute).join("current_value"))
}

pub fn write_attribute_value(root: &Path, attribute: &str, value: &str) -> Result<(), String> {
    write_file(
        &root.join("attributes").join(attribute).join("current_value"),
        value,
    )
}

pub fn pending_reboot(root: &Path) -> bool {
    read_file(&root.join("attributes").join("pending_reboot"))
        .map(|value| value == "1")
        .unwrap_or(false)
}

pub fn resolve_auth_role(root: &Path) -> Option<String> {
    let auth_dir = root.join("authentication");
    let Ok(entries) = fs::read_dir(&auth_dir) else {
        return None;
    };
    for preferred in ADMIN_AUTH_ROLES {
        let candidate = auth_dir.join(preferred);
        if candidate.is_dir() {
            return Some(preferred.to_string());
        }
    }
    for entry in entries.flatten() {
        if entry.file_type().ok().map(|kind| kind.is_dir()).unwrap_or(false) {
            if let Some(name) = entry.file_name().to_str() {
                return Some(name.to_string());
            }
        }
    }
    None
}

pub fn auth_is_enabled(root: &Path, role: &str) -> bool {
    read_file(&root.join("authentication").join(role).join("is_enabled"))
        .map(|value| value == "1")
        .unwrap_or(false)
}

pub fn auth_mechanism(root: &Path, role: &str) -> Option<String> {
    read_file(&root.join("authentication").join(role).join("mechanism")).ok()
}

pub fn set_new_password(root: &Path, role: &str, password: &str) -> Result<(), String> {
    write_file(
        &root.join("authentication").join(role).join("new_password"),
        password,
    )
}

pub fn set_current_password(root: &Path, role: &str, password: &str) -> Result<(), String> {
    write_file(
        &root.join("authentication").join(role).join("current_password"),
        password,
    )
}

pub fn clear_current_password(root: &Path, role: &str) -> Result<(), String> {
    write_file(
        &root.join("authentication").join(role).join("current_password"),
        "",
    )
}

pub struct AuthSession<'a> {
    root: &'a Path,
    role: String,
}

impl<'a> AuthSession<'a> {
    pub fn open(root: &'a Path, role: &str, password: &str) -> Result<Self, String> {
        set_current_password(root, role, password)?;
        Ok(Self {
            root,
            role: role.to_string(),
        })
    }
}

impl Drop for AuthSession<'_> {
    fn drop(&mut self) {
        let _ = clear_current_password(self.root, &self.role);
    }
}

pub fn audit_sysfs_interface(iface: &FirmwareInterface) -> Result<HardwareReceipt, String> {
    let attributes = list_attributes(&iface.root);
    let mut receipt = HardwareReceipt::new(
        "linux",
        &iface.oem,
        None,
        Some(iface.interface.clone()),
    );
    receipt.vendor_tool = json!({
        "name": "sysfs",
        "path": iface.root.display().to_string(),
        "present": true,
    });

    if let Some(role) = resolve_auth_role(&iface.root) {
        let enabled = auth_is_enabled(&iface.root, &role);
        receipt.supervisor_password.actual = json!(if enabled { "set" } else { "unset" });
        if let Some(mechanism) = auth_mechanism(&iface.root, &role) {
            if mechanism.eq_ignore_ascii_case("certificate") {
                receipt.supervisor_password.error = Some(
                    "Certificate-based BIOS authentication is not supported by Guardian".to_string(),
                );
            }
        }
    }

    if let Some(name) = find_attribute_name(&attributes, SECURE_BOOT_ALIASES) {
        if let Ok(value) = read_attribute_value(&iface.root, name) {
            let (actual, enabled) = evaluate_secure_boot(&value);
            receipt.secure_boot_enabled.actual = actual;
            receipt.secure_boot_enabled.applied = enabled;
        }
    } else {
        receipt.secure_boot_enabled.error = Some("Secure Boot attribute not found".to_string());
    }

    if let Some(name) = find_attribute_name(&attributes, USB_BOOT_ALIASES) {
        if let Ok(value) = read_attribute_value(&iface.root, name) {
            let (actual, disabled) = evaluate_usb_boot(&value);
            receipt.usb_boot_disabled.actual = actual;
            receipt.usb_boot_disabled.applied = disabled;
        }
    } else {
        receipt.usb_boot_disabled.error = Some("USB boot attribute not found".to_string());
    }

    receipt.pending_reboot = pending_reboot(&iface.root);
    receipt.finalize();
    Ok(receipt)
}

pub fn apply_sysfs_interface(
    iface: &FirmwareInterface,
    force_reset_password: bool,
) -> Result<(HardwareReceipt, Option<String>), String> {
    let mut receipt = audit_sysfs_interface(iface)?;
    let role = resolve_auth_role(&iface.root)
        .ok_or_else(|| "No BIOS authentication role found in sysfs".to_string())?;

    if let Some(mechanism) = auth_mechanism(&iface.root, &role) {
        if mechanism.eq_ignore_ascii_case("certificate") {
            receipt.supervisor_password.applied = false;
            receipt.supervisor_password.error = Some(
                "Certificate-based BIOS authentication is not supported by Guardian".to_string(),
            );
            receipt.finalize();
            return Ok((receipt, None));
        }
    }

    let password_already_set = auth_is_enabled(&iface.root, &role);
    let mut escrow_password = None;
    let mut session_password = String::new();

    if password_already_set && !force_reset_password {
        receipt.supervisor_password.applied = false;
        receipt.supervisor_password.error =
            Some("Existing BIOS supervisor password blocks change".to_string());
    } else if password_already_set && force_reset_password {
        receipt.supervisor_password.applied = false;
        receipt.supervisor_password.error = Some(
            "Cannot reset an existing BIOS password without knowing the current password".to_string(),
        );
    } else {
        let mut generated = super::super::password::generate_supervisor_password(16);
        set_new_password(&iface.root, &role, &generated)?;
        receipt.supervisor_password.applied = true;
        receipt.supervisor_password.actual = json!("set");
        session_password = generated.clone();
        escrow_password = Some(generated.clone());
        super::super::password::zeroize_string(&mut generated);
    }

    if !session_password.is_empty() {
        let _session = AuthSession::open(&iface.root, &role, &session_password)?;
        let attributes = list_attributes(&iface.root);

        if let Some(name) = find_attribute_name(&attributes, SECURE_BOOT_ALIASES) {
            if let Ok(current) = read_attribute_value(&iface.root, name) {
                let (_, enabled) = evaluate_secure_boot(&current);
                if !enabled {
                    for candidate in ["Enabled", "Enable", "1", "On"] {
                        if write_attribute_value(&iface.root, name, candidate).is_ok() {
                            receipt.secure_boot_enabled.applied = true;
                            receipt.secure_boot_enabled.actual = json!(true);
                            break;
                        }
                    }
                    if !receipt.secure_boot_enabled.applied {
                        receipt.secure_boot_enabled.error =
                            Some("Failed to enable Secure Boot".to_string());
                    }
                }
            }
        }

        if let Some(name) = find_attribute_name(&attributes, USB_BOOT_ALIASES) {
            if let Ok(current) = read_attribute_value(&iface.root, name) {
                let (_, disabled) = evaluate_usb_boot(&current);
                if !disabled {
                    for candidate in ["Disabled", "Disable", "0", "Off", "InternalOnly"] {
                        if write_attribute_value(&iface.root, name, candidate).is_ok() {
                            receipt.usb_boot_disabled.applied = true;
                            receipt.usb_boot_disabled.actual = json!(true);
                            break;
                        }
                    }
                    if !receipt.usb_boot_disabled.applied {
                        receipt.usb_boot_disabled.error =
                            Some("Failed to disable USB boot".to_string());
                    }
                }
            }
        }
    }

    super::super::password::zeroize_string(&mut session_password);
    receipt.pending_reboot = pending_reboot(&iface.root);
    receipt = audit_sysfs_interface(iface)?;
    if escrow_password.is_some() {
        receipt.supervisor_password.applied = true;
        receipt.supervisor_password.actual = json!("set");
    }
    receipt.finalize();
    Ok((receipt, escrow_password))
}

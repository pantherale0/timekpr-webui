pub const SECURE_BOOT_ALIASES: &[&str] = &[
    "SecureBoot",
    "Secure Boot",
    "secure_boot",
    "SecureBootControl",
];

pub const USB_BOOT_ALIASES: &[&str] = &[
    "UsbBoot",
    "USB Boot",
    "BootUsb",
    "UsbBootSupport",
    "ExternalUsbBoot",
    "USBFlashDriveEmulation",
    "Boot Mode",
];

pub const ADMIN_AUTH_ROLES: &[&str] = &["Admin", "Supervisor", "Setup"];

pub fn normalize_manufacturer(raw: &str) -> Option<String> {
    let value = raw.trim();
    if value.is_empty() {
        return None;
    }
    let lower = value.to_ascii_lowercase();
    if lower.contains("dell") {
        return Some("dell".to_string());
    }
    if lower.contains("lenovo") {
        return Some("lenovo".to_string());
    }
    if lower.contains("hewlett-packard") || lower == "hp" || lower.starts_with("hp ") {
        return Some("hp".to_string());
    }
    if lower.contains("microsoft") {
        return Some("surface".to_string());
    }
    None
}

pub fn find_attribute_name<'a>(attributes: &'a [String], aliases: &[&str]) -> Option<&'a str> {
    for alias in aliases {
        let alias_lower = alias.to_ascii_lowercase();
        for name in attributes {
            if name.eq_ignore_ascii_case(alias) || name.to_ascii_lowercase().contains(&alias_lower) {
                return Some(name.as_str());
            }
        }
    }
    None
}

pub fn value_is_enabled(raw: &str) -> bool {
    let value = raw.trim().trim_matches('\0');
    let lower = value.to_ascii_lowercase();
    matches!(lower.as_str(), "1" | "true" | "enabled" | "enable" | "on" | "yes")
}

pub fn value_is_disabled(raw: &str) -> bool {
    let value = raw.trim().trim_matches('\0');
    let lower = value.to_ascii_lowercase();
    matches!(lower.as_str(), "0" | "false" | "disabled" | "disable" | "off" | "no")
}

pub fn usb_boot_disabled_value(raw: &str) -> bool {
    let value = raw.trim().trim_matches('\0');
    let lower = value.to_ascii_lowercase();
    value_is_disabled(raw)
        || lower.contains("disabled")
        || lower.contains("internal")
        || lower == "hdd"
        || lower == "hard drive"
}

pub mod baseline;
pub mod compliance;
pub mod password;

#[cfg(target_os = "linux")]
pub mod linux;
#[cfg(target_os = "windows")]
pub mod windows;

use serde_json::{json, Value};

pub use compliance::{HardwareReceipt, OemDetectResult};

#[cfg(target_os = "linux")]
pub use linux::run_apply;
#[cfg(target_os = "linux")]
pub use linux::run_audit;

#[cfg(target_os = "windows")]
pub use windows::run_apply;
#[cfg(target_os = "windows")]
pub use windows::run_audit;
#[cfg(target_os = "windows")]
pub use windows::run_detect;

pub fn detect_hardware_oem() -> OemDetectResult {
    #[cfg(target_os = "linux")]
    {
        return linux::detect();
    }
    #[cfg(target_os = "windows")]
    {
        return windows::oem_detect::detect();
    }
    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    {
        OemDetectResult::unsupported("android")
    }
}

pub fn audit_hardware_baseline() -> Result<HardwareReceipt, String> {
    #[cfg(any(target_os = "linux", target_os = "windows"))]
    {
        return run_audit();
    }
    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    {
        Err("Hardware baseline is not supported on this platform".to_string())
    }
}

pub fn apply_hardware_baseline(force_reset_password: bool) -> Result<(HardwareReceipt, Option<String>), String> {
    #[cfg(any(target_os = "linux", target_os = "windows"))]
    {
        return run_apply(force_reset_password);
    }
    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    {
        let _ = force_reset_password;
        Err("Hardware baseline is not supported on this platform".to_string())
    }
}

pub fn handle_command(action: &str, args: &Value) -> (bool, String, Value) {
    match action {
        "detect_hardware_oem" => {
            let detect = detect_hardware_oem();
            (
                true,
                "Hardware OEM detected".to_string(),
                json!(detect.to_json()),
            )
        }
        "audit_hardware_baseline" => match audit_hardware_baseline() {
            Ok(receipt) => (
                true,
                "Hardware baseline audited".to_string(),
                json!({ "receipt": receipt.to_json() }),
            ),
            Err(message) => (false, message, json!({})),
        },
        "apply_hardware_baseline" => {
            let force_reset_password = args
                .get("force_reset_password")
                .and_then(|value| value.as_bool())
                .unwrap_or(false);
            match apply_hardware_baseline(force_reset_password) {
                Ok((receipt, escrow_password)) => {
                    let mut data = json!({ "receipt": receipt.to_json() });
                    if let Some(password) = escrow_password {
                        data["escrow_password"] = json!(password);
                    }
                    (
                        true,
                        "Hardware baseline applied".to_string(),
                        data,
                    )
                }
                Err(message) => (false, message, json!({})),
            }
        }
        _ => (
            false,
            format!("Unknown hardware baseline action '{action}'"),
            json!({}),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::baseline::normalize_manufacturer;

    #[test]
    fn normalizes_dell_manufacturer() {
        assert_eq!(normalize_manufacturer("Dell Inc."), Some("dell".to_string()));
    }

    #[test]
    fn normalizes_lenovo_manufacturer() {
        assert_eq!(normalize_manufacturer("LENOVO"), Some("lenovo".to_string()));
    }
}

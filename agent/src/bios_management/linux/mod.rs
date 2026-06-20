mod oem_detect;
mod sysfs;

use super::compliance::{HardwareReceipt, OemDetectResult};

pub use oem_detect::detect;

pub fn run_detect() -> OemDetectResult {
    detect()
}

pub fn run_audit() -> Result<HardwareReceipt, String> {
    let detect = detect();
    if !detect.supported {
        return Err(
            detect
                .message
                .unwrap_or_else(|| "Hardware baseline is not supported on this device".to_string()),
        );
    }
    let iface = sysfs::resolve_interface(&detect.oem)
        .ok_or_else(|| "Firmware attributes sysfs interface is not available".to_string())?;
    let mut receipt = sysfs::audit_sysfs_interface(&iface)?;
    if let Some(model) = detect.model {
        receipt.model = Some(model);
    }
    Ok(receipt)
}

pub fn run_apply(force_reset_password: bool) -> Result<(HardwareReceipt, Option<String>), String> {
    let detect = detect();
    if !detect.supported {
        return Err(
            detect
                .message
                .unwrap_or_else(|| "Hardware baseline is not supported on this device".to_string()),
        );
    }
    let iface = sysfs::resolve_interface(&detect.oem)
        .ok_or_else(|| "Firmware attributes sysfs interface is not available".to_string())?;
    let (mut receipt, escrow_password) = sysfs::apply_sysfs_interface(&iface, force_reset_password)?;
    if let Some(model) = detect.model {
        receipt.model = Some(model);
    }
    Ok((receipt, escrow_password))
}

use chrono::Utc;
use serde_json::{json, Value};

use super::baseline;

#[derive(Debug, Clone)]
pub struct SettingResult {
    pub desired: Value,
    pub actual: Value,
    pub applied: bool,
    pub error: Option<String>,
}

impl SettingResult {
    pub fn to_json(&self) -> Value {
        let mut payload = json!({
            "desired": self.desired,
            "actual": self.actual,
            "applied": self.applied,
        });
        if let Some(error) = &self.error {
            payload["error"] = json!(error);
        }
        payload
    }
}

#[derive(Debug, Clone)]
pub struct HardwareReceipt {
    pub platform: String,
    pub oem: String,
    pub model: Option<String>,
    pub interface: Option<String>,
    pub overall: String,
    pub pending_reboot: bool,
    pub supervisor_password: SettingResult,
    pub usb_boot_disabled: SettingResult,
    pub secure_boot_enabled: SettingResult,
    pub vendor_tool: Value,
}

#[derive(Debug, Clone)]
pub struct OemDetectResult {
    pub oem: String,
    pub model: Option<String>,
    pub platform: String,
    pub interface: Option<String>,
    pub supported: bool,
    pub message: Option<String>,
}

impl OemDetectResult {
    pub fn unsupported(platform: &str) -> Self {
        Self {
            oem: "unsupported".to_string(),
            model: None,
            platform: platform.to_string(),
            interface: None,
            supported: false,
            message: Some("Hardware baseline is not supported on this platform".to_string()),
        }
    }

    pub fn to_json(&self) -> Value {
        let mut payload = json!({
            "oem": self.oem,
            "platform": self.platform,
            "supported": self.supported,
        });
        if let Some(model) = &self.model {
            payload["model"] = json!(model);
        }
        if let Some(interface) = &self.interface {
            payload["interface"] = json!(interface);
        }
        if let Some(message) = &self.message {
            payload["message"] = json!(message);
        }
        payload
    }
}

impl HardwareReceipt {
    pub fn new(platform: &str, oem: &str, model: Option<String>, interface: Option<String>) -> Self {
        Self {
            platform: platform.to_string(),
            oem: oem.to_string(),
            model,
            interface,
            overall: "unknown".to_string(),
            pending_reboot: false,
            supervisor_password: SettingResult {
                desired: json!("set"),
                actual: json!("unknown"),
                applied: false,
                error: None,
            },
            usb_boot_disabled: SettingResult {
                desired: json!(true),
                actual: json!("unknown"),
                applied: false,
                error: None,
            },
            secure_boot_enabled: SettingResult {
                desired: json!(true),
                actual: json!("unknown"),
                applied: false,
                error: None,
            },
            vendor_tool: json!({ "name": "unknown", "present": false }),
        }
    }

    pub fn finalize(&mut self) {
        let compliant = self.supervisor_password.applied
            && self.usb_boot_disabled.applied
            && self.secure_boot_enabled.applied;
        self.overall = if compliant {
            "compliant".to_string()
        } else {
            "non_compliant".to_string()
        };
    }

    pub fn to_json(&self) -> Value {
        json!({
            "platform": self.platform,
            "oem": self.oem,
            "model": self.model,
            "interface": self.interface,
            "checked_at": Utc::now().to_rfc3339(),
            "overall": self.overall,
            "pending_reboot": self.pending_reboot,
            "settings": {
                "supervisor_password": self.supervisor_password.to_json(),
                "usb_boot_disabled": self.usb_boot_disabled.to_json(),
                "secure_boot_enabled": self.secure_boot_enabled.to_json(),
            },
            "vendor_tool": self.vendor_tool,
        })
    }
}

pub fn evaluate_secure_boot(actual: &str) -> (Value, bool) {
    let enabled = baseline::value_is_enabled(actual);
    (json!(enabled), enabled)
}

pub fn evaluate_usb_boot(actual: &str) -> (Value, bool) {
    let disabled = baseline::usb_boot_disabled_value(actual);
    (json!(disabled), disabled)
}

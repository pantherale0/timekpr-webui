use std::process::Command;

use crate::bios_management::compliance::OemDetectResult;
use crate::bios_management::baseline::normalize_manufacturer;

pub fn detect() -> OemDetectResult {
    let (manufacturer, model) = match read_wmi_identity() {
        Ok(values) => values,
        Err(message) => {
            return OemDetectResult {
                oem: "unknown".to_string(),
                model: None,
                platform: "windows".to_string(),
                interface: None,
                supported: false,
                message: Some(message),
            };
        }
    };

    let mut oem = normalize_manufacturer(&manufacturer).unwrap_or_else(|| "unknown".to_string());
    if oem == "surface" && !model.to_ascii_lowercase().contains("surface") {
        oem = "unknown".to_string();
    }

    if oem == "unknown" {
        return OemDetectResult {
            oem,
            model: Some(model),
            platform: "windows".to_string(),
            interface: None,
            supported: false,
            message: Some(format!("Unsupported hardware manufacturer: {manufacturer}")),
        };
    }

    let interface = match oem.as_str() {
        "dell" => Some("cctk".to_string()),
        "hp" => Some("cmsl".to_string()),
        "lenovo" => Some("lenovo-wmi".to_string()),
        "surface" => Some("semm".to_string()),
        _ => None,
    };

    OemDetectResult {
        oem,
        model: Some(model),
        platform: "windows".to_string(),
        interface,
        supported: interface.is_some(),
        message: None,
    }
}

fn read_wmi_identity() -> Result<(String, String), String> {
    let script = r#"
$system = Get-CimInstance -ClassName Win32_ComputerSystem
$bios = Get-CimInstance -ClassName Win32_BIOS
Write-Output ("MANUFACTURER=" + $system.Manufacturer)
Write-Output ("MODEL=" + $bios.Name)
"#;
    let output = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .output()
        .map_err(|error| format!("Failed to execute WMI detection script: {error}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("WMI detection script failed: {stderr}"));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut manufacturer = String::new();
    let mut model = String::new();
    for line in stdout.lines() {
        if let Some(value) = line.strip_prefix("MANUFACTURER=") {
            manufacturer = value.trim().to_string();
        } else if let Some(value) = line.strip_prefix("MODEL=") {
            model = value.trim().to_string();
        }
    }

    if manufacturer.is_empty() {
        return Err("Could not determine system manufacturer from WMI".to_string());
    }
    Ok((manufacturer, model))
}

use dmidecode::{EntryPoint, Structure};

use super::super::baseline::normalize_manufacturer;
use super::super::compliance::OemDetectResult;
use super::sysfs::{ensure_kernel_module, resolve_interface};

const SMBIOS_ENTRY: &str = "/sys/firmware/dmi/tables/smbios_entry_point";
const DMI_TABLE: &str = "/sys/firmware/dmi/tables/DMI";

pub fn detect() -> OemDetectResult {
    let (manufacturer, product_name) = match read_dmi_identity() {
        Ok(values) => values,
        Err(message) => {
            return OemDetectResult {
                oem: "unknown".to_string(),
                model: None,
                platform: "linux".to_string(),
                interface: None,
                supported: false,
                message: Some(message),
            };
        }
    };

    let oem = normalize_manufacturer(&manufacturer).unwrap_or_else(|| "unknown".to_string());
    if oem == "unknown" {
        return OemDetectResult {
            oem,
            model: Some(product_name),
            platform: "linux".to_string(),
            interface: None,
            supported: false,
            message: Some(format!("Unsupported hardware manufacturer: {manufacturer}")),
        };
    }

    if let Err(message) = ensure_kernel_module(&oem) {
        return OemDetectResult {
            oem,
            model: Some(product_name),
            platform: "linux".to_string(),
            interface: None,
            supported: false,
            message: Some(message),
        };
    }

    let iface = resolve_interface(&oem);
    OemDetectResult {
        oem,
        model: Some(product_name),
        platform: "linux".to_string(),
        interface: iface.as_ref().map(|value| value.interface.clone()),
        supported: iface.is_some(),
        message: if iface.is_some() {
            None
        } else {
            Some("Firmware attributes sysfs interface is not available".to_string())
        },
    }
}

fn read_dmi_identity() -> Result<(String, String), String> {
    let entry_buf = std::fs::read(SMBIOS_ENTRY)
        .map_err(|error| format!("Failed to read SMBIOS entry point: {error}"))?;
    let dmi_buf =
        std::fs::read(DMI_TABLE).map_err(|error| format!("Failed to read DMI table: {error}"))?;
    let entry = EntryPoint::search(&entry_buf)
        .map_err(|error| format!("Failed to parse SMBIOS entry point: {error}"))?;

    let mut manufacturer = String::new();
    let mut product_name = String::new();

    for table in entry.structures(&dmi_buf) {
        let Ok(table) = table else {
            continue;
        };
        if let Structure::System(system) = table {
            manufacturer = system.manufacturer.to_string();
            product_name = system.product.to_string();
            break;
        }
    }

    if manufacturer.is_empty() {
        return Err("Could not determine system manufacturer from DMI".to_string());
    }
    Ok((manufacturer, product_name))
}

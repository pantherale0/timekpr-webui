//! Ensures GuardianAgent is registered under Windows SafeBoot hives.

#[cfg(target_os = "windows")]
use std::ffi::OsStr;
#[cfg(target_os = "windows")]
use std::os::windows::ffi::OsStrExt;
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::Registry::{
    RegCloseKey, RegCreateKeyExW, RegOpenKeyExW, RegQueryValueExW, RegSetValueExW, HKEY_LOCAL_MACHINE,
    KEY_READ, KEY_SET_VALUE, KEY_WOW64_64KEY, REG_OPTION_NON_VOLATILE, REG_SZ,
};

#[cfg(target_os = "windows")]
const SAFE_BOOT_MINIMAL: &str =
    r"SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal\GuardianAgent";
#[cfg(target_os = "windows")]
const SAFE_BOOT_NETWORK: &str =
    r"SYSTEM\CurrentControlSet\Control\SafeBoot\Network\GuardianAgent";
#[cfg(target_os = "windows")]
const SERVICE_VALUE: &str = "Service";

#[cfg(target_os = "windows")]
fn wide_string(value: &str) -> Vec<u16> {
    OsStr::new(value).encode_wide().chain(std::iter::once(0)).collect()
}

#[cfg(target_os = "windows")]
fn read_value(key_path: &str) -> Option<String> {
    unsafe {
        let mut key = 0isize;
        let path = wide_string(key_path);
        if RegOpenKeyExW(
            HKEY_LOCAL_MACHINE,
            path.as_ptr(),
            0,
            KEY_READ | KEY_WOW64_64KEY,
            &mut key,
        ) != 0
        {
            return None;
        }

        let value_name = wide_string("");
        let mut value_type = 0u32;
        let mut data_len = 0u32;
        if RegQueryValueExW(
            key,
            value_name.as_ptr(),
            std::ptr::null_mut(),
            &mut value_type,
            std::ptr::null_mut(),
            &mut data_len,
        ) != 0
            || value_type != REG_SZ
            || data_len < 2
        {
            RegCloseKey(key);
            return None;
        }

        let mut buffer = vec![0u16; (data_len as usize / 2).max(1)];
        if RegQueryValueExW(
            key,
            value_name.as_ptr(),
            std::ptr::null_mut(),
            &mut value_type,
            buffer.as_mut_ptr() as *mut u8,
            &mut data_len,
        ) != 0
        {
            RegCloseKey(key);
            return None;
        }
        RegCloseKey(key);

        let len = buffer.iter().position(|&ch| ch == 0).unwrap_or(buffer.len());
        Some(String::from_utf16_lossy(&buffer[..len]))
    }
}

#[cfg(target_os = "windows")]
fn write_value(key_path: &str) -> Result<(), String> {
    unsafe {
        let mut key = 0isize;
        let path = wide_string(key_path);
        let status = RegCreateKeyExW(
            HKEY_LOCAL_MACHINE,
            path.as_ptr(),
            0,
            std::ptr::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE | KEY_WOW64_64KEY,
            std::ptr::null(),
            &mut key,
            std::ptr::null_mut(),
        );
        if status != 0 {
            return Err(format!(
                "failed to create SafeBoot registry key {} (status={})",
                key_path, status
            ));
        }

        let value_name = wide_string("");
        let value_data = wide_string(SERVICE_VALUE);
        let data_bytes = std::slice::from_raw_parts(
            value_data.as_ptr() as *const u8,
            value_data.len() * 2,
        );
        let set_status = RegSetValueExW(
            key,
            value_name.as_ptr(),
            0,
            REG_SZ,
            data_bytes.as_ptr(),
            data_bytes.len() as u32,
        );
        RegCloseKey(key);
        if set_status != 0 {
            return Err(format!(
                "failed to set SafeBoot registry value for {} (status={})",
                key_path, set_status
            ));
        }
        Ok(())
    }
}

#[cfg(target_os = "windows")]
pub fn ensure_registered() {
    let paths = [SAFE_BOOT_MINIMAL, SAFE_BOOT_NETWORK];
    let mut repaired = false;
    for path in paths {
        let current = read_value(path);
        if current.as_deref() != Some(SERVICE_VALUE) {
            match write_value(path) {
                Ok(()) => {
                    repaired = true;
                    println!("Repaired SafeBoot registry entry: {}", path);
                }
                Err(err) => eprintln!("SafeBoot registry repair failed for {}: {}", path, err),
            }
        }
    }
    if repaired {
        crate::netlink::send_app_alert(
            "hardware_non_compliant",
            "system",
            serde_json::json!({
                "reason": "safeboot_registry_repaired",
                "component": "GuardianAgent",
            }),
        );
    }
}

#[cfg(not(target_os = "windows"))]
pub fn ensure_registered() {}

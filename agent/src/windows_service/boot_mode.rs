//! Detect Windows Safe Mode boot state.

#[cfg(target_os = "windows")]
use std::ffi::OsStr;
#[cfg(target_os = "windows")]
use std::os::windows::ffi::OsStrExt;
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::Registry::{
    RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_64KEY,
    REG_DWORD,
};
#[cfg(target_os = "windows")]
use windows_sys::Win32::UI::WindowsAndMessaging::GetSystemMetrics;

#[cfg(target_os = "windows")]
const SM_CLEANBOOT: i32 = 67;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SafeModeVariant {
    Minimal,
    Network,
    Unknown,
}

#[cfg(target_os = "windows")]
fn wide_string(value: &str) -> Vec<u16> {
    OsStr::new(value).encode_wide().chain(std::iter::once(0)).collect()
}

#[cfg(target_os = "windows")]
pub fn is_safe_mode_boot() -> bool {
    unsafe { GetSystemMetrics(SM_CLEANBOOT) != 0 }
}

#[cfg(not(target_os = "windows"))]
pub fn is_safe_mode_boot() -> bool {
    false
}

#[cfg(target_os = "windows")]
pub fn safe_mode_variant() -> Option<SafeModeVariant> {
    if !is_safe_mode_boot() {
        return None;
    }

    unsafe {
        let mut key = 0isize;
        let path = wide_string(r"SYSTEM\CurrentControlSet\Control\SafeBoot\Option");
        if RegOpenKeyExW(
            HKEY_LOCAL_MACHINE,
            path.as_ptr(),
            0,
            KEY_READ | KEY_WOW64_64KEY,
            &mut key,
        ) != 0
        {
            return Some(SafeModeVariant::Unknown);
        }

        let value_name = wide_string("OptionValue");
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
            || value_type != REG_DWORD
            || data_len < 4
        {
            RegCloseKey(key);
            return Some(SafeModeVariant::Unknown);
        }

        let mut option_value = 0u32;
        if RegQueryValueExW(
            key,
            value_name.as_ptr(),
            std::ptr::null_mut(),
            &mut value_type,
            &mut option_value as *mut u32 as *mut u8,
            &mut data_len,
        ) != 0
        {
            RegCloseKey(key);
            return Some(SafeModeVariant::Unknown);
        }
        RegCloseKey(key);

        match option_value {
            1 | 3 => Some(SafeModeVariant::Minimal),
            2 => Some(SafeModeVariant::Network),
            _ => Some(SafeModeVariant::Unknown),
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn safe_mode_variant() -> Option<SafeModeVariant> {
    None
}

#[cfg(test)]
mod tests {
    #[test]
    fn safe_mode_variant_is_none_on_non_windows() {
        #[cfg(not(target_os = "windows"))]
        assert!(super::safe_mode_variant().is_none());
    }
}

use std::process::Command;

pub async fn configure_system_dns() -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        println!("Configuring Windows active adapters to use loopback DNS server...");
        // Set DNS address to 127.0.0.1
        let status = Command::new("powershell")
            .args([
                "-Command",
                "Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.ConnectionState -eq 2 -and $_.InterfaceAlias -notlike '*Loopback*' } | Set-DnsClientServerAddress -ServerAddresses '127.0.0.1'"
            ])
            .status();

        if status.is_err() || !status.unwrap().success() {
            return Err("Failed to configure DNS client server address via PowerShell".to_string());
        }

        // Add firewall rule to block external DNS queries on port 53 (prevent bypass)
        let _ = Command::new("powershell")
            .args([
                "-Command",
                "New-NetFirewallRule -DisplayName 'TimeKpr Block DNS Bypass' -Direction Outbound -LocalPort 53 -Protocol UDP -Action Block -Force"
            ])
            .status();
    }
    Ok(())
}

pub async fn restore_system_dns() -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        println!("Restoring original DNS server addresses...");
        let _ = Command::new("powershell")
            .args([
                "-Command",
                "Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.ConnectionState -eq 2 -and $_.InterfaceAlias -notlike '*Loopback*' } | Set-DnsClientServerAddress -ResetServerAddresses"
            ])
            .status();

        let _ = Command::new("powershell")
            .args([
                "-Command",
                "Remove-NetFirewallRule -DisplayName 'TimeKpr Block DNS Bypass' -ErrorAction SilentlyContinue"
            ])
            .status();
    }
    Ok(())
}

// Win32 Session Info helper to find active logged-on user's RID
#[cfg(target_os = "windows")]
pub fn get_active_session_user_rid() -> Option<u32> {
    unsafe {
        let active_session_id = windows_sys::Win32::System::RemoteDesktop::WTSGetActiveConsoleSessionId();
        if active_session_id == u32::MAX {
            return None;
        }

        let mut buffer: *mut u16 = std::ptr::null_mut();
        let mut bytes_returned = 0;

        let success = windows_sys::Win32::System::RemoteDesktop::WTSQuerySessionInformationW(
            0, // WTS_CURRENT_SERVER_HANDLE
            active_session_id,
            windows_sys::Win32::System::RemoteDesktop::WTSUserName,
            &mut buffer,
            &mut bytes_returned,
        );

        if success != 0 && !buffer.is_null() {
            let username = read_wide_string(buffer);
            windows_sys::Win32::System::RemoteDesktop::WTSFreeMemory(buffer as *mut std::ffi::c_void);

            if !username.is_empty() {
                // Find matching RID for this username
                let users_map = crate::windows_service::policy::get_windows_users_map();
                for (rid, name) in users_map {
                    if name.eq_ignore_ascii_case(&username) {
                        return Some(rid);
                    }
                }
            }
        }
    }
    None
}

#[cfg(target_os = "windows")]
pub fn get_active_session_username() -> Option<String> {
    unsafe {
        let active_session_id = windows_sys::Win32::System::RemoteDesktop::WTSGetActiveConsoleSessionId();
        if active_session_id == u32::MAX {
            return None;
        }

        let mut buffer: *mut u16 = std::ptr::null_mut();
        let mut bytes_returned = 0;

        let success = windows_sys::Win32::System::RemoteDesktop::WTSQuerySessionInformationW(
            0, // WTS_CURRENT_SERVER_HANDLE
            active_session_id,
            windows_sys::Win32::System::RemoteDesktop::WTSUserName,
            &mut buffer,
            &mut bytes_returned,
        );

        if success != 0 && !buffer.is_null() {
            let username = read_wide_string(buffer);
            windows_sys::Win32::System::RemoteDesktop::WTSFreeMemory(buffer as *mut std::ffi::c_void);

            if !username.trim().is_empty() {
                return Some(username);
            }
        }
    }
    None
}

#[cfg(not(target_os = "windows"))]
pub fn get_active_session_user_rid() -> Option<u32> {
    None
}

#[cfg(not(target_os = "windows"))]
pub fn get_active_session_username() -> Option<String> {
    None
}

#[cfg(target_os = "windows")]
unsafe fn read_wide_string(ptr: *const u16) -> String {
    if ptr.is_null() {
        return String::new();
    }
    unsafe {
        let mut len = 0;
        while *ptr.add(len) != 0 {
            len += 1;
        }
        let slice = std::slice::from_raw_parts(ptr, len);
        String::from_utf16_lossy(slice)
    }
}

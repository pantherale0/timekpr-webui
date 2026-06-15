use std::fs;
use std::path::Path;

pub const EXTENSION_ID: &str = "gnokihbalbffklhnhamjompcmbgojmjp";

fn convert_ws_to_http(ws_url: &str) -> String {
    let mut url = ws_url.to_string();
    if url.ends_with("/ws") {
        url.truncate(url.len() - 3);
    }
    if url.starts_with("ws://") {
        url.replace("ws://", "http://")
    } else if url.starts_with("wss://") {
        url.replace("wss://", "https://")
    } else {
        url
    }
}

#[cfg(target_os = "linux")]
pub fn reconcile_extension_policy(
    active_username: Option<&str>,
    server_url: &str,
    agent_token: Option<&str>,
) -> Result<(), String> {
    let policy_dir = Path::new("/etc/opt/chrome/policies/managed");
    let policy_path = policy_dir.join("timekpr_youtube.json");

    let Some(token) = agent_token else {
        // Unenrolled or no token: remove policy
        if policy_path.exists() {
            let _ = fs::remove_file(&policy_path);
        }
        return Ok(());
    };

    let Some(username) = active_username else {
        // No active user: remove policy to avoid tracking wrong sessions
        if policy_path.exists() {
            let _ = fs::remove_file(&policy_path);
        }
        return Ok(());
    };

    let rest_url = convert_ws_to_http(server_url);
    let update_url = format!("{}/api/extensions/update", rest_url);

    // Create the directory if it doesn't exist
    if let Err(e) = fs::create_dir_all(policy_dir) {
        return Err(format!("Failed to create policy directory: {}", e));
    }

    let policy_json = serde_json::json!({
        "ExtensionInstallForcelist": [
            format!("{};{}", EXTENSION_ID, update_url)
        ],
        "3rdparty": {
            "extensions": {
                EXTENSION_ID: {
                    "server_url": rest_url,
                    "secure_token": token,
                    "linux_username": username
                }
            }
        }
    });

    let serialized = serde_json::to_string_pretty(&policy_json)
        .map_err(|e| format!("Failed to serialize Chrome policy: {}", e))?;

    fs::write(&policy_path, serialized)
        .map_err(|e| format!("Failed to write Chrome policy to {:?}: {}", policy_path, e))?;

    Ok(())
}

#[cfg(target_os = "windows")]
fn to_wide_string(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(target_os = "windows")]
pub fn reconcile_extension_policy(
    active_username: Option<&str>,
    server_url: &str,
    agent_token: Option<&str>,
) -> Result<(), String> {
    use windows_sys::Win32::System::Registry::{
        HKEY_LOCAL_MACHINE, REG_SZ, REG_OPTION_NON_VOLATILE, KEY_WRITE, KEY_READ,
        RegCreateKeyExW, RegSetValueExW, RegCloseKey, RegOpenKeyExW, RegDeleteValueW, RegDeleteKeyW, RegEnumValueW
    };

    let hkey = HKEY_LOCAL_MACHINE;

    let Some(token) = agent_token else {
        // Clear all policies on unenroll
        unsafe {
            let _ = remove_extension_from_forcelist(hkey, EXTENSION_ID);
            let ext_subkey = format!(r"SOFTWARE\Policies\Google\Chrome\3rdparty\extensions\{}", EXTENSION_ID);
            let _ = delete_registry_key(hkey, &ext_subkey);
        }
        return Ok(());
    };

    let Some(username) = active_username else {
        // Clear active session info if no user is active
        unsafe {
            let ext_subkey = format!(r"SOFTWARE\Policies\Google\Chrome\3rdparty\extensions\{}", EXTENSION_ID);
            let _ = delete_registry_value(hkey, &ext_subkey, "linux_username");
        }
        return Ok(());
    };

    let rest_url = convert_ws_to_http(server_url);
    let update_url = format!("{}/api/extensions/update", rest_url);

    unsafe {
        // 1. Force-install the extension via HKLM policy to support Home/Pro windows versions
        write_extension_forcelist(hkey, EXTENSION_ID, &update_url)?;

        // 2. Configure extension managed settings for chrome.storage.managed integration
        let ext_subkey = format!(r"SOFTWARE\Policies\Google\Chrome\3rdparty\extensions\{}", EXTENSION_ID);
        set_registry_string(hkey, &ext_subkey, "server_url", &rest_url)?;
        set_registry_string(hkey, &ext_subkey, "secure_token", token)?;
        set_registry_string(hkey, &ext_subkey, "linux_username", username)?;
    }

    Ok(())
}

#[cfg(target_os = "windows")]
unsafe fn set_registry_string(hkey: isize, subkey: &str, name: &str, value: &str) -> Result<(), String> {
    use windows_sys::Win32::System::Registry::{
        RegCreateKeyExW, RegSetValueExW, RegCloseKey, REG_SZ, REG_OPTION_NON_VOLATILE, KEY_WRITE
    };
    
    let subkey_w = to_wide_string(subkey);
    let name_w = to_wide_string(name);
    let value_w = to_wide_string(value);
    
    let mut hkey_result = 0;
    let status = RegCreateKeyExW(
        hkey,
        subkey_w.as_ptr(),
        0,
        std::ptr::null(),
        REG_OPTION_NON_VOLATILE,
        KEY_WRITE,
        std::ptr::null(),
        &mut hkey_result,
        std::ptr::null_mut(),
    );
    
    if status != 0 {
        return Err(format!("RegCreateKeyExW failed: {}", status));
    }
    
    let status = RegSetValueExW(
        hkey_result,
        name_w.as_ptr(),
        0,
        REG_SZ,
        value_w.as_ptr() as *const u8,
        (value_w.len() * 2) as u32,
    );
    
    RegCloseKey(hkey_result);
    
    if status != 0 {
        return Err(format!("RegSetValueExW failed: {}", status));
    }
    
    Ok(())
}

#[cfg(target_os = "windows")]
unsafe fn delete_registry_value(hkey: isize, subkey: &str, name: &str) -> Result<(), String> {
    use windows_sys::Win32::System::Registry::{
        RegOpenKeyExW, RegDeleteValueW, RegCloseKey, KEY_WRITE
    };
    
    let subkey_w = to_wide_string(subkey);
    let name_w = to_wide_string(name);
    
    let mut hkey_result = 0;
    let status = RegOpenKeyExW(
        hkey,
        subkey_w.as_ptr(),
        0,
        KEY_WRITE,
        &mut hkey_result,
    );
    
    if status == 0 {
        RegDeleteValueW(hkey_result, name_w.as_ptr());
        RegCloseKey(hkey_result);
    }
    
    Ok(())
}

#[cfg(target_os = "windows")]
unsafe fn delete_registry_key(hkey: isize, subkey: &str) -> Result<(), String> {
    use windows_sys::Win32::System::Registry::RegDeleteKeyW;
    
    let subkey_w = to_wide_string(subkey);
    RegDeleteKeyW(hkey, subkey_w.as_ptr());
    Ok(())
}

#[cfg(target_os = "windows")]
unsafe fn write_extension_forcelist(hkey: isize, extension_id: &str, update_url: &str) -> Result<(), String> {
    use windows_sys::Win32::System::Registry::{
        RegCreateKeyExW, RegEnumValueW, RegSetValueExW, RegCloseKey,
        REG_SZ, REG_OPTION_NON_VOLATILE, KEY_READ, KEY_WRITE
    };
    
    let subkey = r"SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist";
    let subkey_w = to_wide_string(subkey);
    
    let mut hkey_result = 0;
    let status = RegCreateKeyExW(
        hkey,
        subkey_w.as_ptr(),
        0,
        std::ptr::null(),
        REG_OPTION_NON_VOLATILE,
        KEY_READ | KEY_WRITE,
        std::ptr::null(),
        &mut hkey_result,
        std::ptr::null_mut(),
    );
    
    if status != 0 {
        return Err(format!("Failed to open/create ExtensionInstallForcelist: {}", status));
    }
    
    let forcelist_value = format!("{};{}", extension_id, update_url);
    
    let mut index = 0;
    let mut name_buf = vec![0u16; 16384];
    let mut value_buf = vec![0u8; 32768];
    let mut found_name: Option<String> = None;
    let mut max_num = 0;
    
    loop {
        let mut name_len = name_buf.len() as u32;
        let mut val_type = 0u32;
        let mut val_len = value_buf.len() as u32;
        
        let status = RegEnumValueW(
            hkey_result,
            index,
            name_buf.as_mut_ptr(),
            &mut name_len,
            std::ptr::null_mut(),
            &mut val_type,
            value_buf.as_mut_ptr(),
            &mut val_len,
        );
        
        if status != 0 {
            break;
        }
        
        let name_str = String::from_utf16_lossy(&name_buf[..name_len as usize]);
        if let Ok(num) = name_str.parse::<u32>() {
            if num > max_num {
                max_num = num;
            }
        }
        
        if val_type == REG_SZ {
            let val_str = String::from_utf16_lossy(std::slice::from_raw_parts(
                value_buf.as_ptr() as *const u16,
                (val_len / 2) as usize
            )).trim_end_matches('\0').to_string();
            
            if val_str.contains(extension_id) {
                found_name = Some(name_str);
                break;
            }
        }
        
        index += 1;
    }
    
    let target_name = match found_name {
        Some(name) => name,
        None => (max_num + 1).to_string(),
    };
    
    let target_name_w = to_wide_string(&target_name);
    let forcelist_value_w = to_wide_string(&forcelist_value);
    
    let status = RegSetValueExW(
        hkey_result,
        target_name_w.as_ptr(),
        0,
        REG_SZ,
        forcelist_value_w.as_ptr() as *const u8,
        (forcelist_value_w.len() * 2) as u32,
    );
    
    RegCloseKey(hkey_result);
    
    if status != 0 {
        return Err(format!("RegSetValueExW failed for forcelist: {}", status));
    }
    
    Ok(())
}

#[cfg(target_os = "windows")]
unsafe fn remove_extension_from_forcelist(hkey: isize, extension_id: &str) -> Result<(), String> {
    use windows_sys::Win32::System::Registry::{
        RegOpenKeyExW, RegEnumValueW, RegDeleteValueW, RegCloseKey, KEY_READ, KEY_WRITE, REG_SZ
    };
    
    let subkey = r"SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist";
    let subkey_w = to_wide_string(subkey);
    
    let mut hkey_result = 0;
    let status = RegOpenKeyExW(
        hkey,
        subkey_w.as_ptr(),
        0,
        KEY_READ | KEY_WRITE,
        &mut hkey_result,
    );
    
    if status != 0 {
        return Ok(()); // Key doesn't exist, nothing to remove
    }
    
    let mut index = 0;
    let mut name_buf = vec![0u16; 16384];
    let mut value_buf = vec![0u8; 32768];
    let mut name_to_delete: Option<Vec<u16>> = None;
    
    loop {
        let mut name_len = name_buf.len() as u32;
        let mut val_type = 0u32;
        let mut val_len = value_buf.len() as u32;
        
        let status = RegEnumValueW(
            hkey_result,
            index,
            name_buf.as_mut_ptr(),
            &mut name_len,
            std::ptr::null_mut(),
            &mut val_type,
            value_buf.as_mut_ptr(),
            &mut val_len,
        );
        
        if status != 0 {
            break;
        }
        
        if val_type == REG_SZ {
            let val_str = String::from_utf16_lossy(std::slice::from_raw_parts(
                value_buf.as_ptr() as *const u16,
                (val_len / 2) as usize
            )).trim_end_matches('\0').to_string();
            
            if val_str.contains(extension_id) {
                name_to_delete = Some(name_buf[..name_len as usize].to_vec());
                if let Some(ref mut name) = name_to_delete {
                    name.push(0);
                }
                break;
            }
        }
        
        index += 1;
    }
    
    if let Some(name_w) = name_to_delete {
        RegDeleteValueW(hkey_result, name_w.as_ptr());
    }
    
    RegCloseKey(hkey_result);
    Ok(())
}

#[derive(serde::Deserialize)]
struct AgentConfig {
    server_url: String,
    agent_token: Option<String>,
}

fn load_agent_config() -> Option<AgentConfig> {
    let config_path = if cfg!(target_os = "windows") {
        let primary_dir = "C:\\ProgramData\\Guardian";
        let primary_path = format!("{}\\config.json", primary_dir);
        if Path::new(&primary_path).exists() {
            primary_path
        } else {
            "config.json".to_string()
        }
    } else {
        let primary_dir = "/etc/guardian-agent";
        let primary_path = format!("{}/config.json", primary_dir);
        if Path::new(&primary_path).exists() {
            primary_path
        } else {
            "config.json".to_string()
        }
    };

    if let Ok(content) = fs::read_to_string(&config_path) {
        if let Ok(config) = serde_json::from_str::<AgentConfig>(&content) {
            return Some(config);
        }
    }
    None
}

pub fn run_reconcile(active_username: Option<&str>) -> Result<(), String> {
    if let Some(config) = load_agent_config() {
        reconcile_extension_policy(
            active_username,
            &config.server_url,
            config.agent_token.as_deref(),
        )
    } else {
        reconcile_extension_policy(None, "", None)
    }
}

use std::fs;
use std::path::PathBuf;
use std::process::Command;

use serde_json::json;

use super::compliance::HardwareReceipt;
use super::password;

mod oem_detect;

use oem_detect::detect;

const PAYLOAD_ROOT: &str = r"C:\ProgramData\Guardian\bios-payloads";

pub fn payload_dir(vendor: &str) -> PathBuf {
    PathBuf::from(PAYLOAD_ROOT).join(vendor)
}

pub fn payload_present(vendor: &str) -> bool {
    payload_dir(vendor).is_dir()
}

pub fn run_vendor_audit(oem: &str, model: Option<String>, interface: Option<String>) -> Result<HardwareReceipt, String> {
    let mut receipt = HardwareReceipt::new("windows", oem, model, interface);
    receipt.vendor_tool = json!({
        "name": receipt.interface.clone().unwrap_or_else(|| "vendor-cli".to_string()),
        "path": payload_dir(oem).display().to_string(),
        "present": payload_present(oem),
    });

    if !payload_present(oem) {
        receipt.supervisor_password.error = Some(format!(
            "Vendor payload for {oem} is not staged. Download from Guardian server first."
        ));
        receipt.usb_boot_disabled.error = receipt.supervisor_password.error.clone();
        receipt.secure_boot_enabled.error = receipt.supervisor_password.error.clone();
        receipt.finalize();
        return Ok(receipt);
    }

    match oem {
        "dell" => audit_dell(&mut receipt)?,
        "hp" => audit_hp(&mut receipt)?,
        "lenovo" => audit_lenovo(&mut receipt)?,
        "surface" => audit_surface(&mut receipt)?,
        _ => return Err(format!("Unsupported OEM for Windows audit: {oem}")),
    }

    receipt.finalize();
    Ok(receipt)
}

pub fn run_vendor_apply(
    oem: &str,
    model: Option<String>,
    interface: Option<String>,
    force_reset_password: bool,
) -> Result<(HardwareReceipt, Option<String>), String> {
    if !payload_present(oem) {
        return Err(format!(
            "Vendor payload for {oem} is not staged under {PAYLOAD_ROOT}"
        ));
    }

    let password = password::generate_supervisor_password(16);
    let mut receipt = HardwareReceipt::new("windows", oem, model, interface);
    receipt.vendor_tool = json!({
        "name": receipt.interface.clone().unwrap_or_else(|| "vendor-cli".to_string()),
        "path": payload_dir(oem).display().to_string(),
        "present": true,
    });

    let apply_result = match oem {
        "dell" => apply_dell(&password, force_reset_password),
        "hp" => apply_hp(&password, force_reset_password),
        "lenovo" => apply_lenovo(&password, force_reset_password),
        "surface" => apply_surface(&password, force_reset_password),
        _ => Err(format!("Unsupported OEM for Windows apply: {oem}")),
    };

    match apply_result {
        Ok(()) => {
            receipt.supervisor_password.applied = true;
            receipt.supervisor_password.actual = json!("set");
            let audited = run_vendor_audit(oem, receipt.model.clone(), receipt.interface.clone())?;
            let escrow = Some(password);
            Ok((audited, escrow))
        }
        Err(message) => {
            receipt.supervisor_password.applied = false;
            receipt.supervisor_password.error = Some(message);
            receipt.finalize();
            Ok((receipt, None))
        }
    }
}

fn run_powershell(script: &str) -> Result<String, String> {
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
        .map_err(|error| format!("Failed to execute PowerShell: {error}"))?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn audit_dell(receipt: &mut HardwareReceipt) -> Result<(), String> {
    let cctk = payload_dir("dell").join("cctk.exe");
    if !cctk.is_file() {
        return Err("Dell CCTK executable not found in staged payload".to_string());
    }
    let secure_boot = Command::new(&cctk)
        .args(["--secureboot", "--val"])
        .output()
        .map_err(|error| format!("Failed to query Dell Secure Boot: {error}"))?;
    let stdout = String::from_utf8_lossy(&secure_boot.stdout);
    receipt.secure_boot_enabled.actual = json!(stdout.contains("Enabled"));
    receipt.secure_boot_enabled.applied = stdout.contains("Enabled");
    Ok(())
}

fn apply_dell(password: &str, _force_reset_password: bool) -> Result<(), String> {
    let cctk = payload_dir("dell").join("cctk.exe");
    if !cctk.is_file() {
        return Err("Dell CCTK executable not found in staged payload".to_string());
    }
    let status = Command::new(&cctk)
        .args(["--setuppwd", password])
        .status()
        .map_err(|error| format!("Failed to set Dell supervisor password: {error}"))?;
    if !status.success() {
        return Err("Dell CCTK rejected supervisor password change".to_string());
    }
    let _ = Command::new(&cctk)
        .args(["--secureboot", "enabled"])
        .status();
    Ok(())
}

fn audit_hp(receipt: &mut HardwareReceipt) -> Result<(), String> {
    let script = format!(
        r#"
$module = Join-Path '{}' 'HP.ClientManagementScriptLibrary.dll'
if (-not (Test-Path $module)) {{ throw "HP CMSL module not found" }}
Import-Module $module
$boot = Get-HPBIOSSettingValue -Name 'Secure Boot'
Write-Output $boot
"#,
        payload_dir("hp").display()
    );
    let stdout = run_powershell(&script)?;
    receipt.secure_boot_enabled.actual = json!(stdout.to_ascii_lowercase().contains("enable"));
    receipt.secure_boot_enabled.applied = stdout.to_ascii_lowercase().contains("enable");
    Ok(())
}

fn apply_hp(password: &str, _force_reset_password: bool) -> Result<(), String> {
    let script = format!(
        r#"
$module = Join-Path '{}' 'HP.ClientManagementScriptLibrary.dll'
if (-not (Test-Path $module)) {{ throw "HP CMSL module not found" }}
Import-Module $module
Set-HPBIOSSettingValue -Name 'Setup Password' -Value '{}'
Set-HPBIOSSettingValue -Name 'Secure Boot' -Value 'Enable'
"#,
        payload_dir("hp").display(),
        password.replace('\'', "''")
    );
    run_powershell(&script).map(|_| ())
}

fn audit_lenovo(receipt: &mut HardwareReceipt) -> Result<(), String> {
    let script = r#"
$boot = (Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting -Filter "Name='Secure Boot'" -ErrorAction SilentlyContinue).CurrentSetting
Write-Output $boot
"#;
    let stdout = run_powershell(script).unwrap_or_default();
    receipt.secure_boot_enabled.actual = json!(stdout.to_ascii_lowercase().contains("enable"));
    receipt.secure_boot_enabled.applied = stdout.to_ascii_lowercase().contains("enable");
    Ok(())
}

fn apply_lenovo(password: &str, _force_reset_password: bool) -> Result<(), String> {
    let script = format!(
        r#"
$password = '{}'
$setter = Get-CimInstance -Namespace root/wmi -ClassName Lenovo_SetBiosSetting -ErrorAction Stop
Invoke-CimMethod -InputObject $setter -MethodName SetBiosSetting -Arguments @{{ Parameter = "Password,$password,ascii,us" }} | Out-Null
Invoke-CimMethod -InputObject $setter -MethodName SetBiosSetting -Arguments @{{ Parameter = "Secure Boot,Enable" }} | Out-Null
"#,
        password.replace('\'', "''")
    );
    run_powershell(&script).map(|_| ())
}

fn audit_surface(receipt: &mut HardwareReceipt) -> Result<(), String> {
    let script = format!(
        r#"
$module = Join-Path '{}' 'SurfaceEnterpriseManagementMode.psd1'
if (-not (Test-Path $module)) {{ throw "Surface SEMM module not found" }}
Import-Module $module
Write-Output 'surface-audit-ok'
"#
    ,
        payload_dir("surface").display()
    );
    run_powershell(&script)?;
    receipt.secure_boot_enabled.actual = json!("unknown");
    Ok(())
}

fn apply_surface(password: &str, _force_reset_password: bool) -> Result<(), String> {
    let script = format!(
        r#"
$module = Join-Path '{}' 'SurfaceEnterpriseManagementMode.psd1'
if (-not (Test-Path $module)) {{ throw "Surface SEMM module not found" }}
Import-Module $module
Write-Output 'surface-apply:{password}'
"#,
        payload_dir("surface").display(),
        password = password.replace('\'', "''")
    );
    run_powershell(&script).map(|_| ())
}

pub fn stage_payload_from_server(vendor: &str, server_url: &str, agent_token: &str) -> Result<(), String> {
    let base = server_url
        .trim_end_matches("/ws")
        .trim_end_matches('/')
        .to_string();
    let url = format!("{base}/api/agent/bios-payloads/{vendor}");
    let output = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            &format!(
                r#"
$headers = @{{ Authorization = 'Bearer {agent_token}' }}
$target = '{payload_root}\{vendor}'
New-Item -ItemType Directory -Force -Path $target | Out-Null
$archive = Join-Path $target 'payload.zip'
Invoke-WebRequest -Uri '{url}' -Headers $headers -OutFile $archive
Expand-Archive -Path $archive -DestinationPath $target -Force
Remove-Item $archive
"#,
                agent_token = agent_token,
                payload_root = PAYLOAD_ROOT,
                vendor = vendor,
                url = url,
            ),
        ])
        .status()
        .map_err(|error| format!("Failed to stage BIOS payload: {error}"))?;
    if !output.success() {
        return Err("PowerShell payload staging script failed".to_string());
    }
    if !payload_dir(vendor).is_dir() {
        return Err("Payload directory missing after staging".to_string());
    }
    Ok(())
}

pub fn ensure_payload_staged(oem: &str, server_url: Option<&str>, agent_token: Option<&str>) {
    if payload_present(oem) {
        return;
    }
    if let (Some(server_url), Some(agent_token)) = (server_url, agent_token) {
        let _ = stage_payload_from_server(oem, server_url, agent_token);
    }
}

pub fn read_agent_config() -> (Option<String>, Option<String>) {
    let config_path = PathBuf::from(r"C:\ProgramData\Guardian\config.json");
    let Ok(raw) = fs::read_to_string(config_path) else {
        return (None, None);
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return (None, None);
    };
    let server_url = value
        .get("server_url")
        .and_then(|entry| entry.as_str())
        .map(str::to_string);
    let agent_token = value
        .get("agent_token")
        .and_then(|entry| entry.as_str())
        .map(str::to_string);
    (server_url, agent_token)
}

pub fn run_detect() -> super::compliance::OemDetectResult {
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
    let (server_url, agent_token) = read_agent_config();
    ensure_payload_staged(&detect.oem, server_url.as_deref(), agent_token.as_deref());
    run_vendor_audit(
        &detect.oem,
        detect.model.clone(),
        detect.interface.clone(),
    )
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
    let (server_url, agent_token) = read_agent_config();
    ensure_payload_staged(&detect.oem, server_url.as_deref(), agent_token.as_deref());
    run_vendor_apply(
        &detect.oem,
        detect.model.clone(),
        detect.interface.clone(),
        force_reset_password,
    )
}

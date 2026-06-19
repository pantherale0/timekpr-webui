#[cfg(target_os = "windows")]
use std::process::Command;
#[cfg(target_os = "windows")]
use tokio::io::AsyncBufReadExt;
#[cfg(target_os = "windows")]
use tokio::net::windows::named_pipe::ClientOptions;

#[cfg(target_os = "windows")]
pub async fn run_user_agent() {
    println!("Starting TimeKpr User Session Agent...");
    let pipe_name = r"\\.\pipe\timekpr_ipc";

    loop {
        // Connect to Named Pipe
        println!("UserAgent: Connecting to Named Pipe service...");
        match ClientOptions::new().open(pipe_name) {
            Ok(client) => {
                println!("UserAgent: Connected to service!");
                let reader = tokio::io::BufReader::new(client);
                let mut lines = reader.lines();

                while let Ok(Some(line)) = lines.next_line().await {
                    if let Ok(payload) = serde_json::from_str::<serde_json::Value>(&line) {
                        if payload["type"] == "toast" {
                            let title = payload["title"].as_str().unwrap_or(&crate::i18n::t("notification_fallback_title"));
                            let message = payload["message"].as_str().unwrap_or("");
                            
                            show_toast_notification(title, message);
                        }
                    }
                }
            }
            Err(e) => {
                eprintln!("UserAgent: Failed to connect to Named Pipe: {}. Retrying...", e);
            }
        }

        tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
    }
}

#[cfg(target_os = "windows")]
fn show_toast_notification(title: &str, message: &str) {
    println!("Showing Toast: {} - {}", title, message);
    
    // Clean string arguments to prevent shell injection
    let clean_title = title.replace('\'', "\"");
    let clean_message = message.replace('\'', "\"");
    
    // PowerShell script to fire a native Windows Toast Notification
    let ps_script = format!(
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; \
         $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); \
         $toastXml = [xml]$template.GetXml(); \
         $toastXml.toast.visual.binding.text[0].AppendChild($toastXml.CreateTextNode('{}')) | Out-Null; \
         $toastXml.toast.visual.binding.text[1].AppendChild($toastXml.CreateTextNode('{}')) | Out-Null; \
         $xml = New-Object Windows.Data.Xml.Dom.XmlDocument; \
         $xml.LoadXml($toastXml.OuterXml); \
         $toast = [Windows.UI.Notifications.ToastNotification]::new($xml); \
         [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('TimeKpr').Show($toast)",
        clean_title, clean_message
    );

    let _ = Command::new("powershell")
        .args(["-Command", &ps_script])
        .spawn();
}

#[cfg(not(target_os = "windows"))]
pub async fn run_user_agent() {
    // No-op for compilation on Linux
}

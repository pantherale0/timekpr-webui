use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Duration;
use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use tokio::time::sleep;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use uuid::Uuid;

type HmacSha256 = Hmac<Sha256>;

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Config {
    server_url: String,
    system_id: Option<String>,
    registration_token: Option<String>,
    agent_token: Option<String>,
}

#[derive(Deserialize, Debug)]
#[serde(tag = "type")]
enum ServerMessage {
    #[serde(rename = "pairing_status")]
    PairingStatus { status: String },
    #[serde(rename = "pairing_approved")]
    PairingApproved { token: String },
    #[serde(rename = "challenge")]
    Challenge { challenge: String },
    #[serde(rename = "auth_result")]
    AuthResult { success: bool, message: String },
    #[serde(rename = "command_request")]
    CommandRequest {
        correlation_id: String,
        action: String,
        username: String,
        args: serde_json::Value,
    },
}

#[derive(Serialize, Debug)]
#[serde(tag = "type")]
enum ClientMessage {
    #[serde(rename = "hello")]
    Hello {
        system_id: String,
        registration_token: Option<String>,
    },
    #[serde(rename = "register")]
    Register {
        system_id: String,
        signature: String,
    },
    #[serde(rename = "command_response")]
    CommandResponse {
        correlation_id: String,
        success: bool,
        message: String,
        data: serde_json::Value,
    },
}

fn get_config_path() -> String {
    let primary_dir = "/etc/timekpr-agent";
    let primary_path = format!("{}/config.json", primary_dir);
    let fallback_path = "config.json";

    if Path::new(&primary_path).exists() {
        primary_path
    } else if Path::new(fallback_path).exists() {
        fallback_path.to_string()
    } else {
        if fs::create_dir_all(primary_dir).is_ok() {
            primary_path
        } else {
            fallback_path.to_string()
        }
    }
}

fn load_or_create_config() -> Config {
    let config_path = get_config_path();
    println!("Loading config from: {}", config_path);

    let mut config = if let Ok(data) = fs::read_to_string(&config_path) {
        if let Ok(c) = serde_json::from_str::<Config>(&data) {
            c
        } else {
            Config {
                server_url: "ws://localhost:5000/ws".to_string(),
                system_id: None,
                registration_token: None,
                agent_token: None,
            }
        }
    } else {
        Config {
            server_url: "ws://localhost:5000/ws".to_string(),
            system_id: None,
            registration_token: None,
            agent_token: None,
        }
    };

    // Auto-generate UUID if missing
    if config.system_id.is_none() || config.system_id.as_ref().unwrap().trim().is_empty() {
        let new_uuid = Uuid::new_v4().to_string();
        println!("------------------------------------------------------------");
        println!("GENERATE NEW HOST UUID: {}", new_uuid);
        println!("PLEASE REGISTER THIS HOST UUID IN THE SERVER WEB UI PANEL!");
        println!("------------------------------------------------------------");
        config.system_id = Some(new_uuid);

        // Try to save updated config back
        if let Ok(serialized) = serde_json::to_string_pretty(&config) {
            if let Err(e) = fs::write(&config_path, serialized) {
                eprintln!("Warning: Failed to save updated config to {}: {}", config_path, e);
            }
        }
    }

    config
}

fn execute_command(cmd_args: &[&str]) -> (i32, String, String) {
    println!("Executing command: {:?}", cmd_args);
    let output = Command::new(cmd_args[0])
        .args(&cmd_args[1..])
        .output();

    match output {
        Ok(out) => {
            let exit_code = out.status.code().unwrap_or(-1);
            let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
            let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
            (exit_code, stdout, stderr)
        }
        Err(e) => {
            (-1, "".to_string(), format!("Command execution failed: {}", e))
        }
    }
}

// Execute command with fallback to sudo if needed
fn execute_command_with_sudo(cmd_args: &[&str]) -> (i32, String, String) {
    let (code, stdout, stderr) = execute_command(cmd_args);
    if code != 0 {
        println!("Command failed (code {}), trying with sudo...", code);
        let mut sudo_args = vec!["sudo"];
        sudo_args.extend_from_slice(cmd_args);
        execute_command(&sudo_args)
    } else {
        (code, stdout, stderr)
    }
}

fn handle_command(action: &str, username: &str, args: &serde_json::Value) -> (bool, String, serde_json::Value) {
    match action {
        "validate_user" => {
            let cmd = ["timekpra", "--userinfo", username];
            let (code, stdout, stderr) = execute_command(&cmd);
            
            // Re-check with sudo if needed
            let (code, stdout, stderr) = if code != 0 {
                let cmd_sudo = ["sudo", "timekpra", "--userinfo", username];
                execute_command(&cmd_sudo)
            } else {
                (code, stdout, stderr)
            };

            let data = serde_json::json!({
                "exit_code": code,
                "stdout": stdout,
                "stderr": stderr
            });

            // check for user not found in output
            if stdout.contains("configuration is not found") || stderr.contains("configuration is not found") {
                (false, format!("User '{}' configuration not found", username), data)
            } else if code == 0 {
                (true, "User validated successfully".to_string(), data)
            } else {
                (false, format!("Validation command failed: {}", stderr), data)
            }
        }
        "modify_time_left" => {
            let op = args.get("operation").and_then(|v| v.as_str()).unwrap_or("+");
            let secs = args.get("seconds").and_then(|v| v.as_u64()).unwrap_or(0);
            
            let secs_str = secs.to_string();
            let cmd = ["timekpra", "--settimeleft", username, op, &secs_str];
            let (code, _stdout, stderr) = execute_command_with_sudo(&cmd);

            if code == 0 {
                (true, format!("Successfully modified time: {}{} seconds", op, secs), serde_json::json!({}))
            } else {
                (false, format!("Failed to modify time: {}", stderr), serde_json::json!({}))
            }
        }
        "set_weekly_time_limits" => {
            let schedule = match args.get("schedule").and_then(|v| v.as_object()) {
                Some(s) => s,
                None => return (false, "Missing 'schedule' argument".to_string(), serde_json::json!({})),
            };

            let day_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
            
            // 1. Calculate allowed days (1=Monday, 7=Sunday)
            let mut allowed_days = Vec::new();
            for (i, day) in day_order.iter().enumerate() {
                let hours = schedule.get(*day).and_then(|v| v.as_f64()).unwrap_or(0.0);
                if hours > 0.0 {
                    allowed_days.push((i + 1).to_string());
                }
            }

            if allowed_days.is_empty() {
                return (false, "No allowed days with time limits configured".to_string(), serde_json::json!({}));
            }

            let allowed_days_str = allowed_days.join(";");
            let cmd_days = ["timekpra", "--setalloweddays", username, &allowed_days_str];
            let (code_days, _, err_days) = execute_command_with_sudo(&cmd_days);
            if code_days != 0 {
                return (false, format!("Failed to set allowed days: {}", err_days), serde_json::json!({}));
            }

            // 2. Set time limits for allowed days
            let mut time_limits = Vec::new();
            for day in day_order.iter() {
                let hours = schedule.get(*day).and_then(|v| v.as_f64()).unwrap_or(0.0);
                if hours > 0.0 {
                    let seconds = (hours * 3600.0) as u64;
                    time_limits.push(seconds.to_string());
                }
            }

            let limits_str = time_limits.join(";");
            let cmd_limits = ["timekpra", "--settimelimits", username, &limits_str];
            let (code_limits, _, err_limits) = execute_command_with_sudo(&cmd_limits);
            if code_limits != 0 {
                return (false, format!("Failed to set time limits: {}", err_limits), serde_json::json!({}));
            }

            (true, "Weekly time limits configured successfully".to_string(), serde_json::json!({}))
        }
        "set_allowed_hours" => {
            let intervals = match args.get("intervals").and_then(|v| v.as_object()) {
                Some(i) => i,
                None => return (false, "Missing 'intervals' argument".to_string(), serde_json::json!({})),
            };

            let mut success_count = 0;
            let mut total_count = 0;
            let mut errors = Vec::new();

            for (day_str, hours_val) in intervals {
                let hour_str = match hours_val.as_str() {
                    Some(s) => s,
                    None => continue,
                };
                total_count += 1;

                let cmd = ["timekpra", "--setallowedhours", username, day_str, hour_str];
                let (code, _, err) = execute_command_with_sudo(&cmd);
                if code == 0 {
                    success_count += 1;
                } else {
                    errors.push(format!("Day {}: {}", day_str, err));
                }
            }

            if success_count == total_count {
                (true, format!("Successfully set allowed hours for {}/{} days", success_count, total_count), serde_json::json!({}))
            } else {
                (false, format!("Errors setting allowed hours: {}", errors.join("; ")), serde_json::json!({}))
            }
        }
        _ => (false, format!("Unknown action '{}'", action), serde_json::json!({})),
    }
}

#[tokio::main]
async fn main() {
    println!("Starting Timekpr Client Agent...");
    
    loop {
        let config = load_or_create_config();
        let server_url = &config.server_url;
        let system_id = config.system_id.clone().unwrap();
        let agent_token = config.agent_token.clone();
        let registration_token = config.registration_token.clone();

        println!("Connecting to server: {}", server_url);
        
        match connect_async(server_url).await {
            Ok((mut ws_stream, _)) => {
                println!("WebSocket connected! Starting handshake...");
                
                // 1. Send initial hello message
                let hello_msg = ClientMessage::Hello {
                    system_id: system_id.clone(),
                    registration_token,
                };
                let hello_json = serde_json::to_string(&hello_msg).unwrap();
                if let Err(e) = ws_stream.send(Message::Text(hello_json)).await {
                    eprintln!("Failed to send hello message: {}", e);
                    sleep(Duration::from_secs(5)).await;
                    continue;
                }
                println!("Sent hello message to server.");

                let mut authenticated = false;

                while let Some(msg_result) = ws_stream.next().await {
                    match msg_result {
                        Ok(Message::Text(text)) => {
                            match serde_json::from_str::<ServerMessage>(&text) {
                                Ok(ServerMessage::PairingStatus { status }) => {
                                    println!("Received pairing status: {}", status);
                                    if status == "pending" {
                                        println!("Device pairing status is PENDING approval. Please approve this device in the server's admin panel.");
                                    }
                                }
                                Ok(ServerMessage::PairingApproved { token }) => {
                                    println!("Pairing approved! Received secure token.");
                                    let mut updated_config = config.clone();
                                    updated_config.agent_token = Some(token);
                                    let config_path = get_config_path();
                                    if let Ok(serialized) = serde_json::to_string_pretty(&updated_config) {
                                        if let Err(e) = fs::write(&config_path, serialized) {
                                            eprintln!("Error saving agent token to config: {}", e);
                                        } else {
                                            println!("Successfully saved agent token to config! Reconnecting in 2 seconds...");
                                        }
                                    }
                                    break;
                                }
                                Ok(ServerMessage::Challenge { challenge }) => {
                                    println!("Received authentication challenge: {}", challenge);
                                    
                                    let token_str = match &agent_token {
                                        Some(t) => t,
                                        None => {
                                            eprintln!("Received authentication challenge but no agent token is configured!");
                                            break;
                                        }
                                    };

                                    // Sign the challenge: HMAC-SHA256(agent_token, challenge + system_id)
                                    let mut mac = HmacSha256::new_from_slice(token_str.as_bytes())
                                        .expect("HMAC key setup failed");
                                    mac.update(format!("{}{}", challenge, system_id).as_bytes());
                                    let signature_bytes = mac.finalize().into_bytes();
                                    let signature_hex = hex::encode(signature_bytes);

                                    let reg_msg = ClientMessage::Register {
                                        system_id: system_id.clone(),
                                        signature: signature_hex,
                                    };

                                    let reg_json = serde_json::to_string(&reg_msg).unwrap();
                                    if let Err(e) = ws_stream.send(Message::Text(reg_json)).await {
                                        eprintln!("Failed to send register message: {}", e);
                                        break;
                                    }
                                }
                                Ok(ServerMessage::AuthResult { success, message }) => {
                                    println!("Handshake result: success = {}, message = {}", success, message);
                                    if success {
                                        authenticated = true;
                                        println!("Agent authenticated successfully!");
                                    } else {
                                        eprintln!("Authentication failed, disconnecting.");
                                        break;
                                    }
                                }
                                Ok(ServerMessage::CommandRequest { correlation_id, action, username, args }) => {
                                    if !authenticated {
                                        eprintln!("Received command before authentication completed. Ignoring.");
                                        continue;
                                    }
                                    println!("Received command: {} for user {} (correlation ID: {})", action, username, correlation_id);
                                    
                                    let (success, msg, data) = handle_command(&action, &username, &args);
                                    
                                    let response = ClientMessage::CommandResponse {
                                        correlation_id,
                                        success,
                                        message: msg,
                                        data,
                                    };

                                    if let Ok(resp_json) = serde_json::to_string(&response) {
                                        if let Err(e) = ws_stream.send(Message::Text(resp_json)).await {
                                            eprintln!("Failed to send command response: {}", e);
                                            break;
                                        }
                                        println!("Sent command response back successfully");
                                    }
                                }
                                Err(e) => {
                                    eprintln!("Failed to parse server message: {}", e);
                                }
                            }
                        }
                        Ok(Message::Close(_)) => {
                            println!("Connection closed by server.");
                            break;
                        }
                        Err(e) => {
                            eprintln!("WebSocket stream error: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }
            }
            Err(e) => {
                eprintln!("Connection failed: {}. Retrying...", e);
            }
        }

        // Wait before reconnecting (exponential backoff / standard retry)
        println!("Reconnecting in 5 seconds...");
        sleep(Duration::from_secs(5)).await;
    }
}

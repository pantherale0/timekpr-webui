use chrono::{SecondsFormat, Utc};
use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use logind_zbus::manager::ManagerProxy;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Duration;
use tokio::sync::{mpsc, watch};
use tokio::task::JoinHandle;
use tokio::time::sleep;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use uuid::Uuid;
use zbus::{Connection, Proxy};

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

#[derive(Serialize, Debug, Clone)]
#[serde(tag = "type")]
enum ClientMessage {
    #[serde(rename = "hello")]
    Hello {
        system_id: String,
        system_hostname: Option<String>,
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
    #[serde(rename = "alert_event")]
    AlertEvent {
        event_type: String,
        occurred_at: String,
        linux_username: Option<String>,
        details: serde_json::Value,
    },
}

#[derive(Clone, Debug, Default)]
struct SessionSnapshot {
    username: Option<String>,
    session_class: Option<String>,
    session_state: Option<String>,
}

fn get_config_path() -> String {
    let primary_dir = "/etc/timekpr-agent";
    let primary_path = format!("{}/config.json", primary_dir);
    let fallback_path = "config.json";

    if Path::new(&primary_path).exists() {
        primary_path
    } else if Path::new(fallback_path).exists() {
        fallback_path.to_string()
    } else if fs::create_dir_all(primary_dir).is_ok() {
        primary_path
    } else {
        fallback_path.to_string()
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

    if config.system_id.is_none() || config.system_id.as_ref().is_some_and(|value| value.trim().is_empty()) {
        let new_uuid = Uuid::new_v4().to_string();
        println!("------------------------------------------------------------");
        println!("GENERATE NEW HOST UUID: {}", new_uuid);
        println!("PLEASE REGISTER THIS HOST UUID IN THE SERVER WEB UI PANEL!");
        println!("------------------------------------------------------------");
        config.system_id = Some(new_uuid);

        if let Ok(serialized) = serde_json::to_string_pretty(&config) {
            if let Err(e) = fs::write(&config_path, serialized) {
                eprintln!("Warning: Failed to save updated config to {}: {}", config_path, e);
            }
        }
    }

    config
}

fn get_system_hostname() -> Option<String> {
    if let Ok(hostname) = std::env::var("HOSTNAME") {
        let trimmed = hostname.trim();
        if !trimmed.is_empty() {
            return Some(trimmed.to_string());
        }
    }

    if let Ok(hostname_file) = fs::read_to_string("/etc/hostname") {
        let trimmed = hostname_file.trim();
        if !trimmed.is_empty() {
            return Some(trimmed.to_string());
        }
    }

    if let Ok(output) = Command::new("hostname").output() {
        if output.status.success() {
            let hostname = String::from_utf8_lossy(&output.stdout);
            let trimmed = hostname.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }

    None
}

fn execute_command(cmd_args: &[&str]) -> (i32, String, String) {
    println!("Executing command: {:?}", cmd_args);
    let output = Command::new(cmd_args[0]).args(&cmd_args[1..]).output();

    match output {
        Ok(out) => {
            let exit_code = out.status.code().unwrap_or(-1);
            let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
            let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
            (exit_code, stdout, stderr)
        }
        Err(e) => (-1, "".to_string(), format!("Command execution failed: {}", e)),
    }
}

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

            let (code, stdout, stderr) = if code != 0 {
                let cmd_sudo = ["sudo", "timekpra", "--userinfo", username];
                execute_command(&cmd_sudo)
            } else {
                (code, stdout, stderr)
            };

            let mut enriched_stdout = stdout.clone();
            if code == 0 && !stdout.contains("LINUX_UID:") {
                let uid_cmd = ["id", "-u", username];
                let (uid_code, uid_stdout, _) = execute_command_with_sudo(&uid_cmd);
                if uid_code == 0 {
                    let uid_clean = uid_stdout.trim();
                    if !uid_clean.is_empty() {
                        if !enriched_stdout.ends_with('\n') {
                            enriched_stdout.push('\n');
                        }
                        enriched_stdout.push_str(&format!("LINUX_UID: {}\n", uid_clean));
                    }
                }
            }

            let data = serde_json::json!({
                "exit_code": code,
                "stdout": enriched_stdout,
                "stderr": stderr
            });

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

            let day_order = ["1", "2", "3", "4", "5", "6", "7"];
            let mut success_count = 0;
            let mut total_count = 0;
            let mut errors = Vec::new();

            for day_str in day_order {
                let hours_val = match intervals.get(day_str) {
                    Some(val) => val,
                    None => continue,
                };
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

fn current_timestamp() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}

fn build_alert_message(
    event_type: &str,
    linux_username: Option<String>,
    details: serde_json::Value,
) -> ClientMessage {
    let normalized_details = if details.is_object() {
        details
    } else {
        serde_json::json!({})
    };

    ClientMessage::AlertEvent {
        event_type: event_type.to_string(),
        occurred_at: current_timestamp(),
        linux_username,
        details: normalized_details,
    }
}

fn is_user_session_class(session_class: Option<&str>) -> bool {
    session_class.is_some_and(|class_name| class_name.starts_with("user"))
}

async fn resolve_session_snapshot(connection: &Connection, object_path: &str) -> Option<SessionSnapshot> {
    let proxy = match Proxy::new(
        connection,
        "org.freedesktop.login1",
        object_path,
        "org.freedesktop.login1.Session",
    )
    .await
    {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind session proxy for {}: {}", object_path, e);
            return None;
        }
    };

    let username = proxy.get_property::<String>("Name").await.ok();
    let session_class = proxy.get_property::<String>("Class").await.ok();
    let session_state = proxy.get_property::<String>("State").await.ok();

    Some(SessionSnapshot {
        username,
        session_class,
        session_state,
    })
}

async fn run_session_listener(
    tx: mpsc::UnboundedSender<ClientMessage>,
    mut shutdown: watch::Receiver<bool>,
) {
    let connection = match Connection::system().await {
        Ok(connection) => connection,
        Err(e) => {
            eprintln!("Failed to connect to the system bus for session alerts: {}", e);
            return;
        }
    };

    let proxy = match ManagerProxy::new(&connection).await {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind manager proxy for session alerts: {}", e);
            return;
        }
    };

    let mut session_new_stream = match proxy.receive_session_new().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to SessionNew events: {}", e);
            return;
        }
    };

    let mut session_removed_stream = match proxy.receive_session_removed().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to SessionRemoved events: {}", e);
            return;
        }
    };

    let mut session_cache: HashMap<String, SessionSnapshot> = HashMap::new();

    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                break;
            }
            signal = session_new_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("SessionNew stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        let session_id = args.session_id.to_string();
                        let object_path = args.object_path.to_string();
                        if let Some(snapshot) = resolve_session_snapshot(&connection, &object_path).await {
                            if is_user_session_class(snapshot.session_class.as_deref()) {
                                let details = serde_json::json!({
                                    "session_id": session_id,
                                    "session_class": snapshot.session_class.clone(),
                                    "session_state": snapshot.session_state.clone(),
                                });
                                session_cache.insert(session_id.clone(), snapshot.clone());
                                if tx.send(build_alert_message("user_signed_in", snapshot.username.clone(), details)).is_err() {
                                    break;
                                }
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse SessionNew signal: {}", e);
                    }
                }
            }
            signal = session_removed_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("SessionRemoved stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        let session_id = args.session_id.to_string();
                        let snapshot = session_cache.remove(&session_id).unwrap_or_default();
                        let details = serde_json::json!({
                            "session_id": session_id,
                            "session_class": snapshot.session_class.clone(),
                            "session_state": snapshot.session_state.clone(),
                        });
                        if tx.send(build_alert_message("user_signed_out", snapshot.username.clone(), details)).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse SessionRemoved signal: {}", e);
                    }
                }
            }
        }
    }
}

async fn run_sleep_listener(
    tx: mpsc::UnboundedSender<ClientMessage>,
    mut shutdown: watch::Receiver<bool>,
) {
    let connection = match Connection::system().await {
        Ok(connection) => connection,
        Err(e) => {
            eprintln!("Failed to connect to the system bus for sleep alerts: {}", e);
            return;
        }
    };

    let proxy = match ManagerProxy::new(&connection).await {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind manager proxy for sleep alerts: {}", e);
            return;
        }
    };

    let mut sleep_stream = match proxy.receive_prepare_for_sleep().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to PrepareForSleep events: {}", e);
            return;
        }
    };

    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                break;
            }
            signal = sleep_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("PrepareForSleep stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        let (event_type, phase) = if args.start {
                            ("system_sleep", "prepare")
                        } else {
                            ("system_resume", "resume")
                        };
                        let details = serde_json::json!({
                            "phase": phase,
                            "signal": "PrepareForSleep",
                        });
                        if tx.send(build_alert_message(event_type, None, details)).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse PrepareForSleep signal: {}", e);
                    }
                }
            }
        }
    }
}

async fn run_shutdown_listener(
    tx: mpsc::UnboundedSender<ClientMessage>,
    mut shutdown: watch::Receiver<bool>,
) {
    let connection = match Connection::system().await {
        Ok(connection) => connection,
        Err(e) => {
            eprintln!("Failed to connect to the system bus for shutdown alerts: {}", e);
            return;
        }
    };

    let proxy = match ManagerProxy::new(&connection).await {
        Ok(proxy) => proxy,
        Err(e) => {
            eprintln!("Failed to create logind manager proxy for shutdown alerts: {}", e);
            return;
        }
    };

    let mut shutdown_stream = match proxy.receive_prepare_for_shutdown().await {
        Ok(stream) => stream,
        Err(e) => {
            eprintln!("Failed to subscribe to PrepareForShutdown events: {}", e);
            return;
        }
    };

    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                break;
            }
            signal = shutdown_stream.next() => {
                let Some(signal) = signal else {
                    eprintln!("PrepareForShutdown stream ended unexpectedly");
                    break;
                };

                match signal.args() {
                    Ok(args) => {
                        if !args.start {
                            continue;
                        }
                        let details = serde_json::json!({
                            "phase": "prepare",
                            "signal": "PrepareForShutdown",
                        });
                        if tx.send(build_alert_message("system_restart", None, details)).is_err() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("Failed to parse PrepareForShutdown signal: {}", e);
                    }
                }
            }
        }
    }
}

fn spawn_logind_listeners(
    tx: mpsc::UnboundedSender<ClientMessage>,
    shutdown_rx: watch::Receiver<bool>,
) -> Vec<JoinHandle<()>> {
    vec![
        tokio::spawn(run_session_listener(tx.clone(), shutdown_rx.clone())),
        tokio::spawn(run_sleep_listener(tx.clone(), shutdown_rx.clone())),
        tokio::spawn(run_shutdown_listener(tx, shutdown_rx)),
    ]
}

#[tokio::main]
async fn main() {
    println!("Starting Timekpr Client Agent...");

    loop {
        let config = load_or_create_config();
        let server_url = config.server_url.clone();
        let system_id = config.system_id.clone().unwrap_or_else(|| Uuid::new_v4().to_string());
        let agent_token = config.agent_token.clone();
        let registration_token = config.registration_token.clone();
        let system_hostname = get_system_hostname();

        println!("Connecting to server: {}", server_url);

        match connect_async(&server_url).await {
            Ok((mut ws_stream, _)) => {
                println!("WebSocket connected! Starting handshake...");

                let hello_msg = ClientMessage::Hello {
                    system_id: system_id.clone(),
                    system_hostname: system_hostname.clone(),
                    registration_token,
                };
                let hello_json = serde_json::to_string(&hello_msg).unwrap();
                if let Err(e) = ws_stream.send(Message::Text(hello_json.into())).await {
                    eprintln!("Failed to send hello message: {}", e);
                    sleep(Duration::from_secs(5)).await;
                    continue;
                }
                println!("Sent hello message to server.");

                let mut authenticated = false;
                while let Some(msg_result) = ws_stream.next().await {
                    match msg_result {
                        Ok(Message::Text(text)) => match serde_json::from_str::<ServerMessage>(&text) {
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
                                    Some(token) => token,
                                    None => {
                                        eprintln!("Received authentication challenge but no agent token is configured!");
                                        break;
                                    }
                                };

                                let mut mac = HmacSha256::new_from_slice(token_str.as_bytes())
                                    .expect("HMAC key setup failed");
                                mac.update(format!("{}{}", challenge, system_id).as_bytes());
                                let signature_bytes = mac.finalize().into_bytes();
                                let signature_hex = hex::encode(signature_bytes);

                                let register_msg = ClientMessage::Register {
                                    system_id: system_id.clone(),
                                    signature: signature_hex,
                                };

                                let register_json = serde_json::to_string(&register_msg).unwrap();
                                if let Err(e) = ws_stream.send(Message::Text(register_json.into())).await {
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
                                }
                                break;
                            }
                            Ok(ServerMessage::CommandRequest { .. }) => {
                                eprintln!("Received command request before the message loop was ready.");
                            }
                            Err(e) => {
                                eprintln!("Failed to parse server message during handshake: {}", e);
                            }
                        },
                        Ok(Message::Close(_)) => {
                            println!("Connection closed by server during handshake.");
                            break;
                        }
                        Err(e) => {
                            eprintln!("WebSocket stream error during handshake: {}", e);
                            break;
                        }
                        _ => {}
                    }
                }

                if !authenticated {
                    println!("Handshake did not complete; reconnecting.");
                    sleep(Duration::from_secs(5)).await;
                    continue;
                }

                let (mut ws_write, mut ws_read) = ws_stream.split();
                let (client_tx, mut client_rx) = mpsc::unbounded_channel::<ClientMessage>();
                let writer_handle = tokio::spawn(async move {
                    while let Some(message) = client_rx.recv().await {
                        let serialized = match serde_json::to_string(&message) {
                            Ok(serialized) => serialized,
                            Err(e) => {
                                eprintln!("Failed to serialize client message: {}", e);
                                continue;
                            }
                        };

                        if let Err(e) = ws_write.send(Message::Text(serialized.into())).await {
                            eprintln!("Failed to send client message: {}", e);
                            break;
                        }
                    }
                });

                let (shutdown_tx, shutdown_rx) = watch::channel(false);
                let listener_handles = spawn_logind_listeners(client_tx.clone(), shutdown_rx);

                let startup_details = serde_json::json!({
                    "source": "agent_service",
                    "hostname": system_hostname.clone(),
                });
                let _ = client_tx.send(build_alert_message("system_startup", None, startup_details));

                while let Some(msg_result) = ws_read.next().await {
                    match msg_result {
                        Ok(Message::Text(text)) => match serde_json::from_str::<ServerMessage>(&text) {
                            Ok(ServerMessage::CommandRequest { correlation_id, action, username, args }) => {
                                println!(
                                    "Received command: {} for user {} (correlation ID: {})",
                                    action,
                                    username,
                                    correlation_id
                                );

                                let (success, message, data) = handle_command(&action, &username, &args);
                                let response = ClientMessage::CommandResponse {
                                    correlation_id,
                                    success,
                                    message,
                                    data,
                                };

                                if client_tx.send(response).is_err() {
                                    eprintln!("Writer channel closed while sending command response");
                                    break;
                                }
                            }
                            Ok(other) => {
                                eprintln!("Ignoring unexpected server message after authentication: {:?}", other);
                            }
                            Err(e) => {
                                eprintln!("Failed to parse server message: {}", e);
                            }
                        },
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

                let _ = shutdown_tx.send(true);
                drop(client_tx);

                for handle in listener_handles {
                    let _ = handle.await;
                }
                let _ = writer_handle.await;
            }
            Err(e) => {
                eprintln!("Connection failed: {}. Retrying...", e);
            }
        }

        println!("Reconnecting in 5 seconds...");
        sleep(Duration::from_secs(5)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::{build_alert_message, is_user_session_class, ClientMessage};

    #[test]
    fn alert_messages_use_expected_shape() {
        let message = build_alert_message(
            "system_startup",
            Some("alice".to_string()),
            serde_json::json!({"source": "test"}),
        );

        match message {
            ClientMessage::AlertEvent {
                event_type,
                occurred_at,
                linux_username,
                details,
            } => {
                assert_eq!(event_type, "system_startup");
                assert_eq!(linux_username.as_deref(), Some("alice"));
                assert!(occurred_at.ends_with('Z'));
                assert_eq!(details["source"], "test");
            }
            other => panic!("unexpected message: {other:?}"),
        }
    }

    #[test]
    fn user_session_class_filter_matches_systemd_user_sessions() {
        assert!(is_user_session_class(Some("user")));
        assert!(is_user_session_class(Some("user-light")));
        assert!(!is_user_session_class(Some("greeter")));
        assert!(!is_user_session_class(None));
    }
}

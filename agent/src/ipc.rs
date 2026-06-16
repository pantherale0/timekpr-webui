use std::fs;
use std::path::Path;
use serde::{Deserialize, Serialize};

#[derive(Deserialize, Debug, Clone)]
struct AgentConfig {
    server_url: String,
    agent_token: Option<String>,
}

fn get_config_path() -> String {
    #[cfg(target_os = "windows")]
    {
        let primary_dir = "C:\\ProgramData\\Guardian";
        let primary_path = format!("{}\\config.json", primary_dir);
        if Path::new(&primary_path).exists() {
            primary_path
        } else {
            "config.json".to_string()
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let primary_dir = "/etc/guardian-agent";
        let primary_path = format!("{}/config.json", primary_dir);
        if Path::new(&primary_path).exists() {
            primary_path
        } else {
            "config.json".to_string()
        }
    }
}

fn load_agent_config() -> Option<AgentConfig> {
    let config_path = get_config_path();
    if let Ok(content) = fs::read_to_string(&config_path) {
        if let Ok(config) = serde_json::from_str::<AgentConfig>(&content) {
            return Some(config);
        }
    }
    None
}

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

pub async fn read_framed_message<R: tokio::io::AsyncReadExt + Unpin>(reader: &mut R) -> Result<Vec<u8>, std::io::Error> {
    let mut len_bytes = [0u8; 4];
    reader.read_exact(&mut len_bytes).await?;
    let len = u32::from_ne_bytes(len_bytes) as usize;
    if len > 10 * 1024 * 1024 { // Cap at 10MB to prevent excessive memory usage
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "Message too large"));
    }
    let mut payload = vec![0u8; len];
    reader.read_exact(&mut payload).await?;
    Ok(payload)
}

pub async fn write_framed_message<W: tokio::io::AsyncWriteExt + Unpin>(writer: &mut W, payload: &[u8]) -> Result<(), std::io::Error> {
    let len = payload.len() as u32;
    writer.write_all(&len.to_ne_bytes()).await?;
    writer.write_all(payload).await?;
    writer.flush().await?;
    Ok(())
}

#[derive(Deserialize, Debug)]
#[serde(tag = "type")]
enum IpcRequest {
    #[serde(rename = "YOUTUBE_LOG")]
    YoutubeLog {
        logs: serde_json::Value,
    },
}

#[cfg(target_os = "linux")]
async fn handle_ipc_connection(mut stream: tokio::net::UnixStream) {
    let username = match stream.peer_cred() {
        Ok(cred) => {
            if let Some(user) = users::get_user_by_uid(cred.uid()) {
                user.name().to_string_lossy().into_owned()
            } else {
                eprintln!("IPC: Connecting UID {} maps to no system user", cred.uid());
                return;
            }
        }
        Err(e) => {
            eprintln!("IPC: Failed to retrieve peer credentials: {}", e);
            return;
        }
    };

    let (mut reader, mut writer) = stream.split();
    process_ipc_messages(&mut reader, &mut writer, &username).await;
}

#[cfg(target_os = "windows")]
async fn handle_ipc_connection(mut server: tokio::net::windows::named_pipe::NamedPipeServer) {
    let username = crate::windows_service::dns_proxy::get_active_session_username()
        .unwrap_or_else(|| "windows_user".to_string());

    let (mut reader, mut writer) = tokio::io::split(server);
    process_ipc_messages(&mut reader, &mut writer, &username).await;
}

async fn process_ipc_messages<R: tokio::io::AsyncReadExt + Unpin, W: tokio::io::AsyncWriteExt + Unpin>(
    reader: &mut R,
    writer: &mut W,
    username: &str,
) {
    loop {
        let payload = match read_framed_message(reader).await {
            Ok(bytes) => bytes,
            Err(e) => {
                if e.kind() != std::io::ErrorKind::UnexpectedEof {
                    eprintln!("IPC connection error reading message: {}", e);
                }
                break;
            }
        };

        let request: IpcRequest = match serde_json::from_slice(&payload) {
            Ok(req) => req,
            Err(e) => {
                let err_res = serde_json::json!({
                    "success": false,
                    "message": format!("Invalid IPC message structure: {}", e)
                });
                let _ = write_framed_message(writer, &serde_json::to_vec(&err_res).unwrap()).await;
                continue;
            }
        };

        match request {
            IpcRequest::YoutubeLog { logs } => {
                let response = handle_youtube_log(username, logs).await;
                if let Ok(res_bytes) = serde_json::to_vec(&response) {
                    if let Err(e) = write_framed_message(writer, &res_bytes).await {
                        eprintln!("IPC error writing response: {}", e);
                        break;
                    }
                }
            }
        }
    }
}

async fn handle_youtube_log(username: &str, logs: serde_json::Value) -> serde_json::Value {
    let Some(config) = load_agent_config() else {
        return serde_json::json!({
            "success": false,
            "message": "Local agent config missing or unreadable"
        });
    };

    let rest_url = convert_ws_to_http(&config.server_url);
    let target_url = format!("{}/api/youtube/log", rest_url);
    let token = config.agent_token.as_deref().unwrap_or("");

    let payload = serde_json::json!({
        "linux_username": username,
        "logs": logs,
    });

    let payload_str = serde_json::to_string(&payload).unwrap_or_default();

    let client = reqwest::Client::new();
    match client.post(&target_url)
        .header("Authorization", format!("Bearer {}", token))
        .header("Content-Type", "application/json")
        .body(payload_str)
        .send()
        .await
    {
        Ok(res) => {
            let status = res.status();
            if status.is_success() {
                let text = res.text().await.unwrap_or_default();
                serde_json::from_str::<serde_json::Value>(&text).unwrap_or_else(|_| {
                    serde_json::json!({ "success": true, "message": "Logs accepted" })
                })
            } else {
                let err_msg = res.text().await.unwrap_or_default();
                serde_json::json!({
                    "success": false,
                    "message": format!("Server returned HTTP {}: {}", status.as_u16(), err_msg)
                })
            }
        }
        Err(e) => {
            serde_json::json!({
                "success": false,
                "message": format!("Failed to route request to backend: {}", e)
            })
        }
    }
}

#[cfg(target_os = "linux")]
pub async fn run_ipc_server() -> Result<(), String> {
    let socket_dir = "/run/guardian-agent";
    let socket_path = "/run/guardian-agent/ipc.sock";

    if let Err(e) = fs::create_dir_all(socket_dir) {
        return Err(format!("Failed to create IPC socket directory: {}", e));
    }

    use std::os::unix::fs::PermissionsExt;
    if let Ok(metadata) = fs::metadata(socket_dir) {
        let mut permissions = metadata.permissions();
        permissions.set_mode(0o755);
        if let Err(e) = fs::set_permissions(socket_dir, permissions) {
            eprintln!("Warning: Failed to set IPC socket directory permissions: {}", e);
        }
    }

    if Path::new(socket_path).exists() {
        let _ = fs::remove_file(socket_path);
    }

    let listener = tokio::net::UnixListener::bind(socket_path)
        .map_err(|e| format!("Failed to bind to Unix socket: {}", e))?;

    // Enable read/write access for all local users (monitored users need to write to it)
    if let Ok(metadata) = fs::metadata(socket_path) {
        let mut permissions = metadata.permissions();
        permissions.set_mode(0o666);
        if let Err(e) = fs::set_permissions(socket_path, permissions) {
            eprintln!("Warning: Failed to set Unix socket permissions: {}", e);
        }
    }

    println!("Guardian client agent: local UDS IPC server listening on {}", socket_path);

    loop {
        match listener.accept().await {
            Ok((stream, _)) => {
                tokio::spawn(handle_ipc_connection(stream));
            }
            Err(e) => {
                eprintln!("IPC Socket accept error: {}", e);
            }
        }
    }
}

#[cfg(target_os = "windows")]
pub async fn run_ipc_server() -> Result<(), String> {
    use tokio::net::windows::named_pipe::ServerOptions;

    let pipe_name = r"\\.\pipe\guardian-agent-ipc";
    let mut first = true;

    println!("Guardian client agent: local Named Pipe IPC server listening on {}", pipe_name);

    loop {
        let server = {
            let mut opts = ServerOptions::new();
            if first {
                opts.first_pipe_instance(true);
                first = false;
            }
            match opts.create(pipe_name) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Failed to create Named Pipe server instance: {}", e);
                    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                    continue;
                }
            }
        };

        match server.connect().await {
            Ok(_) => {
                tokio::spawn(handle_ipc_connection(server));
            }
            Err(e) => {
                eprintln!("IPC Named Pipe client connection error: {}", e);
            }
        }
    }
}

pub async fn run_native_messaging_proxy() {
    let mut stdin = tokio::io::stdin();
    let mut stdout = tokio::io::stdout();

    #[cfg(target_os = "linux")]
    let stream_opt = tokio::net::UnixStream::connect("/run/guardian-agent/ipc.sock").await;
    
    #[cfg(target_os = "windows")]
    let stream_opt = tokio::net::windows::named_pipe::ClientOptions::new()
        .open(r"\\.\pipe\guardian-agent-ipc");

    let stream = match stream_opt {
        Ok(s) => s,
        Err(e) => {
            let err_res = serde_json::json!({
                "success": false,
                "message": format!("Failed to connect to local agent daemon: {}", e)
            });
            let _ = write_framed_message(&mut stdout, &serde_json::to_vec(&err_res).unwrap()).await;
            return;
        }
    };

    let (mut socket_reader, mut socket_writer) = tokio::io::split(stream);

    let to_socket = async {
        loop {
            match read_framed_message(&mut stdin).await {
                Ok(bytes) => {
                    if write_framed_message(&mut socket_writer, &bytes).await.is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    };

    let to_stdout = async {
        loop {
            match read_framed_message(&mut socket_reader).await {
                Ok(bytes) => {
                    if write_framed_message(&mut stdout, &bytes).await.is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    };

    tokio::join!(to_socket, to_stdout);
}

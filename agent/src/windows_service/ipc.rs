use std::sync::{Arc, Mutex, OnceLock};
use tokio::io::AsyncWriteExt;
use tokio::sync::mpsc;
use std::collections::HashMap;

fn get_ipc_connections() -> &'static Arc<Mutex<HashMap<u32, mpsc::UnboundedSender<String>>>> {
    static IPC_CONNECTIONS: OnceLock<Arc<Mutex<HashMap<u32, mpsc::UnboundedSender<String>>>>> = OnceLock::new();
    IPC_CONNECTIONS.get_or_init(|| Arc::new(Mutex::new(HashMap::new())))
}

pub fn register_ipc_client(id: u32, tx: mpsc::UnboundedSender<String>) {
    let mut guard = get_ipc_connections().lock().unwrap();
    guard.insert(id, tx);
}

pub fn unregister_ipc_client(id: u32) {
    let mut guard = get_ipc_connections().lock().unwrap();
    guard.remove(&id);
}

pub fn broadcast_toast_notification(title: &str, message: &str) {
    broadcast_json(&serde_json::json!({
        "type": "toast",
        "title": title,
        "message": message
    }));
}

pub fn broadcast_json(payload: &serde_json::Value) {
    if let Ok(serialized) = serde_json::to_string(payload) {
        let guard = get_ipc_connections().lock().unwrap();
        for tx in guard.values() {
            let _ = tx.send(serialized.clone());
        }
    }
}

// Named Pipe IPC Server Listener
pub async fn start_ipc_server() {
    println!("Starting Windows Named Pipe IPC Server...");
    let mut client_id_counter = 0;
    
    loop {
        #[cfg(target_os = "windows")]
        {
            use tokio::net::windows::named_pipe::ServerOptions;
            let pipe_name = r"\\.\pipe\timekpr_ipc";
            let server = match ServerOptions::new()
                .first_pipe_instance(client_id_counter == 0)
                .create(pipe_name)
            {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("Failed to create Named Pipe server instance: {}", e);
                    tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                    continue;
                }
            };

            // Wait for client connection
            if server.connect().await.is_ok() {
                client_id_counter += 1;
                let id = client_id_counter;
                let (tx, mut rx) = mpsc::unbounded_channel::<String>();
                register_ipc_client(id, tx);
                
                tokio::spawn(async move {
                    let mut pipe = server;
                    while let Some(msg) = rx.recv().await {
                        // Write message to the named pipe
                        if pipe.write_all(msg.as_bytes()).await.is_err() {
                            break;
                        }
                        let _ = pipe.write_all(b"\n").await;
                    }
                    unregister_ipc_client(id);
                });
            }
        }
        #[cfg(not(target_os = "windows"))]
        {
            // No-op for compilation on Linux
            tokio::time::sleep(tokio::time::Duration::from_secs(60)).await;
        }
    }
}

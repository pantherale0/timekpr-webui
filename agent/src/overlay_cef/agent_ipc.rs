//! IPC to the guardian-agent daemon for overlay access requests.

#[cfg(unix)]
mod unix_impl {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    const IPC_SOCKET: &str = "/run/guardian-agent/ipc.sock";

    fn write_framed_message(stream: &mut UnixStream, payload: &[u8]) -> std::io::Result<()> {
        let len = payload.len() as u32;
        stream.write_all(&len.to_ne_bytes())?;
        stream.write_all(payload)?;
        stream.flush()
    }

    pub fn forward_access_request(reason: &str, message: &str) -> Result<(), String> {
        let mut stream =
            UnixStream::connect(IPC_SOCKET).map_err(|e| format!("IPC connect failed: {e}"))?;

        let payload = serde_json::json!({
            "type": "ACCESS_REQUEST",
            "reason": reason,
            "message": message,
        });
        let bytes =
            serde_json::to_vec(&payload).map_err(|e| format!("JSON encode failed: {e}"))?;
        write_framed_message(&mut stream, &bytes).map_err(|e| format!("IPC write failed: {e}"))?;

        let mut len_bytes = [0u8; 4];
        if stream.read_exact(&mut len_bytes).is_ok() {
            let len = u32::from_ne_bytes(len_bytes) as usize;
            if len <= 1024 * 1024 {
                let mut buf = vec![0u8; len];
                let _ = stream.read_exact(&mut buf);
            }
        }

        Ok(())
    }
}

#[cfg(windows)]
mod win_impl {
    use std::io::{Read, Write};
    use std::time::Duration;

    const IPC_PIPE: &str = r"\\.\pipe\guardian-agent-ipc";

    fn write_framed_message(
        stream: &mut std::fs::File,
        payload: &[u8],
    ) -> std::io::Result<()> {
        let len = payload.len() as u32;
        stream.write_all(&len.to_ne_bytes())?;
        stream.write_all(payload)?;
        stream.flush()
    }

    pub fn forward_access_request(reason: &str, message: &str) -> Result<(), String> {
        use std::fs::OpenOptions;

        let mut stream = OpenOptions::new()
            .read(true)
            .write(true)
            .open(IPC_PIPE)
            .map_err(|e| format!("IPC connect failed: {e}"))?;

        let payload = serde_json::json!({
            "type": "ACCESS_REQUEST",
            "reason": reason,
            "message": message,
        });
        let bytes =
            serde_json::to_vec(&payload).map_err(|e| format!("JSON encode failed: {e}"))?;
        write_framed_message(&mut stream, &bytes).map_err(|e| format!("IPC write failed: {e}"))?;

        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .ok();
        let mut len_bytes = [0u8; 4];
        if stream.read_exact(&mut len_bytes).is_ok() {
            let len = u32::from_ne_bytes(len_bytes) as usize;
            if len <= 1024 * 1024 {
                let mut buf = vec![0u8; len];
                let _ = stream.read_exact(&mut buf);
            }
        }

        Ok(())
    }
}

#[cfg(unix)]
pub use unix_impl::forward_access_request;

#[cfg(windows)]
pub use win_impl::forward_access_request;

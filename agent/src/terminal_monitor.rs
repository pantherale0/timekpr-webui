use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::HashMap;
use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::mpsc;
use users::os::unix::UserExt;

use crate::netlink::AppAlert;

const STATE_FILE: &str = "/etc/guardian-agent/terminal_offsets.json";

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
struct UserOffsets {
    bash: u64,
    zsh: u64,
    fish: u64,
}

#[derive(Deserialize, Debug)]
struct TerminalLogPayload {
    tty: String,
    pwd: String,
    cmd: String,
    session_id: String,
    user: String,
}

/// Helper to get current Unix timestamp in seconds
fn current_unix_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Write Bash/Zsh/Fish global profile hooks system-wide
pub fn deploy_shell_hooks() {
    let bash_zsh_hook = r#"# Guardian-WebUI terminal logger hook for Bash and Zsh
if [ -n "$BASH_VERSION" ] || [ -n "$ZSH_VERSION" ]; then
    if [ -z "$GUARDIAN_SESSION_ID" ]; then
        export GUARDIAN_SESSION_ID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "$$_$(date +%s)")
    fi

    log_guardian_cmd() {
        local last_cmd="$1"
        if [ -n "$last_cmd" ] && [[ "$last_cmd" != logger* ]]; then
            logger -t "guardian-terminal" -- "{\"tty\":\"$(tty 2>/dev/null || echo unknown)\",\"pwd\":\"$PWD\",\"cmd\":\"$last_cmd\",\"session_id\":\"$GUARDIAN_SESSION_ID\",\"user\":\"$USER\"}"
        fi
    }

    if [ -n "$BASH_VERSION" ]; then
        guardian_bash_preexec() {
            log_guardian_cmd "$BASH_COMMAND"
        }
        trap 'guardian_bash_preexec' DEBUG
    elif [ -n "$ZSH_VERSION" ]; then
        preexec() {
            log_guardian_cmd "$1"
        }
    fi
fi
"#;

    let fish_hook = r#"# Guardian-WebUI terminal logger hook for Fish
if status is-interactive
    if not set -q GUARDIAN_SESSION_ID
        set -gx GUARDIAN_SESSION_ID (cat /proc/sys/kernel/random/uuid 2>/dev/null; or echo "$fish_pid"_(date +%s))
    end

    function guardian_preexec --on-event fish_preexec
        set -l last_cmd $argv[1]
        if test -n "$last_cmd"; and not string match -r '^logger' "$last_cmd"
            logger -t "guardian-terminal" -- "{\"tty\":\""(tty 2>/dev/null; or echo unknown)"\",\"pwd\":\"$PWD\",\"cmd\":\"$last_cmd\",\"session_id\":\"$GUARDIAN_SESSION_ID\",\"user\":\"$USER\"}"
        end
    end
end
"#;

    let sh_path = "/etc/profile.d/guardian-terminal.sh";
    if let Err(e) = fs::write(sh_path, bash_zsh_hook) {
        eprintln!("terminal_monitor: failed to write shell hook to {}: {}", sh_path, e);
    } else {
        println!("terminal_monitor: wrote shell hook to {}", sh_path);
    }

    let fish_dir = "/etc/fish/conf.d";
    if fs::create_dir_all(fish_dir).is_ok() {
        let fish_path = format!("{}/guardian-terminal.fish", fish_dir);
        if let Err(e) = fs::write(&fish_path, fish_hook) {
            eprintln!("terminal_monitor: failed to write fish hook to {}: {}", fish_path, e);
        } else {
            println!("terminal_monitor: wrote fish hook to {}", fish_path);
        }
    }
}

/// Load the persisted file read offsets
fn load_offsets() -> HashMap<String, UserOffsets> {
    if let Ok(content) = fs::read_to_string(STATE_FILE) {
        if let Ok(offsets) = serde_json::from_str(&content) {
            return offsets;
        }
    }
    HashMap::new()
}

/// Save the file read offsets
fn save_offsets(offsets: &HashMap<String, UserOffsets>) {
    if let Ok(content) = serde_json::to_string_pretty(offsets) {
        let _ = fs::write(STATE_FILE, content);
    }
}

/// Main monitor starter
pub async fn run_terminal_monitor(
    uid_map: HashMap<u32, String>,
    alert_tx: mpsc::UnboundedSender<AppAlert>,
) {
    if uid_map.is_empty() {
        println!("terminal_monitor: no monitored users, terminal monitor idle");
        return;
    }

    println!(
        "terminal_monitor: starting terminal command monitor for users {:?}",
        uid_map.values().collect::<Vec<_>>()
    );

    // Deploy shell hooks (self-healing)
    deploy_shell_hooks();

    // Cache of recent hook commands to avoid duplicate logs from backfills:
    // Key: "username|cmd" -> Value: Instant sent
    let recent_hook_cmds = Arc::new(Mutex::new(HashMap::<String, Instant>::new()));

    // Spawn journalctl tailing task
    let tail_tx = alert_tx.clone();
    let tail_recent = recent_hook_cmds.clone();
    let tail_users = uid_map.values().cloned().collect::<std::collections::HashSet<_>>();
    tokio::spawn(async move {
        if let Err(e) = run_journal_tailer(tail_users, tail_tx, tail_recent).await {
            eprintln!("terminal_monitor: journal tailer error: {}", e);
        }
    });

    // Spawn history polling task
    let poll_tx = alert_tx;
    let poll_recent = recent_hook_cmds;
    tokio::spawn(async move {
        run_history_poller(uid_map, poll_tx, poll_recent).await;
    });
}

/// Tails journalctl -t timekpr-terminal to capture hook executions in real-time
async fn run_journal_tailer(
    monitored_users: std::collections::HashSet<String>,
    alert_tx: mpsc::UnboundedSender<AppAlert>,
    recent_hook_cmds: Arc<Mutex<HashMap<String, Instant>>>,
) -> Result<(), String> {
    let mut child = Command::new("journalctl")
        .args([
            "--follow",
            "--no-pager",
            "-t",
            "guardian-terminal",
            "--output=cat",
            "--since=now",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to spawn journalctl: {}", e))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "failed to capture journalctl stdout".to_string())?;

    let mut reader = BufReader::new(stdout).lines();

    println!("terminal_monitor: tailing journalctl for guardian-terminal logs");

    while let Ok(Some(line)) = reader.next_line().await {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        if let Ok(payload) = serde_json::from_str::<TerminalLogPayload>(trimmed) {
            if !monitored_users.contains(&payload.user) {
                continue;
            }

            // Track in recent commands cache for deduplication
            let cache_key = format!("{}|{}", payload.user, payload.cmd);
            {
                let mut guard = recent_hook_cmds.lock().unwrap();
                guard.insert(cache_key, Instant::now());
                
                // Keep cache small: clean up entries older than 30s
                guard.retain(|_, time| time.elapsed() < Duration::from_secs(30));
            }

            println!(
                "terminal_monitor (hook): user={} tty={} pwd={} cmd={}",
                payload.user, payload.tty, payload.pwd, payload.cmd
            );

            let _ = alert_tx.send(AppAlert {
                event_type: "terminal_command".to_string(),
                linux_username: payload.user.clone(),
                payload: json!({
                    "cmd": payload.cmd,
                    "pwd": payload.pwd,
                    "tty": payload.tty,
                    "session_id": payload.session_id,
                    "source": "hook",
                }),
            });
        }
    }

    Ok(())
}

/// Periodically checks command history files for monitored users
async fn run_history_poller(
    uid_map: HashMap<u32, String>,
    alert_tx: mpsc::UnboundedSender<AppAlert>,
    recent_hook_cmds: Arc<Mutex<HashMap<String, Instant>>>,
) {
    let mut offsets = load_offsets();

    // On startup, initialize offsets to the current size of history files if they aren't tracked yet
    for username in uid_map.values() {
        if let Some(user_meta) = users::get_user_by_name(username) {
            let home = user_meta.home_dir();
            let user_offsets = offsets.entry(username.clone()).or_default();

            initialize_offset(&home.join(".bash_history"), &mut user_offsets.bash);
            initialize_offset(&home.join(".zsh_history"), &mut user_offsets.zsh);
            initialize_offset(
                &home.join(".local/share/fish/fish_history"),
                &mut user_offsets.fish,
            );
        }
    }
    save_offsets(&offsets);

    loop {
        tokio::time::sleep(Duration::from_secs(10)).await;

        let mut changed = false;
        for username in uid_map.values() {
            let user_meta = match users::get_user_by_name(username) {
                Some(u) => u,
                None => continue,
            };
            let home = user_meta.home_dir();
            let user_offsets = offsets.entry(username.clone()).or_default();

            // 1. Bash
            if poll_history_file(
                username,
                &home.join(".bash_history"),
                &mut user_offsets.bash,
                "bash",
                &alert_tx,
                &recent_hook_cmds,
            ) {
                changed = true;
            }

            // 2. Zsh
            if poll_history_file(
                username,
                &home.join(".zsh_history"),
                &mut user_offsets.zsh,
                "zsh",
                &alert_tx,
                &recent_hook_cmds,
            ) {
                changed = true;
            }

            // 3. Fish
            if poll_history_file(
                username,
                &home.join(".local/share/fish/fish_history"),
                &mut user_offsets.fish,
                "fish",
                &alert_tx,
                &recent_hook_cmds,
            ) {
                changed = true;
            }
        }

        if changed {
            save_offsets(&offsets);
        }
    }
}

fn initialize_offset(path: &Path, offset: &mut u64) {
    if *offset == 0 && path.exists() {
        if let Ok(metadata) = fs::metadata(path) {
            *offset = metadata.len();
        }
    }
}

fn poll_history_file(
    username: &str,
    path: &Path,
    offset: &mut u64,
    shell: &str,
    alert_tx: &mpsc::UnboundedSender<AppAlert>,
    recent_hook_cmds: &Arc<Mutex<HashMap<String, Instant>>>,
) -> bool {
    if !path.exists() {
        return false;
    }

    let metadata = match fs::metadata(path) {
        Ok(m) => m,
        Err(_) => return false,
    };

    let len = metadata.len();
    if len <= *offset {
        if len < *offset {
            // History file got truncated or cleared, reset offset
            *offset = len;
            return true;
        }
        return false;
    }

    // Read new lines
    let mut file = match fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return false,
    };

    if file.seek(SeekFrom::Start(*offset)).is_err() {
        return false;
    }

    let bytes_to_read = len - *offset;
    let mut buffer = vec![0; bytes_to_read as usize];
    if file.read_exact(&mut buffer).is_err() {
        return false;
    }

    *offset = len;

    let content = String::from_utf8_lossy(&buffer);
    let entries = match shell {
        "fish" => parse_fish_history(&content),
        "zsh" => parse_zsh_history(&content),
        _ => parse_bash_history(&content),
    };

    let now_ts = current_unix_timestamp();

    for (cmd, ts_opt) in entries {
        let cmd_trimmed = cmd.trim().to_string();
        if cmd_trimmed.is_empty() {
            continue;
        }

        // Deduplication against recent hook events (10 seconds window)
        let cache_key = format!("{}|{}", username, cmd_trimmed);
        {
            let guard = recent_hook_cmds.lock().unwrap();
            if let Some(sent_time) = guard.get(&cache_key) {
                if sent_time.elapsed() < Duration::from_secs(10) {
                    // Already logged in real-time
                    continue;
                }
            }
        }

        println!(
            "terminal_monitor (history poll): user={} shell={} cmd={} ts={:?}",
            username, shell, cmd_trimmed, ts_opt
        );

        let occurred_at = if let Some(ts) = ts_opt {
            // Ensure timestamp is in UTC ISO-8601 format or timestamp conversion
            let d = UNIX_EPOCH + Duration::from_secs(ts);
            chrono::DateTime::<chrono::Utc>::from(d).to_rfc3339()
        } else {
            chrono::DateTime::<chrono::Utc>::from(UNIX_EPOCH + Duration::from_secs(now_ts)).to_rfc3339()
        };

        let _ = alert_tx.send(AppAlert {
            event_type: "terminal_command".to_string(),
            linux_username: username.to_string(),
            payload: json!({
                "cmd": cmd_trimmed,
                "pwd": "", // unknown from history file
                "tty": format!("{}/history", shell),
                "session_id": "backfill",
                "source": "history_poll",
                "_occurred_at": occurred_at,
            }),
        });
    }

    true
}

fn parse_bash_history(content: &str) -> Vec<(String, Option<u64>)> {
    let mut entries = Vec::new();
    let mut last_timestamp = None;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if trimmed.starts_with('#') {
            if let Ok(ts) = trimmed[1..].parse::<u64>() {
                last_timestamp = Some(ts);
                continue;
            }
        }
        entries.push((trimmed.to_string(), last_timestamp.take()));
    }
    entries
}

fn parse_zsh_history(content: &str) -> Vec<(String, Option<u64>)> {
    let mut entries = Vec::new();
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        if trimmed.starts_with(": ") {
            if let Some(semi) = trimmed.find(';') {
                let meta = &trimmed[2..semi];
                let parts: Vec<&str> = meta.split(':').collect();
                if !parts.is_empty() {
                    if let Ok(ts) = parts[0].trim().parse::<u64>() {
                        entries.push((trimmed[semi + 1..].to_string(), Some(ts)));
                        continue;
                    }
                }
                entries.push((trimmed[semi + 1..].to_string(), None));
            } else {
                entries.push((trimmed.to_string(), None));
            }
        } else {
            entries.push((trimmed.to_string(), None));
        }
    }
    entries
}

fn parse_fish_history(content: &str) -> Vec<(String, Option<u64>)> {
    let mut entries = Vec::new();
    let mut current_cmd = None;
    let mut current_when = None;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("- cmd:") {
            if let Some(cmd) = current_cmd.take() {
                entries.push((cmd, current_when.take()));
            }
            current_cmd = Some(trimmed[6..].trim().to_string());
        } else if trimmed.starts_with("cmd:") {
            if let Some(cmd) = current_cmd.take() {
                entries.push((cmd, current_when.take()));
            }
            current_cmd = Some(trimmed[4..].trim().to_string());
        } else if trimmed.starts_with("when:") {
            if let Ok(ts) = trimmed[5..].trim().parse::<u64>() {
                current_when = Some(ts);
            }
        }
    }
    if let Some(cmd) = current_cmd {
        entries.push((cmd, current_when));
    }
    entries
}

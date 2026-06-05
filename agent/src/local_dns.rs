use hickory_proto::op::{Message, ResponseCode};
use std::collections::{HashMap, HashSet};
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream, UdpSocket};
use tokio::sync::{oneshot, RwLock};
use tokio::task::JoinHandle;
use tokio::time::{timeout, Duration};

use crate::domain_notify;

const DNS_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DnsPolicy {
    pub uid: u32,
    pub listen_port: u16,
    pub blocked_domains: Vec<String>,
    pub allowed_domains: Vec<String>,
    pub domain_access_mode: String,
    pub linux_username: String,
}

#[derive(Clone)]
struct DnsQueryContext {
    blocked_domains: Arc<RwLock<HashSet<String>>>,
    allowed_domains: Arc<RwLock<HashSet<String>>>,
    domain_access_mode: Arc<RwLock<String>>,
    linux_username: Arc<RwLock<String>>,
}

struct ListenerHandle {
    blocked_domains: Arc<RwLock<HashSet<String>>>,
    allowed_domains: Arc<RwLock<HashSet<String>>>,
    domain_access_mode: Arc<RwLock<String>>,
    linux_username: Arc<RwLock<String>>,
    shutdown_tx: Option<oneshot::Sender<()>>,
    task: JoinHandle<()>,
}

pub struct LocalDnsController {
    listeners: HashMap<u16, ListenerHandle>,
}

impl LocalDnsController {
    pub fn new() -> Self {
        Self {
            listeners: HashMap::new(),
        }
    }

    pub async fn reconcile(&mut self, policies: &[DnsPolicy]) -> Result<(), String> {
        let desired_ports: HashSet<u16> = policies.iter().map(|policy| policy.listen_port).collect();

        for policy in policies {
            let next_blocked = policy
                .blocked_domains
                .iter()
                .map(|domain| domain.to_ascii_lowercase())
                .collect::<HashSet<_>>();
            let next_allowed = policy
                .allowed_domains
                .iter()
                .map(|domain| domain.to_ascii_lowercase())
                .collect::<HashSet<_>>();
            let next_mode = policy.domain_access_mode.trim().to_string();
            let next_username = policy.linux_username.trim().to_string();

            if let Some(existing) = self.listeners.get_mut(&policy.listen_port) {
                *existing.blocked_domains.write().await = next_blocked;
                *existing.allowed_domains.write().await = next_allowed;
                *existing.domain_access_mode.write().await = next_mode;
                *existing.linux_username.write().await = next_username;
                continue;
            }

            let blocked_domains = Arc::new(RwLock::new(next_blocked));
            let allowed_domains = Arc::new(RwLock::new(next_allowed));
            let domain_access_mode = Arc::new(RwLock::new(next_mode));
            let linux_username = Arc::new(RwLock::new(next_username));
            let (shutdown_tx, shutdown_rx) = oneshot::channel();
            let listener_blocked_domains = blocked_domains.clone();
            let listener_allowed_domains = allowed_domains.clone();
            let listener_domain_access_mode = domain_access_mode.clone();
            let listener_linux_username = linux_username.clone();
            let port = policy.listen_port;
            let task = tokio::spawn(async move {
                if let Err(error) = run_dns_listener(
                    port,
                    listener_blocked_domains,
                    listener_allowed_domains,
                    listener_domain_access_mode,
                    listener_linux_username,
                    shutdown_rx,
                )
                .await
                {
                    eprintln!("Local DNS listener on port {} exited: {}", port, error);
                }
            });

            self.listeners.insert(
                policy.listen_port,
                ListenerHandle {
                    blocked_domains,
                    allowed_domains,
                    domain_access_mode,
                    linux_username,
                    shutdown_tx: Some(shutdown_tx),
                    task,
                },
            );
        }

        let stale_ports: Vec<u16> = self
            .listeners
            .keys()
            .copied()
            .filter(|port| !desired_ports.contains(port))
            .collect();
        for port in stale_ports {
            if let Some(mut handle) = self.listeners.remove(&port) {
                if let Some(shutdown_tx) = handle.shutdown_tx.take() {
                    let _ = shutdown_tx.send(());
                }
                let _ = handle.task.await;
            }
        }

        Ok(())
    }
}

async fn run_dns_listener(
    port: u16,
    blocked_domains: Arc<RwLock<HashSet<String>>>,
    allowed_domains: Arc<RwLock<HashSet<String>>>,
    domain_access_mode: Arc<RwLock<String>>,
    linux_username: Arc<RwLock<String>>,
    mut shutdown_rx: oneshot::Receiver<()>,
) -> Result<(), String> {
    let bind_addr = SocketAddr::new(IpAddr::V4(Ipv4Addr::UNSPECIFIED), port);
    let udp_socket = UdpSocket::bind(bind_addr)
        .await
        .map_err(|error| format!("failed to bind UDP DNS socket on {}: {}", port, error))?;
    let tcp_listener = TcpListener::bind(bind_addr)
        .await
        .map_err(|error| format!("failed to bind TCP DNS socket on {}: {}", port, error))?;
    let upstream_servers = Arc::new(load_upstream_servers());

    let mut udp_buffer = [0u8; 4096];
    loop {
        tokio::select! {
            _ = &mut shutdown_rx => {
                break;
            }
            udp_result = udp_socket.recv_from(&mut udp_buffer) => {
                let (received, peer_addr) = udp_result.map_err(|error| format!("UDP receive failed: {}", error))?;
                let response = handle_udp_query(
                    &udp_buffer[..received],
                    DnsQueryContext {
                        blocked_domains: blocked_domains.clone(),
                        allowed_domains: allowed_domains.clone(),
                        domain_access_mode: domain_access_mode.clone(),
                        linux_username: linux_username.clone(),
                    },
                    upstream_servers.clone(),
                ).await?;
                udp_socket
                    .send_to(&response, peer_addr)
                    .await
                    .map_err(|error| format!("UDP send failed: {}", error))?;
            }
            tcp_result = tcp_listener.accept() => {
                let (stream, _) = tcp_result.map_err(|error| format!("TCP accept failed: {}", error))?;
                let context = DnsQueryContext {
                    blocked_domains: blocked_domains.clone(),
                    allowed_domains: allowed_domains.clone(),
                    domain_access_mode: domain_access_mode.clone(),
                    linux_username: linux_username.clone(),
                };
                let upstream_servers = upstream_servers.clone();
                tokio::spawn(async move {
                    if let Err(error) = handle_tcp_client(stream, context, upstream_servers).await {
                        eprintln!("TCP DNS client failed: {}", error);
                    }
                });
            }
        }
    }

    Ok(())
}

async fn handle_udp_query(
    query_bytes: &[u8],
    context: DnsQueryContext,
    upstream_servers: Arc<Vec<SocketAddr>>,
) -> Result<Vec<u8>, String> {
    if let Some(blocked_domain) =
        resolve_blocked_query(query_bytes, &context).await?
    {
        notify_blocked_domain(&context, &blocked_domain).await;
        return build_blocked_response(query_bytes);
    }

    forward_udp_query(query_bytes, upstream_servers.as_ref()).await
}

async fn handle_tcp_client(
    mut stream: TcpStream,
    context: DnsQueryContext,
    upstream_servers: Arc<Vec<SocketAddr>>,
) -> Result<(), String> {
    let mut length_bytes = [0u8; 2];
    timeout(DNS_TIMEOUT, stream.read_exact(&mut length_bytes))
        .await
        .map_err(|_| "timeout reading TCP DNS length".to_string())?
        .map_err(|error| format!("failed to read TCP DNS length: {}", error))?;
        
    let expected_length = u16::from_be_bytes(length_bytes) as usize;
    let mut query_bytes = vec![0u8; expected_length];
    timeout(DNS_TIMEOUT, stream.read_exact(&mut query_bytes))
        .await
        .map_err(|_| "timeout reading TCP DNS payload".to_string())?
        .map_err(|error| format!("failed to read TCP DNS payload: {}", error))?;

    let response = if let Some(blocked_domain) =
        resolve_blocked_query(&query_bytes, &context).await?
    {
        notify_blocked_domain(&context, &blocked_domain).await;
        build_blocked_response(&query_bytes)?
    } else {
        forward_tcp_query(&query_bytes, upstream_servers.as_ref()).await?
    };

    let response_length = u16::try_from(response.len())
        .map_err(|_| "TCP DNS response exceeds 65535 bytes".to_string())?;
        
    timeout(DNS_TIMEOUT, stream.write_all(&response_length.to_be_bytes()))
        .await
        .map_err(|_| "timeout writing TCP DNS response length".to_string())?
        .map_err(|error| format!("failed to write TCP DNS length: {}", error))?;
        
    timeout(DNS_TIMEOUT, stream.write_all(&response))
        .await
        .map_err(|_| "timeout writing TCP DNS payload".to_string())?
        .map_err(|error| format!("failed to write TCP DNS payload: {}", error))?;
        
    Ok(())
}

async fn resolve_blocked_query(
    query_bytes: &[u8],
    context: &DnsQueryContext,
) -> Result<Option<String>, String> {
    let blocked_domains = context.blocked_domains.read().await;
    let allowed_domains = context.allowed_domains.read().await;
    let query = Message::from_vec(query_bytes)
        .map_err(|error| format!("failed to parse DNS query: {}", error))?;

    for entry in &query.queries {
        let domain = entry.name().to_ascii();
        if !domain_is_blocked(&domain, &blocked_domains) {
            continue;
        }
        if domain_is_allowed(&domain, &allowed_domains) {
            continue;
        }
        return Ok(Some(registrable_domain(&domain)));
    }
    Ok(None)
}

async fn notify_blocked_domain(context: &DnsQueryContext, blocked_domain: &str) {
    let mode = context.domain_access_mode.read().await.clone();
    let username = context.linux_username.read().await.clone();
    if username.is_empty() {
        return;
    }
    domain_notify::on_domain_blocked(&username, blocked_domain, &mode);
}

fn query_matches_blocked_domain(
    query_bytes: &[u8],
    blocked_domains: &HashSet<String>,
) -> Result<bool, String> {
    let query = Message::from_vec(query_bytes)
        .map_err(|error| format!("failed to parse DNS query: {}", error))?;

    Ok(query
        .queries
        .iter()
        .any(|entry| domain_is_blocked(&entry.name().to_ascii(), blocked_domains)))
}

pub fn domain_is_allowed(domain_name: &str, allowed_domains: &HashSet<String>) -> bool {
    domain_is_blocked(domain_name, allowed_domains)
}

pub fn registrable_domain(domain_name: &str) -> String {
    let candidate = domain_name.trim_end_matches('.').to_ascii_lowercase();
    let parts: Vec<&str> = candidate.split('.').filter(|part| !part.is_empty()).collect();
    if parts.len() >= 2 {
        format!("{}.{}", parts[parts.len() - 2], parts[parts.len() - 1])
    } else {
        candidate
    }
}

pub fn domain_is_blocked(domain_name: &str, blocked_domains: &HashSet<String>) -> bool {
    let mut candidate = domain_name.trim_end_matches('.').to_ascii_lowercase();
    loop {
        if blocked_domains.contains(&candidate) {
            return true;
        }
        let Some((_, remainder)) = candidate.split_once('.') else {
            break;
        };
        candidate = remainder.to_string();
    }
    false
}

pub fn build_blocked_response(query_bytes: &[u8]) -> Result<Vec<u8>, String> {
    let query = Message::from_vec(query_bytes)
        .map_err(|error| format!("failed to parse DNS query: {}", error))?;

    let mut response = Message::error_msg(
        query.metadata.id,
        query.metadata.op_code,
        ResponseCode::NXDomain,
    );
    response.metadata.recursion_desired = query.metadata.recursion_desired;
    response.metadata.recursion_available = true;
    response.metadata.checking_disabled = query.metadata.checking_disabled;
    response.queries = query.queries.clone();

    response
        .to_vec()
        .map_err(|error| format!("failed to serialize blocked response: {}", error))
}

async fn forward_udp_query(query_bytes: &[u8], upstream_servers: &[SocketAddr]) -> Result<Vec<u8>, String> {
    let socket = UdpSocket::bind("0.0.0.0:0")
        .await
        .map_err(|error| format!("failed to bind upstream UDP socket: {}", error))?;
    let mut buffer = [0u8; 4096];

    for upstream in upstream_servers {
        socket
            .send_to(query_bytes, upstream)
            .await
            .map_err(|error| format!("failed to send UDP DNS query: {}", error))?;
        match timeout(DNS_TIMEOUT, socket.recv_from(&mut buffer)).await {
            Ok(Ok((received, _))) => return Ok(buffer[..received].to_vec()),
            Ok(Err(error)) => return Err(format!("failed to receive UDP DNS response: {}", error)),
            Err(_) => continue,
        }
    }

    Err("all upstream UDP resolvers timed out".to_string())
}

async fn forward_tcp_query(query_bytes: &[u8], upstream_servers: &[SocketAddr]) -> Result<Vec<u8>, String> {
    for upstream in upstream_servers {
        let mut stream = match timeout(DNS_TIMEOUT, TcpStream::connect(upstream)).await {
            Ok(Ok(stream)) => stream,
            _ => continue,
        };

        let query_length = u16::try_from(query_bytes.len())
            .map_err(|_| "TCP DNS query exceeds 65535 bytes".to_string())?;
            
        let write_res = timeout(DNS_TIMEOUT, async {
            stream.write_all(&query_length.to_be_bytes()).await?;
            stream.write_all(query_bytes).await?;
            Ok::<(), std::io::Error>(())
        }).await;
        
        if write_res.is_err() || write_res.unwrap().is_err() {
            continue;
        }

        let mut response_length = [0u8; 2];
        let read_len_res = timeout(DNS_TIMEOUT, stream.read_exact(&mut response_length)).await;
        if read_len_res.is_err() || read_len_res.unwrap().is_err() {
            continue;
        }
        
        let expected_length = u16::from_be_bytes(response_length) as usize;
        let mut response = vec![0u8; expected_length];
        let read_payload_res = timeout(DNS_TIMEOUT, stream.read_exact(&mut response)).await;
        if read_payload_res.is_err() || read_payload_res.unwrap().is_err() {
            continue;
        }
        
        return Ok(response);
    }

    Err("all upstream TCP resolvers timed out or failed".to_string())
}

fn load_upstream_servers() -> Vec<SocketAddr> {
    let mut upstreams = Vec::new();
    if let Ok(resolv_conf) = std::fs::read_to_string("/etc/resolv.conf") {
        for line in resolv_conf.lines() {
            let trimmed = line.trim();
            if !trimmed.starts_with("nameserver ") {
                continue;
            }
            let Some(address) = trimmed.split_whitespace().nth(1) else {
                continue;
            };
            let Ok(ip_addr) = address.parse::<IpAddr>() else {
                continue;
            };
            if ip_addr.is_loopback() {
                continue;
            }
            upstreams.push(SocketAddr::new(ip_addr, 53));
        }
    }

    if upstreams.is_empty() {
        upstreams.push(SocketAddr::new(IpAddr::V4(Ipv4Addr::new(1, 1, 1, 1)), 53));
    }
    upstreams
}

#[cfg(test)]
mod tests {
    use super::{build_blocked_response, domain_is_allowed, domain_is_blocked, registrable_domain};
    use hickory_proto::op::{Message, MessageType, OpCode, ResponseCode};
    use hickory_proto::rr::{Name, RecordType};
    use std::collections::HashSet;

    fn build_query(name: &str) -> Vec<u8> {
        let mut query = Message::new(7, MessageType::Query, OpCode::Query);
        query.add_query(hickory_proto::op::Query::query(
            Name::from_ascii(name).unwrap(),
            RecordType::A,
        ));
        query.to_vec().unwrap()
    }

    #[test]
    fn blocked_domains_match_subdomains() {
        let blocked = HashSet::from([
            "example.com".to_string(),
            "dns.google".to_string(),
        ]);

        assert!(domain_is_blocked("example.com.", &blocked));
        assert!(domain_is_blocked("api.example.com.", &blocked));
        assert!(!domain_is_blocked("example.net.", &blocked));
    }

    #[test]
    fn allowed_domains_bypass_blocklist_entries() {
        let blocked = HashSet::from(["example.com".to_string()]);
        let allowed = HashSet::from(["example.com".to_string()]);
        assert!(domain_is_blocked("api.example.com.", &blocked));
        assert!(domain_is_allowed("api.example.com.", &allowed));
    }

    #[test]
    fn registrable_domain_uses_last_two_labels() {
        assert_eq!(registrable_domain("api.example.com."), "example.com");
    }

    #[test]
    fn blocked_response_returns_nxdomain() {
        let query = build_query("example.com.");
        let response = build_blocked_response(&query).unwrap();
        let parsed = Message::from_vec(&response).unwrap();

        assert_eq!(parsed.metadata.id, 7);
        assert_eq!(parsed.metadata.message_type, MessageType::Response);
        assert_eq!(parsed.metadata.response_code, ResponseCode::NXDomain);
        assert_eq!(parsed.queries.len(), 1);
    }
}

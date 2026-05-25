use crate::firewall::{self, FirewallPolicy};
use crate::local_dns::{DnsPolicy, LocalDnsController};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};
use tokio::sync::Mutex;

const POLICY_PORT_START: u16 = 23000;
const POLICY_PORT_END: u16 = 32000;

static DOMAIN_POLICY_RUNTIME: OnceLock<Arc<Mutex<DomainPolicyRuntime>>> = OnceLock::new();

#[derive(Deserialize, Serialize, Clone, Debug, Default, PartialEq, Eq)]
pub struct DeviceDomainPolicyPayload {
    pub sources: HashMap<String, Vec<String>>,
    pub policies: HashMap<String, IncomingUidPolicy>,
}

#[derive(Deserialize, Serialize, Clone, Debug, Default, PartialEq, Eq)]
pub struct IncomingUidPolicy {
    pub linux_username: String,
    pub source_ids: Vec<String>,
}

#[derive(Deserialize, Serialize, Clone, Debug, Default, PartialEq, Eq)]
struct PersistedDomainPolicyState {
    sources: HashMap<String, Vec<String>>,
    policies: HashMap<String, PersistedUidPolicy>,
}

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq)]
struct PersistedUidPolicy {
    linux_username: String,
    source_ids: Vec<String>,
    listen_port: u16,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ResolvedUidPolicy {
    uid: u32,
    linux_username: String,
    source_ids: Vec<String>,
    listen_port: u16,
    blocked_domains: Vec<String>,
}

pub async fn initialize_runtime() -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await
}

pub async fn sync_from_args(args: &serde_json::Value) -> Result<String, String> {
    let payload: DeviceDomainPolicyPayload = serde_json::from_value(args.clone())
        .map_err(|error| format!("invalid domain policy payload: {}", error))?;

    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    guard.sync(payload).await
}

fn get_runtime() -> Arc<Mutex<DomainPolicyRuntime>> {
    DOMAIN_POLICY_RUNTIME
        .get_or_init(|| Arc::new(Mutex::new(DomainPolicyRuntime::new())))
        .clone()
}

struct DomainPolicyRuntime {
    dns_controller: LocalDnsController,
    restored: bool,
    state_path: PathBuf,
    current_state: PersistedDomainPolicyState,
}

impl DomainPolicyRuntime {
    fn new() -> Self {
        Self {
            dns_controller: LocalDnsController::new(),
            restored: false,
            state_path: policy_state_path(),
            current_state: PersistedDomainPolicyState::default(),
        }
    }

    async fn ensure_restored(&mut self) -> Result<(), String> {
        if self.restored {
            return Ok(());
        }
        self.restored = true;

        if self.state_path.exists() {
            let restored = load_state(&self.state_path)?;
            self.current_state = restored.clone();
            self.apply_state(&restored).await?;
        } else {
            self.apply_state(&PersistedDomainPolicyState::default()).await?;
        }

        Ok(())
    }

    async fn sync(&mut self, payload: DeviceDomainPolicyPayload) -> Result<String, String> {
        let next_state = resolve_next_state(&self.current_state, payload)?;
        self.apply_state(&next_state).await?;
        self.current_state = next_state.clone();
        save_state(&self.state_path, &next_state)?;
        Ok(format!(
            "Applied domain policy for {} UID(s)",
            self.current_state.policies.len()
        ))
    }

    async fn apply_state(&mut self, state: &PersistedDomainPolicyState) -> Result<(), String> {
        let resolved_policies = resolve_uid_policies(state);
        let dns_policies: Vec<DnsPolicy> = resolved_policies
            .iter()
            .map(|policy| DnsPolicy {
                uid: policy.uid,
                listen_port: policy.listen_port,
                blocked_domains: policy.blocked_domains.clone(),
            })
            .collect();
        let firewall_policies: Vec<FirewallPolicy> = resolved_policies
            .iter()
            .map(|policy| FirewallPolicy {
                uid: policy.uid,
                listen_port: policy.listen_port,
            })
            .collect();

        self.dns_controller.reconcile(&dns_policies).await?;
        firewall::reconcile(&firewall_policies)?;
        Ok(())
    }
}

fn policy_state_path() -> PathBuf {
    let primary_dir = PathBuf::from("/var/lib/timekpr-agent");
    if fs::create_dir_all(&primary_dir).is_ok() {
        return primary_dir.join("domain-policy.json");
    }

    let fallback_dir = PathBuf::from("/etc/timekpr-agent");
    let _ = fs::create_dir_all(&fallback_dir);
    fallback_dir.join("domain-policy.json")
}

fn load_state(path: &PathBuf) -> Result<PersistedDomainPolicyState, String> {
    let raw = fs::read_to_string(path)
        .map_err(|error| format!("failed to read domain policy state: {}", error))?;
    serde_json::from_str(&raw)
        .map_err(|error| format!("failed to parse domain policy state: {}", error))
}

fn save_state(path: &PathBuf, state: &PersistedDomainPolicyState) -> Result<(), String> {
    let serialized = serde_json::to_string_pretty(state)
        .map_err(|error| format!("failed to serialize domain policy state: {}", error))?;
    let temp_path = path.with_extension("json.tmp");
    fs::write(&temp_path, serialized)
        .map_err(|error| format!("failed to write domain policy state: {}", error))?;
    fs::rename(&temp_path, path)
        .map_err(|error| format!("failed to finalize domain policy state: {}", error))?;
    Ok(())
}

fn resolve_next_state(
    current_state: &PersistedDomainPolicyState,
    payload: DeviceDomainPolicyPayload,
) -> Result<PersistedDomainPolicyState, String> {
    let normalized_sources = payload
        .sources
        .into_iter()
        .map(|(source_id, domains)| (source_id, normalize_domains(domains)))
        .collect::<HashMap<_, _>>();
    let mut used_ports = current_state
        .policies
        .values()
        .map(|policy| policy.listen_port)
        .collect::<HashSet<_>>();
    let mut next_policies = HashMap::new();

    let mut ordered_uids = payload.policies.into_iter().collect::<Vec<_>>();
    ordered_uids.sort_by(|left, right| left.0.cmp(&right.0));

    for (uid_text, policy) in ordered_uids {
        let uid = uid_text
            .parse::<u32>()
            .map_err(|_| format!("invalid uid in domain policy payload: {}", uid_text))?;
        let source_ids = normalize_source_ids(policy.source_ids);

        let listen_port = if let Some(existing) = current_state.policies.get(&uid.to_string()) {
            existing.listen_port
        } else {
            allocate_policy_port(&used_ports)?
        };
        used_ports.insert(listen_port);

        next_policies.insert(
            uid.to_string(),
            PersistedUidPolicy {
                linux_username: policy.linux_username.trim().to_string(),
                source_ids,
                listen_port,
            },
        );
    }

    Ok(PersistedDomainPolicyState {
        sources: normalized_sources,
        policies: next_policies,
    })
}

fn allocate_policy_port(used_ports: &HashSet<u16>) -> Result<u16, String> {
    for port in POLICY_PORT_START..=POLICY_PORT_END {
        if !used_ports.contains(&port) {
            return Ok(port);
        }
    }
    Err("no free local DNS ports available for domain policies".to_string())
}

fn resolve_uid_policies(state: &PersistedDomainPolicyState) -> Vec<ResolvedUidPolicy> {
    let mut resolved = Vec::new();
    let mut ordered_uids = state.policies.iter().collect::<Vec<_>>();
    ordered_uids.sort_by(|left, right| left.0.cmp(right.0));

    for (uid_text, policy) in ordered_uids {
        let Ok(uid) = uid_text.parse::<u32>() else {
            continue;
        };

        let mut blocked_domains = HashSet::new();
        for source_id in &policy.source_ids {
            if let Some(domains) = state.sources.get(source_id) {
                blocked_domains.extend(domains.iter().cloned());
            }
        }

        let mut blocked_domains = blocked_domains.into_iter().collect::<Vec<_>>();
        blocked_domains.sort();
        resolved.push(ResolvedUidPolicy {
            uid,
            linux_username: policy.linux_username.clone(),
            source_ids: policy.source_ids.clone(),
            listen_port: policy.listen_port,
            blocked_domains,
        });
    }

    resolved
}

fn normalize_source_ids(source_ids: Vec<String>) -> Vec<String> {
    let mut ids = source_ids
        .into_iter()
        .map(|source_id| source_id.trim().to_string())
        .filter(|source_id| !source_id.is_empty())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    ids.sort();
    ids
}

fn normalize_domains(domains: Vec<String>) -> Vec<String> {
    let mut normalized = domains
        .into_iter()
        .map(|domain| domain.trim().trim_end_matches('.').to_ascii_lowercase())
        .filter(|domain| !domain.is_empty())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    normalized.sort();
    normalized
}

#[cfg(test)]
mod tests {
    use super::{
        resolve_next_state, resolve_uid_policies, DeviceDomainPolicyPayload, IncomingUidPolicy,
        PersistedDomainPolicyState, PersistedUidPolicy,
    };
    use std::collections::HashMap;

    #[test]
    fn next_state_reuses_existing_ports() {
        let current_state = PersistedDomainPolicyState {
            sources: HashMap::new(),
            policies: HashMap::from([(
                "1000".to_string(),
                PersistedUidPolicy {
                    linux_username: "alice".to_string(),
                    source_ids: vec!["1".to_string()],
                    listen_port: 23010,
                },
            )]),
        };

        let payload = DeviceDomainPolicyPayload {
            sources: HashMap::from([("1".to_string(), vec!["example.com".to_string()])]),
            policies: HashMap::from([(
                "1000".to_string(),
                IncomingUidPolicy {
                    linux_username: "alice".to_string(),
                    source_ids: vec!["1".to_string()],
                },
            )]),
        };

        let next_state = resolve_next_state(&current_state, payload).unwrap();
        assert_eq!(next_state.policies["1000"].listen_port, 23010);
    }

    #[test]
    fn next_state_allocates_ports_for_new_users() {
        let payload = DeviceDomainPolicyPayload {
            sources: HashMap::from([("1".to_string(), vec!["example.com".to_string()])]),
            policies: HashMap::from([(
                "1001".to_string(),
                IncomingUidPolicy {
                    linux_username: "bob".to_string(),
                    source_ids: vec!["1".to_string()],
                },
            )]),
        };

        let next_state = resolve_next_state(&PersistedDomainPolicyState::default(), payload).unwrap();
        assert!(next_state.policies["1001"].listen_port >= 23000);
    }

    #[test]
    fn resolved_policies_merge_domains_across_sources() {
        let state = PersistedDomainPolicyState {
            sources: HashMap::from([
                ("1".to_string(), vec!["example.com".to_string(), "api.example.com".to_string()]),
                ("2".to_string(), vec!["dns.google".to_string()]),
            ]),
            policies: HashMap::from([(
                "1002".to_string(),
                PersistedUidPolicy {
                    linux_username: "charlie".to_string(),
                    source_ids: vec!["1".to_string(), "2".to_string()],
                    listen_port: 23012,
                },
            )]),
        };

        let resolved = resolve_uid_policies(&state);
        assert_eq!(resolved.len(), 1);
        assert_eq!(resolved[0].uid, 1002);
        assert!(resolved[0].blocked_domains.contains(&"dns.google".to_string()));
        assert!(resolved[0].blocked_domains.contains(&"example.com".to_string()));
    }
}

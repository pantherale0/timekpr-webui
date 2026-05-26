use crate::firewall::{self, FirewallPolicy};
use crate::local_dns::{DnsPolicy, LocalDnsController};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
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
struct PersistedSourceState {
    #[serde(default)]
    revision: String,
    #[serde(default)]
    domains: Vec<String>,
}

#[derive(Deserialize, Serialize, Clone, Debug, Default, PartialEq, Eq)]
struct PersistedDomainPolicyState {
    sources: HashMap<String, PersistedSourceState>,
    policies: HashMap<String, PersistedUidPolicy>,
}

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq)]
struct PersistedUidPolicy {
    linux_username: String,
    source_ids: Vec<String>,
    listen_port: u16,
}

#[derive(Serialize, Clone, Debug, Default, PartialEq, Eq)]
struct DomainPolicyStateSummary {
    source_revisions: HashMap<String, String>,
}

#[derive(Deserialize)]
struct SyncRequest {
    sync_id: String,
}

#[derive(Deserialize)]
struct DeleteSourcesRequest {
    sync_id: String,
    #[serde(default)]
    source_ids: Vec<String>,
}

#[derive(Deserialize)]
struct SourceChunkRequest {
    sync_id: String,
    source_id: String,
    revision: String,
    #[serde(default)]
    domains: Vec<String>,
}

#[derive(Deserialize)]
struct ManifestRequest {
    sync_id: String,
    #[serde(default)]
    policies: HashMap<String, IncomingUidPolicy>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ResolvedUidPolicy {
    uid: u32,
    linux_username: String,
    source_ids: Vec<String>,
    listen_port: u16,
    blocked_domains: Vec<String>,
}

#[derive(Default)]
struct PendingDomainPolicySync {
    sync_id: String,
    deleted_source_ids: HashSet<String>,
    source_updates: HashMap<String, PendingSourceUpdate>,
    policies: Option<HashMap<String, IncomingUidPolicy>>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct PendingSourceUpdate {
    revision: String,
    domains: Vec<String>,
}

pub async fn initialize_runtime() -> Result<(), String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await
}

pub async fn get_state_summary() -> Result<serde_json::Value, String> {
    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    serde_json::to_value(guard.state_summary())
        .map_err(|error| format!("failed to serialize domain policy state summary: {}", error))
}

pub async fn begin_sync_from_args(args: &serde_json::Value) -> Result<String, String> {
    let request: SyncRequest = serde_json::from_value(args.clone())
        .map_err(|error| format!("invalid domain policy sync request: {}", error))?;

    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    guard.begin_sync(request.sync_id)
}

pub async fn delete_sources_from_args(args: &serde_json::Value) -> Result<String, String> {
    let request: DeleteSourcesRequest = serde_json::from_value(args.clone())
        .map_err(|error| format!("invalid source delete request: {}", error))?;

    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    guard.delete_sources(&request.sync_id, request.source_ids)
}

pub async fn push_source_chunk_from_args(args: &serde_json::Value) -> Result<String, String> {
    let request: SourceChunkRequest = serde_json::from_value(args.clone())
        .map_err(|error| format!("invalid source chunk request: {}", error))?;

    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    guard.push_source_chunk(
        &request.sync_id,
        request.source_id,
        request.revision,
        request.domains,
    )
}

pub async fn update_manifest_from_args(args: &serde_json::Value) -> Result<String, String> {
    let request: ManifestRequest = serde_json::from_value(args.clone())
        .map_err(|error| format!("invalid policy manifest request: {}", error))?;

    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    guard.set_manifest(&request.sync_id, request.policies)
}

pub async fn finalize_sync_from_args(args: &serde_json::Value) -> Result<String, String> {
    let request: SyncRequest = serde_json::from_value(args.clone())
        .map_err(|error| format!("invalid domain policy sync request: {}", error))?;

    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    guard.finalize_sync(&request.sync_id).await
}

pub async fn abort_sync_from_args(args: &serde_json::Value) -> Result<String, String> {
    let request: SyncRequest = serde_json::from_value(args.clone())
        .map_err(|error| format!("invalid domain policy sync request: {}", error))?;

    let runtime = get_runtime();
    let mut guard = runtime.lock().await;
    guard.ensure_restored().await?;
    guard.abort_sync(&request.sync_id)
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
    pending_sync: Option<PendingDomainPolicySync>,
}

impl DomainPolicyRuntime {
    fn new() -> Self {
        Self {
            dns_controller: LocalDnsController::new(),
            restored: false,
            state_path: policy_state_path(),
            current_state: PersistedDomainPolicyState::default(),
            pending_sync: None,
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

    fn state_summary(&self) -> DomainPolicyStateSummary {
        DomainPolicyStateSummary {
            source_revisions: self
                .current_state
                .sources
                .iter()
                .map(|(source_id, source_state)| {
                    (source_id.clone(), source_state.revision.clone())
                })
                .collect(),
        }
    }

    fn begin_sync(&mut self, sync_id: String) -> Result<String, String> {
        let normalized_sync_id = normalize_sync_id(&sync_id)?;
        self.pending_sync = Some(PendingDomainPolicySync::new(normalized_sync_id.clone()));
        Ok(format!("Started domain policy sync {}", normalized_sync_id))
    }

    fn delete_sources(&mut self, sync_id: &str, source_ids: Vec<String>) -> Result<String, String> {
        let normalized_source_ids = normalize_source_ids(source_ids);
        let pending = self.pending_sync_mut(sync_id)?;
        for source_id in &normalized_source_ids {
            pending.deleted_source_ids.insert(source_id.clone());
            pending.source_updates.remove(source_id);
        }
        Ok(format!(
            "Queued {} source deletion(s)",
            normalized_source_ids.len()
        ))
    }

    fn push_source_chunk(
        &mut self,
        sync_id: &str,
        source_id: String,
        revision: String,
        domains: Vec<String>,
    ) -> Result<String, String> {
        let normalized_source_id = normalize_source_id(&source_id)?;
        let normalized_revision = revision.trim().to_string();
        if normalized_revision.is_empty() {
            return Err("domain policy chunk revision is required".to_string());
        }

        let normalized_domains = normalize_domains(domains);
        let pending = self.pending_sync_mut(sync_id)?;
        pending.deleted_source_ids.remove(&normalized_source_id);
        let entry = pending
            .source_updates
            .entry(normalized_source_id.clone())
            .or_insert_with(|| PendingSourceUpdate {
                revision: normalized_revision.clone(),
                domains: Vec::new(),
            });

        if entry.revision != normalized_revision {
            if entry.domains.is_empty() {
                entry.revision = normalized_revision.clone();
            } else {
                return Err(format!(
                    "conflicting revisions for source {} in sync {}",
                    normalized_source_id, sync_id
                ));
            }
        }

        let chunk_len = normalized_domains.len();
        entry.domains.extend(normalized_domains);
        Ok(format!(
            "Queued {} domain(s) for source {}",
            chunk_len, normalized_source_id
        ))
    }

    fn set_manifest(
        &mut self,
        sync_id: &str,
        policies: HashMap<String, IncomingUidPolicy>,
    ) -> Result<String, String> {
        let pending = self.pending_sync_mut(sync_id)?;
        pending.policies = Some(policies);
        Ok(format!(
            "Queued domain policy manifest with {} UID(s)",
            pending.policies.as_ref().map_or(0, |payload| payload.len())
        ))
    }

    fn abort_sync(&mut self, sync_id: &str) -> Result<String, String> {
        let normalized_sync_id = normalize_sync_id(sync_id)?;
        match self.pending_sync.as_ref() {
            Some(pending) if pending.sync_id == normalized_sync_id => {
                self.pending_sync = None;
                Ok(format!("Aborted domain policy sync {}", normalized_sync_id))
            }
            Some(pending) => Err(format!(
                "active domain policy sync {} does not match {}",
                pending.sync_id, normalized_sync_id
            )),
            None => Ok("No pending domain policy sync to abort".to_string()),
        }
    }

    async fn finalize_sync(&mut self, sync_id: &str) -> Result<String, String> {
        let normalized_sync_id = normalize_sync_id(sync_id)?;
        if self
            .pending_sync
            .as_ref()
            .map(|pending| pending.sync_id.as_str())
            != Some(normalized_sync_id.as_str())
        {
            return Err(format!("no pending domain policy sync {}", normalized_sync_id));
        }

        let pending = self.pending_sync.take().unwrap_or_default();
        let next_state = build_next_state_from_pending(&self.current_state, pending)?;
        self.apply_state(&next_state).await?;
        self.current_state = next_state.clone();
        save_state(&self.state_path, &next_state)?;
        Ok(format!(
            "Applied domain policy for {} UID(s)",
            self.current_state.policies.len()
        ))
    }

    async fn sync(&mut self, payload: DeviceDomainPolicyPayload) -> Result<String, String> {
        let next_state = resolve_full_sync_state(&self.current_state, payload)?;
        self.apply_state(&next_state).await?;
        self.current_state = next_state.clone();
        self.pending_sync = None;
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

    fn pending_sync_mut(&mut self, sync_id: &str) -> Result<&mut PendingDomainPolicySync, String> {
        let normalized_sync_id = normalize_sync_id(sync_id)?;
        match self.pending_sync.as_mut() {
            Some(pending) if pending.sync_id == normalized_sync_id => Ok(pending),
            Some(pending) => Err(format!(
                "active domain policy sync {} does not match {}",
                pending.sync_id, normalized_sync_id
            )),
            None => Err(format!("no pending domain policy sync {}", normalized_sync_id)),
        }
    }
}

impl PendingDomainPolicySync {
    fn new(sync_id: String) -> Self {
        Self {
            sync_id,
            deleted_source_ids: HashSet::new(),
            source_updates: HashMap::new(),
            policies: None,
        }
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
    let parsed: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|error| format!("failed to parse domain policy state: {}", error))?;
    parse_state_value(parsed)
}

fn parse_state_value(value: serde_json::Value) -> Result<PersistedDomainPolicyState, String> {
    let Some(object) = value.as_object() else {
        return Err("failed to parse domain policy state: root value must be an object".to_string());
    };

    let sources_value = object
        .get("sources")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let policies_value = object
        .get("policies")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));

    let source_object = sources_value
        .as_object()
        .ok_or_else(|| "failed to parse domain policy state: sources must be an object".to_string())?;

    let mut sources = HashMap::new();
    for (source_id, source_value) in source_object {
        let normalized_source_id = normalize_source_id(source_id)?;
        let source_state = if source_value.is_array() {
            let domains: Vec<String> = serde_json::from_value(source_value.clone())
                .map_err(|error| format!("failed to parse legacy source domains: {}", error))?;
            let normalized_domains = normalize_domains(domains);
            PersistedSourceState {
                revision: compute_source_revision_from_domains(&normalized_domains),
                domains: normalized_domains,
            }
        } else {
            let mut parsed_source: PersistedSourceState = serde_json::from_value(source_value.clone())
                .map_err(|error| format!("failed to parse source state: {}", error))?;
            parsed_source.domains = normalize_domains(parsed_source.domains);
            parsed_source.revision = if parsed_source.revision.trim().is_empty() {
                compute_source_revision_from_domains(&parsed_source.domains)
            } else {
                parsed_source.revision.trim().to_string()
            };
            parsed_source
        };
        sources.insert(normalized_source_id, source_state);
    }

    let policies: HashMap<String, PersistedUidPolicy> = serde_json::from_value(policies_value)
        .map_err(|error| format!("failed to parse domain policy state policies: {}", error))?;

    Ok(PersistedDomainPolicyState { sources, policies })
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

fn resolve_full_sync_state(
    current_state: &PersistedDomainPolicyState,
    payload: DeviceDomainPolicyPayload,
) -> Result<PersistedDomainPolicyState, String> {
    let mut normalized_sources = HashMap::new();
    for (source_id, domains) in payload.sources {
        let normalized_source_id = normalize_source_id(&source_id)?;
        let normalized_domains = normalize_domains(domains);
        normalized_sources.insert(
            normalized_source_id,
            PersistedSourceState {
                revision: compute_source_revision_from_domains(&normalized_domains),
                domains: normalized_domains,
            },
        );
    }

    Ok(PersistedDomainPolicyState {
        sources: normalized_sources,
        policies: resolve_next_policies(current_state, payload.policies)?,
    })
}

fn build_next_state_from_pending(
    current_state: &PersistedDomainPolicyState,
    pending: PendingDomainPolicySync,
) -> Result<PersistedDomainPolicyState, String> {
    let policies = pending
        .policies
        .ok_or_else(|| "domain policy manifest is required before finalize".to_string())?;

    let mut next_sources = current_state.sources.clone();
    for source_id in pending.deleted_source_ids {
        next_sources.remove(&source_id);
    }

    for (source_id, source_update) in pending.source_updates {
        let normalized_domains = normalize_domains(source_update.domains);
        let revision = if source_update.revision.trim().is_empty() {
            compute_source_revision_from_domains(&normalized_domains)
        } else {
            source_update.revision.trim().to_string()
        };
        next_sources.insert(
            source_id,
            PersistedSourceState {
                revision,
                domains: normalized_domains,
            },
        );
    }

    Ok(PersistedDomainPolicyState {
        sources: next_sources,
        policies: resolve_next_policies(current_state, policies)?,
    })
}

fn resolve_next_policies(
    current_state: &PersistedDomainPolicyState,
    policies: HashMap<String, IncomingUidPolicy>,
) -> Result<HashMap<String, PersistedUidPolicy>, String> {
    let mut used_ports = current_state
        .policies
        .values()
        .map(|policy| policy.listen_port)
        .collect::<HashSet<_>>();
    let mut next_policies = HashMap::new();

    let mut ordered_uids = policies.into_iter().collect::<Vec<_>>();
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

    Ok(next_policies)
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
            if let Some(source_state) = state.sources.get(source_id) {
                blocked_domains.extend(source_state.domains.iter().cloned());
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

fn normalize_sync_id(sync_id: &str) -> Result<String, String> {
    let normalized = sync_id.trim().to_string();
    if normalized.is_empty() {
        return Err("sync_id is required".to_string());
    }
    Ok(normalized)
}

fn normalize_source_id(source_id: &str) -> Result<String, String> {
    let normalized = source_id.trim().to_string();
    if normalized.is_empty() {
        return Err("source_id is required".to_string());
    }
    Ok(normalized)
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

fn compute_source_revision_from_domains(domains: &[String]) -> String {
    let mut digest = Sha256::new();
    for domain in domains {
        digest.update(domain.as_bytes());
        digest.update(b"\n");
    }
    format!("{:x}", digest.finalize())
}

#[cfg(test)]
mod tests {
    use super::{
        build_next_state_from_pending, parse_state_value, resolve_full_sync_state,
        resolve_uid_policies, DeviceDomainPolicyPayload, IncomingUidPolicy,
        PendingDomainPolicySync, PendingSourceUpdate, PersistedDomainPolicyState,
        PersistedSourceState, PersistedUidPolicy,
    };
    use std::collections::{HashMap, HashSet};

    #[test]
    fn full_sync_reuses_existing_ports() {
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

        let next_state = resolve_full_sync_state(&current_state, payload).unwrap();
        assert_eq!(next_state.policies["1000"].listen_port, 23010);
        assert_eq!(next_state.sources["1"].domains, vec!["example.com".to_string()]);
    }

    #[test]
    fn incremental_finalize_applies_deletes_and_updates() {
        let current_state = PersistedDomainPolicyState {
            sources: HashMap::from([
                (
                    "1".to_string(),
                    PersistedSourceState {
                        revision: "rev-1".to_string(),
                        domains: vec!["example.com".to_string()],
                    },
                ),
                (
                    "2".to_string(),
                    PersistedSourceState {
                        revision: "rev-2".to_string(),
                        domains: vec!["dns.google".to_string()],
                    },
                ),
            ]),
            policies: HashMap::from([(
                "1000".to_string(),
                PersistedUidPolicy {
                    linux_username: "alice".to_string(),
                    source_ids: vec!["1".to_string()],
                    listen_port: 23010,
                },
            )]),
        };

        let pending = PendingDomainPolicySync {
            sync_id: "sync-1".to_string(),
            deleted_source_ids: HashSet::from(["2".to_string()]),
            source_updates: HashMap::from([(
                "1".to_string(),
                PendingSourceUpdate {
                    revision: "rev-1b".to_string(),
                    domains: vec![
                        "example.com".to_string(),
                        "api.example.com".to_string(),
                    ],
                },
            )]),
            policies: Some(HashMap::from([(
                "1000".to_string(),
                IncomingUidPolicy {
                    linux_username: "alice".to_string(),
                    source_ids: vec!["1".to_string()],
                },
            )])),
        };

        let next_state = build_next_state_from_pending(&current_state, pending).unwrap();
        assert!(!next_state.sources.contains_key("2"));
        assert_eq!(next_state.sources["1"].revision, "rev-1b");
        assert!(next_state.sources["1"]
            .domains
            .contains(&"api.example.com".to_string()));
        assert_eq!(next_state.policies["1000"].listen_port, 23010);
    }

    #[test]
    fn resolved_policies_merge_domains_across_cached_sources() {
        let state = PersistedDomainPolicyState {
            sources: HashMap::from([
                (
                    "1".to_string(),
                    PersistedSourceState {
                        revision: "rev-1".to_string(),
                        domains: vec!["api.example.com".to_string(), "example.com".to_string()],
                    },
                ),
                (
                    "2".to_string(),
                    PersistedSourceState {
                        revision: "rev-2".to_string(),
                        domains: vec!["dns.google".to_string()],
                    },
                ),
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

    #[test]
    fn load_state_upgrades_legacy_source_arrays() {
        let value = serde_json::json!({
            "sources": {
                "1": ["example.com", "api.example.com"]
            },
            "policies": {
                "1001": {
                    "linux_username": "alice",
                    "source_ids": ["1"],
                    "listen_port": 23011
                }
            }
        });

        let parsed = parse_state_value(value).unwrap();
        assert_eq!(parsed.sources["1"].domains.len(), 2);
        assert!(!parsed.sources["1"].revision.is_empty());
    }
}

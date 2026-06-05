use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::approval_policy::ApprovalPolicy;

const COOLDOWN: Duration = Duration::from_secs(5 * 60);

static DEDUPER: OnceLock<Mutex<ApprovalDeduper>> = OnceLock::new();

struct ApprovalDeduper {
    last_emitted_at: HashMap<String, Instant>,
}

impl ApprovalDeduper {
    fn new() -> Self {
        Self {
            last_emitted_at: HashMap::new(),
        }
    }

    fn should_emit(&mut self, request_type: &str, target_value: &str) -> bool {
        let key = dedupe_key(request_type, target_value);
        let now = Instant::now();
        if let Some(last) = self.last_emitted_at.get(&key) {
            if now.duration_since(*last) < COOLDOWN {
                return false;
            }
        }
        self.last_emitted_at.insert(key, now);
        true
    }

    fn clear_target(&mut self, request_type: &str, target_value: &str) {
        self.last_emitted_at.remove(&dedupe_key(request_type, target_value));
    }
}

fn dedupe_key(request_type: &str, target_value: &str) -> String {
    format!(
        "{}:{}",
        request_type.trim().to_ascii_lowercase(),
        target_value.trim().to_ascii_lowercase()
    )
}

fn with_deduper_mut<F, R>(f: F) -> R
where
    F: FnOnce(&mut ApprovalDeduper) -> R,
{
    let mutex = DEDUPER.get_or_init(|| Mutex::new(ApprovalDeduper::new()));
    let mut guard = mutex.lock().expect("approval deduper mutex poisoned");
    f(&mut *guard)
}

pub fn should_emit(request_type: &str, target_value: &str) -> bool {
    with_deduper_mut(|deduper| deduper.should_emit(request_type, target_value))
}

pub fn on_app_approval_policy_synced(approval: Option<&ApprovalPolicy>) {
    let Some(approval) = approval else {
        return;
    };
    with_deduper_mut(|deduper| {
        for identifier in &approval.approved_packages {
            deduper.clear_target("app_launch", identifier);
        }
    });
}

pub fn on_domain_grants_synced(allowed_domains: &[String]) {
    with_deduper_mut(|deduper| {
        for domain in allowed_domains {
            deduper.clear_target("domain_access", domain);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cooldown_blocks_repeat_emits() {
        let mutex = Mutex::new(ApprovalDeduper::new());
        let mut deduper = mutex.lock().unwrap();
        assert!(deduper.should_emit("app_launch", "/usr/bin/steam"));
        assert!(!deduper.should_emit("app_launch", "/usr/bin/steam"));
        deduper.clear_target("app_launch", "/usr/bin/steam");
        assert!(deduper.should_emit("app_launch", "/usr/bin/steam"));
    }
}

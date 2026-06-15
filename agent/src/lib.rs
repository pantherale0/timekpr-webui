// UniFFI scaffolding for Android JNI bindings
uniffi::setup_scaffolding!();

use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::collections::HashSet;
use hickory_proto::op::{Message, ResponseCode};

type HmacSha256 = Hmac<Sha256>;

// --- Phase 2.1: HMAC Authentication ---
#[uniffi::export]
pub fn generate_auth_signature(token: String, challenge: String, system_id: String) -> String {
    let mut mac = HmacSha256::new_from_slice(token.as_bytes())
        .expect("HMAC key setup failed");
    mac.update(format!("{}{}", challenge, system_id).as_bytes());
    let signature_bytes = mac.finalize().into_bytes();
    hex::encode(signature_bytes)
}

// --- Phase 2.2: DNS & Domain Filtering ---
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

#[uniffi::export]
pub fn check_and_build_blocked_response(
    query_bytes: Vec<u8>,
    blocked_domains: Vec<String>,
    allowed_domains: Vec<String>,
) -> Option<Vec<u8>> {
    let blocked_set: HashSet<String> = blocked_domains.into_iter().map(|d| d.to_ascii_lowercase()).collect();
    let allowed_set: HashSet<String> = allowed_domains.into_iter().map(|d| d.to_ascii_lowercase()).collect();

    let query = Message::from_vec(&query_bytes).ok()?;

    let mut should_block = false;
    for entry in &query.queries {
        let domain = entry.name().to_ascii();
        if domain_is_blocked(&domain, &blocked_set) {
            if !domain_is_allowed(&domain, &allowed_set) {
                should_block = true;
                break;
            }
        }
    }

    if should_block {
        build_blocked_response(&query_bytes).ok()
    } else {
        None
    }
}

// --- Phase 2.3: Screen Time & Allowed Hours Evaluation ---
#[derive(uniffi::Record, Clone, Debug)]
pub struct HourSlot {
    pub start_min: i32,
    pub end_min: i32,
    pub uacc: i32,
}

#[derive(uniffi::Record, Clone, Debug)]
pub struct UserTimeState {
    pub enabled: bool,
    pub time_left_day: i32,
    pub allowed_days: Vec<i32>,
}

#[uniffi::export]
pub fn check_screentime_allowed(
    state: UserTimeState,
    current_hour: i32,
    current_minute: i32,
    day_of_week: i32, // 1 (Monday) to 7 (Sunday)
    allowed_hours: std::collections::HashMap<String, std::collections::HashMap<String, HourSlot>>,
) -> bool {
    if !state.enabled {
        return false;
    }
    if state.time_left_day <= 0 {
        return false;
    }
    if !state.allowed_days.contains(&day_of_week) {
        return false;
    }

    let day_key = day_of_week.to_string();
    if let Some(day_hours) = allowed_hours.get(&day_key) {
        let hour_key = current_hour.to_string();
        if let Some(slot) = day_hours.get(&hour_key) {
            return current_minute >= slot.start_min && current_minute < slot.end_min && slot.uacc == 0;
        }
    }
    true
}

#[uniffi::export]
pub fn init_native_sentry() {
    if let Some(dsn) = option_env!("SENTRY_DSN") {
        if !dsn.is_empty() {
            let options = sentry::ClientOptions {
                release: Some(env!("CARGO_PKG_VERSION").into()),
                ..Default::default()
            };
            let guard = sentry::init((dsn, options));
            if guard.is_enabled() {
                std::mem::forget(guard);
            }
        }
    }
}


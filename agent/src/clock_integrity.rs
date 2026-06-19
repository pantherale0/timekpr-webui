//! Wall-clock tamper detection via boottime cross-check and optional NTP.
//!
//! Uses boottime (CLOCK_BOOTTIME / Android elapsedRealtime), not CLOCK_MONOTONIC,
//! so suspend/resume does not produce false positives.

use serde::{Deserialize, Serialize};
use std::time::Duration;

pub const DEFAULT_THRESHOLD_SECS: i64 = 300;
pub const DEFAULT_CLEAR_CHECKS: u8 = 2;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DetectionSource {
    Boottime,
    Ntp,
    Both,
}

impl DetectionSource {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Boottime => "boottime",
            Self::Ntp => "ntp",
            Self::Both => "both",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PersistedClockIntegrity {
    pub baseline_wall_ms: i64,
    pub baseline_boottime_ms: i64,
    pub tamper_active: bool,
    pub consecutive_passing_checks: u8,
    pub otp_override_active: bool,
    #[serde(default = "default_threshold_secs")]
    pub threshold_secs: i64,
}

fn default_threshold_secs() -> i64 {
    DEFAULT_THRESHOLD_SECS
}

impl Default for PersistedClockIntegrity {
    fn default() -> Self {
        Self {
            baseline_wall_ms: 0,
            baseline_boottime_ms: 0,
            tamper_active: false,
            consecutive_passing_checks: 0,
            otp_override_active: false,
            threshold_secs: DEFAULT_THRESHOLD_SECS,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TickStatus {
    Ok,
    TamperDetected,
    TamperCleared,
}

#[derive(Debug, Clone)]
pub struct TickOutcome {
    pub status: TickStatus,
    pub skew_seconds: i64,
    pub detection_source: Option<DetectionSource>,
    pub tamper_active: bool,
    pub expected_wall_ms: i64,
}

pub struct ClockIntegrityState {
    inner: PersistedClockIntegrity,
}

impl ClockIntegrityState {
    pub fn new(persisted: PersistedClockIntegrity) -> Self {
        Self { inner: persisted }
    }

    pub fn from_json(json: &str) -> Result<Self, String> {
        if json.trim().is_empty() {
            return Ok(Self::new(PersistedClockIntegrity::default()));
        }
        let inner: PersistedClockIntegrity =
            serde_json::from_str(json).map_err(|e| format!("invalid clock integrity state: {e}"))?;
        Ok(Self::new(inner))
    }

    pub fn to_json(&self) -> Result<String, String> {
        serde_json::to_string(&self.inner)
            .map_err(|e| format!("failed to serialize clock integrity state: {e}"))
    }

    pub fn tamper_active(&self) -> bool {
        self.inner.tamper_active && !self.inner.otp_override_active
    }

    pub fn otp_override_active(&self) -> bool {
        self.inner.otp_override_active
    }

    pub fn set_otp_override(&mut self, active: bool) {
        self.inner.otp_override_active = active;
        if active {
            self.inner.tamper_active = false;
            self.inner.consecutive_passing_checks = 0;
        }
    }

    pub fn threshold_ms(&self) -> i64 {
        self.inner.threshold_secs.saturating_mul(1000)
    }

    pub fn trusted_wall_ms(&self, boottime_ms: i64) -> i64 {
        let delta = boottime_ms.saturating_sub(self.inner.baseline_boottime_ms);
        self.inner.baseline_wall_ms.saturating_add(delta)
    }

    pub fn tick(
        &mut self,
        wall_ms: i64,
        boottime_ms: i64,
        ntp_ms: Option<i64>,
    ) -> TickOutcome {
        if self.inner.baseline_wall_ms == 0 && self.inner.baseline_boottime_ms == 0 {
            self.rebaseline(wall_ms, boottime_ms, ntp_ms);
            return TickOutcome {
                status: TickStatus::Ok,
                skew_seconds: 0,
                detection_source: None,
                tamper_active: self.tamper_active(),
                expected_wall_ms: wall_ms,
            };
        }

        let expected_wall_ms = self.trusted_wall_ms(boottime_ms);
        let boottime_skew_ms = (wall_ms - expected_wall_ms).abs();
        let boottime_breach = boottime_skew_ms > self.threshold_ms();

        let mut ntp_breach = false;
        let mut ntp_skew_ms = 0_i64;
        if let Some(ntp) = ntp_ms {
            ntp_skew_ms = (wall_ms - ntp).abs();
            ntp_breach = ntp_skew_ms > self.threshold_ms();
        }

        let breached = boottime_breach || ntp_breach;
        let skew_seconds = boottime_skew_ms
            .max(ntp_skew_ms)
            .saturating_add(999)
            / 1000;
        let detection_source = match (boottime_breach, ntp_breach) {
            (true, true) => Some(DetectionSource::Both),
            (true, false) => Some(DetectionSource::Boottime),
            (false, true) => Some(DetectionSource::Ntp),
            (false, false) => None,
        };

        if breached {
            let was_active = self.inner.tamper_active;
            self.inner.tamper_active = true;
            self.inner.consecutive_passing_checks = 0;
            self.inner.otp_override_active = false;
            return TickOutcome {
                status: if was_active {
                    TickStatus::Ok
                } else {
                    TickStatus::TamperDetected
                },
                skew_seconds,
                detection_source,
                tamper_active: true,
                expected_wall_ms,
            };
        }

        if self.inner.tamper_active {
            self.inner.consecutive_passing_checks =
                self.inner.consecutive_passing_checks.saturating_add(1);
            if self.inner.consecutive_passing_checks >= DEFAULT_CLEAR_CHECKS {
                self.inner.tamper_active = false;
                self.inner.consecutive_passing_checks = 0;
                self.rebaseline(wall_ms, boottime_ms, ntp_ms);
                return TickOutcome {
                    status: TickStatus::TamperCleared,
                    skew_seconds: 0,
                    detection_source: None,
                    tamper_active: false,
                    expected_wall_ms: wall_ms,
                };
            }
            return TickOutcome {
                status: TickStatus::Ok,
                skew_seconds: 0,
                detection_source: None,
                tamper_active: true,
                expected_wall_ms,
            };
        }

        self.rebaseline(wall_ms, boottime_ms, ntp_ms);
        TickOutcome {
            status: TickStatus::Ok,
            skew_seconds: 0,
            detection_source: None,
            tamper_active: false,
            expected_wall_ms: wall_ms,
        }
    }

    fn rebaseline(&mut self, wall_ms: i64, boottime_ms: i64, ntp_ms: Option<i64>) {
        if let Some(ntp) = ntp_ms {
            let ntp_skew = (wall_ms - ntp).abs();
            if ntp_skew <= self.threshold_ms() {
                self.inner.baseline_wall_ms = ntp;
            } else {
                self.inner.baseline_wall_ms = wall_ms;
            }
        } else {
            self.inner.baseline_wall_ms = wall_ms;
        }
        self.inner.baseline_boottime_ms = boottime_ms;
    }
}

/// Read CLOCK_BOOTTIME in milliseconds (Linux agent binary).
#[cfg(target_os = "linux")]
pub fn boottime_ms() -> Result<i64, String> {
    use std::mem::MaybeUninit;

    let mut ts = MaybeUninit::<libc::timespec>::uninit();
    let rc = unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, ts.as_mut_ptr()) };
    if rc != 0 {
        return Err(format!("clock_gettime(CLOCK_BOOTTIME) failed: errno {}", rc));
    }
    let ts = unsafe { ts.assume_init() };
    Ok(ts.tv_sec as i64 * 1000 + ts.tv_nsec as i64 / 1_000_000)
}

#[cfg(target_os = "windows")]
pub fn boottime_ms() -> Result<i64, String> {
    use windows_sys::Win32::System::SystemInformation::QueryInterruptTime;

    let mut interrupt_100ns = 0_u64;
    unsafe {
        QueryInterruptTime(&mut interrupt_100ns);
    }
    Ok((interrupt_100ns / 10_000) as i64)
}

#[cfg(all(not(target_os = "linux"), not(target_os = "windows")))]
pub fn boottime_ms() -> Result<i64, String> {
    Err("boottime_ms is not available on this platform".to_string())
}

/// 100-ns intervals between the Windows epoch (1601-01-01) and Unix epoch (1970-01-01).
pub const FILETIME_EPOCH_DIFF_100NS: u64 = 11_644_473_600_000_000;

/// Convert a Windows FILETIME (100-ns since 1601 UTC) to Unix epoch milliseconds.
pub fn filetime_to_unix_ms(low: u32, high: u32) -> i64 {
    let filetime: u64 = (high as u64) << 32 | (low as u64);
    let unix_100ns = filetime.saturating_sub(FILETIME_EPOCH_DIFF_100NS);
    (unix_100ns / 10_000) as i64
}

/// Wall clock in milliseconds (Linux REALTIME).
#[cfg(target_os = "linux")]
pub fn wall_clock_ms() -> Result<i64, String> {
    use std::mem::MaybeUninit;

    let mut ts = MaybeUninit::<libc::timespec>::uninit();
    let rc = unsafe { libc::clock_gettime(libc::CLOCK_REALTIME, ts.as_mut_ptr()) };
    if rc != 0 {
        return Err(format!("clock_gettime(CLOCK_REALTIME) failed: errno {}", rc));
    }
    let ts = unsafe { ts.assume_init() };
    Ok(ts.tv_sec as i64 * 1000 + ts.tv_nsec as i64 / 1_000_000)
}

#[cfg(target_os = "windows")]
pub fn wall_clock_ms() -> Result<i64, String> {
    use windows_sys::Win32::Foundation::FILETIME;
    use windows_sys::Win32::System::SystemInformation::GetSystemTimePreciseAsFileTime;

    let mut filetime = FILETIME {
        dwLowDateTime: 0,
        dwHighDateTime: 0,
    };
    unsafe {
        GetSystemTimePreciseAsFileTime(&mut filetime);
    }
    Ok(filetime_to_unix_ms(filetime.dwLowDateTime, filetime.dwHighDateTime))
}

#[cfg(all(not(target_os = "linux"), not(target_os = "windows")))]
pub fn wall_clock_ms() -> Result<i64, String> {
    Err("wall_clock_ms is not available on this platform".to_string())
}

const NTP_EPOCH_OFFSET_SECS: u64 = 2_208_988_800;

/// Query NTP time via SNTP (UDP). Returns Unix epoch milliseconds.
pub async fn query_ntp_ms(host: &str, timeout: Duration) -> Result<i64, String> {
    use tokio::net::UdpSocket;
    use tokio::time;

    let addr = format!("{host}:123");
    let socket = UdpSocket::bind("0.0.0.0:0")
        .await
        .map_err(|e| format!("failed to bind UDP socket: {e}"))?;
    socket
        .connect(&addr)
        .await
        .map_err(|e| format!("failed to connect to {addr}: {e}"))?;

    let mut packet = [0_u8; 48];
    packet[0] = 0x1B;

    socket
        .send(&packet)
        .await
        .map_err(|e| format!("failed to send SNTP request: {e}"))?;

    let recv = time::timeout(timeout, socket.recv(&mut packet))
        .await
        .map_err(|_| "SNTP query timed out".to_string())?
        .map_err(|e| format!("failed to receive SNTP response: {e}"))?;

    if recv < 48 {
        return Err("SNTP response too short".to_string());
    }

    let seconds = u32::from_be_bytes([packet[40], packet[41], packet[42], packet[43]]) as u64;
    let fraction = u32::from_be_bytes([packet[44], packet[45], packet[46], packet[47]]) as u64;
    if seconds < NTP_EPOCH_OFFSET_SECS {
        return Err("invalid SNTP timestamp".to_string());
    }
    let unix_secs = seconds - NTP_EPOCH_OFFSET_SECS;
    let unix_ms = unix_secs * 1000 + (fraction * 1000) / u64::from(u32::MAX);
    Ok(unix_ms as i64)
}

pub const DEFAULT_NTP_HOST: &str = "pool.ntp.org";

/// Load platform clocks and optional NTP without holding caller locks across await.
pub async fn fetch_tick_inputs() -> Result<(i64, i64, Option<i64>), String> {
    let wall_ms = wall_clock_ms()?;
    let boottime_ms = boottime_ms()?;
    let ntp_ms = query_ntp_ms(DEFAULT_NTP_HOST, Duration::from_secs(3))
        .await
        .ok();
    Ok((wall_ms, boottime_ms, ntp_ms))
}

/// Apply one integrity check using pre-fetched clock readings.
pub fn apply_tick(
    state: &mut ClockIntegrityState,
    wall_ms: i64,
    boottime_ms: i64,
    ntp_ms: Option<i64>,
) -> TickOutcome {
    state.tick(wall_ms, boottime_ms, ntp_ms)
}

/// Run one integrity check using platform clocks and optional NTP.
pub async fn perform_tick(state: &mut ClockIntegrityState) -> Result<TickOutcome, String> {
    let (wall_ms, boottime_ms, ntp_ms) = fetch_tick_inputs().await?;
    Ok(apply_tick(state, wall_ms, boottime_ms, ntp_ms))
}

// --- UniFFI exports (Android) ---

#[derive(uniffi::Record, Clone, Debug)]
pub struct ClockIntegrityTickResult {
    pub status: String,
    pub skew_seconds: i64,
    pub detection_source: String,
    pub tamper_active: bool,
    pub expected_wall_ms: i64,
    pub persisted_json: String,
}

#[uniffi::export]
pub fn clock_integrity_init(persisted_state_json: String) -> String {
    match ClockIntegrityState::from_json(&persisted_state_json) {
        Ok(state) => state.to_json().unwrap_or_default(),
        Err(_) => serde_json::to_string(&PersistedClockIntegrity::default()).unwrap_or_default(),
    }
}

#[uniffi::export]
pub fn clock_integrity_tick(
    persisted_state_json: String,
    wall_ms: i64,
    boottime_ms: i64,
    ntp_ms: i64,
) -> ClockIntegrityTickResult {
    let ntp_opt = if ntp_ms < 0 { None } else { Some(ntp_ms) };
    let mut state = ClockIntegrityState::from_json(&persisted_state_json)
        .unwrap_or_else(|_| ClockIntegrityState::new(PersistedClockIntegrity::default()));

    let outcome = state.tick(wall_ms, boottime_ms, ntp_opt);
    let status = match outcome.status {
        TickStatus::Ok => "ok",
        TickStatus::TamperDetected => "tamper_detected",
        TickStatus::TamperCleared => "tamper_cleared",
    };
    let detection_source = outcome
        .detection_source
        .map(|s| s.as_str().to_string())
        .unwrap_or_default();
    let persisted_json = state.to_json().unwrap_or_default();

    ClockIntegrityTickResult {
        status: status.to_string(),
        skew_seconds: outcome.skew_seconds,
        detection_source,
        tamper_active: state.tamper_active(),
        expected_wall_ms: outcome.expected_wall_ms,
        persisted_json,
    }
}

#[uniffi::export]
pub fn clock_integrity_trusted_wall_ms(persisted_state_json: String, boottime_ms: i64) -> i64 {
    let state = ClockIntegrityState::from_json(&persisted_state_json)
        .unwrap_or_else(|_| ClockIntegrityState::new(PersistedClockIntegrity::default()));
    state.trusted_wall_ms(boottime_ms)
}

#[uniffi::export]
pub fn clock_integrity_set_otp_override(persisted_state_json: String, active: bool) -> String {
    let mut state = ClockIntegrityState::from_json(&persisted_state_json)
        .unwrap_or_else(|_| ClockIntegrityState::new(PersistedClockIntegrity::default()));
    state.set_otp_override(active);
    state.to_json().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state_with_baseline(wall: i64, boottime: i64) -> ClockIntegrityState {
        ClockIntegrityState::new(PersistedClockIntegrity {
            baseline_wall_ms: wall,
            baseline_boottime_ms: boottime,
            ..Default::default()
        })
    }

    #[test]
    fn matched_suspend_advance_does_not_tamper() {
        let mut state = state_with_baseline(1_000_000, 10_000);
        let outcome = state.tick(4_600_000, 3_610_000, None);
        assert_eq!(outcome.status, TickStatus::Ok);
        assert!(!state.tamper_active());
    }

    #[test]
    fn wall_jump_without_boottime_is_tamper() {
        let mut state = state_with_baseline(1_000_000, 10_000);
        let outcome = state.tick(1_000_000 + 400_000, 10_500, None);
        assert_eq!(outcome.status, TickStatus::TamperDetected);
        assert!(state.tamper_active());
    }

    #[test]
    fn ntp_mismatch_triggers_tamper() {
        let mut state = state_with_baseline(1_000_000, 10_000);
        let outcome = state.tick(1_000_000, 10_500, Some(1_000_000 + 400_000));
        assert_eq!(outcome.status, TickStatus::TamperDetected);
        assert_eq!(outcome.detection_source, Some(DetectionSource::Ntp));
    }

    #[test]
    fn hysteresis_requires_two_passes_to_clear() {
        let mut inner = PersistedClockIntegrity::default();
        inner.baseline_wall_ms = 1_000_000;
        inner.baseline_boottime_ms = 10_000;
        inner.tamper_active = true;
        let mut state = ClockIntegrityState::new(inner);

        let first = state.tick(1_000_500, 10_500, Some(1_000_500));
        assert_eq!(first.status, TickStatus::Ok);
        assert!(state.tamper_active());

        let second = state.tick(1_001_000, 11_000, Some(1_001_000));
        assert_eq!(second.status, TickStatus::TamperCleared);
        assert!(!state.tamper_active());
    }

    #[test]
    fn otp_override_clears_active_tamper_flag() {
        let mut inner = PersistedClockIntegrity::default();
        inner.tamper_active = true;
        let mut state = ClockIntegrityState::new(inner);
        state.set_otp_override(true);
        assert!(!state.tamper_active());
    }

    #[test]
    fn filetime_to_unix_ms_known_value() {
        // 2024-01-01 00:00:00 UTC
        let unix_ms = 1_704_067_200_000_i64;
        let filetime_100ns = (unix_ms as u64) * 10_000 + FILETIME_EPOCH_DIFF_100NS;
        let low = filetime_100ns as u32;
        let high = (filetime_100ns >> 32) as u32;
        assert_eq!(filetime_to_unix_ms(low, high), unix_ms);
    }
}

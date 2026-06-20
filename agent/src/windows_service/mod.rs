pub mod bcd_integrity;
pub mod boot_mode;
pub mod clock_integrity_monitor;
pub mod dns_proxy;
pub mod ipc;
pub mod laps;
pub mod overlay;
pub mod policy;
pub mod process_monitor;
pub mod safe_mode_lockdown;
pub mod safeboot_registry;
pub mod tamper_state;

#[cfg(target_os = "windows")]
pub const SERVICE_NAME: &str = "GuardianAgent";

#[cfg(target_os = "windows")]
use std::sync::{Arc, Mutex};
#[cfg(target_os = "windows")]
use tokio::sync::mpsc;
#[cfg(target_os = "windows")]
use windows_service::{
    define_windows_service,
    service::{
        PowerEventParam, ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState,
        ServiceStatus, ServiceType,
    },
    service_control_handler::{self, ServiceControlHandlerResult},
    service_dispatcher,
};

#[cfg(target_os = "windows")]
define_windows_service!(ffi_service_main, timekpr_service_main);

#[cfg(target_os = "windows")]
pub async fn run_service() {
    println!("Starting Windows Service Dispatcher...");
    if let Err(e) = service_dispatcher::start(SERVICE_NAME, ffi_service_main) {
        eprintln!("Failed to start service dispatcher: {}", e);
        println!("Running in standalone/console mode...");
        run_service_tasks(None, boot_mode::is_safe_mode_boot()).await;
    }
}

#[cfg(target_os = "windows")]
fn timekpr_service_main(_args: Vec<std::ffi::OsString>) {
    let event_loop = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap();

    event_loop.block_on(async {
        let (status_tx, mut status_rx) = mpsc::unbounded_channel::<ServiceStatus>();
        let (power_resume_tx, power_resume_rx) = mpsc::unbounded_channel::<()>();

        let status_handle = service_control_handler::register(
            SERVICE_NAME,
            move |control_event| {
                match control_event {
                    ServiceControl::Stop | ServiceControl::Shutdown => {
                        let _ = status_tx.send(ServiceStatus {
                            service_type: ServiceType::OWN_PROCESS,
                            current_state: ServiceState::StopPending,
                            controls_accepted: ServiceControlAccept::empty(),
                            exit_code: ServiceExitCode::Win32(0),
                            checkpoint: 0,
                            wait_hint: std::time::Duration::from_secs(5),
                            process_id: None,
                        });
                        ServiceControlHandlerResult::NoError
                    }
                    ServiceControl::PowerEvent(event) => {
                        if matches!(
                            event,
                            PowerEventParam::ResumeAutomatic | PowerEventParam::ResumeSuspend
                        ) {
                            let _ = power_resume_tx.send(());
                        }
                        ServiceControlHandlerResult::NoError
                    }
                    _ => ServiceControlHandlerResult::NotImplemented,
                }
            },
        );

        let status_handle = match status_handle {
            Ok(h) => h,
            Err(e) => {
                eprintln!("Failed to register service control handler: {}", e);
                return;
            }
        };

        let _ = status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::StartPending,
            controls_accepted: ServiceControlAccept::empty(),
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: std::time::Duration::from_secs(2),
            process_id: None,
        });

        safeboot_registry::ensure_registered();

        let safe_mode = boot_mode::is_safe_mode_boot();
        if safe_mode {
            safe_mode_lockdown::on_safe_mode_service_start();
        } else {
            safe_mode_lockdown::on_normal_boot_service_start();
        }

        if !safe_mode {
            let _ = dns_proxy::configure_system_dns().await;
        } else {
            println!("Safe Mode boot detected; skipping DNS reconfiguration.");
        }

        let service_task = tokio::spawn(run_service_tasks(Some(power_resume_rx), safe_mode));

        let _ = status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Running,
            controls_accepted: ServiceControlAccept::STOP
                | ServiceControlAccept::SHUTDOWN
                | ServiceControlAccept::POWER_EVENT,
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: std::time::Duration::from_secs(0),
            process_id: None,
        });

        if let Some(status) = status_rx.recv().await {
            let _ = status_handle.set_service_status(status);
        }

        let _ = dns_proxy::restore_system_dns().await;
        let _ = policy::clear_on_unenroll();

        service_task.abort();

        let _ = status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Stopped,
            controls_accepted: ServiceControlAccept::empty(),
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: std::time::Duration::from_secs(0),
            process_id: None,
        });
    });
}

#[cfg(target_os = "windows")]
async fn run_service_tasks(
    power_resume_rx: Option<mpsc::UnboundedReceiver<()>>,
    safe_mode: bool,
) {
    let active_client_tx: Arc<Mutex<Option<mpsc::UnboundedSender<crate::ClientMessage>>>> =
        Arc::new(Mutex::new(None));

    tokio::spawn(ipc::start_ipc_server());
    let ipc_client_tx = active_client_tx.clone();
    tokio::spawn(async move {
        let _ = crate::ipc::run_ipc_server(ipc_client_tx).await;
    });

    let (alert_tx, mut alert_rx) = mpsc::unbounded_channel::<crate::netlink::AppAlert>();
    crate::netlink::register_alert_sender(alert_tx);

    let active_tx_clone = active_client_tx.clone();
    tokio::spawn(async move {
        while let Some(alert) = alert_rx.recv().await {
            let msg = crate::build_alert_message(
                &alert.event_type,
                Some(alert.linux_username),
                alert.payload,
            );
            let opt_tx = {
                let guard = active_tx_clone.lock().unwrap();
                guard.clone()
            };
            if let Some(tx) = opt_tx {
                let _ = tx.send(msg);
            }
        }
    });

    tokio::spawn(process_monitor::start_process_monitor());

    if !safe_mode {
        tokio::spawn(bcd_integrity::start_bcd_monitor());
    }

    let users_map = policy::get_windows_users_map();
    let clock_monitor = clock_integrity_monitor::start(users_map);
    clock_integrity_monitor::spawn_periodic_monitor(clock_monitor.clone());
    if let Some(resume_rx) = power_resume_rx {
        clock_integrity_monitor::spawn_resume_hook(clock_monitor, resume_rx);
    }

    if let Err(message) = crate::domain_policy::initialize_runtime().await {
        eprintln!("Failed to restore persisted domain policy: {}", message);
    }

    crate::start_agent_reconnect_loop(active_client_tx, !safe_mode).await;
}

#[cfg(not(target_os = "windows"))]
pub async fn run_service() {}

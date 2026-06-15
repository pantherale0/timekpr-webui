pub mod dns_proxy;
pub mod ipc;
pub mod policy;
pub mod process_monitor;

#[cfg(target_os = "windows")]
use std::sync::{Arc, Mutex};
#[cfg(target_os = "windows")]
use tokio::sync::mpsc;
#[cfg(target_os = "windows")]
use windows_service::{
    define_windows_service,
    service::{
        ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState, ServiceStatus,
        ServiceType,
    },
    service_control_handler::{self, ServiceControlHandlerResult},
    service_dispatcher,
};

#[cfg(target_os = "windows")]
define_windows_service!(ffi_service_main, timekpr_service_main);

#[cfg(target_os = "windows")]
pub async fn run_service() {
    println!("Starting Windows Service Dispatcher...");
    // Register and start the windows service FFI loop
    if let Err(e) = service_dispatcher::start("TimeKprAgent", ffi_service_main) {
        eprintln!("Failed to start service dispatcher: {}", e);
        println!("Running in standalone/console mode...");
        run_service_tasks().await;
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

        // Register service control handler
        let status_handle = service_control_handler::register(
            "TimeKprAgent",
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

        // Report StartPending
        let _ = status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::StartPending,
            controls_accepted: ServiceControlAccept::empty(),
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: std::time::Duration::from_secs(2),
            process_id: None,
        });

        // Initialize DNS and policies
        let _ = dns_proxy::configure_system_dns().await;

        // Start background tasks
        let service_task = tokio::spawn(run_service_tasks());

        // Report Running
        let _ = status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Running,
            controls_accepted: ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN,
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: std::time::Duration::from_secs(0),
            process_id: None,
        });

        // Block waiting for stop signal
        if let Some(status) = status_rx.recv().await {
            let _ = status_handle.set_service_status(status);
        }

        // Cleanup DNS and policies
        let _ = dns_proxy::restore_system_dns().await;
        let _ = policy::clear_on_unenroll();

        // Stop all background tasks
        service_task.abort();

        // Report Stopped
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
async fn run_service_tasks() {
    // 1. Start Named Pipe IPC Server
    tokio::spawn(ipc::start_ipc_server());
    tokio::spawn(async {
        let _ = crate::ipc::run_ipc_server().await;
    });

    // 2. Set up global AppAlert channel for process monitor
    let (alert_tx, mut alert_rx) = mpsc::unbounded_channel::<crate::netlink::AppAlert>();
    crate::netlink::register_alert_sender(alert_tx);

    let active_client_tx: Arc<Mutex<Option<mpsc::UnboundedSender<crate::ClientMessage>>>> =
        Arc::new(Mutex::new(None));
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

    // 3. Start Process Monitor
    tokio::spawn(process_monitor::start_process_monitor());

    // 4. Start local DNS Controller / server from local_dns
    // (the actual hickory DNS listener)
    if let Err(message) = crate::domain_policy::initialize_runtime().await {
        eprintln!("Failed to restore persisted domain policy: {}", message);
    }

    // 5. Start WebSocket Agent loop from main.rs
    crate::start_agent_reconnect_loop(active_client_tx).await;
}

#[cfg(not(target_os = "windows"))]
pub async fn run_service() {
    // No-op for compilation on Linux
}

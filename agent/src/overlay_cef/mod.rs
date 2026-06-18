//! Chromium Embedded Framework (CEF) kiosk overlay for Guardian Space.
//!
//! Spawns a borderless, fullscreen, always-on-top browser window that loads
//! `blockedv2.html` and forwards child access requests to the guardian-agent
//! daemon via Unix socket IPC.

mod agent_ipc;

use std::cell::RefCell;
use std::ffi::c_int;
use std::sync::{Arc, OnceLock};

use cef::wrapper::message_router::{
    BrowserSideHandler, BrowserSideRouter, MessageRouterBrowserSide,
    MessageRouterBrowserSideHandlerCallbacks, MessageRouterConfig,
    MessageRouterRendererSide, MessageRouterRendererSideHandlerCallbacks, RendererSideRouter,
};
use cef::*;

static OVERLAY_URL: OnceLock<String> = OnceLock::new();

fn router_config() -> MessageRouterConfig {
    MessageRouterConfig {
        js_query_function: "cefQuery".into(),
        js_cancel_function: "cefQueryCancel".into(),
        message_size_threshold: 16 * 1024,
    }
}

/// Access-request handler: JSON payload `{"type":"ACCESS_REQUEST","reason":"…","message":"…"}`.
struct AccessRequestHandler;

impl BrowserSideHandler for AccessRequestHandler {
    fn on_query_str(
        &self,
        _browser: Option<Browser>,
        _frame: Option<Frame>,
        _query_id: i64,
        request: &str,
        _persistent: bool,
        callback: Arc<std::sync::Mutex<dyn cef::wrapper::message_router::BrowserSideCallback>>,
    ) -> bool {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(request) else {
            return false;
        };
        if value.get("type").and_then(|v| v.as_str()) != Some("ACCESS_REQUEST") {
            return false;
        }
        let reason = value
            .get("reason")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let message = value
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        match agent_ipc::forward_access_request(reason, message) {
            Ok(()) => callback.lock().unwrap().success_str("ok"),
            Err(e) => callback.lock().unwrap().failure(-1, &e),
        }
        true
    }
}

wrap_client! {
    struct OverlayClient {
        router: Arc<BrowserSideRouter>,
    }

    impl Client {
        fn life_span_handler(&self) -> Option<LifeSpanHandler> {
            Some(OverlayLifeSpanHandler::new(self.router.clone()))
        }

        fn load_handler(&self) -> Option<LoadHandler> {
            Some(OverlayLoadHandler::new())
        }

        fn on_process_message_received(
            &self,
            browser: Option<&mut Browser>,
            frame: Option<&mut Frame>,
            source_process: ProcessId,
            message: Option<&mut ProcessMessage>,
        ) -> c_int {
            self.router.on_process_message_received(
                browser.cloned(),
                frame.cloned(),
                source_process,
                message.cloned(),
            )
            .into()
        }
    }
}

wrap_life_span_handler! {
    struct OverlayLifeSpanHandler {
        router: Arc<BrowserSideRouter>,
    }

    impl LifeSpanHandler {
        fn on_before_close(&self, browser: Option<&mut Browser>) {
            self.router.on_before_close(browser.cloned());
            quit_message_loop();
        }
    }
}

wrap_load_handler! {
    struct OverlayLoadHandler;

    impl LoadHandler {
        fn on_load_end(
            &self,
            _browser: Option<&mut Browser>,
            frame: Option<&mut Frame>,
            _http_status_code: i32,
        ) {
            let Some(frame) = frame else { return };
            if frame.is_main() == 0 {
                return;
            }
            // Expose guardianBridge for blockedv2.html (same contract as Android WebView).
            let bridge = r#"
                window.guardianBridge = {
                    sendAccessRequest: function(reason, message) {
                        window.cefQuery({
                            request: JSON.stringify({
                                type: 'ACCESS_REQUEST',
                                reason: reason,
                                message: message
                            }),
                            onSuccess: function() {},
                            onFailure: function() {}
                        });
                    }
                };
            "#;
            frame.execute_java_script(Some(&CefString::from(bridge)), None, 0);
        }
    }
}

wrap_render_process_handler! {
    struct OverlayRenderProcessHandler {
        router: Arc<RendererSideRouter>,
    }

    impl RenderProcessHandler {
        fn on_context_created(
            &self,
            browser: Option<&mut Browser>,
            frame: Option<&mut Frame>,
            context: Option<&mut V8Context>,
        ) {
            self.router.on_context_created(
                browser.cloned(),
                frame.cloned(),
                context.cloned(),
            );
        }

        fn on_context_released(
            &self,
            browser: Option<&mut Browser>,
            frame: Option<&mut Frame>,
            context: Option<&mut V8Context>,
        ) {
            self.router.on_context_released(
                browser.cloned(),
                frame.cloned(),
                context.cloned(),
            );
        }

        fn on_process_message_received(
            &self,
            browser: Option<&mut Browser>,
            frame: Option<&mut Frame>,
            source_process: ProcessId,
            message: Option<&mut ProcessMessage>,
        ) -> i32 {
            self.router.on_process_message_received(
                browser.cloned(),
                frame.cloned(),
                Some(source_process),
                message.cloned(),
            )
            .into()
        }
    }
}

wrap_window_delegate! {
    struct OverlayWindowDelegate {
        browser_view: RefCell<Option<BrowserView>>,
    }

    impl ViewDelegate {
        fn preferred_size(&self, _view: Option<&mut View>) -> Size {
            Size {
                width: 1920,
                height: 1080,
            }
        }
    }

    impl PanelDelegate {}

    impl WindowDelegate {
        fn on_window_created(&self, window: Option<&mut Window>) {
            let browser_view = self.browser_view.borrow();
            let (Some(window), Some(browser_view)) = (window, browser_view.as_ref()) else {
                return;
            };
            let mut view = View::from(browser_view);
            window.add_child_view(Some(&mut view));
            window.set_always_on_top(1);
            window.set_fullscreen(1);
            window.show();
            window.bring_to_top();
        }

        fn on_window_destroyed(&self, _window: Option<&mut Window>) {
            *self.browser_view.borrow_mut() = None;
        }

        fn can_close(&self, _window: Option<&mut Window>) -> i32 {
            1
        }

        fn initial_show_state(&self, _window: Option<&mut Window>) -> ShowState {
            ShowState::MAXIMIZED
        }
    }
}

wrap_browser_view_delegate! {
    struct OverlayBrowserViewDelegate {}
    impl ViewDelegate {}
    impl BrowserViewDelegate {}
}

wrap_browser_process_handler! {
    struct OverlayBrowserProcessHandler {
        client: RefCell<Option<Client>>,
        router: Arc<BrowserSideRouter>,
    }

    impl BrowserProcessHandler {
        fn on_context_initialized(&self) {
            let url = OVERLAY_URL
                .get()
                .map(|u| u.as_str())
                .unwrap_or("about:blank");
            let url = CefString::from(url);
            let settings = BrowserSettings::default();

            let mut client_slot = self.client.borrow_mut();
            *client_slot = Some(OverlayClient::new(self.router.clone()));
            let mut client = client_slot.clone();

            let mut delegate = OverlayBrowserViewDelegate::new();
            let browser_view = browser_view_create(
                client.as_mut(),
                Some(&url),
                Some(&settings),
                None,
                None,
                Some(&mut delegate),
            );

            let mut window_delegate =
                OverlayWindowDelegate::new(RefCell::new(browser_view));
            window_create_top_level(Some(&mut window_delegate));
        }

        fn default_client(&self) -> Option<Client> {
            self.client.borrow().clone()
        }
    }
}

wrap_app! {
    struct OverlayApp {
        browser_router: Arc<BrowserSideRouter>,
        render_router: Arc<RendererSideRouter>,
    }

    impl App {
        fn browser_process_handler(&self) -> Option<BrowserProcessHandler> {
            self.browser_router.add_handler(Arc::new(AccessRequestHandler), false);
            Some(OverlayBrowserProcessHandler::new(
                RefCell::new(None),
                self.browser_router.clone(),
            ))
        }

        fn render_process_handler(&self) -> Option<RenderProcessHandler> {
            Some(OverlayRenderProcessHandler::new(self.render_router.clone()))
        }
    }
}

#[cfg(target_os = "macos")]
fn init_cef_platform() {
    // macOS requires bundle-specific setup via LibraryLoader (see cef-rs cefsimple).
}

#[cfg(not(target_os = "macos"))]
fn init_cef_platform() {
    let _ = api_hash(sys::CEF_API_VERSION_LAST, 0);
}

/// Run the CEF overlay until the window is closed.
pub fn run(url: String) -> Result<(), String> {
    OVERLAY_URL
        .set(url)
        .map_err(|_| "overlay URL already set".to_string())?;

    init_cef_platform();

    let args = args::Args::new();
    let Some(cmd_line) = args.as_cmd_line() else {
        return Err("failed to parse command line".into());
    };

    let switch = CefString::from("type");
    let is_browser_process = cmd_line.has_switch(Some(&switch)) != 1;
    let ret = execute_process(Some(&args.as_main_args()), None, std::ptr::null_mut());

    if is_browser_process {
        if ret != -1 {
            return Err(format!("execute_process returned {ret}, expected -1 for browser process"));
        }
    } else if ret < 0 {
        return Err(format!("subprocess execute_process failed: {ret}"));
    } else {
        return Ok(());
    }

    let config = router_config();
    let browser_router = BrowserSideRouter::new(config.clone());
    let render_router = RendererSideRouter::new(config);

    let mut app = OverlayApp::new(browser_router, render_router);

    let settings = Settings {
        no_sandbox: 1,
        ..Default::default()
    };

    if initialize(
        Some(&args.as_main_args()),
        Some(&settings),
        Some(&mut app),
        std::ptr::null_mut(),
    ) != 1
    {
        return Err("CEF initialize failed".into());
    }

    run_message_loop();
    shutdown();
    Ok(())
}

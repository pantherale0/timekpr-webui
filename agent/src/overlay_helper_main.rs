//! Guardian Space overlay helper — CEF kiosk window.
//!
//! Spawned by the main agent on `show_overlay`.  Loads `blockedv2.html` in a
//! borderless fullscreen Chromium Embedded Framework window and forwards child
//! access requests to the guardian-agent IPC socket.
//!
//! Usage:
//!   guardian-overlay-helper <url> [<username>]
//!
//! Build:
//!   cargo build --release --bin guardian-overlay-helper --features cef-overlay

#[cfg(feature = "cef-overlay")]
mod overlay_cef;

#[cfg(feature = "cef-overlay")]
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let url = match args.get(1) {
        Some(u) if !u.is_empty() => u.clone(),
        _ => {
            eprintln!("guardian-overlay-helper: usage: guardian-overlay-helper <url> [<username>]");
            std::process::exit(1);
        }
    };

    if let Err(e) = overlay_cef::run(url) {
        eprintln!("guardian-overlay-helper: {e}");
        std::process::exit(1);
    }
}

#[cfg(not(feature = "cef-overlay"))]
fn main() {
    eprintln!(
        "guardian-overlay-helper: built without CEF support. \
         Rebuild with: cargo build --bin guardian-overlay-helper --features cef-overlay"
    );
    std::process::exit(1);
}

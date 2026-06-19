use serde_json::json;
#[cfg(target_os = "linux")]
use std::process::Command;

#[cfg(target_os = "linux")]
use crate::approval_deduper;
use crate::local_dns;
use crate::netlink;

const MODE_APPROVAL_ON_BLOCK: &str = "approval_on_block";

pub fn on_domain_blocked(linux_username: &str, domain: &str, domain_access_mode: &str) {
    let registrable = local_dns::registrable_domain(domain);
    if registrable.is_empty() {
        return;
    }

    let should_alert = {
        #[cfg(target_os = "linux")]
        {
            domain_access_mode == MODE_APPROVAL_ON_BLOCK
                && approval_deduper::should_emit("domain_access", &registrable)
        }
        #[cfg(target_os = "windows")]
        {
            domain_access_mode == MODE_APPROVAL_ON_BLOCK
        }
    };

    if should_alert {
        netlink::send_app_alert(
            "access_requested",
            linux_username,
            json!({
                "request_type": "domain_access",
                "target_kind": "domain",
                "target_value": registrable,
                "display_label": registrable,
            }),
        );
        show_domain_blocked_notification(&registrable);
    }
}

fn show_domain_blocked_notification(domain: &str) {
    let body = crate::i18n::t_fmt("domain_blocked_body", &[("domain", domain)]);
    let title = crate::i18n::t("domain_blocked_title");
    let product = crate::i18n::t("product_name");
    let agent_name = crate::i18n::t("agent_name");
    #[cfg(target_os = "linux")]
    {
        let _ = Command::new("notify-send")
            .args([&title, &body, "-u", "normal", "-a", &agent_name])
            .spawn();
    }
    #[cfg(target_os = "windows")]
    {
        crate::windows_service::ipc::broadcast_toast_notification(&title, &body);
    }
    let _ = product;
}

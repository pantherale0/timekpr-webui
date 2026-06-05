use serde_json::json;
use std::process::Command;

use crate::approval_deduper;
use crate::local_dns;
use crate::netlink;

const MODE_APPROVAL_ON_BLOCK: &str = "approval_on_block";

pub fn on_domain_blocked(linux_username: &str, domain: &str, domain_access_mode: &str) {
    let registrable = local_dns::registrable_domain(domain);
    if registrable.is_empty() {
        return;
    }

    if domain_access_mode == MODE_APPROVAL_ON_BLOCK
        && approval_deduper::should_emit("domain_access", &registrable)
    {
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
    let body = format!(
        "{domain} is blocked. A request was sent to your parent. \
         You can ask them to approve access in TimeKpr."
    );
    let _ = Command::new("notify-send")
        .args([
            "TimeKpr",
            &body,
            "-u",
            "normal",
            "-a",
            "TimeKpr Agent",
        ])
        .spawn();
}

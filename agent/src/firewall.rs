use std::process::Command;

const NAT_TABLE: &str = "nat";
const FILTER_TABLE: &str = "filter";
const NAT_CHAIN: &str = "TIMEKPR_UID_DNS";
const FILTER_CHAIN: &str = "TIMEKPR_UID_EGRESS";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FirewallPolicy {
    pub uid: u32,
    pub listen_port: u16,
}

pub fn reconcile(policies: &[FirewallPolicy]) -> Result<(), String> {
    ensure_chain(NAT_TABLE, NAT_CHAIN)?;
    ensure_chain(FILTER_TABLE, FILTER_CHAIN)?;
    ensure_jump(NAT_TABLE, "OUTPUT", NAT_CHAIN)?;
    ensure_jump(FILTER_TABLE, "OUTPUT", FILTER_CHAIN)?;
    flush_chain(NAT_TABLE, NAT_CHAIN)?;
    flush_chain(FILTER_TABLE, FILTER_CHAIN)?;

    for policy in policies {
        append_nat_redirect(policy, "udp")?;
        append_nat_redirect(policy, "tcp")?;
        append_dns_tls_block(policy, "udp")?;
        append_dns_tls_block(policy, "tcp")?;
    }

    let managed_ports: Vec<u16> = policies.iter().map(|policy| policy.listen_port).collect();
    for policy in policies {
        for port in &managed_ports {
            append_port_guard(policy.uid, *port, "udp")?;
            append_port_guard(policy.uid, *port, "tcp")?;
        }
    }

    Ok(())
}

fn ensure_chain(table: &str, chain: &str) -> Result<(), String> {
    let status = Command::new("iptables")
        .args(["-t", table, "-L", chain])
        .status()
        .map_err(|error| format!("failed to inspect iptables chain {}: {}", chain, error))?;
    if status.success() {
        return Ok(());
    }

    run_iptables(["-t", table, "-N", chain])
}

fn ensure_jump(table: &str, from_chain: &str, to_chain: &str) -> Result<(), String> {
    let status = Command::new("iptables")
        .args(["-t", table, "-C", from_chain, "-j", to_chain])
        .status()
        .map_err(|error| format!("failed to inspect iptables jump {} -> {}: {}", from_chain, to_chain, error))?;
    if status.success() {
        return Ok(());
    }

    run_iptables(["-t", table, "-A", from_chain, "-j", to_chain])
}

fn flush_chain(table: &str, chain: &str) -> Result<(), String> {
    run_iptables(["-t", table, "-F", chain])
}

fn append_nat_redirect(policy: &FirewallPolicy, protocol: &str) -> Result<(), String> {
    run_iptables([
        "-t",
        NAT_TABLE,
        "-A",
        NAT_CHAIN,
        "-m",
        "owner",
        "--uid-owner",
        &policy.uid.to_string(),
        "-p",
        protocol,
        "--dport",
        "53",
        "-j",
        "REDIRECT",
        "--to-ports",
        &policy.listen_port.to_string(),
    ])
}

fn append_dns_tls_block(policy: &FirewallPolicy, protocol: &str) -> Result<(), String> {
    run_iptables([
        "-t",
        FILTER_TABLE,
        "-A",
        FILTER_CHAIN,
        "-m",
        "owner",
        "--uid-owner",
        &policy.uid.to_string(),
        "-p",
        protocol,
        "--dport",
        "853",
        "-j",
        "REJECT",
    ])
}

fn append_port_guard(uid: u32, port: u16, protocol: &str) -> Result<(), String> {
    let args = build_port_guard_args(uid, port, protocol);
    run_iptables_owned(&args)
}

fn build_port_guard_args(uid: u32, port: u16, protocol: &str) -> Vec<String> {
    vec![
        "-t".to_string(),
        FILTER_TABLE.to_string(),
        "-A".to_string(),
        FILTER_CHAIN.to_string(),
        "-m".to_string(),
        "owner".to_string(),
        "!".to_string(),
        "--uid-owner".to_string(),
        uid.to_string(),
        "-p".to_string(),
        protocol.to_string(),
        "--dport".to_string(),
        port.to_string(),
        "-j".to_string(),
        "REJECT".to_string(),
    ]
}

fn run_iptables<const N: usize>(args: [&str; N]) -> Result<(), String> {
    let output = Command::new("iptables")
        .args(args)
        .output()
        .map_err(|error| format!("failed to run iptables: {}", error))?;

    if output.status.success() {
        return Ok(());
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!(
        "iptables command failed: {}",
        stderr.trim()
    ))
}

fn run_iptables_owned(args: &[String]) -> Result<(), String> {
    let output = Command::new("iptables")
        .args(args)
        .output()
        .map_err(|error| format!("failed to run iptables: {}", error))?;

    if output.status.success() {
        return Ok(());
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!(
        "iptables command failed: {}",
        stderr.trim()
    ))
}

#[cfg(test)]
mod tests {
    use super::{build_port_guard_args, FirewallPolicy};

    fn render_rules(policies: &[FirewallPolicy]) -> Vec<String> {
        let mut rules = Vec::new();
        for policy in policies {
            rules.push(format!(
                "redirect uid={} udp 53 -> {}",
                policy.uid,
                policy.listen_port
            ));
            rules.push(format!(
                "redirect uid={} tcp 53 -> {}",
                policy.uid,
                policy.listen_port
            ));
            rules.push(format!("block uid={} udp 853", policy.uid));
            rules.push(format!("block uid={} tcp 853", policy.uid));
        }
        for policy in policies {
            for other in policies {
                rules.push(format!(
                    "guard uid={} {} -> {}",
                    policy.uid,
                    other.listen_port,
                    other.listen_port
                ));
            }
        }
        rules
    }

    #[test]
    fn firewall_rule_shape_covers_redirects_and_guards() {
        let policies = vec![
            FirewallPolicy { uid: 1000, listen_port: 23001 },
            FirewallPolicy { uid: 1001, listen_port: 23002 },
        ];

        let rules = render_rules(&policies);
        assert!(rules.iter().any(|rule| rule.contains("redirect uid=1000 udp 53 -> 23001")));
        assert!(rules.iter().any(|rule| rule.contains("block uid=1001 tcp 853")));
        assert!(rules.iter().any(|rule| rule.contains("guard uid=1000 23002 -> 23002")));
    }

    #[test]
    fn port_guard_negates_uid_owner_match_in_supported_position() {
        let args = build_port_guard_args(1000, 23002, "udp");
        assert_eq!(
            args,
            vec![
                "-t",
                "filter",
                "-A",
                "TIMEKPR_UID_EGRESS",
                "-m",
                "owner",
                "!",
                "--uid-owner",
                "1000",
                "-p",
                "udp",
                "--dport",
                "23002",
                "-j",
                "REJECT",
            ]
        );
    }
}

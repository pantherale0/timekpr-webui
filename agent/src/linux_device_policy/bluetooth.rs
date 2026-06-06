use std::process::Command;

pub fn reconcile(bluetooth_disabled: bool) -> Result<(), String> {
    let action = if bluetooth_disabled { "block" } else { "unblock" };
    let status = Command::new("rfkill")
        .args([action, "bluetooth"])
        .status()
        .map_err(|e| format!("failed to run rfkill {action} bluetooth: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("rfkill {action} bluetooth exited with {status}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reconcile_unblock_does_not_panic() {
        let _ = reconcile(false);
    }
}

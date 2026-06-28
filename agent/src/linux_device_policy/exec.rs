const SHELLS: &[&str] = &[
    "/bin/sh",
    "/usr/bin/sh",
    "/bin/bash",
    "/usr/bin/bash",
    "/usr/bin/zsh",
    "/usr/bin/fish",
    "/usr/bin/dash",
];

const TERMINAL_EMULATORS: &[&str] = &[
    "/usr/bin/konsole",
    "/usr/bin/gnome-terminal",
    "/usr/bin/xfce4-terminal",
    "/usr/bin/xterm",
    "/usr/bin/alacritty",
    "/usr/bin/kitty",
    "/usr/bin/wezterm",
    "/usr/bin/tilix",
    "/usr/bin/qterminal",
    "/usr/bin/terminator",
    "/usr/bin/x-terminal-emulator",
];

pub fn is_terminal_blocked(exe_path: &str, argv: &[String]) -> bool {
    let normalized = normalize_path(exe_path);

    // Unconditionally block terminal emulators
    if TERMINAL_EMULATORS
        .iter()
        .any(|candidate| normalized == *candidate)
    {
        return true;
    }

    // Check if it's a shell
    let is_shell = SHELLS.iter().any(|candidate| normalized == *candidate);
    if !is_shell {
        return false;
    }

    // A shell is considered interactive if it is not executing a script or command.
    // Check if any argument is "-c" or similar command-exec flags
    let has_cmd_flag = argv.iter().skip(1).any(|arg| arg == "-c" || arg == "--code");
    if has_cmd_flag {
        return false;
    }

    // Check if there is any positional argument (does not start with "-")
    let has_script_file = argv.iter().skip(1).any(|arg| !arg.starts_with('-'));
    if has_script_file {
        return false;
    }

    true
}

fn normalize_path(path: &str) -> String {
    path.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_common_shells() {
        // Interactive shell (no args or options only) should be blocked
        assert!(is_terminal_blocked("/usr/bin/bash", &[]));
        assert!(is_terminal_blocked("/bin/sh", &["/bin/sh".to_string()]));
        assert!(is_terminal_blocked("/bin/sh", &["/bin/sh".to_string(), "-i".to_string()]));

        // Non-interactive command/script executions should NOT be blocked
        assert!(!is_terminal_blocked(
            "/usr/bin/bash",
            &[
                "bash".to_string(),
                "-c".to_string(),
                "echo hello".to_string()
            ]
        ));
        assert!(!is_terminal_blocked(
            "/bin/sh",
            &["sh".to_string(), "/home/user/script.sh".to_string()]
        ));
    }

    #[test]
    fn detects_terminal_emulators() {
        // Terminal emulators should be blocked unconditionally
        assert!(is_terminal_blocked("/usr/bin/konsole", &[]));
        assert!(is_terminal_blocked(
            "/usr/bin/konsole",
            &[
                "konsole".to_string(),
                "-e".to_string(),
                "htop".to_string()
            ]
        ));

        // Regular applications should NOT be blocked
        assert!(!is_terminal_blocked("/usr/bin/firefox", &[]));
    }
}

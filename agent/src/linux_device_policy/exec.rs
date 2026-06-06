const TERMINAL_EXECUTABLES: &[&str] = &[
    "/bin/sh",
    "/usr/bin/sh",
    "/bin/bash",
    "/usr/bin/bash",
    "/usr/bin/zsh",
    "/usr/bin/fish",
    "/usr/bin/dash",
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

pub fn is_terminal_executable(exe_path: &str) -> bool {
    let normalized = normalize_path(exe_path);
    TERMINAL_EXECUTABLES
        .iter()
        .any(|candidate| normalized == *candidate)
}

fn normalize_path(path: &str) -> String {
    path.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_common_shells() {
        assert!(is_terminal_executable("/usr/bin/bash"));
        assert!(is_terminal_executable("/bin/sh"));
    }

    #[test]
    fn detects_terminal_emulators() {
        assert!(is_terminal_executable("/usr/bin/konsole"));
        assert!(!is_terminal_executable("/usr/bin/firefox"));
    }
}

const PASSWORD_CHARS: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

pub fn generate_supervisor_password(length: usize) -> String {
    let length = length.clamp(12, 20);
    let mut bytes = vec![0u8; length];
    getrandom::fill(&mut bytes).expect("failed to read random bytes for BIOS password");
    bytes
        .into_iter()
        .map(|byte| PASSWORD_CHARS[(byte as usize) % PASSWORD_CHARS.len()] as char)
        .collect()
}

pub fn zeroize_string(value: &mut String) {
    unsafe {
        let vec = value.as_mut_vec();
        for byte in vec.iter_mut() {
            *byte = 0;
        }
    }
    value.clear();
}

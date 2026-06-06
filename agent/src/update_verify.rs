use minisign_verify::{PublicKey, Signature};

const EMBEDDED_PUBLIC_KEY: &str = include_str!("../keys/update-signing.pub");

pub fn verify_release_asset(asset_bytes: &[u8], signature_text: &str) -> Result<(), String> {
    let public_key = PublicKey::decode(EMBEDDED_PUBLIC_KEY)
        .map_err(|error| format!("Invalid embedded update signing key: {error}"))?;
    let signature = Signature::decode(signature_text)
        .map_err(|error| format!("Invalid release signature format: {error}"))?;
    public_key
        .verify(asset_bytes, &signature, false)
        .map_err(|error| format!("Release signature verification failed: {error}"))
}

pub fn is_valid_release_version(version: &str) -> bool {
    let version = version.trim();
    if version.is_empty() || version.len() > 64 {
        return false;
    }
    let Some(tag) = version.strip_prefix('v') else {
        return false;
    };
    tag.chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | '_'))
}

pub fn is_valid_github_repo(repo: &str) -> bool {
    let repo = repo.trim();
    if repo.is_empty() || repo.len() > 128 {
        return false;
    }
    let Some((owner, name)) = repo.split_once('/') else {
        return false;
    };
    if owner.is_empty() || name.is_empty() {
        return false;
    }
    let valid_segment = |segment: &str| {
        !segment.is_empty()
            && segment != "."
            && segment != ".."
            && !segment.starts_with('.')
            && segment
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | '_'))
    };
    valid_segment(owner) && valid_segment(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_expected_release_tags() {
        assert!(is_valid_release_version("v0.1.0"));
        assert!(is_valid_release_version("v1.2.3-rc1"));
    }

    #[test]
    fn rejects_unsafe_release_tags() {
        assert!(!is_valid_release_version(""));
        assert!(!is_valid_release_version("0.1.0"));
        assert!(!is_valid_release_version("v../etc/passwd"));
        assert!(!is_valid_release_version("vtag/with/slash"));
    }

    #[test]
    fn accepts_expected_github_repos() {
        assert!(is_valid_github_repo("pantherale0/timekpr-webui"));
    }

    #[test]
    fn rejects_unsafe_github_repos() {
        assert!(!is_valid_github_repo(""));
        assert!(!is_valid_github_repo("owner-only"));
        assert!(!is_valid_github_repo("owner/repo/extra"));
        assert!(!is_valid_github_repo("../timekpr-webui"));
    }

    #[test]
    fn verifies_signed_release_fixture() {
        let asset = include_bytes!("testdata/signed-release.tar.gz");
        let signature = include_str!("testdata/signed-release.tar.gz.minisig");
        verify_release_asset(asset, signature).expect("fixture signature should verify");
    }
}

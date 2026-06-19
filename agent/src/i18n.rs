//! Embedded desktop notification strings from i18n agent catalogs.

use std::collections::HashMap;
use std::sync::OnceLock;

static DEFAULT_LOCALE: &str = "en";

#[derive(Debug)]
struct Catalog {
    _locale: String,
    strings: HashMap<String, String>,
}

fn flatten_desktop(value: &serde_json::Value, prefix: &str, out: &mut HashMap<String, String>) {
    match value {
        serde_json::Value::Object(map) => {
            for (key, nested) in map {
                let path = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}.{key}")
                };
                flatten_desktop(nested, &path, out);
            }
        }
        serde_json::Value::String(text) => {
            if !prefix.is_empty() {
                out.insert(prefix.to_string(), text.clone());
            }
        }
        _ => {}
    }
}

fn load_catalog(locale: &str) -> Catalog {
    let raw = match locale {
        "en" => include_str!("../resources/i18n/en.json"),
        _ => include_str!("../resources/i18n/en.json"),
    };
    let parsed: serde_json::Value =
        serde_json::from_str(raw).unwrap_or_else(|_| serde_json::json!({}));
    let mut strings = HashMap::new();
    if let Some(desktop) = parsed.get("desktop") {
        flatten_desktop(desktop, "", &mut strings);
    }
    Catalog {
        _locale: parsed
            .get("locale")
            .and_then(|v| v.as_str())
            .unwrap_or(locale)
            .to_string(),
        strings,
    }
}

fn active_catalog() -> &'static Catalog {
    static CACHE: OnceLock<Catalog> = OnceLock::new();
    CACHE.get_or_init(|| {
        let requested = std::env::var("GUARDIAN_LOCALE")
            .or_else(|_| std::env::var("LANG"))
            .unwrap_or_else(|_| DEFAULT_LOCALE.to_string());
        let locale = requested.split('.').next().unwrap_or(DEFAULT_LOCALE);
        let primary = locale.split('_').next().unwrap_or(locale);
        load_catalog(primary)
    })
}

pub fn t(key: &str) -> String {
    let catalog = active_catalog();
    catalog
        .strings
        .get(key)
        .cloned()
        .unwrap_or_else(|| key.to_string())
}

pub fn t_fmt(key: &str, params: &[(&str, &str)]) -> String {
    let mut text = t(key);
    for (name, value) in params {
        text = text.replace(&format!("{{{name}}}"), value);
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_domain_blocked_body_template() {
        let body = t("domain_blocked_body");
        assert!(body.contains("{domain}"));
    }
}

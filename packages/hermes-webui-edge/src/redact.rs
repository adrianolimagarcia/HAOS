use regex::Regex;
use std::sync::LazyLock;

static CRED_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}|glpat-[A-Za-z0-9_-]{20}|sk-[A-Za-z0-9]{32,}|sk-ant-[A-Za-z0-9_-]{32,}|xox[baprs]-[A-Za-z0-9_-]{10,}|discordapp\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+|hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}|key-[0-9a-zA-Z]{32}|AKIA[0-9A-Z]{16}|sq0atp-[0-9A-Za-z_-]{22}|sq0csp-[0-9A-Za-z_-]{43}|access_token\$production\$[0-9a-z]{16}\$[0-9a-f]{32}|hf_[A-Za-z0-9]{34,}|np_[A-Za-z0-9]{30,}|pplx-[A-Za-z0-9]{32,}|openrouter-[A-Za-z0-9]{32,}|dop_v1_[A-Za-z0-9]{64}|doo_v1_[A-Za-z0-9]{10,}|am_[A-Za-z0-9_-]{10,}|sk_[A-Za-z0-9_]{10,}|tvly-[A-Za-z0-9]{10,}|exa_[A-Za-z0-9]{10,}|gsk_[A-Za-z0-9]{10,}|syt_[A-Za-z0-9]{10,}|retaindb_[A-Za-z0-9]{10,}|hsk-[A-Za-z0-9]{10,}|mem0_[A-Za-z0-9]{10,}|brv_[A-Za-z0-9]{10,})\b").unwrap()
});

static AUTH_HDR_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(Authorization:\s*(?:Bearer|Bot)\s+)([^\s,\]\)]+)").unwrap()
});

static EMBEDDED_AWS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"AKIA[A-Z0-9]{16}").unwrap()
});

static PRIVKEY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?s)-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----").unwrap()
});

static ENV_RE_UNQUOTED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)([A-Z0-9_]{0,50}(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Z0-9_]{0,50})\s*=\s*([^\s]+)").unwrap()
});

fn mask_token(token: &str) -> String {
    let clean = token.trim_matches(|c| c == '\'' || c == '"');
    if clean.len() >= 18 {
        format!("{}...{}", &clean[..6], &clean[clean.len() - 4..])
    } else {
        "***".to_string()
    }
}

pub fn redact_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }

    // 1. Bearer / Auth Headers
    let text1 = AUTH_HDR_RE.replace_all(text, |caps: &regex::Captures| {
        format!("{}{}", &caps[1], mask_token(&caps[2]))
    });

    // 2. Private Keys
    let text2 = PRIVKEY_RE.replace_all(&text1, "[REDACTED PRIVATE KEY]");

    // 3. Known Credential Prefixes
    let text3 = CRED_RE.replace_all(&text2, |caps: &regex::Captures| {
        mask_token(&caps[0])
    });

    // 4. Embedded AWS
    let text4 = EMBEDDED_AWS_RE.replace_all(&text3, |caps: &regex::Captures| {
        mask_token(&caps[0])
    });

    // 5. ENV assignments
    let text5 = ENV_RE_UNQUOTED.replace_all(&text4, |caps: &regex::Captures| {
        let key = &caps[1];
        let raw_val = &caps[2];
        if raw_val.chars().any(|c| c.is_alphanumeric()) {
            format!("{key}={}", mask_token(raw_val))
        } else {
            caps[0].to_string()
        }
    });

    text5.into_owned()
}

pub fn redact_value(val: &mut serde_json::Value) {
    match val {
        serde_json::Value::String(s) => {
            let has_marker = s.contains("key")
                || s.contains("KEY")
                || s.contains("token")
                || s.contains("TOKEN")
                || s.contains("secret")
                || s.contains("SECRET")
                || s.contains("Bearer")
                || s.contains("bearer")
                || s.contains("sk-")
                || s.contains("ghp_")
                || s.contains("AKIA")
                || s.contains("PRIVATE");

            if s.len() > 8 && has_marker {
                *s = redact_text(s);
            }
        }
        serde_json::Value::Array(arr) => {
            for item in arr {
                redact_value(item);
            }
        }
        serde_json::Value::Object(map) => {
            let sensitive_keys = [
                "title",
                "content",
                "text",
                "arguments",
                "output",
                "result",
                "messages",
                "tool_calls",
            ];
            for (k, v) in map {
                if sensitive_keys.contains(&k.as_str()) {
                    redact_value(v);
                }
            }
        }
        _ => {}
    }
}

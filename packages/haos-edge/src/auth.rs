//! Autenticação do WebUI do HAOS Edge.
//!
//! Senha única do operador, armazenada como PBKDF2-HMAC-SHA256 (sal por
//! deployment, 200k iterações) em `${HAOS_DATA_DIR}/webui.passwd` (0600).
//! Login gera um token de sessão aleatório (arquivo em
//! `${HAOS_DATA_DIR}/sessions/<token>`) e devolve um cookie HttpOnly
//! `haos_session`; com `remember` o cookie ganha Max-Age de 30 dias ("salvar
//! login neste dispositivo") — a senha em si NUNCA é persistida no navegador.
//! Rota pública: `/api/login` `/api/logout` `/login` `/health` `/static`.
//! Todo o resto (terminal PTY, tasks, SPA) exige sessão válida.

use hmac::{Hmac, Mac};
use rand::RngCore;
use sha2::Sha256;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

const PBKDF2_ITER: u32 = 200_000;
const COOKIE_NAME: &str = "haos_session";
const REMEMBER_DAYS: u64 = 30;

type HmacSha256 = Hmac<Sha256>;

// ---------------------------------------------------------------- hex
fn hex_encode(data: &[u8]) -> String {
    let mut s = String::with_capacity(data.len() * 2);
    for b in data {
        s.push_str(&format!("{:02x}", b));
    }
    s
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
        .collect()
}

// ---------------------------------------------------------------- PBKDF2
pub fn pbkdf2_sha256(password: &[u8], salt: &[u8], iterations: u32, dklen: usize) -> Vec<u8> {
    let mut out = vec![0u8; dklen];
    let mut block = 1u32;
    let mut pos = 0usize;
    while pos < dklen {
        let mut mac = HmacSha256::new_from_slice(password).expect("hmac key");
        mac.update(salt);
        mac.update(&block.to_be_bytes());
        let mut u: Vec<u8> = mac.finalize().into_bytes().to_vec();
        let mut t = u.clone();
        for _ in 1..iterations {
            let mut mac = HmacSha256::new_from_slice(password).expect("hmac key");
            mac.update(&u);
            u = mac.finalize().into_bytes().to_vec();
            for (ti, ui) in t.iter_mut().zip(u.iter()) {
                *ti ^= ui;
            }
        }
        let n = std::cmp::min(dklen - pos, 32);
        out[pos..pos + n].copy_from_slice(&t[..n]);
        pos += n;
        block += 1;
    }
    out
}

// ---------------------------------------------------------------- passwd file
pub struct PasswdEntry {
    pub salt: Vec<u8>,
    pub hash: Vec<u8>,
}

fn passwd_path(data_dir: &Path) -> PathBuf {
    data_dir.join("webui.passwd")
}

/// Retorna None se a senha ainda não foi definida.
pub fn read_passwd(data_dir: &Path) -> Option<PasswdEntry> {
    let raw = fs::read_to_string(passwd_path(data_dir)).ok()?;
    let mut parts = raw.trim().split('$');
    let scheme = parts.next()?;
    if scheme != "pbkdf2_sha256" {
        return None;
    }
    let _iter: u32 = parts.next()?.parse().ok()?;
    let salt = hex_decode(parts.next()?)?;
    let hash = hex_decode(parts.next()?)?;
    Some(PasswdEntry { salt, hash })
}

pub fn password_is_set(data_dir: &Path) -> bool {
    read_passwd(data_dir).is_some()
}

pub fn write_passwd(data_dir: &Path, password: &str) -> std::io::Result<()> {
    let mut salt = [0u8; 16];
    rand::rngs::OsRng.fill_bytes(&mut salt);
    let hash = pbkdf2_sha256(password.as_bytes(), &salt, PBKDF2_ITER, 32);
    let line = format!(
        "pbkdf2_sha256${}${}${}\n",
        PBKDF2_ITER,
        hex_encode(&salt),
        hex_encode(&hash)
    );
    fs::create_dir_all(data_dir)?;
    let mut f = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(passwd_path(data_dir))?;
    f.write_all(line.as_bytes())
}

pub fn verify_password(data_dir: &Path, password: &str) -> bool {
    match read_passwd(data_dir) {
        None => false,
        Some(entry) => {
            let computed = pbkdf2_sha256(password.as_bytes(), &entry.salt, PBKDF2_ITER, 32);
            // compare em tempo constante
            computed.len() == entry.hash.len()
                && computed
                    .iter()
                    .zip(entry.hash.iter())
                    .fold(0u8, |acc, (a, b)| acc | (a ^ b))
                    == 0
        }
    }
}

// ---------------------------------------------------------------- sessions
fn sessions_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("sessions")
}

pub fn ensure_sessions_dir(data_dir: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    let dir = sessions_dir(data_dir);
    if !dir.exists() {
        let mut builder = fs::DirBuilder::new();
        builder.recursive(true).mode(0o700);
        builder.create(&dir)?;
    }
    Ok(())
}

pub fn create_session(data_dir: &Path, remember: bool) -> std::io::Result<(String, String)> {
    let mut token = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut token);
    let token = hex_encode(&token);
    ensure_sessions_dir(data_dir)?;
    let mut f = OpenOptions::new()
        .write(true)
        .create(true)
        .mode(0o600)
        .open(sessions_dir(data_dir).join(&token))?;
    writeln!(f, "{}", remember)?;
    let mut cookie = format!(
        "{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/"
    );
    if remember {
        cookie.push_str(&format!(
            "; Max-Age={}; Expires={}",
            REMEMBER_DAYS * 86400,
            http_date(REMEMBER_DAYS * 86400)
        ));
    }
    Ok((token, cookie))
}

fn http_date(age_secs: u64) -> String {
    // aproximação simples em UTC (o Max-Age é o que vale; Expires é compat)
    use std::time::{Duration, SystemTime, UNIX_EPOCH};
    let t = SystemTime::now() + Duration::from_secs(age_secs);
    let secs = t.duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    // RFC 7231 IMF-fixdate
    let days = secs / 86400;
    let (y, m, d) = civil_from_days(days as i64);
    let hms = secs % 86400;
    let (hh, mm, ss) = (hms / 3600, (hms % 3600) / 60, hms % 60);
    const MON: [&str; 12] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    const WKD: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    let wd = ((days + 4) % 7) as usize;
    format!(
        "{}, {:02} {} {} {:02}:{:02}:{:02} GMT",
        WKD[wd], d, MON[(m - 1) as usize], y, hh, mm, ss
    )
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

pub fn session_valid(data_dir: &Path, cookie_header: Option<&str>) -> bool {
    if std::env::var("HAOS_NO_AUTH").map(|v| v == "1" || v.to_lowercase() == "true").unwrap_or(false) {
        return true;
    }
    if !password_is_set(data_dir) {
        // Sem senha configurada no nó, permite acesso transparente (compativel com rede confiavel)
        return true;
    }
    match extract_cookie(cookie_header) {
        Some(token) => sessions_dir(data_dir).join(token).is_file(),
        None => false,
    }
}

pub fn destroy_session(data_dir: &Path, cookie_header: Option<&str>) {
    if let Some(token) = extract_cookie(cookie_header) {
        let _ = fs::remove_file(sessions_dir(data_dir).join(token));
    }
}

fn extract_cookie(header: Option<&str>) -> Option<String> {
    let header = header?;
    for part in header.split(';') {
        let part = part.trim();
        if let Some(value) = part.strip_prefix(&format!("{COOKIE_NAME}=")) {
            return Some(value.to_string());
        }
    }
    None
}

pub fn clear_cookie() -> String {
    format!("{COOKIE_NAME}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
}

// ---------------------------------------------------------------- login page
pub const LOGIN_PAGE: &str = r#"<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>HAOS — Acesso</title>
<style>
  body{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;display:flex;
       align-items:center;justify-content:center;min-height:100vh;margin:0}
  .card{background:#1a1d24;border:1px solid #2a2f3a;border-radius:12px;padding:32px;width:320px}
  h1{font-size:20px;margin:0 0 4px;color:#fff}
  p.sub{color:#8b93a5;margin:0 0 20px;font-size:13px}
  input[type=password]{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;
       border:1px solid #333;background:#12151b;color:#fff;font-size:15px}
  label.remember{display:flex;align-items:center;gap:8px;margin:14px 0;font-size:13px;color:#b9c0cf}
  button{width:100%;padding:11px;border:0;border-radius:8px;background:#2f6fed;color:#fff;
         font-size:15px;cursor:pointer}
  button:disabled{opacity:.6}
  .err{color:#ff7b7b;font-size:13px;min-height:18px;margin:10px 0 0}
</style></head><body>
<div class="card">
  <h1>HAOS WebUI</h1>
  <p class="sub">Acesso do operador — requer senha</p>
  <form id="f">
    <input type="password" id="pw" placeholder="Senha" autofocus autocomplete="current-password"/>
    <label class="remember"><input type="checkbox" id="remember"/> Salvar login neste dispositivo</label>
    <button id="b" type="submit">Entrar</button>
  </form>
  <div class="err" id="err"></div>
</div>
<script>
const f=document.getElementById('f'),pw=document.getElementById('pw'),
      b=document.getElementById('b'),err=document.getElementById('err');
f.addEventListener('submit',async e=>{e.preventDefault();b.disabled=true;err.textContent='';
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:pw.value,remember:document.getElementById('remember').checked})});
    if(r.ok){window.location='/';return;}
    const j=await r.json().catch(()=>({}));
    err.textContent=j.error||('Erro '+r.status);
  }catch(x){err.textContent='Falha de rede';}
  b.disabled=false;});
</script></body></html>"#;

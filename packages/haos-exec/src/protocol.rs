use serde::{Deserialize, Serialize};

pub const MAX_REQUEST_BYTES: usize = 1024 * 1024;
#[derive(Deserialize)] pub struct Request { pub id: serde_json::Value, pub method: String, pub params: Option<Params> }
#[derive(Deserialize)] pub struct Params { pub command: Option<String>, pub argv: Option<Vec<String>>, pub cwd: Option<String>, pub env: Option<std::collections::BTreeMap<String,String>>, pub timeout_ms: Option<u64>, pub max_output_bytes: Option<usize>, pub max_pids: Option<u64>, pub max_memory_bytes: Option<u64>, pub max_cpu_seconds: Option<u64>, pub stdin: Option<String> }
#[derive(Serialize)] pub struct Response { pub id: serde_json::Value, pub result: Option<ResultBody>, pub error: Option<ErrorBody> }
#[derive(Serialize)] pub struct ResultBody { pub output: String, pub returncode: i32, pub truncated: bool, pub timed_out: bool, pub duration_ms: u128 }
#[derive(Serialize)] pub struct ErrorBody { pub code: String, pub message: String }
pub fn error(id: serde_json::Value, code: &str, message: &str) -> Response { Response { id, result: None, error: Some(ErrorBody { code: code.into(), message: message.into() }) } }
pub fn validate(p: &Params) -> Result<(), &'static str> {
    if let Some(vars)=&p.env { if vars.len()>128 || vars.iter().any(|(k,v)| k.is_empty() || k.contains('=') || k.contains('\0') || v.contains('\0') || k.len()>256 || v.len()>64*1024) { return Err("environment entry invalid"); } }
    if let Some(argv)=&p.argv {
        if argv.is_empty() || argv.len()>128 { return Err("argv count out of range"); }
        if argv.iter().any(|a| a.len()>64*1024 || a.contains('\0')) { return Err("argv item invalid"); }
    }
    if let Some(command)=&p.command { if command.len()>1024*1024 || command.contains('\0') { return Err("command invalid"); } }
    if let Some(stdin)=&p.stdin { if stdin.len()>16*1024*1024 { return Err("stdin too large"); } }
    if p.timeout_ms.unwrap_or(120_000)>86_400_000 { return Err("timeout out of range"); }
    if p.max_output_bytes.unwrap_or(1_048_576)>64*1024*1024 { return Err("output limit out of range"); }
    Ok(())
}

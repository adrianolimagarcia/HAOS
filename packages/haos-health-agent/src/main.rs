use std::{io::{self, Write}};
use haos_health_agent::{snapshot, to_bounded_json};
fn main() {
    let readiness = std::env::var("HAOS_HEALTH_READY").map(|v| v != "0").unwrap_or(true);
    let response = match to_bounded_json(&snapshot(readiness)) { Ok(v) => v, Err(e) => format!("{{\"error\":{}}}", serde_json::to_string(&e).unwrap()) };
    println!("{response}"); let _=io::stdout().flush();
}

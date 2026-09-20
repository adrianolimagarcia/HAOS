use serde::Serialize;
use std::fs;

pub const MAX_JSON_BYTES: usize = 64 * 1024;
#[derive(Debug, Serialize, Clone)]
pub struct Snapshot {
    pub schema: &'static str,
    pub liveness: bool,
    pub readiness: bool,
    pub pid: u32,
    pub uptime_ms: u128,
    pub memory_bytes: Option<u64>,
    pub load_average: Option<[f64; 3]>,
    pub cgroup: Option<CgroupSnapshot>,
}
#[derive(Debug, Serialize, Clone)]
pub struct CgroupSnapshot { pub memory_current: Option<u64>, pub memory_max: Option<String>, pub pids_current: Option<u64>, pub pids_max: Option<String> }

pub fn liveness() -> bool { true }
pub fn snapshot(readiness: bool) -> Snapshot {
    Snapshot { schema: "haos.health.v1", liveness: true, readiness, pid: std::process::id(), uptime_ms: uptime_ms(), memory_bytes: memory_bytes(), load_average: load_average(), cgroup: cgroup_snapshot() }
}
pub fn to_bounded_json(snapshot: &Snapshot) -> Result<String, String> {
    let text=serde_json::to_string(snapshot).map_err(|e| e.to_string())?;
    if text.len()>MAX_JSON_BYTES { return Err("health snapshot exceeds maximum size".into()); }
    Ok(text)
}
fn uptime_ms() -> u128 { static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new(); START.get_or_init(std::time::Instant::now).elapsed().as_millis() }
fn memory_bytes() -> Option<u64> {
    #[cfg(target_os="linux")] { let text=fs::read_to_string("/proc/self/status").ok()?; return text.lines().find_map(|l| l.strip_prefix("VmRSS:")?.split_whitespace().nth(0)?.parse::<u64>().ok().map(|v|v*1024)); }
    #[cfg(not(target_os="linux"))] { None }
}
fn load_average() -> Option<[f64;3]> {
    #[cfg(unix)] { let text=fs::read_to_string("/proc/loadavg").ok()?; let mut it=text.split_whitespace().take(3).map(|x|x.parse().ok()); return Some([it.next()??,it.next()??,it.next()??]); }
    #[cfg(not(unix))] { None }
}
fn read_value(path: &str) -> Option<String> { fs::read_to_string(path).ok().map(|x|x.trim().to_string()) }
fn cgroup_snapshot() -> Option<CgroupSnapshot> {
    #[cfg(target_os="linux")] { let current=read_value("/sys/fs/cgroup/memory.current").and_then(|x|x.parse().ok()); let max=read_value("/sys/fs/cgroup/memory.max"); let pc=read_value("/sys/fs/cgroup/pids.current").and_then(|x|x.parse().ok()); let pm=read_value("/sys/fs/cgroup/pids.max"); if current.is_none()&&max.is_none()&&pc.is_none()&&pm.is_none(){None}else{Some(CgroupSnapshot{memory_current:current,memory_max:max,pids_current:pc,pids_max:pm})} }
    #[cfg(not(target_os="linux"))] { None }
}

#[cfg(test)] mod tests { use super::*; #[test] fn live(){assert!(liveness());} #[test] fn bounded(){let s=to_bounded_json(&snapshot(true)).unwrap(); assert!(s.len()<=MAX_JSON_BYTES); assert!(s.contains("haos.health.v1"));} #[test] fn states(){assert!(snapshot(false).liveness); assert!(!snapshot(false).readiness); assert!(snapshot(true).readiness);} }

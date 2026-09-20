#[cfg(unix)]
pub fn apply(cpu: Option<u64>, memory: Option<u64>, pids: Option<u64>) {
    use nix::sys::resource::{setrlimit, Resource};
    if let Some(v) = cpu { let _ = setrlimit(Resource::RLIMIT_CPU, v, v); }
    if let Some(v) = memory { let _ = setrlimit(Resource::RLIMIT_AS, v, v); }
    if let Some(v) = pids { let _ = setrlimit(Resource::RLIMIT_NPROC, v, v); }
}
#[cfg(not(unix))]
pub fn apply(_: Option<u64>, _: Option<u64>, _: Option<u64>) {}

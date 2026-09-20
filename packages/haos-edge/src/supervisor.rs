//! HAOS MCP Death Supervisor in native Rust.
//!
//! Replaces tools/mcp_death_supervisor.py (saving ~4.5 MB RAM per Hermes instance).
//! Reads "register <pgid>" and "unregister <pgid>" on stdin.
//! On EOF (Hermes exit/crash), sends SIGTERM, waits up to 3s grace, then SIGKILL.

use nix::sys::signal::{killpg, Signal};
use nix::unistd::{getpgid, Pid};
use std::collections::HashSet;
use std::io::{BufRead, BufReader};
use std::time::{Duration, Instant};

const TERM_GRACE: Duration = Duration::from_millis(3000);
const REAP_POLL: Duration = Duration::from_millis(100);

pub fn run_mcp_supervisor(parent_pgid_val: i32) -> Result<(), Box<dyn std::error::Error>> {
    let own_pgid = getpgid(None)?.as_raw();
    if own_pgid == parent_pgid_val {
        eprintln!("haos mcp-supervisor: refusing to run inside parent process group");
        std::process::exit(2);
    }

    // Ignore SIGINT and SIGHUP so pipe EOF is our sole termination signal
    unsafe {
        libc::signal(libc::SIGINT, libc::SIG_IGN);
        libc::signal(libc::SIGHUP, libc::SIG_IGN);
    }

    let mut registered: HashSet<i32> = HashSet::new();
    let stdin = std::io::stdin();
    let mut reader = BufReader::new(stdin.lock());
    let mut line = String::with_capacity(256);

    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break, // EOF: parent process is dead!
            Ok(_) => {
                if !line.ends_with('\n') {
                    continue;
                }
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() != 2 {
                    continue;
                }
                let verb = parts[0];
                if let Ok(pgid) = parts[1].parse::<i32>() {
                    if pgid <= 1 || pgid == own_pgid || pgid == parent_pgid_val {
                        continue;
                    }
                    if verb == "register" {
                        registered.insert(pgid);
                    } else if verb == "unregister" {
                        registered.remove(&pgid);
                    }
                }
            }
            Err(_) => break,
        }
    }

    reap(registered);
    Ok(())
}

fn reap(pgids: HashSet<i32>) {
    if pgids.is_empty() {
        return;
    }

    let mut alive = HashSet::new();
    for pgid in pgids {
        let pid = Pid::from_raw(pgid);
        if killpg(pid, Signal::SIGTERM).is_ok() {
            alive.insert(pgid);
        }
    }

    let deadline = Instant::now() + TERM_GRACE;
    while !alive.is_empty() && Instant::now() < deadline {
        std::thread::sleep(REAP_POLL);
        alive.retain(|&pgid| {
            let pid = Pid::from_raw(pgid);
            killpg(pid, None).is_ok()
        });
    }

    for pgid in alive {
        let pid = Pid::from_raw(pgid);
        let _ = killpg(pid, Signal::SIGKILL);
    }
}

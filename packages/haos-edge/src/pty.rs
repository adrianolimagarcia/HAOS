use nix::pty::{openpty, OpenptyResult};
use nix::sys::signal::{killpg, Signal};
use nix::unistd::{close, dup2, execvp, fork, setsid, ForkResult, Pid};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::ffi::CString;
use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{FromRawFd, IntoRawFd, RawFd};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct SessionInfo {
    pub session_id: String,
    pub pid: i32,
    pub running: bool,
    pub started_at: u64,
}

pub struct PtySession {
    pub id: String,
    pub pid: Pid,
    pub master_fd: RawFd,
    pub master_file: Arc<Mutex<File>>,
    pub buffer: Arc<Mutex<Vec<u8>>>,
    pub running: Arc<Mutex<bool>>,
    pub last_drain: Arc<Mutex<Instant>>,
}

impl PtySession {
    pub fn new(cwd: Option<&str>, env_vars: Option<HashMap<String, String>>) -> Result<Self, String> {
        let OpenptyResult { master, slave } = openpty(None, None).map_err(|e| format!("openpty failed: {e}"))?;

        let master_raw: RawFd = master.into_raw_fd();
        let slave_raw: RawFd = slave.into_raw_fd();
        let id = format!("term-{}", &uuid_simple()[..8]);

        match unsafe { fork() } {
            Ok(ForkResult::Parent { child }) => {
                let _ = close(slave_raw); // Close slave in parent

                // Set master to non-blocking
                unsafe {
                    let flags = libc::fcntl(master_raw, libc::F_GETFL);
                    libc::fcntl(master_raw, libc::F_SETFL, flags | libc::O_NONBLOCK);
                }

                let master_file = Arc::new(Mutex::new(unsafe { File::from_raw_fd(master_raw) }));
                let buffer = Arc::new(Mutex::new(Vec::new()));
                let running = Arc::new(Mutex::new(true));
                let last_drain = Arc::new(Mutex::new(Instant::now()));

                // Reader thread pulling PTY output into ring buffer
                let m_file = Arc::clone(&master_file);
                let buf_clone = Arc::clone(&buffer);
                let run_clone = Arc::clone(&running);

                std::thread::spawn(move || {
                    let mut temp_buf = [0u8; 4096];
                    loop {
                        let read_res = {
                            let mut f = m_file.lock().unwrap();
                            f.read(&mut temp_buf)
                        };
                        match read_res {
                            Ok(0) => {
                                *run_clone.lock().unwrap() = false;
                                break;
                            }
                            Ok(n) => {
                                let mut b = buf_clone.lock().unwrap();
                                if b.len() > 100_000 {
                                    b.drain(0..30_000); // Ring buffer eviction
                                }
                                b.extend_from_slice(&temp_buf[..n]);
                            }
                            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                                std::thread::sleep(Duration::from_millis(15));
                            }
                            Err(_) => {
                                *run_clone.lock().unwrap() = false;
                                break;
                            }
                        }
                    }
                });

                Ok(PtySession {
                    id,
                    pid: child,
                    master_fd: master_raw,
                    master_file,
                    buffer,
                    running,
                    last_drain,
                })
            }
            Ok(ForkResult::Child) => {
                let _ = close(master_raw);

                // Set session leader and control terminal
                let _ = setsid();
                unsafe {
                    libc::ioctl(slave_raw, libc::TIOCSCTTY, 0);
                    let _ = dup2(slave_raw, 0);
                    let _ = dup2(slave_raw, 1);
                    let _ = dup2(slave_raw, 2);
                    if slave_raw > 2 {
                        let _ = close(slave_raw);
                    }
                }

                if let Some(d) = cwd {
                    let _ = std::env::set_current_dir(d);
                }

                if let Some(vars) = env_vars {
                    for (k, v) in vars {
                        std::env::set_var(k, v);
                    }
                }
                std::env::set_var("TERM", "xterm-256color");
                std::env::set_var("HAOS_PTY", "1");

                let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/bash".into());
                let c_shell = CString::new(shell.clone()).unwrap();
                let c_dash_i = CString::new("-i").unwrap();

                let args = [c_shell.clone(), c_dash_i];
                let _ = execvp(&c_shell, &args);
                std::process::exit(1);
            }
            Err(e) => Err(format!("fork failed: {e}")),
        }
    }

    pub fn write_input(&self, data: &[u8]) -> Result<(), String> {
        let mut f = self.master_file.lock().unwrap();
        f.write_all(data).map_err(|e| format!("write error: {e}"))?;
        f.flush().map_err(|e| format!("flush error: {e}"))
    }

    pub fn drain(&self) -> (String, bool) {
        let mut drain_time = self.last_drain.lock().unwrap();
        *drain_time = Instant::now();

        let mut b = self.buffer.lock().unwrap();
        let out = String::from_utf8_lossy(&b).to_string();
        b.clear();
        let running = *self.running.lock().unwrap();
        (out, running)
    }

    pub fn resize(&self, rows: u16, cols: u16) -> Result<(), String> {
        #[repr(C)]
        struct Winsize {
            ws_row: u16,
            ws_col: u16,
            ws_xpixel: u16,
            ws_ypixel: u16,
        }
        let ws = Winsize {
            ws_row: rows.clamp(8, 80),
            ws_col: cols.clamp(20, 240),
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        unsafe {
            if libc::ioctl(self.master_fd, libc::TIOCSWINSZ, &ws) == -1 {
                return Err("ioctl TIOCSWINSZ failed".into());
            }
        }
        Ok(())
    }

    pub fn kill(&self) {
        *self.running.lock().unwrap() = false;
        // Kill entire process group to avoid any orphan / zombie
        let _ = killpg(self.pid, Signal::SIGKILL);
    }
}

pub struct PtyManager {
    sessions: Arc<Mutex<HashMap<String, Arc<PtySession>>>>,
}

impl PtyManager {
    pub fn new() -> Self {
        let manager = PtyManager {
            sessions: Arc::new(Mutex::new(HashMap::new())),
        };

        // Background reaper for stale sessions
        let sessions_clone = Arc::clone(&manager.sessions);
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(60)).await;
                let mut map = sessions_clone.lock().unwrap();
                let mut dead = Vec::new();
                for (id, s) in map.iter() {
                    let idle = s.last_drain.lock().unwrap().elapsed();
                    let running = *s.running.lock().unwrap();
                    if !running && idle > Duration::from_secs(60) {
                        dead.push(id.clone());
                    } else if idle > Duration::from_secs(1800) {
                        s.kill();
                        dead.push(id.clone());
                    }
                }
                for id in dead {
                    map.remove(&id);
                }
            }
        });

        manager
    }

    pub fn start(&self, cwd: Option<&str>, env_vars: Option<HashMap<String, String>>) -> Result<Arc<PtySession>, String> {
        let session = PtySession::new(cwd, env_vars)?;
        let arc = Arc::new(session);
        let mut map = self.sessions.lock().unwrap();
        if map.len() >= 8 {
            // Evict oldest
            if let Some(first_key) = map.keys().next().cloned() {
                if let Some(old) = map.remove(&first_key) {
                    old.kill();
                }
            }
        }
        map.insert(arc.id.clone(), Arc::clone(&arc));
        Ok(arc)
    }

    pub fn get(&self, id: &str) -> Option<Arc<PtySession>> {
        let map = self.sessions.lock().unwrap();
        map.get(id).cloned()
    }

    pub fn remove(&self, id: &str) -> bool {
        let mut map = self.sessions.lock().unwrap();
        if let Some(s) = map.remove(id) {
            s.kill();
            true
        } else {
            false
        }
    }

    pub fn list(&self) -> Vec<SessionInfo> {
        let map = self.sessions.lock().unwrap();
        map.values()
            .map(|s| SessionInfo {
                session_id: s.id.clone(),
                pid: s.pid.as_raw(),
                running: *s.running.lock().unwrap(),
                started_at: 0,
            })
            .collect()
    }
}

fn uuid_simple() -> String {
    use std::time::SystemTime;
    let nanos = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("{:x}", nanos)
}

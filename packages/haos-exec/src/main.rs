mod cgroup;
mod limits;
mod output;
mod process;
mod protocol;
mod workspace;

use protocol::{error, Params, Request, Response, ResultBody, MAX_REQUEST_BYTES};
use std::{
    env,
    io::{self, BufRead, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

fn main() {
    if std::env::args().any(|a| a == "--version") {
        println!("hermes-exec 0.1.0");
        return;
    }
    for line in io::stdin().lock().lines() {
        let line = match line {
            Ok(v) => v,
            Err(_) => break,
        };
        if line.len() > MAX_REQUEST_BYTES {
            let r = error(
                serde_json::Value::Null,
                "request_too_large",
                "JSON request exceeds 1 MiB",
            );
            println!("{}", serde_json::to_string(&r).unwrap());
            let _ = io::stdout().flush();
            continue;
        }
        if line.trim().is_empty() {
            continue;
        };
        let r = match serde_json::from_str::<Request>(&line) {
            Ok(x) => handle(x),
            Err(e) => error(serde_json::Value::Null, "invalid_request", &e.to_string()),
        };
        println!("{}", serde_json::to_string(&r).unwrap());
        let _ = io::stdout().flush();
    }
}
fn handle(req: Request) -> Response {
    let p = match req.params {
        Some(x) => x,
        None => return err(req.id, "invalid_params", "params are required"),
    };
    if let Err(msg) = protocol::validate(&p) {
        return err(req.id, "invalid_params", msg);
    };
    if req.method != "exec" {
        return err(req.id, "method_not_found", "only exec is supported");
    };
    let root = match env::var("HERMES_EXEC_ROOT") {
        Ok(x) => PathBuf::from(x),
        Err(_) => return err(req.id, "configuration", "HERMES_EXEC_ROOT is required"),
    };
    let group_mode = match cgroup::Mode::from_env() {
        Ok(x) => x,
        Err(e) => return err(req.id.clone(), "cgroup_config", &e),
    };
    let group = match cgroup::Group::create(
        group_mode,
        0,
        p.max_memory_bytes,
        p.max_pids,
        p.max_cpu_seconds,
    ) {
        Ok(x) => x,
        Err(e) => return err(req.id.clone(), "cgroup_setup", &e),
    };
    let cwd = match workspace::resolve(&root, p.cwd.as_deref().unwrap_or(".")) {
        Ok(x) => x,
        Err(e) => return err(req.id, "unsafe_cwd", &e),
    };
    let timeout = Duration::from_millis(p.timeout_ms.unwrap_or(120_000).min(86_400_000));
    let cap = p.max_output_bytes.unwrap_or(1_048_576).max(1);
    let mut c = if let Some(argv) = p.argv.as_ref() {
        if argv.is_empty() {
            return err(req.id, "invalid_params", "argv must not be empty");
        };
        let mut x = Command::new(&argv[0]);
        x.args(&argv[1..]);
        x
    } else {
        let command = match p.command.as_ref() {
            Some(x) => x,
            None => return err(req.id, "invalid_params", "command or argv is required"),
        };
        if cfg!(windows) {
            let mut x = Command::new("cmd");
            x.args(["/C", command]);
            x
        } else {
            let mut x = Command::new("/bin/sh");
            x.args(["-c", command]);
            x
        }
    };
    if let Some(vars) = p.env.as_ref() {
        c.env_clear();
        c.envs(vars);
    }
    c.current_dir(cwd)
        .stdin(if p.stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let cpu = p.max_cpu_seconds;
        let mem = p.max_memory_bytes;
        let pids = p.max_pids;
        unsafe {
            c.pre_exec(move || {
                let q = Params {
                    command: None,
                    argv: None,
                    cwd: None,
                    env: None,
                    timeout_ms: None,
                    max_output_bytes: None,
                    max_pids: pids,
                    max_memory_bytes: mem,
                    max_cpu_seconds: cpu,
                    stdin: None,
                };
                apply_limits(&q);
                #[cfg(target_os = "linux")]
                {
                    process::configure_parent_death();
                }
                Ok(())
            });
        }
        c.process_group(0);
    }
    let mut child = match c.spawn() {
        Ok(x) => x,
        Err(e) => return err(req.id, "spawn", &e.to_string()),
    };
    if let Some(input) = p.stdin {
        if let Some(mut s) = child.stdin.take() {
            let _ = s.write_all(input.as_bytes());
        }
    }
    let pid = child.id();
    if let Some(ref g) = group {
        if let Err(e) = g.attach(pid) {
            let _ = child.kill();
            let _ = child.wait();
            return err(req.id.clone(), "cgroup_attach", &e);
        }
    }
    let mut pidfd = process::try_pidfd_open(pid);
    let start = Instant::now();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let oh = output::drain_bounded(stdout, cap);
    let eh = output::drain_bounded(stderr, cap);
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let o = child.wait();
        let _ = tx.send(o);
    });
    let status = match rx.recv_timeout(timeout) {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => {
            if let Some(fd) = pidfd.take() {
                process::close_pidfd(fd);
            }
            return err(req.id, "wait", &e.to_string());
        }
        Err(mpsc::RecvTimeoutError::Timeout) => {
            if let Some(fd) = pidfd {
                let _ = process::pidfd_kill(fd);
            }
            if let Some(ref g) = group {
                if let Err(e) = g.kill() {
                    if let Some(fd) = pidfd.take() {
                        process::close_pidfd(fd);
                    }
                    return err(req.id.clone(), "cgroup_kill", &e);
                }
            }
            kill_pid(pid);
            let _ = rx.recv_timeout(Duration::from_secs(2));
            let _ = oh.join();
            let _ = eh.join();
            if let Some(fd) = pidfd.take() {
                process::close_pidfd(fd);
            }
            return ok(
                req.id,
                "[command timed out]".into(),
                124,
                true,
                false,
                start.elapsed().as_millis(),
            );
        }
        Err(_) => return err(req.id, "wait", "worker disconnected"),
    };
    if let Some(fd) = pidfd {
        process::close_pidfd(fd);
    }
    let (mut out, tr1) = oh.join().unwrap_or_default();
    let (errout, tr2) = eh.join().unwrap_or_default();
    out.extend(errout);
    let trunc = tr1 || tr2;
    ok(
        req.id,
        String::from_utf8_lossy(&out).into_owned(),
        status.code().unwrap_or(1),
        false,
        trunc,
        start.elapsed().as_millis(),
    )
}
#[cfg(unix)]
fn apply_limits(p: &Params) {
    limits::apply(p.max_cpu_seconds, p.max_memory_bytes, p.max_pids);
}
#[cfg(not(unix))]
fn apply_limits(_: &Params) {}
#[allow(dead_code)]
fn safe_cwd(root: &Path, requested: &str) -> Result<PathBuf, String> {
    workspace::resolve(root, requested)
}

fn kill_pid(pid: u32) {
    process::kill_tree(pid)
}
fn ok(
    id: serde_json::Value,
    output: String,
    returncode: i32,
    timed_out: bool,
    truncated: bool,
    duration_ms: u128,
) -> Response {
    Response {
        id,
        result: Some(ResultBody {
            output,
            returncode,
            timed_out,
            truncated,
            duration_ms,
        }),
        error: None,
    }
}
fn err(id: serde_json::Value, code: &str, message: &str) -> Response {
    error(id, code, message)
}

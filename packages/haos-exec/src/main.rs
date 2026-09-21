mod cgroup;
mod limits;
mod output;
mod process;
mod protocol;
mod workspace;

use protocol::{error, Params, Request, Response, ResultBody, MAX_REQUEST_BYTES};
use std::{
    env,
    io::{self, BufRead, Read, Write},
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
    if let Some(cmd) = std::env::args().nth(1) {
        if cmd == "check-command" {
            let target_cmd = std::env::args().nth(2).unwrap_or_default();
            let res = check_command_fast(&target_cmd);
            println!("{}", serde_json::to_string(&res).unwrap());
            return;
        } else if cmd == "pack-output" {
            let input_file = std::env::args().nth(2).filter(|s| !s.is_empty() && s != "-");
            let identifier = std::env::args().nth(3).unwrap_or_else(|| "run".to_string());
            let log_dir = std::env::args().nth(4).unwrap_or_else(|| "/tmp/haos_logs".to_string());
            let max_lines: usize = std::env::args().nth(5).and_then(|s| s.parse().ok()).unwrap_or(60);
            let max_chars: usize = std::env::args().nth(6).and_then(|s| s.parse().ok()).unwrap_or(4000);
            let head_lines: usize = 15;
            let tail_lines: usize = 25;

            let content = match input_file {
                Some(ref p) => std::fs::read_to_string(p).unwrap_or_default(),
                None => {
                    let mut s = String::new();
                    let _ = std::io::stdin().read_to_string(&mut s);
                    s
                }
            };

            let res = pack_output_fast(&content, &identifier, &log_dir, max_lines, max_chars, head_lines, tail_lines);
            println!("{}", serde_json::to_string(&res).unwrap());
            return;
        } else if cmd == "snip-messages" {
            // Snip & AST De-duplication de mensagens em Rust Ultra SOTA (Anthropic 2026 pattern)
            let mut s = String::new();
            let _ = std::io::stdin().read_to_string(&mut s);
            let res = snip_messages_fast(&s);
            println!("{}", serde_json::to_string(&res).unwrap());
            return;
        } else if cmd == "isolate-run" {
            // Isolamento de processo por cgroups v2 / rlimits (Frente 2)
            // Uso: hermes-exec isolate-run <max_mem_mb> <max_pids> <max_cpu_sec> -- <command...>
            let args: Vec<String> = std::env::args().collect();
            let sep_idx = args.iter().position(|a| a == "--").unwrap_or(5);
            let max_mem_mb: Option<u64> = args.get(2).and_then(|s| s.parse().ok());
            let max_pids: Option<u64> = args.get(3).and_then(|s| s.parse().ok());
            let max_cpu: Option<u64> = args.get(4).and_then(|s| s.parse().ok());
            let cmd_to_run = &args[(sep_idx + 1)..];
            if cmd_to_run.is_empty() {
                eprintln!("Usage: hermes-exec isolate-run <mem_mb> <pids> <cpu_s> -- <cmd...>");
                std::process::exit(1);
            }
            run_isolated(cmd_to_run, max_mem_mb, max_pids, max_cpu);
            return;
        } else if cmd == "sanitize-path" {
            // Path Security & Jail Sanitizer (Frente 2)
            // Uso: hermes-exec sanitize-path <root_dir> <target_path>
            let root = std::env::args().nth(2).unwrap_or_default();
            let target = std::env::args().nth(3).unwrap_or_default();
            let res = sanitize_path_fast(&root, &target);
            println!("{}", serde_json::to_string(&res).unwrap());
            return;
        } else if cmd == "hash-context" {
            // SIMD Context Hasher & Rolling Prefix Fingerprinter (Frente 3)
            // Uso: hermes-exec hash-context <segments> (lê stdin)
            let segments: usize = std::env::args().nth(2).and_then(|s| s.parse().ok()).unwrap_or(4);
            let mut s = String::new();
            let _ = std::io::stdin().read_to_string(&mut s);
            let res = hash_context_fast(&s, segments);
            println!("{}", serde_json::to_string(&res).unwrap());
            return;
        } else if cmd == "supervise-tree" {
            // Process Tree Subreaper & Zombie Killer (Frente 3)
            // Uso: hermes-exec supervise-tree <timeout_sec> -- <command...>
            let args: Vec<String> = std::env::args().collect();
            let sep_idx = args.iter().position(|a| a == "--").unwrap_or(3);
            let timeout_s: u64 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(3600);
            let cmd_to_run = &args[(sep_idx + 1)..];
            if cmd_to_run.is_empty() {
                eprintln!("Usage: hermes-exec supervise-tree <timeout_sec> -- <cmd...>");
                std::process::exit(1);
            }
            supervise_process_tree(cmd_to_run, timeout_s);
            return;
        } else if cmd == "compress-context" {
            // Decision-Preserving Context Compression (A3, Emenda 30)
            // Uso: hermes-exec compress-context [max_chars] (lê texto de stdin)
            let max_chars: Option<usize> = std::env::args().nth(2).and_then(|s| s.parse().ok());
            let mut s = String::new();
            let _ = std::io::stdin().read_to_string(&mut s);
            let res = compress_context_fast(&s, max_chars);
            print!("{}", res);
            return;
        }
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
fn snip_messages_fast(json_str: &str) -> serde_json::Value {
    let Ok(mut msgs) = serde_json::from_str::<Vec<serde_json::Value>>(json_str) else {
        return serde_json::json!({
            "ok": false,
            "error": "invalid_json_input",
            "messages": serde_json::Value::Null
        });
    };

    let total = msgs.len();
    if total <= 6 {
        return serde_json::json!({
            "ok": true,
            "snipped_count": 0,
            "messages": msgs
        });
    }

    // Identifica tool results intermediários obsoletos (ex: saídas de grep/read antigas)
    // Mantém intacto o sistema (índice 0), a mensagem do usuário (índice 1) e os últimos 4 turnos.
    let protected_head = 2.min(total);
    let protected_tail_start = if total > 4 { total - 4 } else { total };

    let mut snipped_count = 0;
    let mut seen_tool_calls = std::collections::HashSet::new();

    // Varre de trás para frente para manter a observação mais recente de ferramentas repetidas
    for i in (protected_head..protected_tail_start).rev() {
        if let Some(obj) = msgs[i].as_object_mut() {
            let role = obj.get("role").and_then(|r| r.as_str()).unwrap_or("");
            if role == "tool" {
                let tool_name = obj.get("name").and_then(|n| n.as_str()).unwrap_or("tool");
                let call_sig = format!("{}:{}", tool_name, obj.get("tool_call_id").and_then(|c| c.as_str()).unwrap_or(""));
                
                if seen_tool_calls.contains(&call_sig) {
                    // Já temos uma observação mais recente desta ferramenta ou chamada
                    let orig_content = obj.get("content").and_then(|c| c.as_str()).unwrap_or("");
                    if orig_content.len() > 120 {
                        let short_summary = format!("[Observation Snipped: output anterior da ferramenta '{}' omitido para economia de contexto]", tool_name);
                        obj.insert("content".to_string(), serde_json::Value::String(short_summary));
                        snipped_count += 1;
                    }
                } else {
                    seen_tool_calls.insert(call_sig);
                    // Se a saída for massiva mesmo não duplicada, comprime se for intermediária
                    let orig_content = obj.get("content").and_then(|c| c.as_str()).unwrap_or("");
                    if orig_content.lines().count() > 40 {
                        let lines: Vec<&str> = orig_content.lines().collect();
                        let collapsed = format!(
                            "{}\n... [{} linhas intermediárias suprimidas via Native Snip] ...\n{}",
                            lines[..5].join("\n"),
                            lines.len() - 10,
                            lines[lines.len() - 5..].join("\n")
                        );
                        obj.insert("content".to_string(), serde_json::Value::String(collapsed));
                        snipped_count += 1;
                    }
                }
            }
        }
    }

    serde_json::json!({
        "ok": true,
        "snipped_count": snipped_count,
        "messages": msgs
    })
}

fn run_isolated(cmd_args: &[String], max_mem_mb: Option<u64>, max_pids: Option<u64>, max_cpu: Option<u64>) {
    let mut command = Command::new(&cmd_args[0]);
    if cmd_args.len() > 1 {
        command.args(&cmd_args[1..]);
    }

    // Aplica rlimits nativos no processo filho
    let mem_bytes = max_mem_mb.map(|m| m * 1024 * 1024);
    limits::apply(max_cpu, mem_bytes, max_pids);

    let mut child = match command.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Failed to spawn child: {e}");
            std::process::exit(1);
        }
    };

    // Aplica cgroups v2 se disponível
    if let Ok(mode) = cgroup::Mode::from_env() {
        if mode != cgroup::Mode::Disabled {
            let _ = cgroup::Group::create(mode, child.id(), mem_bytes, max_pids, max_cpu);
        }
    }

    let status = child.wait().unwrap();
    std::process::exit(status.code().unwrap_or(0));
}

fn pack_output_fast(
    content: &str,
    identifier: &str,
    log_dir_str: &str,
    max_lines: usize,
    max_chars: usize,
    head_lines: usize,
    tail_lines: usize,
) -> serde_json::Value {
    if content.is_empty() {
        return serde_json::json!({
            "truncated": false,
            "file_path": serde_json::Value::Null,
            "text": ""
        });
    }

    let lines: Vec<&str> = content.lines().collect();
    let total_lines = lines.len();
    let total_chars = content.len();

    if total_lines <= max_lines && total_chars <= max_chars {
        return serde_json::json!({
            "truncated": false,
            "file_path": serde_json::Value::Null,
            "text": content
        });
    }

    let log_dir = Path::new(log_dir_str);
    let _ = std::fs::create_dir_all(log_dir);
    let safe_id: String = identifier
        .chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
        .take(32)
        .collect();
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let file_name = format!("{}_{}.log", safe_id, ts);
    let file_path = log_dir.join(file_name);
    let _ = std::fs::write(&file_path, content);
    let file_path_str = file_path.to_string_lossy().to_string();

    let (head_part, tail_part, omitted_lines) = if total_lines > (head_lines + tail_lines) {
        let h = lines[..head_lines].join("\n");
        let t = lines[total_lines - tail_lines..].join("\n");
        (h, t, total_lines - head_lines - tail_lines)
    } else {
        let half = total_lines / 2;
        let h = lines[..half].join("\n");
        let t = lines[half..].join("\n");
        (h, t, 0)
    };

    let excerpt = format!(
        "[ObservationPack: Saída volumosa contida para preservar contexto e prompt caching]\n\
         [Log integral arquivado em: {} ({} linhas, {} bytes)]\n\
         --- INÍCIO DA SAÍDA (primeiras {} linhas) ---\n\
         {}\n\
         ... [{} linhas omitidas do meio — veja o arquivo acima se necessário] ...\n\
         --- FINAL DA SAÍDA (últimas {} linhas) ---\n\
         {}",
        file_path_str,
        total_lines,
        total_chars,
        head_lines,
        head_part,
        omitted_lines,
        tail_lines,
        tail_part
    );

    serde_json::json!({
        "truncated": true,
        "file_path": file_path_str,
        "text": excerpt
    })
}

fn check_command_fast(cmd: &str) -> serde_json::Value {
    let trimmed = cmd.trim();
    if trimmed.is_empty() {
        return serde_json::json!({
            "allowed": true,
            "reason": "empty_command",
            "risk": "none"
        });
    }

    // Regras estritas de segurança em Rust nativo (nanosegundos)
    // 1. Bloqueio de comandos destrutivos perigosos de sistema
    let destructive_patterns = [
        "rm -rf /",
        "rm -rf /*",
        ":(){ :|:& };:",
        "mkfs.",
        "dd if=/dev/zero of=/dev/sd",
        "dd if=/dev/urandom of=/dev/sd",
        "> /dev/sda",
        "chmod -R 777 /",
    ];

    for pattern in &destructive_patterns {
        if trimmed.contains(pattern) {
            return serde_json::json!({
                "allowed": false,
                "reason": format!("Destructive pattern detected: {pattern}"),
                "risk": "critical"
            });
        }
    }

    // 2. Detecção de scripts Python/Node/Bash embutidos
    let is_inline_eval = trimmed.starts_with("python") && trimmed.contains(" -c ")
        || trimmed.starts_with("node") && trimmed.contains(" -e ")
        || trimmed.starts_with("perl") && trimmed.contains(" -e ")
        || trimmed.starts_with("ruby") && trimmed.contains(" -e ");

    serde_json::json!({
        "allowed": true,
        "is_inline_eval": is_inline_eval,
        "length": trimmed.len(),
        "risk": "low"
    })
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

#[derive(serde::Serialize)]
struct SanitizePathResult {
    ok: bool,
    canonical_path: Option<String>,
    error: Option<String>,
}

fn sanitize_path_fast(root: &str, target: &str) -> SanitizePathResult {
    let root_path = Path::new(root);
    let target_path = Path::new(target);

    // Normaliza componentes léxicos eliminando '.' e '..'
    let full_path = if target_path.is_absolute() {
        target_path.to_path_buf()
    } else {
        root_path.join(target_path)
    };

    let mut normalized = PathBuf::new();
    for comp in full_path.components() {
        match comp {
            std::path::Component::Prefix(p) => normalized.push(p.as_os_str()),
            std::path::Component::RootDir => normalized.push("/"),
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                normalized.pop();
            }
            std::path::Component::Normal(c) => normalized.push(c),
        }
    }

    let canonical_root = match root_path.canonicalize() {
        Ok(r) => r,
        Err(_) => root_path.to_path_buf(),
    };

    // Se o target existir, canonicalize com resolução de symlinks
    let final_path = if let Ok(p) = normalized.canonicalize() {
        p
    } else if let Some(parent) = normalized.parent() {
        if let Ok(mut parent_canon) = parent.canonicalize() {
            if let Some(file_name) = normalized.file_name() {
                parent_canon.push(file_name);
                parent_canon
            } else {
                normalized
            }
        } else {
            normalized
        }
    } else {
        normalized
    };

    if final_path.starts_with(&canonical_root) {
        SanitizePathResult {
            ok: true,
            canonical_path: Some(final_path.display().to_string()),
            error: None,
        }
    } else {
        SanitizePathResult {
            ok: false,
            canonical_path: None,
            error: Some(format!(
                "Path traversal denied: '{}' escapes root '{}'",
                target, root
            )),
        }
    }
}

#[derive(serde::Serialize)]
struct ContextFingerprintResult {
    ok: bool,
    total_bytes: usize,
    full_hash: String,
    prefix_hashes: Vec<String>,
}

fn hash_context_fast(text: &str, chunk_segments: usize) -> ContextFingerprintResult {
    use sha2::{Digest, Sha256};
    let bytes = text.as_bytes();
    let total_bytes = bytes.len();

    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let full_hash = format!("{:x}", hasher.finalize());

    let segments = chunk_segments.max(1).min(16);
    let mut prefix_hashes = Vec::with_capacity(segments);

    for i in 1..=segments {
        let slice_end = (total_bytes * i) / segments;
        let slice = &bytes[..slice_end];
        let mut h = Sha256::new();
        h.update(slice);
        prefix_hashes.push(format!("{:x}", h.finalize()));
    }

    ContextFingerprintResult {
        ok: true,
        total_bytes,
        full_hash,
        prefix_hashes,
    }
}

fn supervise_process_tree(cmd: &[String], timeout_seconds: u64) {
    #[cfg(target_os = "linux")]
    unsafe {
        // Define este processo como SUBREAPER no kernel Linux
        // Qualquer filho órfão adotado por init/systemd passa a ser adotado por nós!
        libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0);
    }

    let program = &cmd[0];
    let prog_args = &cmd[1..];

    let mut child = match Command::new(program)
        .args(prog_args)
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Failed to spawn child: {e}");
            std::process::exit(127);
        }
    };

    let child_pid = child.id();
    let start = std::time::Instant::now();
    let max_dur = std::time::Duration::from_secs(timeout_seconds);

    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                // Processo filho principal terminou. Agora limpa qualquer processo órfão que ele deixou!
                reap_all_zombies(child_pid);
                std::process::exit(status.code().unwrap_or(0));
            }
            Ok(None) => {
                if start.elapsed() > max_dur {
                    eprintln!("Supervised process {} timed out after {}s. Killing tree.", child_pid, timeout_seconds);
                    process::kill_tree(child_pid);
                    reap_all_zombies(child_pid);
                    std::process::exit(124); // 124 = standard timeout exit code
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            Err(e) => {
                eprintln!("Wait error: {e}");
                process::kill_tree(child_pid);
                reap_all_zombies(child_pid);
                std::process::exit(1);
            }
        }
    }
}

fn reap_all_zombies(root_pid: u32) {
    process::kill_tree(root_pid);
    #[cfg(unix)]
    unsafe {
        // Dá waitpid(-1) non-blocking até esgotar todos os órfãos re-adotados
        let mut status = 0;
        loop {
            let pid = libc::waitpid(-1, &mut status, libc::WNOHANG);
            if pid <= 0 {
                break;
            }
        }
    }
}

const DEFAULT_DECISION_MARKERS: &[&str] = &[
    "DECIDED", "DECISION", "ACCEPTED", "REJECTED", "APPROVED", "REJECT",
    "CHANGED", "FIXED", "CHOSEN", "STATUS:", "VERSION", "RESOLUTION",
    "CONCLUSION", "RATIONALE", "BECAUSE", "OUTCOME",
];

const HEAD_KEEP: usize = 3;

fn is_decision_line_fast(line: &str) -> bool {
    let upper = line.to_uppercase();
    DEFAULT_DECISION_MARKERS.iter().any(|&m| upper.contains(m))
}

fn compress_context_fast(text: &str, max_chars: Option<usize>) -> String {
    if let Some(limit) = max_chars {
        if text.len() <= limit {
            return text.to_string();
        }
    }

    let lines: Vec<&str> = text.lines().collect();
    let decision_lines: Vec<&str> = lines
        .iter()
        .copied()
        .filter(|ln| is_decision_line_fast(ln))
        .collect();

    let ellipsis = "…";

    if decision_lines.is_empty() {
        let mut kept: Vec<String> = Vec::new();
        let head_count = lines.len().min(HEAD_KEEP);
        for i in 0..head_count {
            kept.push(lines[i].to_string());
        }
        if lines.len() > HEAD_KEEP {
            let note = format!("{} [+{lines_count} lines compacted]", ellipsis, lines_count = lines.len() - HEAD_KEEP);
            kept.push(note);
        }
        return kept.join("\n");
    }

    let dropped_filler = lines.len().saturating_sub(decision_lines.len()).saturating_sub(HEAD_KEEP);
    let mut out: Vec<String> = decision_lines.iter().map(|s| s.to_string()).collect();
    let note = format!(
        "{} [+{dropped} lines compacted; decisions preserved]",
        ellipsis,
        dropped = dropped_filler
    );

    if dropped_filler > 0 {
        out.push(note.clone());
    }

    let mut result = out.join("\n");
    if let Some(limit) = max_chars {
        if result.len() > limit {
            if let Some(last_newline) = result[..limit].rfind('\n') {
                result.truncate(last_newline);
                result.push('\n');
                result.push_str(&note);
            }
        }
    }

    result
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

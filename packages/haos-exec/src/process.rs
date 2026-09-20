pub fn kill_tree(pid: u32) {
    #[cfg(unix)] unsafe { libc::kill(-(pid as i32), libc::SIGKILL); }
    #[cfg(windows)] { let _ = std::process::Command::new("taskkill").args(["/PID", &pid.to_string(), "/T", "/F"]).status(); }
}
pub fn configure_parent_death() {
    #[cfg(target_os = "linux")] unsafe { libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL); }
}
#[cfg(target_os = "linux")]
pub fn try_pidfd_open(pid: u32) -> Option<i32> { let fd=unsafe{libc::syscall(libc::SYS_pidfd_open,pid as libc::pid_t,0)} as i32; (fd>=0).then_some(fd) }
#[cfg(not(target_os = "linux"))]
pub fn try_pidfd_open(_: u32)->Option<i32>{None}
#[cfg(target_os = "linux")]
pub fn pidfd_kill(fd:i32)->bool { unsafe{libc::syscall(libc::SYS_pidfd_send_signal,fd,libc::SIGKILL,0,0)==0} }
#[cfg(not(target_os = "linux"))]
pub fn pidfd_kill(_:i32)->bool{false}

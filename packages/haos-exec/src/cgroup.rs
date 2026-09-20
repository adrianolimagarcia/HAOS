//! Optional Linux cgroups v2 resource isolation.
#[cfg(target_os="linux")]
use std::{fs, io, path::{Path, PathBuf}};
#[derive(Clone,Copy,Debug,PartialEq,Eq)] pub enum Mode{Disabled,BestEffort,Required}
impl Mode{pub fn from_env()->Result<Self,String>{match std::env::var("HERMES_EXEC_CGROUP_MODE").unwrap_or_else(|_|"disabled".into()).to_ascii_lowercase().as_str(){"disabled"=>Ok(Self::Disabled),"best_effort"|"best-effort"=>Ok(Self::BestEffort),"required"=>Ok(Self::Required),x=>Err(format!("invalid HERMES_EXEC_CGROUP_MODE: {x}"))}}}
#[cfg(target_os="linux")] pub struct Group{path:PathBuf,mode:Mode}
#[cfg(not(target_os="linux"))] pub struct Group;
impl Group{
 pub fn create(mode:Mode,_pid:u32,memory:Option<u64>,pids:Option<u64>,cpu:Option<u64>)->Result<Option<Self>,String>{if mode==Mode::Disabled{return Ok(None)};#[cfg(not(target_os="linux"))]{let _=(memory,pids,cpu);return Err("cgroups v2 requires Linux".into())} #[cfg(target_os="linux")]{let root=Path::new("/sys/fs/cgroup"); if !root.join("cgroup.controllers").exists(){if mode==Mode::Required{return Err("cgroups v2 unavailable".into())}return Ok(None)};let path=root.join(format!("hermes-exec-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d|d.as_nanos()).unwrap_or(0)));let r=(||->io::Result<()>{fs::create_dir(&path)?;if let Some(v)=memory{fs::write(path.join("memory.max"),v.to_string())?}if let Some(v)=pids{fs::write(path.join("pids.max"),v.to_string())?}if let Some(v)=cpu{fs::write(path.join("cpu.max"),format!("{} 100000",v.saturating_mul(100000)))?}Ok(())})();if let Err(e)=r{let _=fs::remove_dir(&path);if mode==Mode::Required{return Err(format!("cgroup setup failed: {e}"))}return Ok(None)}Ok(Some(Self{path,mode}))}}
 pub fn attach(&self,pid:u32)->Result<(),String>{#[cfg(target_os="linux")]if let Err(e)=fs::write(self.path.join("cgroup.procs"),pid.to_string()){if self.mode==Mode::Required{return Err(format!("cgroup attach failed: {e}"))}}Ok(())}
 pub fn kill(&self)->Result<(),String>{#[cfg(target_os="linux")]match fs::write(self.path.join("cgroup.kill"),"1"){Ok(())=>Ok(()),Err(e) if self.mode==Mode::BestEffort=>Ok(()),Err(e)=>Err(format!("cgroup kill failed: {e}"))}#[cfg(not(target_os="linux"))]Ok(())}
}
impl Drop for Group{fn drop(&mut self){#[cfg(target_os="linux")] {let _=fs::remove_dir(&self.path);}}}
#[cfg(test)]mod tests{use super::*;#[test]fn mode_defaults_disabled(){std::env::remove_var("HERMES_EXEC_CGROUP_MODE");assert_eq!(Mode::from_env().unwrap(),Mode::Disabled)}#[test]fn rejects_unknown_mode(){std::env::set_var("HERMES_EXEC_CGROUP_MODE","wat");assert!(Mode::from_env().is_err());std::env::remove_var("HERMES_EXEC_CGROUP_MODE");}}

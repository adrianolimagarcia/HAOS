use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{fs::{self, File, OpenOptions}, io::{self, Read, Write}, path::{Path, PathBuf}};

pub const DEFAULT_MAX_BYTES: u64 = 64 * 1024 * 1024;

pub fn resolve_bounded(root: impl AsRef<Path>, requested: impl AsRef<Path>) -> io::Result<PathBuf> {
    let root = fs::canonicalize(root)?;
    let requested = requested.as_ref();
    if requested.is_absolute() { return Err(io::Error::new(io::ErrorKind::PermissionDenied, "absolute paths are forbidden")); }
    let mut current = root.clone();
    for component in requested.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => return Err(io::Error::new(io::ErrorKind::PermissionDenied, "parent traversal is forbidden")),
            std::path::Component::Normal(part) => {
                current.push(part);
                let metadata = fs::symlink_metadata(&current)?;
                if metadata.file_type().is_symlink() { return Err(io::Error::new(io::ErrorKind::PermissionDenied, "symlinks are forbidden")); }
            }
            _ => return Err(io::Error::new(io::ErrorKind::PermissionDenied, "unsupported path component")),
        }
    }
    let resolved = fs::canonicalize(&current)?;
    if resolved == root || resolved.starts_with(&root) { Ok(resolved) }
    else { Err(io::Error::new(io::ErrorKind::PermissionDenied, "path escapes root")) }
}

pub fn hash_file(path: impl AsRef<Path>, max_bytes: u64) -> io::Result<(String, u64)> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 64 * 1024];
    let mut total = 0u64;
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 { break; }
        total = total.saturating_add(n as u64);
        if total > max_bytes { return Err(io::Error::new(io::ErrorKind::FileTooLarge, "file exceeds hash limit")); }
        hasher.update(&buf[..n]);
    }
    Ok((format!("{:x}", hasher.finalize()), total))
}

pub fn atomic_write(root: impl AsRef<Path>, requested: impl AsRef<Path>, data: &[u8], max_bytes: u64) -> io::Result<PathBuf> {
    if data.len() as u64 > max_bytes { return Err(io::Error::new(io::ErrorKind::FileTooLarge, "data exceeds write limit")); }
    let parent_req = requested.as_ref().parent().unwrap_or_else(|| Path::new("."));
    let parent = resolve_bounded(&root, parent_req)?;
    let name = requested.as_ref().file_name().ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing filename"))?;
    if name.to_string_lossy().contains('/') { return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid filename")); }
    let path = parent.join(name);
    let tmp = parent.join(format!(".{}.tmp", path.file_name().unwrap().to_string_lossy()));
    let mut file = OpenOptions::new().write(true).create_new(true).open(&tmp)?;
    file.write_all(data)?;
    file.sync_all()?;
    fs::rename(&tmp, &path)?;
    Ok(path)
}

#[derive(Serialize)]
pub struct ManifestEntry { pub path: String, pub sha256: String, pub bytes: u64 }

pub fn manifest(root: impl AsRef<Path>, paths: &[impl AsRef<Path>], max_bytes: u64) -> io::Result<Vec<ManifestEntry>> {
    let root = root.as_ref();
    paths.iter().map(|p| {
        let bounded = resolve_bounded(root, p)?;
        let (sha256, bytes) = hash_file(&bounded, max_bytes)?;
        Ok(ManifestEntry { path: p.as_ref().to_string_lossy().into_owned(), sha256, bytes })
    }).collect()
}

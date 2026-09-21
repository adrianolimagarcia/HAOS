use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub const DEFAULT_MAX_BYTES: u64 = 64 * 1024 * 1024;

#[cfg(target_os = "linux")]
fn openat2_bounded(root: &Path, requested: &Path) -> io::Result<PathBuf> {
    use std::ffi::CString;
    use std::os::fd::FromRawFd;
    #[repr(C)]
    struct OpenHow {
        flags: u64,
        mode: u64,
        resolve: u64,
    }
    const RESOLVE_NO_XDEV: u64 = 0x01;
    const RESOLVE_NO_MAGICLINKS: u64 = 0x02;
    const RESOLVE_NO_SYMLINKS: u64 = 0x04;
    const RESOLVE_BENEATH: u64 = 0x08;
    let root_c = CString::new(root.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "root contains NUL"))?;
    let req_c = CString::new(requested.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    let root_fd = unsafe {
        libc::open(
            root_c.as_ptr(),
            libc::O_PATH | libc::O_DIRECTORY | libc::O_CLOEXEC,
        )
    };
    if root_fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let how = OpenHow {
        flags: libc::O_PATH as u64 | libc::O_CLOEXEC as u64,
        mode: 0,
        resolve: RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV,
    };
    let fd = unsafe {
        libc::syscall(
            libc::SYS_openat2,
            root_fd,
            req_c.as_ptr(),
            &how,
            std::mem::size_of::<OpenHow>(),
        ) as i32
    };
    let saved = io::Error::last_os_error();
    unsafe {
        libc::close(root_fd);
    }
    if fd < 0 {
        return Err(saved);
    }
    unsafe {
        libc::close(fd);
    }
    Ok(root.join(requested))
}

pub fn resolve_bounded(root: impl AsRef<Path>, requested: impl AsRef<Path>) -> io::Result<PathBuf> {
    let root = fs::canonicalize(root)?;
    let requested = requested.as_ref();
    if requested.is_absolute() {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "absolute paths are forbidden",
        ));
    }
    if requested == Path::new(".") || requested.as_os_str().is_empty() {
        return Ok(root);
    }
    #[cfg(target_os = "linux")]
    {
        if requested
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "parent traversal is forbidden",
            ));
        }
        return openat2_bounded(&root, requested).map_err(|e| {
            if e.raw_os_error() == Some(libc::ENOSYS) {
                io::Error::new(io::ErrorKind::Unsupported, "openat2 is required on Linux")
            } else {
                e
            }
        });
    }
    #[cfg(not(target_os = "linux"))]
    {
        let mut current = root.clone();
        for component in requested.components() {
            match component {
                std::path::Component::CurDir => {}
                std::path::Component::ParentDir => {
                    return Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        "parent traversal is forbidden",
                    ))
                }
                std::path::Component::Normal(part) => {
                    current.push(part);
                    let metadata = fs::symlink_metadata(&current)?;
                    if metadata.file_type().is_symlink() {
                        return Err(io::Error::new(
                            io::ErrorKind::PermissionDenied,
                            "symlinks are forbidden",
                        ));
                    }
                }
                _ => {
                    return Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        "unsupported path component",
                    ))
                }
            }
        }
        let resolved = fs::canonicalize(&current)?;
        if resolved == root || resolved.starts_with(&root) {
            Ok(resolved)
        } else {
            Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "path escapes root",
            ))
        }
    }
}

#[cfg(target_os = "linux")]
fn open_regular_nofollow(path: &Path) -> io::Result<File> {
    use std::ffi::CString;
    use std::os::fd::FromRawFd;
    let c = CString::new(path.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    let fd = unsafe {
        libc::open(
            c.as_ptr(),
            libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let mut st = unsafe { std::mem::zeroed::<libc::stat>() };
    if unsafe { libc::fstat(fd, &mut st) } != 0 {
        let e = io::Error::last_os_error();
        unsafe {
            libc::close(fd);
        }
        return Err(e);
    }
    if (st.st_mode & libc::S_IFMT) != libc::S_IFREG {
        unsafe {
            libc::close(fd);
        }
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "hash source must be a regular file",
        ));
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}
pub fn hash_file(path: impl AsRef<Path>, max_bytes: u64) -> io::Result<(String, u64)> {
    let path = path.as_ref();
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "hash source must be a regular file",
        ));
    }
    #[cfg(target_os = "linux")]
    let mut file = open_regular_nofollow(path)?;
    #[cfg(not(target_os = "linux"))]
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 64 * 1024];
    let mut total = 0u64;
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        total = total.saturating_add(n as u64);
        if total > max_bytes {
            return Err(io::Error::new(
                io::ErrorKind::FileTooLarge,
                "file exceeds hash limit",
            ));
        }
        hasher.update(&buf[..n]);
    }
    Ok((format!("{:x}", hasher.finalize()), total))
}

#[cfg(target_os = "linux")]
fn rename_relative(parent: &Path, from: &Path, to: &Path) -> io::Result<()> {
    use std::ffi::CString;
    let pc = CString::new(parent.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "parent contains NUL"))?;
    let fc = CString::new(from.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "source contains NUL"))?;
    let tc = CString::new(to.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "destination contains NUL"))?;
    let dfd = unsafe {
        libc::open(
            pc.as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
        )
    };
    if dfd < 0 {
        return Err(io::Error::last_os_error());
    }
    let rc = unsafe { libc::renameat(dfd, fc.as_ptr(), dfd, tc.as_ptr()) };
    let result = if rc == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    };
    unsafe {
        libc::close(dfd);
    }
    result
}

pub fn atomic_write(
    root: impl AsRef<Path>,
    requested: impl AsRef<Path>,
    data: &[u8],
    max_bytes: u64,
) -> io::Result<PathBuf> {
    if data.len() as u64 > max_bytes {
        return Err(io::Error::new(
            io::ErrorKind::FileTooLarge,
            "data exceeds write limit",
        ));
    }
    let parent_req = requested
        .as_ref()
        .parent()
        .unwrap_or_else(|| Path::new("."));
    let parent = resolve_bounded(&root, parent_req)?;
    let name = requested
        .as_ref()
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing filename"))?;
    let path = parent.join(name);
    let unique = format!(
        "{}.{}.{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos(),
        std::thread::current()
            .name()
            .unwrap_or("worker")
            .replace(|c: char| !c.is_ascii_alphanumeric(), "_")
    );
    let tmp = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name().unwrap().to_string_lossy(),
        unique
    ));
    let mut file = OpenOptions::new().write(true).create_new(true).open(&tmp)?;
    file.write_all(data)?;
    file.sync_all()?;
    #[cfg(target_os = "linux")]
    let result = rename_relative(
        &parent,
        Path::new(tmp.file_name().unwrap()),
        Path::new(path.file_name().unwrap()),
    );
    #[cfg(not(target_os = "linux"))]
    let result = fs::rename(&tmp, &path);
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result.map(|_| path)
}

#[derive(Serialize)]
pub struct ManifestEntry {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
}

pub fn manifest(
    root: impl AsRef<Path>,
    paths: &[impl AsRef<Path>],
    max_bytes: u64,
) -> io::Result<Vec<ManifestEntry>> {
    let root = root.as_ref();
    paths
        .iter()
        .map(|p| {
            let bounded = resolve_bounded(root, p)?;
            let metadata = fs::symlink_metadata(&bounded)?;
            if !metadata.file_type().is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "manifest source must be a regular file",
                ));
            }
            let (sha256, bytes) = hash_file(&bounded, max_bytes)?;
            Ok(ManifestEntry {
                path: p.as_ref().to_string_lossy().into_owned(),
                sha256,
                bytes,
            })
        })
        .collect()
}

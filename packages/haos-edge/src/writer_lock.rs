//! Exclusive lock for the logical state database writer.
//!
//! The lock is deliberately independent from server startup and SQLite.  A holder is
//! represented by a file created with `create_new`, so two processes cannot acquire the
//! same profile lock by racing through a check-then-create sequence.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

/// Name of the per-profile writer lock file.
pub const WRITER_LOCK_FILE_NAME: &str = "state.db.writer.lock";

/// Information written to a held lock file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriterLockInfo {
    /// Process ID of the holder.
    pub pid: u32,
    /// SHA-256 fingerprint of the holder's executable.
    pub fingerprint: String,
}

/// An acquired writer lock.
///
/// The lock remains held while this value is alive.  Dropping it removes the lock file,
/// but only when the file still contains this holder's metadata.
pub struct WriterLock {
    path: PathBuf,
    info: WriterLockInfo,
    file: Option<File>,
}

impl WriterLock {
    /// Acquire the writer lock for `data_dir` and one profile.
    ///
    /// The fingerprint is calculated from the current executable.  An existing lock is
    /// never considered stale or reclaimed: callers must fail closed and investigate the
    /// holder instead.
    pub fn acquire(data_dir: impl AsRef<Path>, profile: &str) -> io::Result<Self> {
        let executable = std::env::current_exe()?;
        let fingerprint = fingerprint_file(&executable)?;
        Self::acquire_with_fingerprint(data_dir, profile, fingerprint)
    }

    /// Acquire a lock with an explicitly supplied fingerprint.
    ///
    /// This is useful to callers that already have a verified binary fingerprint and keeps
    /// the acquisition primitive independently testable without changing filesystem state.
    pub fn acquire_with_fingerprint(
        data_dir: impl AsRef<Path>,
        profile: &str,
        fingerprint: impl Into<String>,
    ) -> io::Result<Self> {
        let path = lock_path(data_dir, profile)?;
        let parent = path
            .parent()
            .expect("writer lock path always has a profile parent");
        fs::create_dir_all(parent)?;

        let info = WriterLockInfo {
            pid: std::process::id(),
            fingerprint: fingerprint.into(),
        };
        let contents = serde_json::to_vec(&info).map_err(io::Error::other)?;

        // `create_new(true)` is the atomic ownership decision.  Do not fall back to an
        // ordinary open: that would allow a second process to overwrite the holder data.
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|error| {
                if error.kind() == io::ErrorKind::AlreadyExists {
                    io::Error::new(
                        io::ErrorKind::AlreadyExists,
                        format!("writer lock is already held: {}", path.display()),
                    )
                } else {
                    error
                }
            })?;

        if let Err(error) = (|| -> io::Result<()> {
            file.write_all(&contents)?;
            file.sync_all()
        })() {
            // We own the newly-created file, so avoid leaving a lock behind when publishing
            // its metadata fails.  Preserve the original I/O error for the caller.
            let _ = fs::remove_file(&path);
            return Err(error);
        }

        Ok(Self {
            path,
            info,
            file: Some(file),
        })
    }

    /// Return the path occupied by this lock.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Return the metadata published by this holder.
    pub fn info(&self) -> &WriterLockInfo {
        &self.info
    }

    /// Read the metadata of an existing lock without acquiring it.
    pub fn read_info(data_dir: impl AsRef<Path>, profile: &str) -> io::Result<WriterLockInfo> {
        let path = lock_path(data_dir, profile)?;
        let bytes = fs::read(path)?;
        serde_json::from_slice(&bytes).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid writer lock metadata: {error}"),
            )
        })
    }
}

impl Drop for WriterLock {
    fn drop(&mut self) {
        let Some(mut file) = self.file.take() else {
            return;
        };

        // Keep the descriptor alive until after the unlink.  Compare the bytes first so a
        // compromised/replaced path cannot make this guard remove another holder's lock.
        let ours = fs::read(&self.path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<WriterLockInfo>(&bytes).ok())
            .as_ref()
            == Some(&self.info);
        if ours {
            let _ = fs::remove_file(&self.path);
        }

        // Explicitly close the descriptor before Drop returns; errors are not actionable from
        // Drop, and the file is no longer a valid lock once the path has been removed.
        let _ = file.flush();
    }
}

/// Return the lock path for a data directory and profile.
pub fn lock_path(data_dir: impl AsRef<Path>, profile: &str) -> io::Result<PathBuf> {
    validate_profile(profile)?;
    Ok(data_dir.as_ref().join(profile).join(WRITER_LOCK_FILE_NAME))
}

/// Compute a stable SHA-256 fingerprint of a binary file.
pub fn fingerprint_file(path: impl AsRef<Path>) -> io::Result<String> {
    let bytes = fs::read(path)?;
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

fn validate_profile(profile: &str) -> io::Result<()> {
    if profile.is_empty()
        || profile == "."
        || profile == ".."
        || profile.contains('/')
        || profile.contains('\\')
        || profile.contains('\0')
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "profile must be a non-empty single path component",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::thread;
    use tempfile::tempdir;

    #[test]
    fn path_is_scoped_by_data_dir_and_profile() {
        let data_dir = tempdir().unwrap();
        let path = lock_path(data_dir.path(), "profile-a").unwrap();
        assert_eq!(
            path,
            data_dir
                .path()
                .join("profile-a")
                .join(WRITER_LOCK_FILE_NAME)
        );
        assert!(lock_path(data_dir.path(), "nested/profile").is_err());
    }

    #[test]
    fn holder_contains_pid_and_fingerprint() {
        let data_dir = tempdir().unwrap();
        let lock =
            WriterLock::acquire_with_fingerprint(data_dir.path(), "default", "test-fp").unwrap();
        assert_eq!(lock.info().pid, std::process::id());
        assert_eq!(lock.info().fingerprint, "test-fp");
        assert_eq!(
            WriterLock::read_info(data_dir.path(), "default").unwrap(),
            *lock.info()
        );
    }

    #[test]
    fn concurrent_holder_fails_closed() {
        let data_dir = tempdir().unwrap();
        let lock =
            WriterLock::acquire_with_fingerprint(data_dir.path(), "default", "holder").unwrap();
        let path = lock.path().to_owned();
        let data_dir = data_dir.path().to_owned();
        let (tx, rx) = mpsc::channel();

        thread::spawn(move || {
            tx.send(
                WriterLock::acquire_with_fingerprint(&data_dir, "default", "contender")
                    .map(|_| ())
                    .unwrap_err()
                    .kind(),
            )
            .unwrap();
        })
        .join()
        .unwrap();

        assert_eq!(rx.recv().unwrap(), io::ErrorKind::AlreadyExists);
        assert!(path.exists());
    }

    #[test]
    fn release_allows_next_holder() {
        let data_dir = tempdir().unwrap();
        let path;
        {
            let lock =
                WriterLock::acquire_with_fingerprint(data_dir.path(), "default", "first").unwrap();
            path = lock.path().to_owned();
            assert!(path.exists());
        }
        assert!(!path.exists());

        let second =
            WriterLock::acquire_with_fingerprint(data_dir.path(), "default", "second").unwrap();
        assert_eq!(second.info().fingerprint, "second");
    }

    #[test]
    fn fingerprint_is_sha256_of_binary_contents() {
        let data_dir = tempdir().unwrap();
        let binary = data_dir.path().join("binary");
        fs::write(&binary, b"writer-binary").unwrap();
        assert_eq!(
            fingerprint_file(&binary).unwrap(),
            "sha256:7cdfd85f72652eafbcc0b6edf24917c5776489401d0dcbee57f8cffd12f527d7"
        );
    }
}

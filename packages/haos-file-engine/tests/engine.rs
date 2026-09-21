use haos_file_engine::*;
use std::fs;
use tempfile::tempdir;

#[test]
fn rejects_absolute_and_parent_paths() {
    let d = tempdir().unwrap();
    assert!(resolve_bounded(d.path(), "/etc/passwd").is_err());
    assert!(resolve_bounded(d.path(), "../escape").is_err());
}

#[test]
fn rejects_symlink_escape() {
    let d = tempdir().unwrap();
    let outside = tempdir().unwrap();
    fs::write(outside.path().join("secret"), b"x").unwrap();
    std::os::unix::fs::symlink(outside.path(), d.path().join("link")).unwrap();
    assert!(resolve_bounded(d.path(), "link/secret").is_err());
}

#[test]
fn hashes_with_limit() {
    let d = tempdir().unwrap();
    let p = d.path().join("x");
    fs::write(&p, b"abc").unwrap();
    let (hash, bytes) = hash_file(&p, 3).unwrap();
    assert_eq!(bytes, 3);
    assert_eq!(
        hash,
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
    assert!(hash_file(&p, 2).is_err());
}

#[test]
fn rejects_non_regular_hash_sources() {
    let d = tempdir().unwrap();
    fs::create_dir(d.path().join("dir")).unwrap();
    assert!(hash_file(d.path().join("dir"), 10).is_err());
    assert!(manifest(d.path(), &["dir"], 10).is_err());
}

#[test]
fn atomic_writes_use_distinct_temp_names() {
    let d = tempdir().unwrap();
    let first = atomic_write(d.path(), "out.txt", b"one", 10).unwrap();
    let second = atomic_write(d.path(), "out.txt", b"two", 10).unwrap();
    assert_eq!(fs::read(first).unwrap(), b"two");
    assert_eq!(fs::read(second).unwrap(), b"two");
    assert!(fs::read_dir(d.path()).unwrap().all(|entry| !entry
        .unwrap()
        .file_name()
        .to_string_lossy()
        .contains(".tmp")));
}
#[test]
fn writes_atomically_under_root() {
    let d = tempdir().unwrap();
    let path = atomic_write(d.path(), "out.txt", b"hello", 10).unwrap();
    assert_eq!(fs::read(path).unwrap(), b"hello");
    assert!(atomic_write(d.path(), "../bad", b"x", 10).is_err());
}

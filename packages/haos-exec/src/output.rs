use std::{io::Read, thread};

pub fn drain_bounded<R: Read + Send + 'static>(
    mut reader: R,
    cap: usize,
) -> thread::JoinHandle<(Vec<u8>, bool)> {
    thread::spawn(move || {
        let mut out = Vec::new();
        let mut buf = [0u8; 8192];
        let mut truncated = false;
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if out.len() < cap {
                        let take = (cap - out.len()).min(n);
                        out.extend_from_slice(&buf[..take]);
                        if take < n {
                            truncated = true;
                        }
                    } else {
                        truncated = true;
                    }
                }
                Err(_) => break,
            }
        }
        (out, truncated)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    #[test]
    fn caps_output() {
        let h = drain_bounded(Cursor::new(b"abcdef".to_vec()), 3);
        let (v, t) = h.join().unwrap();
        assert_eq!(v, b"abc");
        assert!(t);
    }
}

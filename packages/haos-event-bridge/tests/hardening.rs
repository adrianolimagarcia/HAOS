#[cfg(test)]
mod hardening_tests {
    use haos_event_bridge::*;
    use std::io::{self, Write};
    fn env() -> Envelope {
        Envelope {
            session_id: "s".into(),
            correlation_id: "c".into(),
            body: Message::Ack,
        }
    }
    #[test]
    fn rejects_empty_identifier() {
        let mut e = env();
        e.session_id.clear();
        assert!(matches!(
            encode_frame(&e, 100),
            Err(ProtocolError::InvalidIdentifier("session_id"))
        ));
    }
    #[test]
    fn rejects_oversized_identifier() {
        let mut e = env();
        e.correlation_id = "x".repeat(MAX_ID_BYTES + 1);
        assert!(matches!(
            encode_frame(&e, DEFAULT_MAX_FRAME),
            Err(ProtocolError::InvalidIdentifier("correlation_id"))
        ));
    }
    struct FailingWriter;
    impl Write for FailingWriter {
        fn write(&mut self, _: &[u8]) -> io::Result<usize> {
            Err(io::Error::from(io::ErrorKind::BrokenPipe))
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    #[test]
    fn write_propagates_io_error() {
        assert!(
            matches!(write_frame(&mut FailingWriter,&env(),100),Err(ProtocolError::Io(e)) if e.kind()==io::ErrorKind::BrokenPipe)
        );
    }
}

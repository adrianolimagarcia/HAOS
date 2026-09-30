from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST_MAIN = ROOT / "packages" / "hermes-webui-edge" / "src" / "main.rs"
RUST_PROXY = ROOT / "packages" / "hermes-webui-edge" / "src" / "proxy.rs"


def test_session_detail_is_forwarded_to_python_canonical_route():
    main = RUST_MAIN.read_text(encoding="utf-8")
    assert '.route("/api/session", get(session_detail_handler))' not in main
    assert '.route("/api/session/status", get(session_status_handler))' in main
    assert ".fallback(proxy::proxy_handler)" in main


def test_session_detail_proxy_has_longer_timeout_than_normal_requests():
    proxy = RUST_PROXY.read_text(encoding="utf-8")
    assert 'let is_session_detail = uri.path() == "/api/session";' in proxy
    assert 'client_req = client_req.timeout(Duration::from_secs(180));' in proxy
    assert 'client_req = client_req.timeout(Duration::from_secs(60));' in proxy

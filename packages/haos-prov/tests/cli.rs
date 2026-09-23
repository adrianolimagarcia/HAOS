use std::path::PathBuf;
use std::process::Command;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/adr_prov")
        .join(name)
}

fn run(vault: &str, args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_haos-prov"))
        .arg("--vault")
        .arg(fixture(vault))
        .args(args)
        .output()
        .expect("run haos-prov")
}

#[test]
fn commands_succeed_with_fixture_vault() {
    for args in [
        ["why", "ADR-001"].as_slice(),
        ["desc", "ADR-001"].as_slice(),
        ["effects", "ADR-001"].as_slice(),
        ["graph"].as_slice(),
        ["check"].as_slice(),
    ] {
        let output = run("vault", args);
        assert!(output.status.success(), "{args:?}: {output:?}");
        assert!(!output.stdout.is_empty(), "{args:?} produced no stdout");
    }
}

#[test]
fn json_output_is_parseable_and_deterministic() {
    let first = run("vault", &["--json", "graph"]);
    let second = run("vault", &["--json", "graph"]);
    assert!(first.status.success());
    assert_eq!(first.stdout, second.stdout);
    let value: serde_json::Value = serde_json::from_slice(&first.stdout).expect("JSON graph");
    assert_eq!(value["nodes"].as_array().expect("nodes").len(), 15);
}

#[test]
fn check_rejects_empty_malformed_ghost_and_cycle_vaults() {
    for vault in [
        "negative_vaults/empty",
        "negative_vaults/malformed",
        "negative_vaults/missing",
        "negative_vaults/ghost",
        "negative_vaults/cycle",
    ] {
        let output = run(vault, &["check"]);
        assert!(
            !output.status.success(),
            "{vault} unexpectedly passed: {output:?}"
        );
    }
}

#[test]
fn resolves_vault_from_environment_without_flag() {
    let output = Command::new(env!("CARGO_BIN_EXE_haos-prov"))
        .env("HAOS_VAULT_ADRS", fixture("vault"))
        .env_remove("HERMES_HOME")
        .args(["check"])
        .output()
        .expect("run haos-prov");
    assert!(output.status.success(), "{output:?}");
}

#[test]
fn unknown_adr_and_bad_vault_fail() {
    let missing = run("vault", &["why", "ADR-999"]);
    assert!(!missing.status.success());
    assert!(String::from_utf8_lossy(&missing.stderr).contains("não encontrada"));

    let bad = Command::new(env!("CARGO_BIN_EXE_haos-prov"))
        .args(["--vault", "/definitely/not/a/vault", "graph"])
        .output()
        .expect("run haos-prov");
    assert!(!bad.status.success());
}

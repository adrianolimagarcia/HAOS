# haos-prov

CLI binária e biblioteca para consultas determinísticas de proveniência causal dos
ADRs em frontmatter YAML. A CLI usa o parser e o grafo deste crate; não mantém
uma implementação paralela.

## Uso

```bash
cargo run -p haos-prov -- --vault tests/fixtures/adr_prov/vault why ADR-001
cargo run -p haos-prov -- --vault tests/fixtures/adr_prov/vault desc ADR-001
cargo run -p haos-prov -- --vault tests/fixtures/adr_prov/vault effects ADR-001
cargo run -p haos-prov -- --vault tests/fixtures/adr_prov/vault graph
cargo run -p haos-prov -- --vault tests/fixtures/adr_prov/vault check
cargo run -p haos-prov -- --vault tests/fixtures/adr_prov/vault --json graph
```

`desc` e `effects` são aliases. A saída JSON é determinística e contém apenas o
resultado estruturado do núcleo Rust.

## Resolução do vault

`--vault PATH` tem precedência e é a forma recomendada para testes reproduzíveis.
Sem essa opção, a CLI consulta, nesta ordem:

1. `HAOS_VAULT_ADRS`;
2. `$HERMES_HOME/obsidian_vault/adrs`;
3. `$HOME/.haos/obsidian_vault/adrs`.

O comando `check` retorna não-zero para vault vazio, frontmatter inválido,
referências ADR fantasmas ou ciclos. Consultas a ADR inexistente e caminhos de
vault inválidos também retornam não-zero.

A compatibilidade de saída com `tools/adr_prov.py` ainda não é paridade total;
este crate expõe o contrato estruturado Rust e renderização CLI própria.

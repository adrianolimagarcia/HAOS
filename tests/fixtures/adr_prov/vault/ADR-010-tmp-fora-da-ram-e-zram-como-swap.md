---
id: ADR-010
titulo: "/tmp fora da RAM e zram como swap (nunca como filesystem)"
status: "Aceito e aplicado"
data: 2026-09-15
decidido_por: "human:adriano"
autor: "adriano"
contexto: "gestão de memória, zram e swap no nó cachyos-x8664"
causado_by:
  - "/tmp em tmpfs consumindo RAM e empurrando 4.8G para compressão no zram por falta de swap em disco"
evidence:
  - "df /tmp indicando 5.1G alocados"
  - "zramctl indicando 14.5G de dados comprimidos para 6G prendendo 6.6G de RAM"
  - "swapon confirmando zram0 como único swap ativo"
affects:
  - "/etc/fstab"
  - "/etc/tmpfiles.d/tmp.conf"
  - "/swap/swapfile"
  - "btrfs subvol @tmp_disk"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:otimizacao-zram-tmpfs"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "incident:zram-exhaustion-tmpfs"
  causado_by: "tmpfs em /tmp competindo diretamente com a RAM e sobrecarregando o zram"
  affects:
    - "/etc/fstab"
    - "/etc/tmpfiles.d/tmp.conf"
  supersedes: []
  superseded_by: null
---

# ADR-010 — /tmp fora da RAM e zram como swap (nunca como filesystem)

- **Status**: aceito e aplicado (2026-09-15)
- **Contexto**: nó `cachyos-x8664` (30G RAM). `/tmp` era tmpfs com `size` implícito = 50% da RAM (15,5G) e o `zram0` (31G, zstd, prio 100) era o **único** swap da máquina.
- **Problema relatado**: "a zram está pegando tudo o que jogamos no /tmp".

## Diagnóstico (observado, não inferido)

| Medida | Valor |
|---|---|
| `df /tmp` | 5,1G usados (tmpfs) |
| `Shmem` residente (`/proc/meminfo`) | 287 MB |
| `zramctl` / `mm_stat` | 14,5G dados → 6G comprimidos (6,6G de RAM presa) |
| `swapon --show` | zram0 = único swap; 15,8G em uso |

`df` do tmpfs reporta blocos **alocados**; o residente era 287MB. Logo ~4,8G de `/tmp` estava **em swap, isto é, comprimido dentro do zram**. Não é o zram "pegando" o /tmp: é o `/tmp` sendo empurrado para o zram por ser RAM anônima e não haver outro swap.

## Decisão

1. **`/tmp` vai para disco**: subvol btrfs `@tmp_disk` no sda2, montado com `noatime,compress=zstd` (fstab linha 15). Deixa de competir com RAM/zram.
2. **Swapfile de 8G em disco** (`/swap/swapfile`, subvol dedicado `@swap`, nodatacow, `pri=10`) para dar caminho de evacuação real a páginas frias. Subvol dedicado porque `@` é snapshotado pelo snapper e um swapfile ativo em subvol snapshotado quebra a criação de snapshots.
3. **Limpeza**: `/etc/tmpfiles.d/tmp.conf` com `x /tmp/ha_fm` + `q /tmp 1777 root root 1d` (o /tmp em disco deixou de ser volátil no reboot).

## Alternativa REJEITADA (pelo dono, com razão)

**Montar um dispositivo zram como filesystem (ex.: `/zram`) e mover o `/tmp` para lá.** Rejeitada porque:

- zram é **dispositivo de swap**, não filesystem. As páginas de um zram-blockdev vivem no pool `zsmalloc`, que **não é reclamável nem swappável** — ficariam presas na RAM.
- Hoje, como tmpfs, as páginas de `/tmp` são shmem: reclamáveis, evictáveis e (com swap em disco) evacuáveis. Num zram-blockdev não há caminho de escape.
- Numa máquina já usando 15,8G de swap, isso **remove a única válvula** em vez de aliviar a pressão.

## Consequências

- `/tmp` persiste entre reboots ⇒ exige a regra de limpeza por idade (item 3).
- Durante a troca a quente há uma janela de "duas verdades": processos que seguravam o tmpfs antigo (cwd/fd) continuam nele até morrer; encerra no reboot.
- `cp -a` **reseta ctime**, então a contagem de idade do tmpfiles reinicia após migração (arquivos parecem novos por 1 dia).
- Efeito medido após aplicação: swap em uso 15,8G→10G; zram DATA 14,5G→12,1G; `compart` 284M→3,2G.

## Verificação

- `findmnt --verify`: 0 erros; `systemctl cat tmp.mount` gerado do fstab com `subvol=/@tmp_disk`.
- Escrita/leitura real em `/tmp` como usuário final (adriano) OK.
- X validado com cliente real (`xset q` como adriano respondeu). O socket X por caminho de arquivo recusa conexão (`ECONNREFUSED`) **já antes da mudança** — clientes usam o socket abstrato; não é regressão.
- Vigias que rodam de `/tmp/ha_fm` seguem vivos após a troca.

## Referências

- Lição OKF: `okf/licoes/tmpfiles-idade-e-exclusao-de-subarvore.md`
- REGISTRY `cachyos-x8664` (seção Serviços deste nó)

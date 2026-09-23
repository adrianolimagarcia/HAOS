# Métricas do Control Plane/WebUI

O servidor standalone expõe instrumentação local, bounded e sem dependências externas:

- `GET /api/metrics` (alias `/metrics`): contagem por rota, latência p95 em ms e contagem por upstream (`python`; `edge`, `gateway` e `fallback` ficam disponíveis para integrações que observem esses upstreams). As amostras são limitadas às últimas 256 por rota.
- `GET /api/status/memory` (alias `/api/memory`): PID do processo do servidor e descendentes diretos, com RSS (`rss_kb`), PSS (`pss_kb`, quando `/proc/<pid>/smaps_rollup` permite), PPID e comando. Falhas de procfs são reportadas como `unavailable`, sem interromper o endpoint.

A coleta é somente em memória e não altera o fluxo das rotas. O endpoint de memória não executa comandos nem expõe argumentos de requisição. Em sistemas sem procfs, o payload continua válido, mas os campos de memória podem estar ausentes.

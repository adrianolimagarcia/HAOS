# Dashboard via Tailscale

Configurado encaminhamento privado persistente com `tailscale serve --bg --tcp=9119 tcp://127.0.0.1:9119`.

URL: http://cachyosdell.tailbb7185.ts.net:9119/framework

Verificado neste host pelo hostname Tailscale: framework redireciona ao login (HTTP200 na página login); API sem autenticação retorna401. Acesso de outro dispositivo ainda não verificado. Exposição apenas na tailnet, sem Funnel; autenticação existente preservada.

Desativação: `tailscale serve --tcp=9119 off`.

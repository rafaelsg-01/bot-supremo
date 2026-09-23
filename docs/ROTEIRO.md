# Roteiro

O que falta fazer e em que ordem. Marque `[x]` ao concluir e registre o que descobrir na seção
[Descobertas e medições](#descobertas-e-medições) no fim deste arquivo.

As regras e o contexto estão no [CLAUDE.md](../CLAUDE.md). Resumo do princípio: Chrome real,
sem webdriver/CDP, nada injetado que altere a página, cliques e digitação reais com xdotool.

---

## Fase 0: base do repositório

- [x] `CLAUDE.md` com contexto, princípio, contrato e infraestrutura
- [x] `docs/ROTEIRO.md` (este arquivo)
- [x] `.gitignore`, `README.md`, `LICENSE` (MIT)
- [x] Repositório público `bot-supremo` no GitHub

## Fase 1: serviço mínimo (URL → HTML), já em Docker

Objetivo: mandar uma URL para a API e receber o HTML renderizado por um Chrome real no notebook.
O Docker e o CI vêm já aqui, porque o notebook não compila nada.

- [x] Levantar o estado do notebook (ver "Descobertas")
- [x] Código escrito: extensão, serviço, Dockerfile, compose, CI e implantação
- [x] Imagem publicada no GHCR e baixável sem login pelo notebook
- [x] Xorg na GPU real subindo dentro do container (senão Xvfb, e anotar o porquê)
- [x] Chrome com sandbox, perfil persistente e as duas extensões instaladas por política
- [x] Extensão conectada ao serviço (`/saude` com `ok: true`)
- [ ] `POST /v1/navegar` só com `url` devolvendo o HTML de uma página de lista do site
      (em 2026-09-23 o servidor do site respondia 522 depois do desafio; tentar de novo)
- [x] Conferir a navegação como "typed" no log (`navegou (transição typed ...)`)
- [ ] Fila, `about:blank` ao fim e reabertura do Chrome por memória
- [x] Vigia do desafio: log + gancho vazio + `status: "desafio"` se não sumir
- [ ] Timer do systemd instalado (`sudo bash implantacao/instalar.sh`) e testado com um reboot real
- [x] Teste de fingerprint (creepjs / sannysoft) pelo próprio bot, com o resultado anotado

## Fase 2: captura de rede

- [x] Extensão observa a rede com `chrome.webRequest` (URL, método, status, tipo, horário)
- [x] Parâmetro `esperarRede` (regex + timeout) e campo `rede` na resposta
- [ ] Testar no site e anotar o padrão da URL do vídeo

## Fase 3: cliques

- [x] Medição do seletor em todos os frames + conta da posição na tela (iframes + janela)
- [x] Clique com xdotool: mouse em curva, rolagem pela roda do mouse se o elemento estiver fora da tela
- [x] Ação `clicar` com `seExistir` e ação `esperar`
- [x] Ações `"quando": "desafio"` + busca dentro de shadow DOM fechado + filtro `frame`
- [x] Clique na caixa do Turnstile resolvendo o desafio (testado em 2026-09-23)
- [ ] Testar o fluxo completo no site: abrir episódio → clicar no play → capturar a URL do vídeo
- [ ] Conferir a precisão do clique dentro de iframe do player

## Fase 4: exposição e operação

- [x] `https://bot.iptv01.asia` por um túnel próprio (`bot-supremo`), serviço `tunel` no compose
- [ ] Avaliar se o cron de reboot a cada 12h ainda faz sentido

## Fase 5: ligar o projeto-iptv

- [ ] Reescrever as chamadas de `../projeto-iptv/src/function_rc.ts` para o contrato novo:
      `getHtmlCriptografado` → só `url`; `getReturnJsExecuted` + `Js_getLinkMp4` → `url` + `acoes`
      de clique + `esperarRede`
- [ ] Adaptar o parser do iptv ao HTML renderizado
- [ ] Conferir o cache de URLs de vídeo (`mp4-list-*`) e o `/proxy-rc` com o serviço novo
- [ ] Rodar alguns dias e observar se há ban

## Fase 6: desligar o FlareSolverr antigo

- [ ] Parar e remover o container `content-proxy-web-01` (libera ~620 MB)
- [ ] Tirar do `~/start.sh` os passos do proxy antigo e conferir que nada mais depende dele

---

## Descobertas e medições

Registre aqui tudo que for descoberto: padrões de URL, tempos de carregamento, uso de RAM,
problemas e soluções. Formato: data, assunto e o que foi visto.

| Data | Assunto | O que foi visto |
|---|---|---|
| 2026-09-23 | Notebook | Debian 13, kernel 6.12, Docker 29.6 (cgroup v2), Compose v5.3. Disco: 87 GB livres de 107 GB. RAM: 3,3 GB, ~2,2 GB disponíveis com o proxy antigo (620 MB) no ar. Swap 3,5 GB. |
| 2026-09-23 | GPU e tela | Intel Ivy Bridge (`/dev/dri/card0`, `renderD128`; grupos `video` 44 e `render` 992). Tela interna `LVDS-1` conectada, nativa 1366x768. `HandleLidSwitch=ignore`. Backlight `intel_backlight` estava no máximo (4882). |
| 2026-09-23 | WARP | O container `warp` (`caomingjun/warp`) roda o WARP completo (modo Warp, MASQUE, interface `CloudflareWARP`) na rede `flare-net`, com SOCKS em `warp:1080`. O `~/start.sh` (`@reboot` no crontab do usuário) recria o `warp` a cada boot. Faixas excluídas do túnel: 10/8, 100.64/10, 169.254/16, 172.16/12. |
| 2026-09-23 | Túneis | Dois `cloudflared` por token (rotas no painel da Cloudflare), na rede `bridge` padrão. O proxy antigo atende em `02proxy-web.iptv01.asia`. |
| 2026-09-23 | Sandbox | Com o seccomp padrão do Docker o Chrome morre (SIGTRAP, "Failed to move to new namespace"). Resolvido com `docker/seccomp-chrome.json` (padrão + clone/unshare/setns). |
| 2026-09-23 | Xorg na GPU | Sobe no container com `/dev/dri`, `/dev/tty0`, `/dev/tty7` e `SYS_TTY_CONFIG`, sem privilégio total. WebGL: "ANGLE (Intel, Mesa Intel(R) HD Graphics 2500 (IVB GT1), OpenGL 4.2)". |
| 2026-09-23 | Fingerprint | bot.sannysoft.com: tudo "passed" (webdriver ausente, Chrome presente, plugins 5, idioma pt-BR, tela 1366x768, inner 1366x681). |
| 2026-09-23 | Memória | Container com Chrome ocioso: ~450 MB. Chrome (USS somado): ~250–600 MB depois de páginas pesadas. RSS somado dá ~3x mais e não serve de medida. |
| 2026-09-23 | Tempos | example.com: 7 s no primeiro pedido, depois ~1,5 s para digitar e navegar. Wikipedia: 6 s no total. Extensão nova se atualiza sozinha em ~4 s. |
| 2026-09-23 | Desafio do site | redecanais.af mostra um desafio **interativo** do Turnstile (`cType: 'interactive'`) pelo IP do WARP. A caixa é `input[type=checkbox]` (aria-label "Confirme que é humano") num shadow root fechado dentro do iframe de `challenges.cloudflare.com`. Com a ação de desafio, o clique aconteceu em ~10 s e o desafio sumiu em ~52 s. No pedido seguinte não houve desafio (cookie no perfil). |
| 2026-09-23 | Site fora | Depois do desafio, o redecanais.af respondia **522** (a Cloudflare não alcança o servidor do site), levando ~50–80 s. Problema do site, não do bot. |

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
- [x] `POST /v1/navegar` só com `url` devolvendo o HTML de uma página de lista do site (redecanais.press, 2026-09-26)
- [x] Conferir a navegação como "typed" no log (`navegou (transição typed ...)`)
- [x] Fila, `about:blank` ao fim e reabertura do Chrome por memória (fila testada em rajada, 2026-09-26)
- [x] Vigia do desafio: log + gancho vazio + `status: "desafio"` se não sumir
- [ ] Timer do systemd instalado (`sudo bash implantacao/instalar.sh`) e testado com um reboot real
- [x] Teste de fingerprint (creepjs / sannysoft) pelo próprio bot, com o resultado anotado

## Fase 2: captura de rede

- [x] Extensão observa a rede com `chrome.webRequest` (URL, método, status, tipo, horário)
- [x] Parâmetro `esperarRede` (regex + timeout) e campo `rede` na resposta
- [x] Testar no site e anotar o padrão da URL do vídeo

## Fase 3: cliques

- [x] Medição do seletor em todos os frames + conta da posição na tela (iframes + janela)
- [x] Clique com xdotool: mouse em curva, rolagem pela roda do mouse se o elemento estiver fora da tela
- [x] Ação `clicar` com `seExistir` e ação `esperar`
- [x] Ações `"quando": "desafio"` + busca dentro de shadow DOM fechado + filtro `frame`
- [x] Clique na caixa do Turnstile resolvendo o desafio (testado em 2026-09-23)
- [x] Testar o fluxo completo no site: abrir episódio → clicar no play → capturar a URL do vídeo
- [x] Conferir a precisão do clique dentro de iframe do player

## Fase 4: exposição e operação

- [x] `https://bot.iptv01.asia` por um túnel próprio (`bot-supremo`), serviço `tunel` no compose
- [ ] Avaliar se o cron de reboot a cada 12h ainda faz sentido

## Fase 5: ligar o projeto-iptv

- [x] Reescrever as chamadas de `../projeto-iptv/src/function_rc.ts` para o contrato novo:
      `getHtmlCriptografado` → só `url`; `getReturnJsExecuted` + `Js_getLinkMp4` → `url` + `acoes`
      de clique + `esperarRede`
- [x] Adaptar o parser do iptv ao HTML renderizado
- [x] Conferir o cache de URLs de vídeo (`mp4-list-*`) e o `/proxy-rc` com o serviço novo
- [ ] Rodar alguns dias e observar se há ban

## Fase 6: desligar o FlareSolverr antigo

- [ ] Parar e remover o container `content-proxy-web-01` (libera ~620 MB)
- [ ] Tirar do `~/start.sh` os passos do proxy antigo e conferir que nada mais depende dele

## Fase 7: vídeo mais rápido (próximo trabalho)

Um episódio fora do cache levava ~30–38 s; agora leva ~15 s pela TV (2026-09-26). Medições, ideias e regras em
[MANUAL.md, seção 11](MANUAL.md#11-tempo-para-achar-o-link-do-vídeo-para-quem-for-otimizar).

- [x] Pedidos repetidos: 10 simultâneos e 12 espalhados no mesmo episódio = 1 visita ao site
- [x] Linha do tempo por pedido (log `tempo ...`, `linhaDoTempo`, `marcarRede`) e medição (MANUAL seção 11)
- [x] Sem a espera fixa de 15 s: clica quando o player termina de carregar, com confirmação pelo site
- [x] Ações logo após o documento chegar (`esperarPagina`, mudança de contrato)
- [x] `ATRASO_DIGITACAO_MS` 25 → 15 e conferência do endereço digitado
- [ ] ~~Buscar o próximo episódio em segundo plano~~: o dono descartou (2026-09-26)
- [x] `focou` lento: janela guardada e só ativada quando precisa (0,02 s)
- [x] Registrar os tempos novos em "Descobertas" e atualizar a seção 11 do MANUAL
- [ ] Observar alguns dias quantas vezes o iptv usa a reserva (`[bot-supremo] reserva:` no log)

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
| 2026-09-23 | Diagnóstico do 522 | A página de erro diz "Browser: Working / Cloudflare (Rio de Janeiro): Working / Host: Error". Todos os caminhos (PC de casa, WARP) recebem só o desafio comum "Just a moment...", sem mensagem de ban (no ban de setembro vinha 403 "has banned your IP", erro 1006/1106). O dono confirmou pelo navegador dele que o site caiu. Lição: **522 = servidor do site fora**, não bloqueio do bot. O notebook direto nem resolve o DNS do site (bloqueio do provedor). |
| 2026-09-26 | Domínio novo | O site agora é **redecanais.press** (o .af caiu). O cookie `cf_clearance` do perfil continuou valendo: nenhum desafio nos testes. |
| 2026-09-26 | Service worker | O site tem service worker que atende as navegações: a página chega pelo SW (`xmlhttprequest`, aba `-1`), sem request `main_frame` na aba. Isso deixava `statusHttp` vazio e fazia o bot achar que a digitação falhou (abria de novo). Corrigido: status pelo SW e início da navegação por `webNavigation.onBeforeNavigate`. |
| 2026-09-26 | Listas | `/final_mapafilmes.txt` (17.250 filmes) e `/final_mapa.txt` (5.635 séries) existem. O Chrome mostra o texto num `<pre>`, escapado e lido como Windows-1252 (UTF-8 sem charset). O iptv desfaz as duas coisas (`textoDoPre`). |
| 2026-09-26 | Série | Página da série (`/browse-...-videos-1-date.html`) renderizada tem o mesmo bloco `pm-category-description` ... `pm-ul-browse-videos` que o parser antigo usa; só troca `<br />` por `<br>`. Episódios são `/musicvideo.php?vid=<id>`. Acentos corretos. |
| 2026-09-26 | Player | O player fica no iframe `player3/server.php?...`. O botão de play grande é `#submit` (`button.captcha_button`). Ao clicar, o player chama `player3/serverforms.api?...` e, se der certo, o vídeo toca (abre em tela cheia). |
| 2026-09-26 | Limite do serverforms | **O `serverforms.api` tem limite por IP e tempo.** Várias tentativas seguidas (cada falha faz o player repetir 4x) → 503/521, inclusive para cliques feitos à mão. Depois de ~10 min parado, volta a tocar. Não é ban permanente, nem Linux, nem o bot. O PC do dono funcionava porque o IP do WARP dele é outro. Pedidos de vídeo precisam ser espaçados (o iptv já guarda as URLs por 4 h). |
| 2026-09-26 | URL do vídeo | O player baixa por um proxy do site: `https://<host>.null-null.shop/<...>/proxy?container=videos&refresh=<n>&url=https://<host>/V/<servidor>/videos/<ID>.mp4?sv=<n>&nu3zAQc9HC3GbwJq=<validade>-<assinatura>`. O mp4 de dentro recusa conexão direta (520); o proxy exige os headers `h31ffadrg3bb7` e `x-requested-with` (os mesmos do `/proxy-rc`). A assinatura vale ~1 h. Padrão usado no iptv: `[?&]url=https?://[^?&]+[.]mp4[?]`. |
| 2026-09-26 | Fluxo do vídeo | `url` do episódio → esperar 15 s → clicar `#submit` (frame `player3/server.php`) → `esperarRede`. Leva ~30–45 s. Testado em produção no iptv (`/get-list-link-mp4-rc` + `/proxy-rc` = 206 `video/mp4`). |
| 2026-09-26 | Host estranho | O host do proxy começa com `-_` (`-_kerberos-__tcp-...null-null.shop`). O `wrangler dev` local recusa esse `fetch` ("internal error"), mas a borda real da Cloudflare aceita. Para testar o `/proxy-rc`, só em produção. |
| 2026-09-26 | iptv no ar | projeto-iptv commit `32a7f68`, versão `89b2bbd6` em produção (anterior: `a65d036f`). Listas, série e mp4 pelo bot. Cache do mp4 limitado pela validade do link. |
| 2026-09-26 | Fila | Rajada de 10 pedidos com a fila antiga (máx. 5 no total): 5 levaram 429. Fila foi para 30, e pedidos com o corpo idêntico a um que ainda roda passaram a esperar o mesmo resultado (testado: 12 simultâneos, 2 repetidos, todos entregues em ordem). |
| 2026-09-26 | 524 da Cloudflare | Resposta que não começa em ~120 s leva 524 (115 s passou; 135 s → 524 em 125 s). Com 10 vídeos pelo iptv, do 4º em diante todos caíam. Correção: `/v1/navegar` manda o cabeçalho na hora e um espaço a cada 20 s até o JSON. |
| 2026-09-26 | Limite na prática | 11 vídeos seguidos (~35 s cada) capturaram o link, sem 503. O limite do `serverforms.api` estoura com tentativas repetidas em rajada, não com uso normal em fila. |
| 2026-09-26 | Páginas de série | A lista usa `/browse-…-videos-1-date.html` (5.634 de 5.635). As páginas `…-lista-completa-de-episodios-video_….html` têm outro formato e o parser do iptv devolve vazio. |
| 2026-09-26 | iptv robusto | Falha do bot não apaga mais lista nem série boa do KV; `fetchBot` tenta de novo em 429/5xx; a busca do link de vídeo usa `waitUntil` e vai para o cache mesmo se a TV desistir. Operação completa em [MANUAL.md](MANUAL.md). |
| 2026-09-26 | TV que desiste | Quando o cliente fecha a conexão, a Cloudflare encerra o Worker (o `waitUntil` ganha só ~30 s) e o link achado pelo bot não chegava ao KV. Solução: `reaproveitarMs` no contrato (resultado ok guardado no bot para o próximo pedido idêntico). Testado: desistiu em 15 s, clique 45 s depois → link em 0,1 s. A Cloudflare também fechou uma conexão cliente→Worker sem resposta em ~270 s. |
| 2026-09-26 | Erro meu no cache | `nu3zAQc9HC3GbwJq=<n>` é o **horário de criação** do link, não a validade (link de 1h26 ainda tocava). A conta de TTL que fiz com ele dava negativa e o iptv não guardava nada; o dono viu o mesmo episódio levar 30 s de novo. Voltou para 4 h fixas. O `reaproveitarMs` do bot foi removido: o dono não quer cache no notebook. |
| 2026-09-26 | Painel | `painel.iptv01.asia` no ar (login do dono, sessão de 10 anos). Testado pela internet: login, semáforo, dados, pausa (pedido esperou e terminou ao voltar), testar o site, reabrir o Chrome, reiniciar o bot, tela (websocket passando pela Cloudflare) e **reiniciar o notebook**: clique 19:29, notebook de volta 19:32, bot e painel no ar 19:37, sem ninguém mexer. |
| 2026-09-26 | Repetidos | 10 cliques simultâneos no mesmo episódio pelo iptv: 1 visita ao site, os 10 com o mesmo link (46 s). 12 cliques, um a cada 8 s por 88 s: 1 visita. Os que chegaram até 2 s depois do fim já acharam no KV (o "não achei" em cache do KV não atrapalhou). |
| 2026-09-26 | Tempo do vídeo | Linha do tempo em 7 vídeos: o player (`player3/server.php`) fica pronto em 7,6–15 s, mas o clique só sai em 28–36 s (página quieta + 15 s fixos). Clique → link: 2,1–6,1 s. `windowactivate` às vezes leva 0,5–4 s. Worker + túnel ~0,75 s. Detalhes na seção 11 do MANUAL. |
| 2026-09-26 | Vídeo rápido | Clicando quando o iframe do player termina de carregar (sem os 15 s nem a página quieta): 10 de 10 vídeos com link (fora a tecla perdida), 1 clique cada (o site sempre reagiu na 1ª), `serverforms` 200, **12,0–14,4 s** (antes 31–41 s). O player aparece "completo" duas vezes, com ~1 s de diferença; clicar depois do 1º já funcionou. |
| 2026-09-26 | Tecla perdida | Uma vez a digitação perdeu as duas primeiras letras (`tps://...`) e o Chrome buscou no Google; o pedido falhou esperando o `#submit`. Não se repetiu em outros 30 pedidos. Correção: o bot confere o endereço no início da navegação e digita de novo. |
| 2026-09-26 | Série | `esperarPagina: completa` dá o mesmo HTML que `quieta` (4 séries, blocos idênticos) e sai em 2,2–2,8 s. |
| 2026-09-26 | Ponta a ponta | iptv `af326e48` + bot `63523e0`: 4 episódios novos pelo `/get-list-link-mp4-rc` em 14,9–21 s (sem usar a reserva), `/proxy-rc` 206 `video/mp4`, 10 cliques simultâneos num episódio novo = 1 visita, 16 s. |

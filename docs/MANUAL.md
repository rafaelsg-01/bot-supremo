# Manual de operação

Para quem (IA ou pessoa) vai **manter** o bot-supremo e a parte do projeto-iptv que usa ele:
como o sistema funciona de ponta a ponta, onde olhar quando algo quebra e como mudar sem estragar.

- As **regras** (o princípio, o contrato da API, o que não pode) estão no [CLAUDE.md](../CLAUDE.md).
  Leia antes deste arquivo.
- O **histórico** do que foi feito e medido está no [ROTEIRO.md](ROTEIRO.md).
- A lista de endereços úteis, com as senhas, está em `../projeto-iptv/LINKS.txt`, **só no PC do dono** (fora do git).
- Este manual é o **como funciona e como consertar**. Atualize-o quando mudar algo que ele descreve.

Estado em 2026-09-26: em produção. A TV e o site do iptv pegam listas, séries e vídeos do Rede Canais
pelo bot-supremo. O FlareSolverr antigo foi removido em 2026-09-28 (o notebook é só do iptv).

---

## 0. Painel: o jeito principal de ver e consertar

**https://painel.iptv01.asia**. O dono entra com usuário e senha (os valores ficam no `.env` do
notebook e no `LINKS.txt` do PC dele). Uma página só, que se atualiza a cada 5 s:
- **semáforo**: "Tudo funcionando" ou a lista do que está errado, em frases simples;
- **notebook, bot, vídeo e containers** (o cartão "Vídeo" mostra quantos estão assistindo, os links
  guardados, a última busca e o IP público do WARP);
- **últimos pedidos**, com os números do dia;
- **log**;
- **a tela do Chrome ao vivo, com mouse e teclado**, de qualquer lugar.

Botões, por nível (a página mostra a ordem por sintoma). Todos testados pela internet em 2026-09-28:

| Nível | Botão | O que faz | Tempo |
|---|---|---|---|
| Testar | Testar o vídeo | link do episódio usado por último (testa ou busca outro) + 4 MB baixados pela internet | 2–30 s |
| Testar | Testar o site | abre `/final_mapa.txt` no domínio da vez (pelo serviço de vídeo, que troca de domínio se preciso) | 2–10 s |
| Leve | Reiniciar o túnel | `bot-supremo-tunel` (responde antes, porque o painel também passa por ele) | ~10 s |
| Leve | Reiniciar o vídeo | `bot-supremo-video` (quem assiste precisa dar play de novo) | ~5 s |
| Leve | Reabrir o Chrome | fecha e abre o Chrome do bot | ~15 s |
| Médio | Reiniciar o bot | container `bot-supremo` | ~30 s |
| Médio | Reiniciar a internet (WARP) | reinicia o `warp`, espera `healthy` e reinicia bot e vídeo. **Troca o IP do WARP** | ~1 min |
| Forte | Reiniciar o notebook | `systemctl reboot` por um container privilegiado. **Troca o IP do WARP** | ~5 min (painel volta em ~4) |

- Vídeo não abre ou trava: Testar o vídeo → Reiniciar o túnel → Reiniciar o vídeo → Reiniciar a
  internet → Reiniciar o notebook.
- Filme ou episódio novo não carrega: Testar o site → Reabrir o Chrome → Reiniciar o bot → Reiniciar
  a internet → Reiniciar o notebook.

**Reparo automático (2026-10-01):** quando o player não pede o vídeo (`nenhuma request casou ...`), o
serviço de vídeo faz sozinho o "Reabrir o Chrome" e tenta a busca mais uma vez, no máximo uma vez a cada
10 min (detalhes na seção 12). O cartão "Vídeo" mostra quando foi a última vez.

**Domínio do site (2026-10-06):** cartão com dois campos. O **1** só o dono escreve (pode ficar vazio);
o **2** o sistema preenche sozinho quando o site muda de endereço (e o dono também pode). O sistema
tenta o 1 e, se ele não servir, o 2. Mostra a última troca automática e avisa no semáforo quando o 1
está sendo pulado. Como funciona: seção 5.

"Pausar o bot" segura os pedidos (até 30 min) para mexer na tela sem conflito. O semáforo também
avisa quando `video.` ou `bot.iptv01.asia` não respondem pela internet (checado a cada 60 s) e quando a
última busca de vídeo deu erro.

Para a IA, os comandos das seções abaixo continuam valendo. O painel é para o dono. Se precisar de algo
que o painel não mostra, acrescente no painel (`painel/app.py` e `painel/pagina.html`).

## 1. O caminho inteiro, de um clique na TV até o vídeo

```
TV / celular
  └─> iptv01.asia  (Cloudflare Worker "iptv-self-2", repo ../projeto-iptv)
        ├─ cache no KV (Kv_iptvSelf): listas e séries (links de vídeo NÃO: ficam no notebook)
        ├─ POST https://video.iptv01.asia/v1/mp4     (Bearer videoRcToken) ─> container bot-supremo-video
        │     (cache SQLite, testa o link antes de entregar; link novo = pede ao bot em 127.0.0.1:8080)
        └─ POST https://bot.iptv01.asia/v1/navegar   (Bearer botSupremoToken)
              └─> Cloudflare ─> túnel "bot-supremo" ─> container bot-supremo-tunel (cloudflared)
                    └─> http://warp:8080  (rede flare-net)
                          └─> container bot-supremo (usa a rede do container warp)
                                ├─ serviço Python (fila, xdotool, regras do pedido)
                                ├─ Chrome real + extensão (lê a página e a rede)
                                └─> redecanais.<domínio da vez>, saindo pela Cloudflare WARP
```

Na hora de **tocar**, a TV recebe `https://video.iptv01.asia/proxy-rc?url=<link>&pagina=<página>&sig=<assinatura>`
e o vídeo **passa pelo notebook** (container `bot-supremo-video`, saindo pelo mesmo WARP do Chrome).
Desde 2026-09-28 o site prende o link ao IP de quem o gerou, então só o notebook consegue baixar.
O site (`static/script.js`) ainda monta `https://iptv01.asia/proxy-rc?url=...`: o Worker só assina e
responde 302 para o `video.iptv01.asia`. Detalhes na seção 12.

## 2. Onde fica cada coisa

| O quê | Onde |
|---|---|
| Código do bot | este repo (público), `github.com/rafaelsg-01/bot-supremo`, branch `main` |
| Imagem Docker | `ghcr.io/rafaelsg-01/bot-supremo:latest` (e `:<sha do commit>`), feita pelo GitHub Actions |
| Notebook | `ssh servidor-caseiro`, repo clonado em `~/bot-supremo` |
| Segredos do bot | `~/bot-supremo/.env` no notebook (`BOT_TOKEN`, `VIDEO_TOKEN`, `VNC_SENHA`, `TUNEL_ID`). Nunca no git |
| Serviço de vídeo | pasta `video/`, container `bot-supremo-video` (mesma imagem), `warp:8070`, público em `video.iptv01.asia` |
| Cache dos links de vídeo | `~/bot-supremo/dados/video/cache.sqlite` no notebook (sem prazo; pode apagar, só perde o cache) |
| Credencial do túnel | `~/bot-supremo/dados/tunel/credenciais.json` no notebook |
| Perfil do Chrome | `~/bot-supremo/dados/perfil` (cookies, `cf_clearance`, service worker do site) |
| Chave da extensão | `~/bot-supremo/dados/estado` (se apagar, o ID da extensão muda e ela é reinstalada) |
| Código do iptv | `../projeto-iptv` (Worker `iptv-self-2`, deploy com `npx wrangler deploy --env production`) |
| Token do bot no iptv | secret `botSupremoToken` do Worker; para `npm run dev`, em `.dev.vars` (fora do git) |
| Token do vídeo no iptv | secret `videoRcToken` do Worker = `VIDEO_TOKEN` do notebook (os dois têm que ser iguais) |
| Domínio do site | no notebook: `dominio1`/`dominio2` no SQLite do serviço de vídeo, editados no painel (cartão "Domínio do site"). Troca sozinho (seção 5). `rcDominio` do iptv é só reserva |

## 3. A vida de um pedido no bot

Arquivos: [servico/principal.py](../servico/principal.py) (API e fila) e
[servico/pedido.py](../servico/pedido.py) (classe `Execucao`).

1. **Validação** (`validar`). Corpo errado → 400. Token errado → 401.
2. **Junção de pedidos iguais.** Se um pedido com o **corpo idêntico** já está na fila ou rodando, o
   novo não entra: espera o mesmo resultado (`Fila.em_andamento`). Isso protege o limite do site
   quando a TV repete o mesmo episódio.
   Nada é guardado depois que o pedido termina: **o dono não quer cache no notebook**. Cache é
   só no KV do iptv.
3. **Fila.** Um pedido por vez, na ordem de chegada. Até 30 no total (`FILA_MAXIMA`). Acima disso → 429.
4. **Resposta que mantém a conexão viva.** O cabeçalho HTTP 200 sai **na hora**, e o serviço manda
   um espaço a cada 20 s até o JSON ficar pronto. **Motivo:** a Cloudflare corta com 524 a resposta
   que não começa em ~120 s, e um pedido esperando na fila passa disso fácil (medido em 2026-09-26).
   Se quem pediu desconectar, o pedido **continua** até o fim (`asyncio.shield`) para a aba não ficar
   pela metade.
5. **Preparar a aba** (`prepararAba` na extensão): uma aba só, em `about:blank`.
6. **Navegar** (`_navegar`): foca a janela (o ID fica guardado; só ativa se ela não for a ativa),
   `ctrl+L`, digita a URL (15 ms por letra), `Delete`, `Enter` (xdotool). Confirma pelo **início** da
   navegação (evento `nav/inicio` ou request `main_frame`) e **confere o endereço**: se começou em
   outro (uma tecla perdida já virou busca no Google), digita de novo; na 2ª vez abre pela extensão.
   Se em 10 s nada começar, também abre pela extensão. Os dois casos avisam em `erros`.
7. **Carregar** (`_esperar_carregar`), conforme `esperarPagina`: `quieta` (padrão) = frame principal
   completo e rede quieta por 1 s (no máximo 8 s extras); `completa` = só o frame completo; `nao` =
   pula esta etapa. Se o título for "Um momento…"/"Just a moment...", entra em `_tratar_desafio`:
   chama o gancho vazio e roda as ações `quando: "desafio"` (clique no Turnstile). Com `nao`, o
   desafio é tratado de dentro do `clicar`.
8. **Ações** `quando: "carregada"`, em ordem. `clicar` pede à extensão a medida do seletor em todos os
   frames (`localizar`), converte para pixel da tela (`_ponto_na_tela`), rola com a roda se
   precisar, move o mouse em curva e clica. Com `frameCompleto`, antes espera o iframe terminar de
   carregar; com `confirmarRede`, depois espera o site reagir e só clica de novo se ele não reagiu.
9. **esperarRede**: espera a primeira request que casar com a regex (inclui as do service worker,
   aba `-1`).
10. **Ler o HTML** (`lerHtml`, mundo isolado) e montar a resposta.
11. **Limpar**: fecha abas extras e volta a `about:blank`. Se o Chrome passou de 1100 MB, reabre.

Tempos típicos no notebook: página de série 2–3 s, lista `.txt` 3–4 s, **vídeo 12–15 s** (Chrome
recém-aberto: ~25 s no primeiro).

## 4. Como o iptv usa o bot

Cliente: [../projeto-iptv/src/bot_supremo.ts](../../projeto-iptv/src/bot_supremo.ts)
- `navegarBot(env, pedido)` acrescenta **sempre** a ação do desafio (clicar
  `input[type=checkbox]` no frame `challenges.cloudflare.com`, `quando: "desafio"`).
- `fetchBot` tenta de novo até 3 vezes, com 10 s entre elas, se o bot responder 429/502/503/504/530
  ou a rede falhar (notebook reiniciando, container sendo recriado). Repetir é seguro por causa da
  junção de pedidos iguais.
- `textoDoPre(html)`: o Chrome mostra `.txt` dentro de um `<pre>`, com o HTML escapado e o UTF-8
  lido como Windows-1252. A função desfaz as duas coisas.

Funções em [../projeto-iptv/src/function_rc.ts](../../projeto-iptv/src/function_rc.ts):

| Função | Pedido ao bot | Cache (KV) |
|---|---|---|
| `Function_getMovieList` / `Function_getSerieList` | `navegarSite(env, '/final_mapafilmes.txt' / '/final_mapa.txt')`: `POST video.iptv01.asia/v1/pagina`, que escolhe o domínio e chama o bot | listas cruas `movies_list_rc` / `series_list_rc`, renovadas em segundo plano depois de 12 h. Lista vazia (falha) **não** substitui a boa |
| `Function_getSerieSingle` | `navegarSite` com a página `/browse-<serie>-videos-1-date.html`, `esperarPagina: 'completa'`; sem temporadas, repete com `quieta` | `movie_info_<serie>_rc`, 3 dias. Resultado sem temporadas (falha) **não** é guardado |
| `Function_getLinkMp4ListComCache` | não fala com o bot: chama `POST video.iptv01.asia/v1/mp4 {pagina}` (o `url` que ainda vai junto é ignorado). Quem pede ao bot é o serviço de vídeo do notebook (`video/busca.py`), com o pedido **rápido** (`esperarPagina: 'nao'` + clicar `#submit` no frame `player3/server.php` com `frameCompleto` e `confirmarRede: player3/serverforms[.]api`, até 3 cliques, + `esperarRede` com a URL do vídeo) e a **reserva** (só se o player não reagiu: 15 s parado e depois o play) | **no notebook** (SQLite, sem prazo, testado a cada entrega). Nada no KV |

- Os 4 lugares que pedem vídeo (`/get-list-link-mp4`, `/get-list-link-mp4-rc`, `src/tv/movie.ts`,
  `src/tv/episode.ts`) usam `Function_getLinkMp4ListComCache` e caem no mesmo cache do notebook (a
  chave é a página, sem o domínio): quem abrir no celular deixa pronto para a TV.
- A TV recebe as URLs de `urlsVideoRcTv` (já apontando para `video.iptv01.asia`, com a página, para o
  notebook se recuperar se o link morrer). O site recebe a lista crua e passa pelo 302 do `/proxy-rc`.
- **O cache do link de vídeo mora no notebook** (decisão do dono, 2026-09-28): sem prazo e **testado
  antes de cada entrega** (1 byte do vídeo, ~0,2–0,7 s). Se o teste falhar, o link é apagado e o bot
  busca outro na hora. Não existe mais `mp4-list-*` no KV (as chaves velhas só expiram).
  Histórico: em 2026-09-26 eu (IA) confundi `nu3zAQc9HC3GbwJq=<n>` com a validade do link; **é o
  horário de criação**. O teste antes de entregar acabou com a necessidade de adivinhar validade.
- O bot continua sem cache: ele não guarda resultado de pedido.
- **Cuidado:** o HTML, o CSS e o JS **do cliente** `/tv` rodam num navegador de TV muito antigo, e
  uma vírgula quebra tudo. Tudo acima é código do servidor (Worker). Não mexa no cliente `/tv` sem
  necessidade e sem testar na TV.

## 5. O que se sabe do Rede Canais (redecanais.ae em 2026-10-06)

- **Domínio muda de tempos em tempos** (`.af`, `.press`, `.ae`…), e **o sistema troca sozinho**
  (desde 2026-10-06, `video/dominio.py`). Como o .press morreu: os caminhos fundos
  (`/episodio_x.html`) passam por `www.google.com/url` e terminam em `notfound.vg/`; alguns (`/final_mapa.txt`)
  vão para `redecanais.ae/<caminho em hex>`; e a **raiz** (`https://redecanais.press/`) vai para
  `https://redecanais.ae/`.
  - Todo pedido ao site leva `hostEsperado: "(^|[.])redecanais[.]"`: se a página parar fora do site, o
    bot desiste em segundos (antes, 40–90 s esperando o `#submit`).
  - **Falha de domínio** = saiu do site, `statusHttp` ≥ 400 ou uma ação `clicar` sem `seExistir` não
    achou o elemento. Player que reagiu e não deu vídeo **não** conta (é o limite por IP).
  - Ordem: domínio 1 → domínio 2. Se os dois falharem, procura um `redecanais.<outro>` por onde a
    página passou (`navegacao`/`urlFinal`) e, se não achar, **abre a raiz** do domínio que falhou e vê
    aonde ela leva. Só aceita se mudou apenas o que vem depois do primeiro ponto (`redecanais.press` →
    `redecanais.ae`; nunca `google.com`, `notfound.vg` ou `redecanais-oficial.chatango.com`). Grava no
    domínio 2 e tenta de novo.
  - Se a página **funcionou** mas terminou em outro `redecanais.*`, esse vira o domínio 2 também.
  - Nunca volta sozinho para um domínio abandonado (`dominios_velhos` no SQLite). O dono pode.
  - Depois de uma falha de domínio no 1, ele é pulado por 30 min (o painel avisa). Nunca é apagado
    sozinho: o dono apaga quando o 2 estiver bom.
  - Se nada funcionar: o erro diz "preencha o domínio no painel". Descubra o domínio (abra o antigo no
    PC pelo WARP ou procure o canal oficial) e escreva no campo 1 ou 2.
- O site tem **service worker** que atende as navegações. Por isso o status HTTP da página às vezes
  só aparece nas requests da aba `-1` (o serviço já trata isso).
- **Listas:** `/final_mapafilmes.txt` e `/final_mapa.txt`.
- **Séries:** a lista aponta para páginas `/browse-…-videos-1-date.html`. O parser do iptv procura o
  bloco `pm-category-description` e separa temporadas por `x-large;`. As páginas
  `…-lista-completa-de-episodios-video_….html` têm outro formato (`itemprop="description"`) e o
  parser não as entende, mas só 1 das 5.635 séries da lista não usa `/browse-`.
- **Player:** iframe `player3/server.php`, botão de play `#submit`. Ao clicar, o player chama
  `player3/serverforms.api` e depois baixa o vídeo pelo proxy do site:
  `https://<host>.null-null.shop/…/proxy?container=videos&refresh=…&url=https://<host>/V/<servidor>/videos/<ID>.mp4?sv=…&nu3zAQc9HC3GbwJq=<horário de criação, epoch>-<assinatura>`.
  A regex usada no iptv é `[?&]url=https?://[^?&]+[.]mp4[?]`.
- **O link do vídeo é preso ao IP de quem o gerou** (desde ~22h40 de 2026-09-27): vem com
  `ip=<IPv6 do WARP>` dentro da assinatura. Só esse IP recebe o vídeo; outro IP, o IPv4 do mesmo WARP
  ou o `ip=` trocado dão `404 Not Found`. Por isso o vídeo passa pelo notebook, e **pelo IPv6** (o DNS
  do WARP não devolve o IPv6 desses hosts; o serviço usa DNS por HTTPS). Os headers antigos
  (`h31ffadrg3bb7`, `x-requested-with`) deixaram de ser conferidos, mas o serviço ainda manda.
  **Desde 2026-10-09 o proxy exige `Referer`**: sem ele, `404 Not Found` (qualquer valor serve; o
  serviço manda `Origin`/`Referer` do domínio da vez, como o Chrome). O link também ganhou `ipv6=` e
  `ip_bind=`.
  O mp4 de dentro recusa conexão direta (520).
- O host do proxy (`-_kerberos-...null-null.shop`) tem um nome que o `ssl` do Python recusa, embora o
  certificado `*.null-null.shop` seja válido: o serviço confere o nome à mão (`video/origem.py`).
- **Limite do `serverforms.api`, por IP e tempo.** Em 2026-09-26, **11 vídeos seguidos** (um a cada
  ~35 s) passaram sem problema. O que estoura o limite são **tentativas repetidas em rajada**: cada
  play que falha faz o player tentar 4 vezes, e cliques repetidos acumulam. Quando estoura, o site
  responde 503/521 **até para cliques feitos à mão**, e volta sozinho depois de ~10 min parado.
- Para depurar o que o player manda e recebe: pedido ao bot com `"cabecalhos": true` (headers de
  cada request de `rede`).

## 6. Diagnóstico: por onde começar

Do PC do dono (Git Bash). O token está no `.env` do notebook:
`ssh servidor-caseiro "grep ^BOT_TOKEN= ~/bot-supremo/.env | cut -d= -f2"`.
A zona `iptv01.asia` bloqueia o User-Agent `Python-urllib` (403). Use `curl` ou mande um UA.

```bash
# 1. O bot está vivo? (sem token)
curl -s -A t https://bot.iptv01.asia/saude
# ok, chrome, memória, extensão conectada/versão, fila (ocupada, pedidos), tela (xorg/xvfb)

# 2. Log do bot: cada pedido vira uma linha "pedido <url> -> <status> em <ms> (<etapas>) erros=[...]"
ssh servidor-caseiro "docker logs --since 30m bot-supremo 2>&1 | grep -E 'pedido https|ERROR|WARN'"

# 3. Um pedido na mão
curl -s -A t -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://redecanais.ae/final_mapa.txt","html":false}' https://bot.iptv01.asia/v1/navegar

# 4. Foto da tela do Chrome (PNG)
curl -s -A t -H "Authorization: Bearer $TOKEN" https://bot.iptv01.asia/v1/tela -o tela.png

# 5. Descobrir seletores: "inspecionar" devolve frames, iframes, inputs e botões de cada frame
#    {"url":"...","inspecionar":"#submit","html":false}

# 6. Log do iptv em produção (linhas "[bot-supremo] <url> -> <status> ...")
cd ../projeto-iptv && npx wrangler tail --env production

# 7. Estado dos containers e memória do notebook
ssh servidor-caseiro "docker ps; free -m; systemctl status bot-supremo-atualizar.timer --no-pager"
ssh servidor-caseiro "journalctl -u bot-supremo-atualizar --since '1 hour ago' --no-pager | tail"
```

Ver e mexer no Chrome do bot: pelo painel (seção 0), de qualquer lugar. Alternativa de dentro da
rede, sem o painel: `powershell -ExecutionPolicy Bypass -File implantacao\tela.ps1` (túnel SSH).
Enquanto alguém mexe pela tela, pause o bot: os pedidos e a pessoa usam a mesma aba.

Teste "no seco" sem o serviço (só xdotool e capturas de tela):
`ssh servidor-caseiro "docker exec -u pessoa -e DISPLAY=:0 bot-supremo xdotool ..."` e
`docker exec -u pessoa -e DISPLAY=:0 bot-supremo scrot -o /tmp/x.png` + `docker cp`.

## 7. Problemas conhecidos e o que fazer

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `/saude` → 502/`error code: 502` | container recriando (deploy, reboot) | esperar 1–2 min. O timer sobe tudo sozinho |
| `/saude` com `extensao.conectada: false` | Chrome travado num aviso | o serviço reabre o Chrome sozinho em 2 min; olhe `/v1/tela` |
| Vídeo volta `erro` com `nenhuma request casou`, e voltou só depois de reabrir o Chrome (2026-10-01) | Chrome num estado ruim | o serviço de vídeo já reabre o Chrome sozinho e tenta de novo (seção 12). Se o erro vier com "mesmo depois de reabrir o Chrome", é o site: ver a linha de baixo |
| Vídeo volta `false`, log do bot com `nenhuma request casou` e o player chamando `serverforms.api` com 503/521 | limite do site estourado | parar de pedir vídeos novos por ~10 min. Os que estão no cache continuam tocando |
| `[bot-supremo] reserva:` no `wrangler tail` | o pedido rápido não fez o player reagir e o iptv usou o pedido antigo | normal de vez em quando. Se for sempre, ver a linha `tempo` do bot (`frame_completo`, `clicou`, `sem_reacao`) |
| `aviso: a digitação abriu ...` em `erros` | uma tecla se perdeu (ex.: alguém mexeu no teclado do notebook) | nada: o bot digita de novo sozinho. Se virar frequente, subir `ATRASO_DIGITACAO_MS` no `.env` |
| `o seletor '#submit' não apareceu` | player demorou ou mudou | rodar um pedido com `inspecionar: "#submit"`, ver `/v1/tela`. Se o site mudou o player, ajustar as ações em `Function_getLinkMp4List` |
| Todas as páginas com `statusHttp` 5xx (521/522) | site fora ou domínio mudou | o sistema tenta o outro domínio e descobre o novo sozinho (seção 5). Se o painel disser "preencha o domínio", abra o site no PC e escreva o novo no cartão "Domínio do site" |
| `o seletor '#submit' não apareceu` com `a página saiu do site: notfound.vg/` | domínio mudou (06/10: .press) | automático (seção 5). Reabrir o Chrome não resolve |
| `status: "desafio"` | o Turnstile não passou | ver `/v1/tela`. Se a caixa mudou, ajustar `Const_acaoDesafio` em `bot_supremo.ts` |
| 524 no iptv | resposta sem começar em ~120 s | não deveria mais acontecer (item 4 da seção 3). Se voltar, conferir se a resposta continua em streaming |
| 429 "fila cheia" | mais de 30 pedidos acumulados | algo está chamando em loop. Achar quem no `wrangler tail` |
| Série aparece sem episódios | página em formato diferente, ou o bot falhou | pedir a página ao bot e testar `parseSeasonsAndEpisodes` com o HTML |
| Lista com acentos quebrados (`NÃºmeros`) | `textoDoPre` não desfez a codificação | conferir o `<pre>` cru que o bot devolve |
| Depois de reboot, bot sem rede | o `warp` foi recriado pelo `~/start.sh` | o `atualizar.sh` recria o nosso container em até 2 min |
| IP do WARP banido (403 em tudo) | ban | refazer o registro do WARP (ver CLAUDE.md, com backup de `~/content-warp/data/`) |
| Vídeo volta `erro` com `o link novo não entregou vídeo (404)`, mas a tela mostra o filme tocando | o proxy do site passou a conferir algum header (09/10: `Referer`) | capturar o link com `cabecalhos: true` e testar pelo container de vídeo, header por header, até achar o que falta (`video/origem.py`) |
| Só a TV antiga (Samsung 2012) não toca: "Servidor 1: Erro", e nenhum pedido dela em "Repasses de vídeo" | o player da TV não conecta em `https://video.` (certificado só ECDSA) | a TV tem que receber o vídeo por `http://video.` ou por https num host com RSA. Seção 12, "TV antiga". Teste: `http://video.iptv01.asia/tv?k=<chave>` |
| Vídeo não abre, `/proxy-rc` 404 | link preso a outro IP (o IP do WARP mudou) | nada: o serviço testa antes de entregar e busca outro. Na TV, a URL leva a página e se recupera sozinha |
| Vídeo não abre, `video.iptv01.asia` 403 "assinatura inválida" | `videoRcToken` do Worker diferente do `VIDEO_TOKEN` do notebook | acertar o secret (`wrangler secret put videoRcToken --env production`) |
| Vídeo não abre, painel "serviço de vídeo não está respondendo" | container `bot-supremo-video` caído ou sem rede (warp recriado) | o `atualizar.sh` recria em até 2 min; `docker logs bot-supremo-video` |
| Vídeo travando / carregando devagar | túnel preso em conexões ruins (em 2026-09-28 ficou em ~7 Mbit/s por um tempo) | `docker restart bot-supremo-tunel` (normal: 80–100 Mbit/s). Medir: `curl -r 0-15728639 -w "%{speed_download}"` numa URL do vídeo |
| 503 "muitos vídeos ao mesmo tempo" | mais de `VIDEO_MAX_STREAMS` (16) conexões de vídeo | subir no `.env` se a CPU aguentar (seção 12) |

## 8. Como mudar e publicar

**bot-supremo:**
- Commit e push na `main` → GitHub Actions publica a imagem (~5 min) → o timer do notebook
  (a cada 2 min) faz `git pull` e `docker compose pull` e recria o container. **Recriar mata os pedidos
  em andamento.** Se houver fila (`/saude`), espere esvaziar antes de fazer push.
- Conferir que a versão nova subiu: `ssh servidor-caseiro "docker exec bot-supremo grep ... /opt/bot-supremo/servico/<arquivo>"`
  e depois `/saude`.
- Mudou a extensão? A versão sobe sozinha (`1.0.<n>`, pelo hash dos arquivos) e o Chrome se atualiza.
  Confira `extensao.versao` = `esperada` no `/saude`.
- Iterar sem esperar o CI: modo dev (ver CLAUDE.md, `touch .modo-dev`). Apague o `.modo-dev` ao terminar.
- **Voltar atrás:** `git revert <commit>` + push (o caminho normal). Emergência: no notebook, trocar
  `:latest` por `:<sha bom>` no `compose.yml`, criar `.modo-dev` para o timer parar de puxar, e
  rodar `docker compose up -d`. Desfazer depois.

**projeto-iptv:**
- `npx tsc --noEmit -p .` (tem que sair limpo) → `npx wrangler deploy --env production`.
- Voltar atrás: `npx wrangler deployments list --env production` e
  `npx wrangler rollback <version-id> --env production`.
- O dono autorizou commit e deploy no iptv, desde que o `/tv` continue funcionando igual.

## 9. Resiliência verificada (2026-09-26)

| Teste | Resultado |
|---|---|
| 10 pedidos simultâneos ao bot (fila antiga, máx. 5) | 5 atendidos, 5 com 429 → fila foi para 30 |
| 12 simultâneos, 2 repetidos | 12 entregues em ordem. Os repetidos pegaram o resultado do original, sem visitar o site de novo |
| Pedido de 115 s direto | passou |
| Pedido de 135 s direto (antes da correção) | 524 em 125 s → resposta passou a começar na hora, com espaços |
| 10 vídeos novos ao mesmo tempo pelo iptv (antes da correção) | 3 OK; os outros 7 com 524 no Worker, mas o bot terminou todos |
| 11 vídeos seguidos no site | todos capturaram o link (28–44 s cada), sem bater no limite |
| 10 vídeos novos ao mesmo tempo pelo iptv (com a resposta em espaços) | 8 entregues, o último depois de 255 s na fila. Os 2 últimos: a Cloudflare fechou a conexão cliente→Worker em ~270 s, mas o bot terminou os dois |
| Mesmo episódio pedido 5 min depois (cache de 4 h no KV, antes de 2026-09-28) | 1ª vez 34,8 s; 2ª vez 0,27 s |
| Mesmo episódio pelo iptv com o cache no notebook (2026-09-28) | 1ª vez 17,7 s; 2ª vez 0,48 s (com o teste do link) |

Na prática (depois da Fase 7): **um vídeo novo leva ~15 s**. Com vários vídeos novos pedidos juntos,
cada um espera a sua vez (~15 s por vídeo à frente). Depois de achado, o link fica no cache do notebook
e abre em menos de 1 s (testado antes de entregar).

## 10. Pendências

- Observar se o IP do WARP muda sozinho (sem reboot); o cartão Vídeo mostra desde quando é o atual.
- Tirar do iptv o código antigo do FlareSolverr (`getReturnJsExecuted`, `getHtmlCriptografado`,
  `jsGetLinkMp4.js`, rotas `/diag/*`) quando o dono concordar.
- Observar por alguns dias se aparece ban (403) ou falhas no `#submit`.

## 11. Tempo para achar o link do vídeo (para quem for otimizar)

Um episódio que **não** está no cache levava **~30–38 s** do clique na TV até o link; depois da
Fase 7 leva **~15 s** (ver "Resultado" abaixo). Depois fica 4 h no KV do iptv e sai em ~0,1 s.

### Onde o tempo vai (medido em 2026-09-26, etapas da linha `pedido ... -> concluido em N ms ({...})`)

| Etapa (`etapas` no log) | Tempo | O que é |
|---|---|---|
| `navegar` | 2–5 s | foco na janela, `ctrl+L`, digitar a URL (25 ms por letra, `ATRASO_DIGITACAO_MS`) até o servidor responder |
| `carregar` | 7,5–15 s | página completa **e** rede quieta por 1 s (`REDE_QUIETA_MS`), no máximo 8 s extras (`ESPERA_MAXIMA_REDE_QUIETA_MS`). O site tem anúncios e scripts que mantêm a rede ocupada |
| `acoes` | 16–18,6 s | **15 s de espera fixa** + ~1,3 s para achar e clicar `#submit` no iframe `player3/server.php` |
| `rede` | 1,4–4,9 s | depois do clique, o player chama `serverforms.api` duas vezes e então pede o vídeo pelo proxy (é essa request que queremos) |
| (Worker + túnel) | ~1 s | ida e volta iptv → bot |

Exemplos reais: 29,6 s = navegar 4,2 + carregar 7,5 + acoes 16,2 + rede 1,4; 38,5 s = 2,1 + 14,8 + 18,6 + 2,3.

### Linha do tempo fina (medida em 2026-09-26, 7 vídeos, 4 séries, 1 lista)

Cada pedido agora loga uma linha `tempo <url> carga=<loadavg> marco=ms[detalhe] ...` (a resposta traz o
mesmo em `linhaDoTempo`), com ms contados **da chegada do pedido**. Para ver os marcos das requests do
player num pedido direto ao bot, mande `"marcarRede": "serverforms|player3/|[?&]url=https?://[^?&]+[.]mp4"`.

Vídeo, em ms desde a chegada (mín.–máx. de 7 episódios da 3ª temporada de Avatar):

| Marco | Quando | O que se aprende |
|---|---|---|
| `focou` (janela do Chrome ativada) | 66–4.052 | em 4 de 7 pedidos o `windowactivate --sync` levou **0,5–4 s**. Nos outros, 70 ms |
| `digitou` | +1,2–1,3 s | 53 letras × 25 ms |
| `enter` → `commit` | 0,4–1,8 s | resposta do servidor do site |
| iframe `player3/server.php` completo | 7,6–15,0 s | **a partir daqui o `#submit` já pode ser clicado** |
| `completo` (página) | 10,8–18,5 s | anúncios, Disqus, chat |
| `quieta` | +0,2–3,7 s depois | |
| `esperou` (15 s fixos) | 28,1–35,5 s | **15 s parados com o player pronto** |
| `achou` → `clicou` | ~0,1 s + 0,6–1,0 s | o `#submit` aparece na 1ª medição; o resto é o mouse andando |
| `serverforms.api` | 0,4 s após o clique | chamado 2 vezes, ~0,8 s no total, sempre 200 |
| link do vídeo (`casou`) | 2,1–6,1 s após o clique | depois do `serverforms` o player ainda carrega scripts por ~2–3 s |
| `limpo` (resposta sai) | +0,2 s | |
| Worker + túnel (iptv) | ~0,75 s | `curl` no iptv − `duracaoMs` do bot |

Total: 31,3–41,2 s no bot. **Do clique possível (player pronto) até o clique real vão de 13 a 21 s**,
e o `focou` lento soma até 4 s. Sem esses dois, o mesmo vídeo sairia em ~12–20 s.

Páginas (`html`): série 4,2–6,1 s (digitar 62–74 letras ~1,5 s; `commit` 1,9–3,8 s; `completo` +0,5–1,7 s;
`quieta` +1,4–1,8 s; ler o HTML 30 ms), lista `.txt` 3,2 s.

Pedidos repetidos (testado em 2026-09-26): 10 cliques simultâneos no mesmo episódio = 1 visita ao site;
12 cliques, um a cada 8 s por 88 s = 1 visita (os que chegaram durante o pedido pegaram o mesmo
resultado, e os de depois, a partir de 2 s após o fim, já acharam no KV).

Carga do notebook: `loadavg` 2,5–5 durante os vídeos (2 núcleos), com a CPU quase livre fora deles
e ~15 % de espera de disco (HD mecânico).

O pedido é montado em `Function_getLinkMp4List` (`../projeto-iptv/src/function_rc.ts`):
`esperar 15000` → `clicar #submit (frame player3/server.php, timeout 20 s)` → `esperarRede` com
`[?&]url=https?://[^?&]+[.]mp4[?]` (timeout 45 s).

### Resultado da Fase 7 (2026-09-26)

Em vez de esperar tempo fixo, o bot **olha a página e clica assim que o player está pronto** (o iframe
`player3/server.php` terminou de carregar e o `#submit` está visível), e confere se o site reagiu
(chamou o `serverforms.api`). Só clica de novo se o site **não** reagiu.

| | Antes | Depois |
|---|---|---|
| Vídeo novo (bot) | 31–41 s | **12,0–14,4 s** (10 de 10 com link, fora a tecla perdida abaixo; 1 clique cada, `serverforms` sempre 200); 18 s com o site lento; ~25 s no 1º pedido depois de abrir o Chrome |
| Página de série | 4,2–6,1 s | **2,2–2,8 s** (`completa`; o HTML é o mesmo byte a byte que com `quieta`) |
| Ativar a janela | 0,07–4 s | ~0,02 s |
| Vídeo novo pela TV/iptv (ponta a ponta) | 32–41 s | **14,9–21 s** (4 episódios; a reserva não foi usada) |
| 10 cliques simultâneos num episódio novo | 1 visita, 46 s | 1 visita, **16 s** para os 10 |

Onde vai o tempo agora (vídeo típico, ms): `commit` 1,5–2,7 s → iframe do player completo 6,3–10 s →
clique +1,1–1,3 s (pausa de reação + mouse) → `serverforms` +0,1 s → link +2,1 s. O que sobra é o
site: carregar o player e, depois do play, os scripts dele.

Achado no caminho: numa das tentativas **duas letras da URL se perderam** e o Chrome fez uma busca no
Google. Agora o bot confere o endereço no início da navegação e digita de novo (ver seção 3).

### Ideias (histórico, antes da Fase 7)

1. **Tirar ou encurtar a espera fixa de 15 s** (até −15 s). Ela entrou enquanto eu investigava os
   503 do `serverforms.api`, com a suspeita de que um clique rápido demais parecesse robô. A causa
   provada depois foi outra: o **limite por IP**, estourado por tentativas repetidas. **Nunca foi
   testado sem a espera.** O `clicar` já espera o `#submit` existir e estar visível, então esperar
   2–3 s, ou nada, pode bastar. Testar em alguns episódios e conferir se o `serverforms.api` continua
   200 e o vídeo aparece.
2. **Não esperar a página "quieta" antes de clicar** (até −5–10 s). O `#submit` aparece bem antes de
   a rede dos anúncios parar. Opção: um campo novo no contrato (por exemplo `"acoesApos": "commit"`,
   ou uma ação `quando: "commit"`) para as ações rodarem logo que o documento chega, com o `clicar`
   esperando o elemento. É mudança de contrato: atualizar o CLAUDE.md.
3. **Digitar mais rápido** (−1–2 s): `ATRASO_DIGITACAO_MS` de 25 para ~10–12. Continua digitação
   real; uma pessoa rápida digita nesse ritmo.
4. **Buscar o próximo episódio antes** (o próximo clique fica instantâneo): quando a TV pede o link
   do episódio N, o iptv dispara em segundo plano (`context.waitUntil`) a busca do N+1 e guarda no
   KV. Custa um uso do limite do site por episódio (11 seguidos passaram sem problema). **Muda o
   quanto o site é usado: perguntar ao dono antes.** Os episódios de uma série estão em
   `getSeriesInfoRc` / `src/tv/episode.ts` (`nextEpisode` já existe lá).

### Regras que continuam valendo

- **O princípio** (CLAUDE.md): sem CDP/webdriver, nada injetado, clique real. Não chamar o
  `serverforms.api` por fora nem "adivinhar" a URL do vídeo.
- **O limite do site**: teste com poucos episódios, espaçados. Rajadas de falhas (cada play que falha
  faz o player tentar 4x) bloqueiam o IP por ~10 min, até para a TV do dono.
- **Cache só do link de vídeo, no notebook, testado a cada entrega** (decisão do dono, 2026-09-28).
  O bot em si não guarda nada. Não inventar outros caches no notebook sem perguntar.
- **Cliente `/tv` frágil**: a mudança fica no bot e no servidor do iptv, nunca no HTML/CSS/JS da TV.

### Como medir

- **Painel** (https://painel.iptv01.asia, seção "Pedidos"): tempo de cada pedido.
- **Etapas:** no log do bot,
  `ssh servidor-caseiro "docker logs --since 1h bot-supremo 2>&1 | grep 'pedido https'"`.
- **Pedir um episódio fora do cache:**
  `curl -s -A Mozilla/5.0 -w "%{time_total}s
" "https://iptv01.asia/get-list-link-mp4-rc?linkPage=%2Fmusicvideo.php%3Fvid%3D<id>"`.
  Tem que devolver uma lista com o link (não `false`), e o link tem que tocar em `/proxy-rc` (206).
  Para forçar busca nova de um episódio já guardado: apagar a linha dele no `cache.sqlite` do notebook.
- **Achar episódios ainda não usados:**
  `https://iptv01.asia/get-list-serie-episode?linkPageSerie=/browse-avatar-a-lenda-de-aang-videos-1-date.html`
  (61 episódios; os usados em teste ficam no cache do notebook).
- **Testar o bot direto, sem o iptv e sem cache:** `POST /v1/navegar` com o mesmo corpo que
  `video/busca.py` monta (`pedido_rapido`).
- **Publicar:**
  - bot: push na `main` e esperar o painel mostrar o commit novo;
  - iptv: `npx tsc --noEmit -p .` e depois `npx wrangler deploy --env production`.

## 12. Serviço de vídeo (`video.iptv01.asia`, container `bot-supremo-video`)

**Por quê:** desde ~22h40 de 2026-09-27 o link do vídeo sai preso ao IP de quem o gerou. Só o notebook
(o WARP do Chrome) consegue baixar. O serviço fica num container próprio (mesma imagem, rede do warp):
se o Chrome travar ou reiniciar, quem está assistindo continua.

**Rotas** (`video/app.py`):
- `POST /v1/mp4` (Bearer `VIDEO_TOKEN`), `{"pagina": "/ep.html"}` (um `url` junto é ignorado) →
  `{"ok", "links": [...], "origem": "cache" | "novo", "ms"}`. Um pedido por página ao mesmo tempo; a
  busca termina e fica no cache mesmo se quem pediu desistir. A resposta começa na hora e recebe um
  espaço a cada 20 s (como o bot).
- `GET|HEAD /proxy-rc?url=&pagina=&sig=`: repassa o vídeo (com `Range`, sem guardar em disco).
  `sig` = HMAC-SHA256(`VIDEO_TOKEN`, `url + "\n" + pagina`), 32 primeiros hex (`urlVideoRc` no iptv).
  **Aceita qualquer URL assinada** (o domínio do site muda). Se a origem der 403/404/410 e houver
  `pagina`, pega um link novo e continua.
- `POST /v1/pagina` (Bearer `VIDEO_TOKEN`), `{"caminho": "/final_mapa.txt", "pedido": {...}}`: o
  `pedido` do bot sem `url`. Roda no domínio da vez, com a troca de domínio (seção 5), e devolve a
  resposta do bot mais `dominio`. Põe a ação do desafio e o `hostEsperado` se não vierem.
- `GET|POST /v1/dominios` (Bearer `VIDEO_TOKEN`): lê ou grava `{dominio1, dominio2}` (o painel usa).
- `GET /saude` (Bearer ou `?token=`): streams, links guardados, buscas em andamento, últimas buscas,
  domínios, IP público do WARP e desde quando (o log anota quando muda), e `repasses` (os últimos 60
  pedidos de vídeo, inclusive os recusados; painel "Repasses de vídeo").
- `GET /tv?k=`, `/tv/video`, `/tvv/<nome>`: página de teste da TV antiga (ver "TV antiga" no fim desta seção).

**Reparo automático** (`_buscar_com_reparo` em `video/app.py`, desde 2026-10-01): se o bot responder mas
o player não pedir nenhum vídeo (`ErroPlayer` em `video/busca.py`), o serviço chama
`POST /v1/reabrir-chrome` no bot (com `motivo`, que aparece no log do bot como `reabrindo o Chrome: ...`)
e repete a busca **uma** vez. No máximo um reparo a cada 10 min (`INTERVALO_REPARO_S`): se o problema
for o limite do site ou o site fora, reabrir não ajuda e cada tentativa a mais gasta o limite do
`serverforms.api`. Se duas páginas falharem juntas, só uma reabre; a outra só tenta de novo. Erros de
rede com o bot (bot fora do ar) não disparam o reparo. O histórico (`ultimos`) marca `reparo` e o
`/saude` mostra `ultimoReparo`. Motivo: em 2026-10-01 00:34–00:39 quatro buscas falharam assim e só
voltaram depois de clicar em "Reabrir o Chrome" no painel.

**Domínios** (`video/dominio.py`): tabela `meta` do mesmo SQLite (`dominio1`, `dominio2`,
`dominio_troca`, `dominios_velhos`). Regras na seção 5. Logs: `falha de domínio em ...` e `DOMÍNIO NOVO: X -> Y (motivo)`.

**Timeouts da busca (2026-10-06, o dono pediu folga):** pedido rápido com `#submit` até 90 s, vídeo até
60 s, total 200 s; reserva com `#submit` até 60 s, vídeo até 60 s, total 240 s. Domínio morto não espera
isso tudo: o `hostEsperado` corta em segundos.

**Cache** (`video/cache.py`): SQLite em `dados/video/cache.sqlite`, chave = página (sem domínio), sem
prazo. A cada entrega testa o 1º link (1 byte). 200/206 = entrega; qualquer outra coisa = apaga e busca
outro pelo bot.

**Medições (2026-09-28):**
- link novo pelo iptv: 17,7 s (29–36 s em outros; 104 s quando o pedido rápido falhou e foi a reserva);
- link do cache, com o teste: 0,2–0,7 s no notebook, ~0,5 s pelo iptv;
- repasse: primeiro byte em 0,1–0,3 s; ~80–100 Mbit/s pelo túnel;
- CPU: o Celeron (2 núcleos, **sem AES-NI**) satura os 2 núcleos a ~230–250 Mbit/s de repasse. Um
  vídeo usa ~2–3 Mbit/s, então a CPU aguenta dezenas; o limite de 16 conexões (`VIDEO_MAX_STREAMS`)
  é folgado e protege o Chrome;
- o upload da casa (~170 Mbit/s) não é o gargalo.

**Diagnóstico:**
```bash
ssh servidor-caseiro 'docker logs --since 1h bot-supremo-video 2>&1 | tail -30'   # mp4 ... -> cache|novo|erro, stream ...
ssh servidor-caseiro 'cd ~/bot-supremo; T=$(grep ^VIDEO_TOKEN= .env | cut -d= -f2-); curl -s -H "Authorization: Bearer $T" http://$(bash implantacao/ip-warp.sh):8070/saude'
```

**Voltar atrás:** o `/proxy-rc` antigo do Worker não funciona mais (o site recusa o IP da Cloudflare).
Se o serviço de vídeo quebrar, conserte-o; não há caminho alternativo.

### TV antiga (Samsung 2012): o player só toca o `video.` por `http://` (descoberto em 2026-10-09)

**Sintoma:** na TV antiga do dono, a página `/tv/play` abre, lista e navega normalmente, mas o vídeo
nunca começa: "Erro - Clique em Reiniciar Página…", "Servidor 1: Erro", "Servidor 2…5: Indefinido".
"Abrir Link Direto" não faz nada (só o título da aba muda). Celular, PC e TVs novas tocam normal. Antes
do `video.iptv01.asia` (vídeo vindo de `https://iptv01.asia/proxy-rc`, pelo Worker) a mesma TV tocava.

**Causa:** a TV tem **dois "motores" de rede**:
1. o **navegador** (`Mozilla/5.0 (SMART-TV; X11; Linux armv7l) ... Chromium/25.0.1349.2`), que abre
   páginas e entende o certificado moderno (ECDSA). Por isso `https://video.iptv01.asia/teste`
   digitado na barra abre normal, e **engana**;
2. o **player/baixador da Samsung** (o primeiro pedido dele chega **sem User-Agent**), mais velho, que
   busca o vídeo do `<video>` e dos links para arquivos. Ele **não consegue conectar no
   `https://video.iptv01.asia`** e desiste sem mandar nada: nem o pedido recusado chega ao notebook.

A diferença está no certificado. O `iptv01.asia` (domínio do Worker, certificado próprio:
`iptv01.asia` + `proxy-manager.iptv01.asia`) oferece **RSA** (GTS WR1) para quem não aceita ECDSA. O
`video.iptv01.asia` usa o certificado universal `*.iptv01.asia`, **só ECDSA** (GTS WE1): um cliente
só-RSA leva `handshake failure`. TLS 1.0 é aceito nos dois, então a versão do TLS não é o problema.
Conferir:

```bash
# o video. não tem RSA (handshake failure); o iptv01.asia tem (WR1)
echo | openssl s_client -connect video.iptv01.asia:443 -servername video.iptv01.asia -tls1_2 -sigalgs "RSA+SHA256:RSA+SHA1" 2>&1 | grep -E "i:|alert|Cipher is"
echo | openssl s_client -connect iptv01.asia:443 -servername iptv01.asia -tls1_2 -sigalgs "RSA+SHA256:RSA+SHA1" 2>&1 | grep -E "i:|alert|Cipher is"
```

**Prova (teste na TV, 2026-10-09 22:42), mesmo vídeo, `<video src=...>` numa página aberta por http:**

| Endereço do vídeo | Tocou? | O que chegou ao notebook |
|---|---|---|
| `/proxy-rc` de verdade (584 caracteres), por `http://` | **sim** | `GET` sem UA → `Range: bytes=<moov>` → `bytes=0-` (206, ~30 MB em 2 s) |
| o mesmo, por `https://` | não | nada |
| endereço curto (44), por `http://` | **sim** | igual ao 1º |
| endereço curto, por `https://` | não | nada |

O tamanho do endereço, os símbolos (`%3A`, `%2F`), o `206`, os cabeçalhos e a velocidade **não** são
o problema: com `http://` o endereço de verdade toca.

**Remédio:** a TV antiga precisa receber o vídeo por um endereço que o player dela alcance:
`http://video.iptv01.asia/...` (o mais simples, só no `urlsVideoRcTv` do iptv), ou `https://` num
host com certificado RSA (o Worker em `iptv01.asia` repassando do `video.`; ou um certificado avançado
RSA para `video.`, pago). **Cuidados para o `http://` funcionar:** na Cloudflare, "Always Use HTTPS"
tem que continuar **desligado** para `video.iptv01.asia` (hoje `http://` responde direto, sem 301).
"Automatic HTTPS Rewrites" troca `http://` por `https://` nos links de páginas servidas **por https**;
a `/tv/play` da TV antiga é aberta por `http://iptv01.asia`, então não é afetada.

**Como foi achado (o método, para a próxima vez):**
1. Medir o que sai do `video.` como a TV pediria (HTTP/1.1, com e sem `Range`, `HEAD`): igual ao Worker
   antigo. Comparar certificados: o `video.` não tem RSA.
2. Testar na TV um endereço de **texto puro** (`/teste`, um 404 do aiohttp). JSON faz a TV "baixar" e
   não prova nada. Abriu, por http e por https, e a teoria pareceu cair (era o navegador, não o player).
3. Ler a página da TV: "Servidor 1: Erro" é só `readyState` 0 depois de 3 s. "Indefinido" nos outros =
   a TV ainda nem desistiu do 1º.
4. Registrar **cada pedido** do `/proxy-rc` (painel, "Repasses de vídeo"), inclusive os recusados:
   **nenhum** pedido da TV chegou. O problema estava antes do HTTP.
5. Página de teste sem JavaScript, servida pelo próprio `video.` (`/tv?k=<chave>`), com o mesmo vídeo
   por endereços de vários tamanhos e por http/https, solto e dentro de `<video>`: só `http://` chegou e
   tocou.

**Ferramentas que ficaram** (`video/app.py`):
- painel → **"Repasses de vídeo"**: cada `GET/HEAD` de vídeo com User-Agent, IP, `Range`, tamanho da URL,
  status, tempos (origem, 1º bloco, total), bytes e como terminou (`completo`, `cliente fechou`,
  `falha na origem`, `recusado`). Os mesmos dados estão em `GET /saude` (`repasses`) e no log
  (`repasse ...`, `repasse recusado ...`);
- `GET /tv?k=<chave>`: a página de teste (texto e links, sem JavaScript; use o último vídeo aberto). A
  chave são os 6 primeiros hex de HMAC-SHA256(`VIDEO_TOKEN`, `"tv-teste"`):
  `ssh servidor-caseiro 'cd ~/bot-supremo; T=$(grep ^VIDEO_TOKEN= .env | cut -d= -f2-); python3 -c "import hmac,hashlib,sys;print(hmac.new(sys.argv[1].encode(),b\"tv-teste\",hashlib.sha256).hexdigest()[:6])" "$T"'`.
  Abra na TV **por `http://`**. `/tv/video?k=&q=R|A|B|D|F|H&p=http|https` é o `<video>` sozinho
  (`R` = o endereço de verdade assinado); `/tvv/<nome>?k=` entrega o vídeo.

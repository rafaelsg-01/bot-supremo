# Manual de operação

Para quem (IA ou pessoa) vai **manter** o bot-supremo e a parte do projeto-iptv que usa ele:
como o sistema funciona de ponta a ponta, onde olhar quando algo quebra e como mudar sem estragar.

- As **regras** (o princípio, o contrato da API, o que não pode) estão no [CLAUDE.md](../CLAUDE.md).
  Leia antes deste arquivo.
- O **histórico** do que foi feito e medido está no [ROTEIRO.md](ROTEIRO.md).
- A lista de endereços úteis, com as senhas, está em `../projeto-iptv/LINKS.txt`, **só no PC do dono** (fora do git).
- Este manual é o **como funciona e como consertar**. Atualize-o quando mudar algo que ele descreve.

Estado em 2026-09-26: em produção. A TV e o site do iptv pegam listas, séries e vídeos do Rede Canais
pelo bot-supremo. O FlareSolverr antigo (`content-proxy-web-01`) ainda está ligado, mas sem uso.

---

## 0. Painel: o jeito principal de ver e consertar

**https://painel.iptv01.asia**. O dono entra com usuário e senha (os valores ficam no `.env` do
notebook e no `LINKS.txt` do PC dele). Uma página só, que se atualiza a cada 5 s:
- **semáforo**: "Tudo funcionando" ou a lista do que está errado, em frases simples;
- **notebook, bot e containers**;
- **últimos pedidos**, com os números do dia;
- **log**;
- **a tela do Chrome ao vivo, com mouse e teclado**, de qualquer lugar.

Botões, na ordem de tentar: Testar o site → Reabrir o Chrome → Reiniciar o bot → Reiniciar o
notebook. "Pausar o bot" segura os pedidos (até 30 min) para mexer na tela sem conflito.

Para a IA, os comandos das seções abaixo continuam valendo. O painel é para o dono. Se precisar de algo
que o painel não mostra, acrescente no painel (`painel/app.py` e `painel/pagina.html`).

## 1. O caminho inteiro, de um clique na TV até o vídeo

```
TV / celular
  └─> iptv01.asia  (Cloudflare Worker "iptv-self-2", repo ../projeto-iptv)
        ├─ cache no KV (Kv_iptvSelf): listas, séries e links de vídeo
        └─ POST https://bot.iptv01.asia/v1/navegar   (Bearer botSupremoToken)
              └─> Cloudflare ─> túnel "bot-supremo" ─> container bot-supremo-tunel (cloudflared)
                    └─> http://warp:8080  (rede flare-net)
                          └─> container bot-supremo (usa a rede do container warp)
                                ├─ serviço Python (fila, xdotool, regras do pedido)
                                ├─ Chrome real + extensão (lê a página e a rede)
                                └─> redecanais.press, saindo pela Cloudflare WARP
```

Na hora de **tocar**, a TV recebe `https://iptv01.asia/proxy-rc?url=<link do vídeo>`. O `/proxy-rc` do
Worker busca o vídeo no proxy do Rede Canais com os headers que ele exige. O bot não participa
do streaming: ele só descobre o link.

## 2. Onde fica cada coisa

| O quê | Onde |
|---|---|
| Código do bot | este repo (público), `github.com/rafaelsg-01/bot-supremo`, branch `main` |
| Imagem Docker | `ghcr.io/rafaelsg-01/bot-supremo:latest` (e `:<sha do commit>`), feita pelo GitHub Actions |
| Notebook | `ssh servidor-caseiro`, repo clonado em `~/bot-supremo` |
| Segredos do bot | `~/bot-supremo/.env` no notebook (`BOT_TOKEN`, `VNC_SENHA`, `TUNEL_ID`). Nunca no git |
| Credencial do túnel | `~/bot-supremo/dados/tunel/credenciais.json` no notebook |
| Perfil do Chrome | `~/bot-supremo/dados/perfil` (cookies, `cf_clearance`, service worker do site) |
| Chave da extensão | `~/bot-supremo/dados/estado` (se apagar, o ID da extensão muda e ela é reinstalada) |
| Código do iptv | `../projeto-iptv` (Worker `iptv-self-2`, deploy com `npx wrangler deploy --env production`) |
| Token do bot no iptv | secret `botSupremoToken` do Worker; para `npm run dev`, em `.dev.vars` (fora do git) |
| Domínio do site | `rcDominio` no `wrangler.toml` do iptv (todos os envs). Trocar o domínio = mudar só isso |

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
6. **Navegar** (`_navegar`): foca a janela, `ctrl+L`, digita a URL, `Delete`, `Enter` (xdotool).
   Confirma pelo **início** da navegação (evento `nav/inicio` ou request `main_frame`). Se em 10 s
   nada começar, abre pela extensão como reserva e avisa em `erros`.
7. **Carregar** (`_esperar_carregar`): frame principal completo e rede quieta por 1 s (no máximo 8 s
   extras). Se o título for "Um momento…"/"Just a moment...", entra em `_tratar_desafio`: chama o
   gancho vazio e roda as ações `quando: "desafio"` (clique no Turnstile).
8. **Ações** `quando: "carregada"`, em ordem. `clicar` pede à extensão a medida do seletor em todos os
   frames (`localizar`), converte para pixel da tela (`_ponto_na_tela`), rola com a roda se
   precisar, move o mouse em curva e clica.
9. **esperarRede**: espera a primeira request que casar com a regex (inclui as do service worker,
   aba `-1`).
10. **Ler o HTML** (`lerHtml`, mundo isolado) e montar a resposta.
11. **Limpar**: fecha abas extras e volta a `about:blank`. Se o Chrome passou de 1100 MB, reabre.

Tempos típicos no notebook: página comum 3–15 s, lista `.txt` 3–7 s, **vídeo 28–45 s** (inclui 15 s
de espera de propósito antes do play).

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
| `Function_getMovieList` / `Function_getSerieList` | `url` = `/final_mapafilmes.txt` / `/final_mapa.txt` | listas cruas `movies_list_rc` / `series_list_rc`, renovadas em segundo plano depois de 12 h. Lista vazia (falha) **não** substitui a boa |
| `Function_getSerieSingle` | `url` = página `/browse-<serie>-videos-1-date.html` | `movie_info_<serie>_rc`, 3 dias. Resultado sem temporadas (falha) **não** é guardado |
| `Function_getLinkMp4List` | episódio/filme + esperar 15 s + clicar `#submit` no frame `player3/server.php` + `esperarRede` com a URL do vídeo | `mp4-list-<linkPage>`, **4 h fixas** (como o dono fez) |

- **Sempre** use `Function_getLinkMp4ListComCache(env, linkPage, context)`, com o `context`. Ele usa
  `waitUntil`, que dá uns 30 s extras para gravar no KV depois que a TV desiste (depois disso a
  Cloudflare encerra o Worker).
- **O cache de vídeo é só este, no KV, com 4 h.** Não mexa no tempo sem falar com o dono. Em
  2026-09-26 eu (IA) troquei por uma conta com o número `nu3zAQc9HC3GbwJq=<n>` achando que era a
  validade do link. **É o horário de criação**: a conta dava negativa e nada era guardado, e o dono
  percebeu na TV. Um link com 1h26 de vida ainda tocava.
- **Nada de cache no notebook** (decisão do dono). O bot não guarda resultado de pedido.
- O KV da Cloudflare pode continuar respondendo "não achei" por até 60 s depois de gravado (cache de
  leitura negativa).
- Os 4 lugares que pedem vídeo (`/get-list-link-mp4`, `/get-list-link-mp4-rc`, `src/tv/movie.ts`,
  `src/tv/episode.ts`) usam a mesma chave de propósito.
- **Cuidado:** o HTML, o CSS e o JS **do cliente** `/tv` rodam num navegador de TV muito antigo, e
  uma vírgula quebra tudo. Tudo acima é código do servidor (Worker). Não mexa no cliente `/tv` sem
  necessidade e sem testar na TV.

## 5. O que se sabe do Rede Canais (redecanais.press)

- **Domínio muda de tempos em tempos** (`.af`, `.press`…). Se tudo começar a falhar com o site "fora",
  confira se o domínio mudou e troque `rcDominio` no `wrangler.toml`.
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
- O proxy exige os headers `h31ffadrg3bb7: h31ffadrg3fj345a` e `x-requested-with: RC-Site-Requests`
  (o `/proxy-rc` já manda). O mp4 de dentro recusa conexão direta (520).
- **Limite do `serverforms.api`, por IP e tempo.** Em 2026-09-26, **11 vídeos seguidos** (um a cada
  ~35 s) passaram sem problema. O que estoura o limite são **tentativas repetidas em rajada**: cada
  play que falha faz o player tentar 4 vezes, e cliques repetidos acumulam. Quando estoura, o site
  responde 503/521 **até para cliques feitos à mão**, e volta sozinho depois de ~10 min parado.
- `wrangler dev` (local) não consegue buscar o host estranho do proxy de vídeo ("internal error").
  Em produção funciona. Teste o `/proxy-rc` em produção.

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
  -d '{"url":"https://redecanais.press/final_mapa.txt","html":false}' https://bot.iptv01.asia/v1/navegar

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
| Vídeo volta `false`, log do bot com `nenhuma request casou` e o player chamando `serverforms.api` com 503/521 | limite do site estourado | parar de pedir vídeos novos por ~10 min. Os que estão no cache continuam tocando |
| `o seletor '#submit' não apareceu` | player demorou ou mudou | rodar um pedido com `inspecionar: "#submit"`, ver `/v1/tela`. Se o site mudou o player, ajustar as ações em `Function_getLinkMp4List` |
| Todas as páginas com `statusHttp` 5xx (521/522) | site fora ou domínio mudou | abrir o site no PC. Se mudou de domínio, trocar `rcDominio` e fazer deploy do iptv |
| `status: "desafio"` | o Turnstile não passou | ver `/v1/tela`. Se a caixa mudou, ajustar `Const_acaoDesafio` em `bot_supremo.ts` |
| 524 no iptv | resposta sem começar em ~120 s | não deveria mais acontecer (item 4 da seção 3). Se voltar, conferir se a resposta continua em streaming |
| 429 "fila cheia" | mais de 30 pedidos acumulados | algo está chamando em loop. Achar quem no `wrangler tail` |
| Série aparece sem episódios | página em formato diferente, ou o bot falhou | pedir a página ao bot e testar `parseSeasonsAndEpisodes` com o HTML |
| Lista com acentos quebrados (`NÃºmeros`) | `textoDoPre` não desfez a codificação | conferir o `<pre>` cru que o bot devolve |
| Depois de reboot, bot sem rede | o `warp` foi recriado pelo `~/start.sh` | o `atualizar.sh` recria o nosso container em até 2 min |
| IP do WARP banido (403 em tudo) | ban | refazer o registro do WARP (ver CLAUDE.md, com backup de `~/content-warp/data/`) |

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
| Mesmo episódio pedido 5 min depois (cache de 4 h no KV) | 1ª vez 34,8 s; 2ª vez 0,27 s |

Na prática: **um vídeo novo leva ~35 s**. Com vários vídeos novos pedidos juntos, cada um espera a sua
vez (~35 s por vídeo à frente). Depois de achado, o link fica 4 h no KV e abre na hora.

## 10. Pendências

- Fase 6 do ROTEIRO: desligar o `content-proxy-web-01` (libera ~620 MB) e tirar os passos dele do
  `~/start.sh`. **Avisar o dono antes.**
- Avaliar o cron de reboot a cada 12 h.
- Tirar do iptv o código antigo do FlareSolverr (`getReturnJsExecuted`, `getHtmlCriptografado`,
  `jsGetLinkMp4.js`, rotas `/diag/*`) quando o dono concordar.
- Observar por alguns dias se aparece ban (403) ou falhas no `#submit`.

## 11. Tempo para achar o link do vídeo (para quem for otimizar)

Um episódio que **não** está no cache leva **~30–38 s** do clique na TV até o link. Depois fica 4 h
no KV do iptv e sai em ~0,1 s. O dono quer diminuir esses ~35 s.

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

### Ideias, da maior economia para a menor

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
- **Nada de cache no notebook.** O cache de vídeo é o KV do iptv, com **4 h fixas**. Não mexer no tempo
  sem perguntar (ver a seção 4 e o que deu errado em 2026-09-26).
- **Cliente `/tv` frágil**: a mudança fica no bot e no servidor do iptv, nunca no HTML/CSS/JS da TV.

### Como medir

- **Painel** (https://painel.iptv01.asia, seção "Pedidos"): tempo de cada pedido.
- **Etapas:** no log do bot,
  `ssh servidor-caseiro "docker logs --since 1h bot-supremo 2>&1 | grep 'pedido https'"`.
- **Pedir um episódio fora do cache:**
  `curl -s -A Mozilla/5.0 -w "%{time_total}s
" "https://iptv01.asia/get-list-link-mp4-rc?linkPage=%2Fmusicvideo.php%3Fvid%3D<id>"`.
  Tem que devolver uma lista com o link (não `false`), e o link tem que tocar em `/proxy-rc` (206).
- **Achar episódios ainda não usados:**
  `https://iptv01.asia/get-list-serie-episode?linkPageSerie=/browse-avatar-a-lenda-de-aang-videos-1-date.html`
  (61 episódios; os usados em teste ficam 4 h no cache).
- **Testar o bot direto, sem o iptv e sem cache:** `POST /v1/navegar` com o mesmo corpo que o
  `Function_getLinkMp4List` monta, mais a ação do desafio (ver `navegarBot` em `bot_supremo.ts`).
- **Publicar:**
  - bot: push na `main` e esperar o painel mostrar o commit novo;
  - iptv: `npx tsc --noEmit -p .` e depois `npx wrangler deploy --env production`.

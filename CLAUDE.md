# CLAUDE.md

Guia para qualquer IA que for trabalhar neste repositório. Leia inteiro antes de mexer em algo.
Depois leia [docs/ROTEIRO.md](docs/ROTEIRO.md) (o que falta fazer e em que ordem).

## O que é

**bot-supremo** é um **navegador real como serviço**. Ele recebe uma URL, abre essa URL
num Chrome de verdade, opcionalmente clica em elementos da tela, e devolve:

- o **HTML renderizado** da página (depois que os scripts dela rodaram);
- as **requests de rede** que a página fez e que casaram com um padrão pedido.

Só isso. O serviço não sabe nada de filme, série, catálogo ou cache. Quem chama decide o que fazer
com o HTML e com as requests.

## Por que existe

O dono tem um sistema de IPTV/VOD pessoal (`projeto-iptv`, um Cloudflare Worker) que pega vídeos de
um site. Até agora a parte que conversa com o site era o `projeto-proxy-web/FlareSolverrWithExecuteScript`:
um Chrome controlado por webdriver mais scripts que **imitavam** o site por fora (baixar a página por
`fetch`, descriptografar o HTML na mão, chamar APIs internas do player na mão). Isso quebrava toda vez
que o site mudava um detalhe, e em setembro de 2026 passou a levar ban de IP em ~2 minutos de uso.

Este projeto existe para **acabar com esse problema de vez**. O histórico do sistema antigo está no
próprio `../projeto-iptv` (ver "Relação com os outros projetos" no fim deste arquivo).

## O princípio (não negociável)

**O notebook tem que ser indistinguível de uma pessoa usando um PC normal.**

- Chrome **real**, com perfil persistente (cookies, service workers e cache sobrevivem entre pedidos
  e entre restarts). **Sem webdriver, sem chromedriver, sem CDP/Puppeteer/Playwright/Selenium.**
- O **site faz tudo sozinho**: registra o service worker dele, chama as APIs dele, monta o player
  dele. Nós nunca reimplementamos nada do site.
- **Nada é injetado que altere a página.** A extensão só observa (rede) e lê (posição de elementos).
- **Cliques são cliques de mouse reais**, dados de fora do navegador (xdotool), e por isso chegam
  à página como eventos confiáveis, iguais aos de uma pessoa.
- A extensão uBlock Origin Lite fica instalada, como no navegador do dono. Com ela o site nem mostra
  o portão de anúncios.

Se uma solução exigir quebrar este princípio, pare e converse com o dono antes.

## Contrato da API

O contrato abaixo descreve a **intenção**. O formato decidido está logo depois, em "Contrato decidido".

**Entrada:**
- `url`: o que abrir;
- `acoes` (opcional): lista ordenada, por exemplo "se o seletor X existir (inclusive dentro de
  iframes), clicar nele", "esperar N ms";
- `esperarRede` (opcional): um padrão (regex) e um timeout. O pedido só termina quando aparecer uma
  request casando com o padrão, ou quando o tempo acabar;
- timeout geral do pedido.

**Saída:**
- `html`: o HTML renderizado;
- `rede`: as requests capturadas que casaram (URL, método, status, tipo, horário);
- status, erros e duração.

Exemplo do uso real pelo projeto-iptv:

| Precisa de | Pedido |
|---|---|
| Lista de filmes, episódios de uma série, temporadas, OVAs | só `url` → usa o `html` |
| URL do vídeo de um filme ou episódio | `url` + clicar no botão de play + `esperarRede` com o padrão da URL do vídeo → usa `rede` |

**Por que a URL do vídeo sai da rede e não do HTML:** o player do site baixa o vídeo por JavaScript,
com headers próprios. O `<video>` só mostra um `blob:`, sem a URL real.

### Contrato decidido (2026-09-23)

**Não há compatibilidade com o formato do FlareSolverr** (`cmd`, `directJs` etc.). O projeto-iptv será
reescrito para este contrato: em vez de mandar JavaScript para rodar no navegador, ele manda **ações
declarativas** ("se o elemento X estiver na tela, clicar nele").

`POST /v1/navegar`, com o header `Authorization: Bearer <BOT_TOKEN>`:

```json
{
  "url": "https://...",
  "acoes": [
    { "quando": "desafio", "tipo": "clicar", "seletor": "input[type=checkbox]",
      "frame": "challenges.cloudflare.com", "seExistir": true, "timeoutMs": 30000 },
    { "tipo": "clicar", "seletor": "#botao-anuncio", "seExistir": true, "timeoutMs": 5000 },
    { "tipo": "clicar", "seletor": "#play", "timeoutMs": 15000 },
    { "tipo": "esperar", "ms": 2000 }
  ],
  "esperarRede": { "padrao": "\\.mp4", "timeoutMs": 30000 },
  "html": true,
  "timeoutMs": 60000
}
```

- `clicar`: espera o seletor existir e estar visível (inclusive dentro de iframes e de shadow DOM,
  aberto ou fechado) até `timeoutMs`. Se preciso, rola a página com a roda do mouse, move o mouse
  numa curva e clica (xdotool). Se não aparecer: com `seExistir: true` segue em frente
  (`resultado: "nao_existia"`); senão o pedido falha. `frame` (opcional) limita a busca aos frames
  cuja URL contém esse texto. "Visível" é ter tamanho e não estar `display:none`/`visibility:hidden`.
  A opacidade não conta, porque caixas estilizadas deixam o `<input>` transparente por cima do desenho.
- `quando` (opcional): `"carregada"` (padrão) roda depois que a página carregou. `"desafio"` roda
  **enquanto** a aba estiver no "Um momento…" / "Just a moment...", uma vez por aparição. **É assim
  que o desafio da Cloudflare é resolvido: quem pede manda o clique na caixa do Turnstile** (decisão
  do dono, 2026-09-23). O exemplo acima funciona: a caixa é
  `<input type="checkbox" aria-label="Confirme que é humano">`, num shadow root fechado dentro do
  iframe de `challenges.cloudflare.com`. Depois de passar, o cookie `cf_clearance` fica no perfil e
  os pedidos seguintes nem veem o desafio.
- `inspecionar` (opcional, para depuração): um seletor. A resposta ganha `inspecao` com a medição
  crua de todos os frames no fim do pedido (URLs dos frames, iframes, os primeiros `input`/`button`
  de cada frame e o alvo). Serve para descobrir seletores.
- `esperarRede.padrao` é uma regex. Ela filtra o que volta em `rede` e o pedido espera a primeira
  request que casar. Requests do service worker do site (aba `-1`) também contam.
- `timeoutMs` geral: padrão 60000, máximo 240000.

Resposta (sempre HTTP 200 quando o pedido rodou; 400 para entrada inválida, 401 para token errado,
429 para fila cheia):

```json
{
  "ok": true,
  "status": "concluido | timeout | desafio | erro",
  "statusHttp": 200,
  "urlFinal": "...", "titulo": "...", "html": "<!DOCTYPE html>...",
  "rede": [{ "url": "...", "metodo": "GET", "status": 200, "tipo": "xmlhttprequest", "horario": 0, "aba": 12 }],
  "acoes": [{ "tipo": "clicar", "quando": "carregada", "seletor": "#play",
              "resultado": "clicou | nao_existia | nao_precisou | esperou", "ms": 812 }],
  "desafio": false,
  "erros": [],
  "duracaoMs": 5230,
  "etapas": { "navegar": 3100, "desafio": 0, "carregar": 1900, "acoes": 200, "rede": 30 }
}
```

- `ok` só é `true` se `status` for `concluido` **e** `statusHttp` (status HTTP do documento principal)
  não for erro (≥ 400). Exemplo: um `522` da Cloudflare (servidor do site fora) carrega, mas vem com
  `ok: false`.
- `etapas.navegar` inclui a espera pela resposta do servidor. Um site lento aparece aqui.
- `nao_precisou`: ação de desafio cujo desafio sumiu antes de o elemento aparecer.

`GET /saude` (sem token): Chrome vivo, extensão conectada e versão, memória do Chrome, fila e tela em uso.
`GET /v1/diagnostico` (com token): permissões efetivas da extensão e contadores de eventos.

**Endereço público:** `https://bot.iptv01.asia` (túnel próprio `bot-supremo`, ID
`a572a95f-5df3-4234-9373-9b4c71ff3eef`, criado com o `cert.pem` de `iptv01.asia` que fica no PC do
dono em `~/.cloudflared/credencial_iptv01.asia/`). O conector é o serviço `tunel` do `compose.yml`, na
rede `flare-net`, apontando para `http://warp:8080`. A credencial do túnel fica só no notebook, em
`dados/tunel/credenciais.json`.

## Peças

1. **Chrome real** (Google Chrome estável, `.deb` oficial, com sandbox) com perfil persistente e
   uBlock Origin Lite. A tela é o **Xorg na GPU Intel real** do notebook (tela interna `LVDS-1`,
   1366x768, backlight apagado), para o WebGL mostrar a placa de verdade e não o "SwiftShader" de uma
   tela simulada. Se o Xorg não subir, o container cai sozinho para o **Xvfb** (`TELA=xvfb` força).
   O Openbox é o gerenciador de janelas. Poucas flags: `--user-data-dir`, `--no-first-run`,
   `--no-default-browser-check`, `--start-maximized` e `--password-store=basic`.
   - **Extensões por política** (`/etc/opt/chrome/policies/managed/bot-supremo.json`), porque o
     Chrome oficial não aceita mais `--load-extension`. O uBO Lite vem da Chrome Web Store. A nossa é
     empacotada como CRX3 na subida do container (`servico/empacotar.py`), com uma chave que fica no
     volume `dados/estado` (fora do git), e servida por um `update.xml` em `127.0.0.1:8081`. Quando o
     código da extensão muda, a versão sobe e a extensão se atualiza sozinha.
2. **Extensão própria** (`extensao/`, MV3):
   - observa a rede com `chrome.webRequest` (roda no navegador, fora da página, e a página não tem
     como perceber);
   - informa a navegação (`webNavigation`) e o título da aba;
   - lê o HTML renderizado com `chrome.scripting` no **mundo isolado**;
   - quando uma ação pede, mede o elemento em todos os frames. As contas de posição **na tela**
     (iframes + janela) ficam no serviço (`_ponto_na_tela` em `servico/pedido.py`);
   - conversa com o serviço por WebSocket em `ws://127.0.0.1:8081/ponte`.

   Ela **não clica, não navega nos pedidos e não altera a página**. Não tem
   `web_accessible_resources`, então nenhuma página consegue sondar que ela existe.
3. **Serviço em Python** (`servico/`, aiohttp):
   - expõe a API HTTP na porta 8080;
   - mantém uma **fila** (um pedido por vez, uma aba por vez, até 5 esperando);
   - conversa com a extensão;
   - **abre a URL digitando na barra de endereço** (`ctrl+L`, digita, `Delete`, Enter) com xdotool,
     como uma pessoa. Assim a navegação chega ao site como digitada; um `chrome.tabs.update` não
     chegaria. Se a digitação falhar, usa `chrome.tabs` como reserva e avisa em `erros`;
   - dá os **cliques** com xdotool nas posições calculadas, com o mouse andando numa curva;
   - considera a página carregada quando o frame principal termina **e** a rede fica quieta por 1 s
     (no máximo 8 s de espera extra);
   - aplica os timeouts;
   - ao terminar cada pedido, fecha abas extras e deixa a aba em `about:blank`. Se o Chrome passar de
     1100 MB, reabre o Chrome entre dois pedidos;
   - sobe e vigia o Openbox e o Chrome (reabre se caírem);
   - tem uma rota de saúde.
4. **Vigia do desafio da Cloudflare**: detecta quando a aba está em "Um momento…" / "Just a
   moment..." (pelo título da janela), registra isso em log e chama uma **função-gancho vazia**.
   **O conteúdo dessa função é decisão e responsabilidade do dono. Não implemente essa ação.** O
   serviço deve tratar o desafio como um estado conhecido: esperar ele sumir até o timeout e, se não
   sumir, falhar o pedido com um erro claro. O gancho é `ao_detectar_desafio` em
   `servico/gancho_desafio.py` e roda numa thread à parte. Se o desafio não sumir, a resposta vem
   com `status: "desafio"`. O dono decidiu que o desafio é resolvido **pelo próprio pedido**, com
   ações `"quando": "desafio"` (ver o contrato). O gancho continua vazio.

## Infraestrutura

### O notebook (`ssh servidor-caseiro`, acesso livre)
- Celeron 1007U (2 núcleos, 1,5 GHz, 2013), **3,3 GB de RAM**, **HD mecânico** (lento, cuidado
  com I/O), **sem nobreak**.
- Debian 13 (trixie), só terminal, sem interface gráfica instalada.
- Fica ligado 24h em cima do armário, com internet por cabo.
- Dockers que já rodam lá (em setembro de 2026): `warp` (Cloudflare WARP como proxy SOCKS5, é a saída
  para a internet), `content-proxy-web-01` (o FlareSolverr antigo, a desligar quando este estiver no
  ar) e dois `cloudflared` (túneis que expõem serviços do notebook para a internet).
- Existe um cron de reboot a cada 12h (`sudo crontab -l`). Avalie se ainda faz sentido.

### Docker
- Tudo deste projeto roda num container (`bot-supremo`): tela, Chrome, extensão, serviço Python e vigia.
- O repositório fica clonado em `~/bot-supremo` no notebook, com o `.env` (token da API e senha do
  VNC) só lá.
- O **perfil do Chrome fica num volume do host** (`~/bot-supremo/dados/perfil`). Apagar ou recriar
  o container não pode perder cookies nem sessão.
- A saída para a internet é pela rede do container `warp` que já existe. O IP direto da casa não é
  aceito pelo site (bloqueio do provedor, não ban).
- **Sandbox do Chrome:** o seccomp padrão do Docker bloqueia a criação de namespaces de usuário,
  e o Chrome morre com "Failed to move to new namespace". O `docker/seccomp-chrome.json` é o padrão
  do Docker (moby/profiles) com `clone`, `unshare` e `setns` liberados. Nunca use `--no-sandbox`.
- **Decidido:** `network_mode: container:warp`. O container `warp` roda o WARP completo (não é só
  um SOCKS), então tudo sai por ele (DNS, WebRTC, QUIC), sem flag de proxy no Chrome. A API fica em
  `warp:8080` para quem está na rede `flare-net` (e no IP do warp, visto do host). Efeito colateral:
  o `~/start.sh` recria o `warp` a cada boot e o nosso container perde a rede. O `atualizar.sh` percebe
  isso e recria o nosso container (testado no reboot de 2026-09-23 12:00).
  - O **IP do warp muda a cada boot** (já foi `172.18.0.2` e `172.18.0.3`). Pegue o atual com
    `docker inspect -f '{{(index .NetworkSettings.Networks "flare-net").IPAddress}}' warp`.
  - O **nome da máquina** dentro do nosso container é o do warp e também muda. O Chrome grava esse
    nome na trava do perfil (`SingletonLock`) e, ao ver outro nome, acha que o perfil está aberto "em
    outro computador" e trava num aviso. Por isso o serviço apaga as travas `Singleton*` antes de abrir
    o Chrome. Se a extensão ainda assim ficar 2 min desconectada, o serviço reabre o Chrome sozinho.
- VNC para depuração, ou para logar no Google no Chrome do bot: porta 5900 no IP do warp, com a senha
  do `.env`. De fora do notebook, use `ssh -L 5900:<ip-do-warp>:5900 servidor-caseiro`.
- Se o IP do WARP for banido: refazer o registro do WARP gera um IP novo (os scripts antigos ficam
  em `~/content-warp/` no notebook). Faça backup de `~/content-warp/data/` antes.
- O Chrome no Docker precisa de `/dev/shm` grande (`shm_size`).

### Deploy automático
- O repositório é **público** no GitHub (`bot-supremo`).
- A cada push na branch principal, o GitHub Actions constrói a imagem e publica no GHCR. O notebook
  **não compila nada**: ele é lento demais para isso.
- No notebook, algo como Watchtower ou um timer do systemd com `docker compose pull && up -d`
  percebe a imagem nova e atualiza o container sozinho.
- **Decidido:** `.github/workflows/imagem.yml` publica `ghcr.io/rafaelsg-01/bot-supremo:latest` a
  cada push na `main` e toda segunda-feira, para o Chrome da imagem nunca ficar velho. No notebook, o
  timer `bot-supremo-atualizar.timer` roda o `implantacao/atualizar.sh` a cada 2 min: faz `git pull`
  e `docker compose pull`, e recria o container se a imagem mudou, se o `warp` foi recriado ou se o
  container não está rodando. Instalação: `sudo bash implantacao/instalar.sh`.
- **Modo dev** (iterar sem esperar o CI): `touch .modo-dev` no notebook. O `atualizar.sh` passa a
  usar o `compose.dev.yml`, que monta `servico/`, `extensao/` e `docker/` da pasta por cima da imagem,
  e para de fazer pull. Copie os arquivos com `scp` e rode
  `docker compose -f compose.yml -f compose.dev.yml up -d --force-recreate`. Apague o `.modo-dev` ao
  terminar.

### Repositório público: cuidado com segredos
- **Nunca** commitar tokens, senhas, IPs da casa, chaves do WARP, perfil do Chrome nem capturas de
  rede (`.json` de net-export). O `.gitignore` já cobre o básico.
- Token da API, endpoints e afins vão em `.env` (fora do git) ou em secrets do GitHub.
- A API vai ficar exposta pelo `cloudflared`, então precisa de autenticação (um token num header basta).

## Como trabalhar aqui

- O dono entregou o notebook e este repo para a IA administrar. Acesso ao notebook, commits e pushes
  estão liberados. Pode abrir o site à vontade: o sistema foi feito para se comportar como uma pessoa.
- Explique as decisões em português simples. Código, comentários e docs em **português**.
- Mantenha [docs/ROTEIRO.md](docs/ROTEIRO.md) atualizado: marque o que foi feito e registre o que
  foi descoberto (padrões de URL, tempos, problemas).
- Ao decidir algo que muda o contrato ou a arquitetura, atualize este arquivo.

## Relação com os outros projetos

- `../projeto-iptv`: o Cloudflare Worker que usa este serviço. Hoje ele chama o proxy antigo por
  `env.proxyWeb` (`getReturnJsExecuted` / `getHtmlCriptografado` em `src/function_rc.ts`). Quando
  este serviço estiver pronto, esses pontos passam a chamar a API nova. O cache de URLs de vídeo
  (`mp4-list-*`, ~4h) e o `/proxy-rc` continuam lá, do lado do Worker.
- `../projeto-iptv/DEBUG_REDECANAIS.md` e `../projeto-iptv/SESSAO_BAN_REDECANAIS_2026-09-22.md`:
  histórico das quebras e do ban do sistema antigo.
- `../projeto-proxy-web/FlareSolverrWithExecuteScript`: o sistema antigo. Serve de referência do que
  **não** fazer e de como a infraestrutura do notebook está montada. Será desligado.

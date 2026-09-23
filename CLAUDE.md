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

O contrato abaixo descreve a **intenção**. Formato exato, nomes de campos e rotas ficam a critério de
quem implementar. Documente aqui o que for decidido.

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

## Peças

1. **Chrome real** numa tela virtual (Xvfb), com perfil persistente e uBlock Origin Lite. O notebook
   não tem tela nem desktop instalado, então tudo roda sem interface física.
2. **Extensão própria**:
   - abre a URL pedida (`chrome.tabs`);
   - observa a rede com `chrome.webRequest` (roda no navegador, fora da página, e a página não tem
     como perceber);
   - quando uma ação pede, localiza o elemento e calcula a posição dele **na tela**, incluindo os
     deslocamentos de iframe e da janela;
   - lê o HTML renderizado.

   Ela **não clica e não altera a página**.
3. **Serviço em Python**:
   - expõe a API HTTP;
   - mantém uma **fila** (um pedido por vez, uma aba por vez);
   - conversa com a extensão;
   - dá os **cliques** com xdotool nas posições que a extensão informa;
   - aplica os timeouts;
   - ao terminar cada pedido, deixa a aba em `about:blank`, para não ficar com o site aberto
     consumindo memória;
   - tem uma rota de saúde.
4. **Vigia do desafio da Cloudflare**: detecta quando a aba está em "Um momento…" / "Just a
   moment..." (pelo título da janela), registra isso em log e chama uma **função-gancho vazia**.
   **O conteúdo dessa função é decisão e responsabilidade do dono. Não implemente essa ação.** O
   serviço deve tratar o desafio como um estado conhecido: esperar ele sumir até o timeout e, se não
   sumir, falhar o pedido com um erro claro.

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
- Tudo deste projeto roda num container: Xvfb, Chrome, extensão, serviço Python e vigia.
- O **perfil do Chrome fica num volume do host**. Apagar ou recriar o container não pode perder
  cookies nem sessão.
- A saída para a internet é pela rede do container `warp` que já existe. O IP direto da casa não é
  aceito pelo site (bloqueio do provedor, não ban).
- Se o IP do WARP for banido: refazer o registro do WARP gera um IP novo (os scripts antigos ficam
  em `~/content-warp/` no notebook). Faça backup de `~/content-warp/data/` antes.
- O Chrome no Docker precisa de `/dev/shm` grande (`shm_size`).

### Deploy automático
- O repositório é **público** no GitHub (`bot-supremo`).
- A cada push na branch principal, o GitHub Actions constrói a imagem e publica no GHCR. O notebook
  **não compila nada**: ele é lento demais para isso.
- No notebook, algo como Watchtower ou um timer do systemd com `docker compose pull && up -d`
  percebe a imagem nova e atualiza o container sozinho.

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

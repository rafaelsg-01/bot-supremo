# Roteiro

O que falta fazer e em que ordem. Marque `[x]` ao concluir e registre o que descobrir na seção
[Descobertas e medições](#descobertas-e-medições) no fim deste arquivo.

As regras e o contexto estão no [CLAUDE.md](../CLAUDE.md). Resumo do princípio: Chrome real,
sem webdriver/CDP, nada injetado que altere a página, cliques reais com xdotool.

---

## Fase 0: base do repositório

- [x] `CLAUDE.md` com contexto, princípio, contrato e infraestrutura
- [x] `docs/ROTEIRO.md` (este arquivo)
- [x] `.gitignore`, `README.md`, `LICENSE` (MIT)
- [ ] Repositório público `bot-supremo` no GitHub

## Fase 1: serviço mínimo (URL → HTML)

Objetivo: mandar uma URL para a API e receber o HTML renderizado por um Chrome real no notebook.

- [ ] Levantar o estado do notebook (`ssh servidor-caseiro`): RAM livre, disco, dockers, rede do `warp`
- [ ] Chrome real (Google Chrome estável) rodando em Xvfb, com perfil persistente
- [ ] uBlock Origin Lite instalado no perfil
- [ ] Extensão própria mínima: abre a URL numa aba (`chrome.tabs`), espera carregar, lê o HTML
- [ ] Canal de conversa entre a extensão e o serviço Python (decidir e documentar no `CLAUDE.md`)
- [ ] Serviço Python com a API HTTP (`url` + timeout → `html`, status, erros, duração)
- [ ] Autenticação por token num header (token no `.env`)
- [ ] Fila: um pedido por vez, uma aba por vez
- [ ] Ao terminar cada pedido, aba volta para `about:blank`
- [ ] Rota de saúde
- [ ] Vigia do desafio da Cloudflare: detecta "Um momento…" / "Just a moment..." pelo título,
      registra em log, chama a função-gancho **vazia**, espera sumir até o timeout, senão falha
      com erro claro
- [ ] Testar com uma página de lista do site e comparar com o que o projeto-iptv espera

## Fase 2: captura de rede

Objetivo: devolver as requests que a página fez e que casaram com um padrão.

- [ ] Extensão observa a rede com `chrome.webRequest` (URL, método, status, tipo, horário)
- [ ] Parâmetro `esperarRede`: regex + timeout; o pedido termina quando casar ou quando o tempo acabar
- [ ] Campo `rede` na resposta
- [ ] Descobrir e anotar o padrão da URL do vídeo

## Fase 3: cliques

Objetivo: clicar em elementos da página como uma pessoa, inclusive dentro de iframes.

- [ ] Extensão localiza o seletor (inclusive em iframes) e calcula a posição **na tela**
      (deslocamentos de iframe, da janela e da barra do navegador)
- [ ] Serviço move o mouse e clica com xdotool na posição informada
- [ ] Parâmetro `acoes`: lista ordenada ("se o seletor X existir, clicar", "esperar N ms")
- [ ] Fluxo completo: abrir episódio → clicar no play → capturar a URL do vídeo

## Fase 4: Docker + deploy automático

Objetivo: tudo num container, atualizado sozinho a cada push.

- [ ] `Dockerfile` (Xvfb, Chrome, extensão, serviço Python, vigia)
- [ ] `docker-compose.yml`: rede do container `warp`, volume do host para o perfil, `shm_size` grande
- [ ] GitHub Actions: build e publicação da imagem no GHCR a cada push na branch principal
- [ ] Atualização automática no notebook (Watchtower ou timer do systemd com `pull && up -d`)
- [ ] Expor a API pelo `cloudflared`
- [ ] Avaliar se o cron de reboot a cada 12h ainda faz sentido

## Fase 5: ligar o projeto-iptv

- [ ] Trocar `getReturnJsExecuted` / `getHtmlCriptografado` em `../projeto-iptv/src/function_rc.ts`
      pela API nova
- [ ] Conferir cache de URLs de vídeo (`mp4-list-*`) e `/proxy-rc` funcionando com o serviço novo
- [ ] Rodar alguns dias e observar se há ban

## Fase 6: desligar o FlareSolverr antigo

- [ ] Parar e remover o container `content-proxy-web-01`
- [ ] Liberar a memória e conferir que nada mais depende dele

---

## Descobertas e medições

Registre aqui tudo que for descoberto: padrões de URL, tempos de carregamento, uso de RAM,
problemas e soluções. Formato: data, o que foi visto, e o que isso muda.

| Data | Assunto | O que foi visto |
|---|---|---|
| | | |

# bot-supremo

Navegador real como serviço. Recebe uma URL, abre num Chrome de verdade, opcionalmente clica em
elementos da tela e devolve o **HTML renderizado** e as **requests de rede** que casaram com um
padrão pedido.

O princípio: o navegador tem que ser indistinguível de uma pessoa usando um PC normal. Chrome real
com perfil persistente, sem webdriver nem CDP, nada injetado na página e cliques de mouse reais.

**Estado:** em construção.

- Contexto, regras e contrato da API: [CLAUDE.md](CLAUDE.md)
- O que falta fazer: [docs/ROTEIRO.md](docs/ROTEIRO.md)

## Licença

[MIT](LICENSE)

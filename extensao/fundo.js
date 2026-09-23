// Bot Supremo: extensão observadora.
//
// Só observa a rede, lê o HTML e mede a posição de elementos. Não altera nenhuma página
// e não clica: quem digita e clica é o serviço Python, de fora do navegador (xdotool).
// Conversa com o serviço por WebSocket em 127.0.0.1 (a página não enxerga nada disso).

const PONTE = "ws://127.0.0.1:8081/ponte";
const PREFIXOS_PROPRIOS = ["ws://127.0.0.1:8081", "http://127.0.0.1:8081"];

let ws = null;

function enviar(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function conectar() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try {
    ws = new WebSocket(PONTE);
  } catch {
    ws = null;
    return;
  }
  ws.onopen = () => enviar({ evento: "ola", versao: chrome.runtime.getManifest().version });
  ws.onmessage = (m) => tratar(JSON.parse(m.data));
  ws.onclose = () => {
    ws = null;
    setTimeout(conectar, 2000);
  };
  ws.onerror = () => {};
}

// O service worker do MV3 dorme quando fica ocioso. O ping a cada 20 s mantém a conexão
// viva, e o alarme acorda o worker para reconectar se ele tiver sido encerrado mesmo assim.
conectar();
setInterval(() => {
  conectar();
  enviar({ evento: "ping" });
}, 20000);
chrome.alarms.create("acordar", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => conectar());

// Versão nova publicada pelo serviço: recarrega sozinha.
chrome.runtime.onUpdateAvailable.addListener(() => chrome.runtime.reload());

// ---------------------------------------------------------------------------
// Comandos vindos do serviço
// ---------------------------------------------------------------------------

async function tratar(msg) {
  if (msg.cmd === "verificarAtualizacao") {
    chrome.runtime.requestUpdateCheck().catch(() => {});
    return;
  }
  if (msg.id === undefined) return;
  try {
    const comando = COMANDOS[msg.cmd];
    if (!comando) throw new Error(`comando desconhecido: ${msg.cmd}`);
    enviar({ id: msg.id, ok: true, resultado: await comando(msg) });
  } catch (e) {
    enviar({ id: msg.id, ok: false, erro: String((e && e.message) || e) });
  }
}

const COMANDOS = {
  // Deixa uma aba só (fecha pop-ups e abas extras) e devolve qual é.
  async prepararAba() {
    const abas = await chrome.tabs.query({});
    const normais = abas.filter((a) => a.id !== chrome.tabs.TAB_ID_NONE);
    if (!normais.length) throw new Error("nenhuma aba do Chrome aberta");
    const principal = normais.find((a) => a.active) || normais[0];
    const outras = normais.filter((a) => a.id !== principal.id).map((a) => a.id);
    if (outras.length) await chrome.tabs.remove(outras);
    return { aba: principal.id, janela: principal.windowId, url: principal.url };
  },

  async irPara({ aba, url }) {
    await chrome.tabs.update(aba, { url });
    return true;
  },

  // Fim de pedido: só a aba principal, em about:blank.
  async limpar({ aba }) {
    const abas = await chrome.tabs.query({});
    const outras = abas.filter((a) => a.id !== aba).map((a) => a.id);
    if (outras.length) await chrome.tabs.remove(outras);
    await chrome.tabs.update(aba, { url: "about:blank" });
    return true;
  },

  async estadoAba({ aba }) {
    const a = await chrome.tabs.get(aba);
    return { url: a.url, titulo: a.title, status: a.status };
  },

  // Lê o HTML renderizado, no mundo isolado (o JavaScript da página não vê este código).
  async lerHtml({ aba }) {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId: aba },
      world: "ISOLATED",
      func: lerDocumento,
    });
    return r ? r.result : null;
  },

  // Mede o seletor em todos os frames. As contas de posição na tela ficam no serviço.
  async localizar({ aba, seletor }) {
    const frames = await chrome.webNavigation.getAllFrames({ tabId: aba });
    const resultados = await chrome.scripting.executeScript({
      target: { tabId: aba, allFrames: true },
      world: "ISOLATED",
      func: medirNoFrame,
      args: [seletor],
    });
    const aTab = await chrome.tabs.get(aba);
    const janela = await chrome.windows.get(aTab.windowId);
    return {
      janela: { x: janela.left, y: janela.top, w: janela.width, h: janela.height, estado: janela.state },
      zoom: await chrome.tabs.getZoom(aba),
      frames: (frames || []).map((f) => ({ id: f.frameId, pai: f.parentFrameId, url: f.url })),
      medidas: resultados.map((r) => ({ frame: r.frameId, ...(r.result || {}) })),
    };
  },
};

// Estas duas funções rodam dentro da página (mundo isolado) e só LEEM.

function lerDocumento() {
  const d = document.doctype;
  const doctype = d ? new XMLSerializer().serializeToString(d) + "\n" : "";
  return doctype + document.documentElement.outerHTML;
}

function medirNoFrame(seletor) {
  const res = { url: location.href, vw: innerWidth, vh: innerHeight, iframes: [], alvo: null };
  for (const f of document.querySelectorAll("iframe, frame")) {
    const r = f.getBoundingClientRect();
    const s = getComputedStyle(f);
    res.iframes.push({
      src: f.src || "",
      x: r.left + f.clientLeft + parseFloat(s.paddingLeft || "0"),
      y: r.top + f.clientTop + parseFloat(s.paddingTop || "0"),
    });
  }
  let el = null;
  try {
    el = document.querySelector(seletor);
  } catch (e) {
    res.erroSeletor = String(e.message);
    return res;
  }
  if (!el) return res;
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  const visivel =
    r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && parseFloat(s.opacity) > 0;
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  let coberto = false;
  if (visivel && cx >= 0 && cy >= 0 && cx < innerWidth && cy < innerHeight) {
    const topo = document.elementFromPoint(cx, cy);
    coberto = !!topo && topo !== el && !el.contains(topo);
  }
  res.alvo = { x: r.left, y: r.top, w: r.width, h: r.height, visivel, coberto };
  return res;
}

// ---------------------------------------------------------------------------
// Eventos observados (rede, navegação, título)
// ---------------------------------------------------------------------------

const FILTRO = { urls: ["<all_urls>"] };

function propria(d) {
  if (PREFIXOS_PROPRIOS.some((p) => d.url.startsWith(p))) return true;
  return (d.initiator || "").startsWith("chrome-extension://");
}

chrome.webRequest.onBeforeRequest.addListener((d) => {
  if (propria(d)) return;
  enviar({
    evento: "req",
    id: d.requestId,
    url: d.url,
    metodo: d.method,
    tipo: d.type,
    aba: d.tabId,
    frame: d.frameId,
    horario: d.timeStamp,
    iniciador: d.initiator || null,
  });
}, FILTRO);

chrome.webRequest.onCompleted.addListener((d) => {
  if (propria(d)) return;
  enviar({ evento: "reqFim", id: d.requestId, aba: d.tabId, status: d.statusCode, horario: d.timeStamp, doCache: d.fromCache });
}, FILTRO);

chrome.webRequest.onErrorOccurred.addListener((d) => {
  if (propria(d)) return;
  enviar({ evento: "reqFim", id: d.requestId, aba: d.tabId, status: null, erro: d.error, horario: d.timeStamp });
}, FILTRO);

chrome.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId !== 0) return;
  enviar({
    evento: "nav",
    fase: "commit",
    aba: d.tabId,
    url: d.url,
    transicao: d.transitionType,
    qualificadores: d.transitionQualifiers,
  });
});

chrome.webNavigation.onCompleted.addListener((d) => {
  if (d.frameId !== 0) return;
  enviar({ evento: "nav", fase: "completo", aba: d.tabId, url: d.url });
});

chrome.tabs.onUpdated.addListener((aba, mudou, t) => {
  if (mudou.title === undefined && mudou.status === undefined && mudou.url === undefined) return;
  enviar({ evento: "aba", aba, titulo: t.title, status: t.status, url: t.url });
});

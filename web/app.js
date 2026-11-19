// A tela: a revisão humana (a fila, a ficha original e os campos extraídos, que o revisor corrige,
// aprova ou rejeita), o catálogo aprovado e o assistente da loja.
//
// O texto das fichas vem de fornecedor: todo dado da API entra na página por textContent, nunca
// como HTML. Os POST vão sempre em JSON (a API recusa outro formato).

const CATEGORY_NAMES = {
  processador: "Processador",
  placa_mae: "Placa-mãe",
  memoria_ram: "Memória RAM",
  placa_de_video: "Placa de vídeo",
  armazenamento: "Armazenamento",
  fonte: "Fonte",
  gabinete: "Gabinete",
  cooler: "Cooler",
  outro: "Outro",
};
const REASON_NAMES = { reprovado: "reprovado", duvida_baixa: "dúvida baixa", auditoria: "auditoria" };
const LEVEL_NAMES = { media: "dúvida média", baixa: "dúvida baixa" };
const SET_NAMES = { dev: "desenvolvimento", holdout: "prova", inbox: "entrada" };
const STAGE_NAMES = { classify: "classificação", extract: "extração" };
const MODE_NAMES = { direct: "direto", batch: "lote" };
const NO_NAME = "Sem nome extraído";

const state = {
  queue: [], // a fila: [{id, id_origem, categoria, nome, motivos}]
  sheet: null, // a ficha aberta, como veio de /api/sheets/{id}
  initial: {}, // o formulário como foi aberto, para saber o que o revisor mudou
  catalog: [],
  approvedNow: new Set(), // aprovadas nesta visita: aparecem primeiro no catálogo, marcadas
  chat: [], // a conversa com o assistente: [{pergunta, resposta}], da mais antiga à mais nova
  asking: false, // uma pergunta esperando resposta
  navigation: 0, // a navegação mais recente; uma resposta de uma navegação antiga é ignorada
};

const $ = (id) => document.getElementById(id);

function el(tag, { className, text, attrs = {} } = {}, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  node.append(...children);
  return node;
}

// --- Formatos.

const money = (value) => `US$ ${value.toFixed(6).replace(".", ",")}`;
const price = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const categoryName = (category) => CATEGORY_NAMES[category] ?? category;

function localTime(text) {
  // "2026-11-19 18:02:33" (hora local, do banco) → "19/11/2026 18:02".
  const [date, time = ""] = text.split(" ");
  const [year, month, day] = date.split("-");
  return `${day}/${month}/${year} ${time.slice(0, 5)}`;
}

function normalize(text) {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

// --- API.

async function request(path, body) {
  const options =
    body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("O servidor não respondeu. Confira se o `cli.py serve` está rodando.");
  }
  const data = await response.json().catch(() => null);
  return { ok: response.ok, status: response.status, data };
}

function errorText(status, data) {
  if (typeof data?.detail === "string") return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map((error) => error.mensagem).join(" ");
  return `O servidor respondeu ${status}.`;
}

async function getJSON(path) {
  const { ok, status, data } = await request(path);
  if (!ok) throw new Error(errorText(status, data));
  return data;
}

// --- Avisos.

let statusTimer;

function announce(message, kind = "info") {
  const status = $("status");
  status.textContent = message;
  status.dataset.kind = kind;
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => (status.textContent = ""), kind === "error" ? 12000 : 6000);
}

const showError = (error) => announce(error.message, "error");

// --- Abas. Cada aba tem um link no <nav>, uma <section data-view> e uma função aqui, que a mostra.
// O endereço guarda a aba e a ficha aberta: #revisao/37, #catalogo, #assistente.

const VIEWS = { revisao: showReview, catalogo: showCatalog, assistente: showAssistant };

function route({ userAction = true } = {}) {
  const [name, id] = location.hash.slice(1).split("/");
  const view = name in VIEWS ? name : "revisao";
  for (const section of document.querySelectorAll("[data-view]")) {
    section.hidden = section.dataset.view !== view;
  }
  for (const link of document.querySelectorAll("[data-tab]")) {
    if (link.dataset.tab === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  VIEWS[view](id ? Number(id) : null, userAction).catch(showError);
}

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

// --- Resumo do topo.

async function loadSummary() {
  const summary = await getJSON("/api/summary");
  $("stat-catalogo").textContent = summary.catalogo;
  $("stat-fila").textContent = summary.fila;
  $("stat-rejeitados").textContent = summary.rejeitados;
  $("stat-motivos").replaceChildren(
    ...Object.entries(summary.fila_por_motivo).map(([reason, count]) =>
      el("span", {
        className: count ? `tag tag-${reason}` : "tag tag-zero",
        text: `${count} ${REASON_NAMES[reason]}`,
      }),
    ),
  );
  $("tab-count-revisao").textContent = summary.fila;
  $("tab-count-catalogo").textContent = summary.catalogo;

  const last = summary.custo.ultima_rodada;
  $("stat-rodada-custo").textContent = last ? money(last.custo) : "nenhuma ainda";
  $("stat-rodada-detalhe").textContent = last
    ? `(${MODE_NAMES[last.modo] ?? last.modo}, ${localTime(last.criado_em)}: ` +
      `${last.fichas} fichas, ${last.chamadas} chamadas)`
    : "";
}

// --- Revisão: a fila.

function reasonTags(reasons) {
  return el(
    "ul",
    { className: "tags", attrs: { "aria-label": "Motivos" } },
    reasons.map((reason) =>
      el("li", { className: `tag tag-${reason}`, text: REASON_NAMES[reason] ?? reason }),
    ),
  );
}

async function loadQueue() {
  state.queue = await getJSON("/api/queue");
  $("queue").replaceChildren(
    ...state.queue.map((item) =>
      el("li", {}, [
        el("a", { className: "queue-item", attrs: { href: `#revisao/${item.id}`, "data-id": item.id } }, [
          el("span", { className: "queue-name", text: item.nome ?? NO_NAME }),
          el("span", { className: "queue-meta" }, [
            el("span", { className: "origin", text: item.id_origem }),
            el("span", { text: categoryName(item.categoria) }),
          ]),
          reasonTags(item.motivos),
        ]),
      ]),
    ),
  );
  $("queue-count").textContent = state.queue.length;
  $("queue-empty").hidden = state.queue.length > 0;
}

function markSelected() {
  for (const link of document.querySelectorAll(".queue-item")) {
    if (Number(link.dataset.id) === state.sheet?.id) link.setAttribute("aria-current", "true");
    else link.removeAttribute("aria-current");
  }
}

async function showReview(id, userAction) {
  const navigation = ++state.navigation;
  await loadQueue();
  if (navigation !== state.navigation) return;
  // Uma ficha que já saiu da fila (ou um endereço velho) abre a primeira da fila.
  const inQueue = (sheetId) => state.queue.some((item) => item.id === sheetId);
  const target = [id, state.sheet?.id, state.queue[0]?.id].find(inQueue) ?? null;
  if (target === null) {
    clearSheet();
  } else if (target !== state.sheet?.id) {
    const sheet = await getJSON(`/api/sheets/${target}`);
    if (navigation !== state.navigation) return;
    openSheet(sheet);
    if (userAction) $("sheet-title").focus();
  }
  markSelected();
}

function clearSheet() {
  state.sheet = null;
  $("sheet-empty").hidden = false;
  $("sheet-body").hidden = true;
  $("form").hidden = true;
}

// --- Revisão: a ficha original.

function openSheet(sheet) {
  state.sheet = sheet;

  $("sheet-empty").hidden = true;
  $("sheet-body").hidden = false;
  $("sheet-title").textContent = sheet.campos.nome ?? NO_NAME;
  $("sheet-meta").replaceChildren(
    el("span", { className: "origin", text: sheet.id_origem }),
    el("span", { text: categoryName(sheet.categoria) }),
    el("span", { className: "muted", text: `conjunto: ${SET_NAMES[sheet.conjunto] ?? sheet.conjunto}` }),
  );
  $("sheet-reasons").replaceChildren(...reasonTags(sheet.motivos).children);
  $("sheet-text").textContent = sheet.texto;

  const attempts = sheet.tentativas;
  const cost = attempts.reduce((total, attempt) => total + attempt.custo, 0);
  $("attempts-summary").textContent = `${attempts.length} chamadas ao modelo, ${money(cost)}`;
  $("attempts").replaceChildren(
    ...attempts.map((attempt) => {
      const cache = attempt.tokens_cache_escrita + attempt.tokens_cache_leitura;
      const tokens = `${attempt.tokens_entrada} / ${attempt.tokens_saida} / ${cache}`;
      return el("tr", {}, [
        el("td", { text: `${STAGE_NAMES[attempt.etapa] ?? attempt.etapa} ${attempt.numero}` }),
        el("td", { text: `${attempt.rodada} (${MODE_NAMES[attempt.modo] ?? attempt.modo})` }),
        el("td", { className: "num", text: tokens }),
        el("td", { className: "num", text: money(attempt.custo) }),
        el("td", { className: attempt.erro ? "attempt-error" : "muted", text: attempt.erro ?? "passou" }),
      ]);
    }),
  );

  renderForm(sheet);
  showErrors(sheet.erros, "Erros que sobraram da extração");
}

// --- Revisão: o formulário, montado a partir do `formulario` da API (tipo, opções, null).

const inputId = (name) => `campo-${name}`;
// O rótulo do campo ("Garantia (meses)"), que vem do esquema; o nome do dado, se não houver.
const fieldLabel = (campo) =>
  state.sheet?.formulario.find((field) => field.campo === campo)?.rotulo ?? campo;

function displayValue(field, value) {
  if (value === null || value === undefined) return "";
  // Número com vírgula, como se escreve aqui; a leitura aceita vírgula e ponto.
  if (typeof value === "number" && field.tipo === "numero") return String(value).replace(".", ",");
  return String(value);
}

function parseNumber(text) {
  // "1.299,90", "1299,9" e "1299.9" valem 1299.9; "18.990" vale 18990 (ponto de milhar).
  let normalized = text;
  if (text.includes(",")) normalized = text.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(text)) normalized = text.replace(/\./g, "");
  return /^-?\d+(\.\d+)?$/.test(normalized) ? Number(normalized) : undefined;
}

function readValue(field) {
  const input = $(inputId(field.campo));
  if (input.tagName === "SELECT") return JSON.parse(input.value);
  const text = input.value.trim();
  if (text === "") return field.aceita_null ? null : "";
  if (field.tipo === "inteiro" || field.tipo === "numero") {
    // Texto que não é número vai como está: a API responde dizendo o que está errado.
    return parseNumber(text) ?? text;
  }
  return text;
}

const readForm = () =>
  Object.fromEntries(state.sheet.formulario.map((field) => [field.campo, readValue(field)]));

function changedFields() {
  return state.sheet.formulario
    .filter((field) => JSON.stringify(readValue(field)) !== JSON.stringify(state.initial[field.campo]))
    .map((field) => field.campo);
}

function makeInput(field, value) {
  const attrs = { id: inputId(field.campo), name: field.campo };
  if (field.tipo !== "booleano" && field.tipo !== "opcao") {
    const inputMode = { inteiro: "numeric", numero: "decimal" }[field.tipo] ?? "text";
    const input = el("input", {
      attrs: { ...attrs, type: "text", inputmode: inputMode, autocomplete: "off" },
    });
    input.value = displayValue(field, value);
    return input;
  }
  const options =
    field.tipo === "booleano"
      ? [[true, "sim"], [false, "não"]]
      : field.opcoes.map((option) => [option, option]);
  if (field.aceita_null) options.unshift([null, "não informado"]);
  // Um valor fora das opções (só num reprovado) aparece, para o revisor ver e trocar.
  if (!options.some(([option]) => option === value)) {
    options.push([value, `${value ?? "não informado"} (inválido)`]);
  }
  const select = el(
    "select",
    { attrs },
    options.map(([option, label]) => el("option", { text: label, attrs: { value: JSON.stringify(option) } })),
  );
  select.value = JSON.stringify(value);
  return select;
}

function fieldBlock(field, doubt) {
  const id = inputId(field.campo);
  const input = makeInput(field, state.sheet.campos[field.campo] ?? null);
  const label = el("label", { text: fieldLabel(field.campo), attrs: { for: id } });
  const head = el("div", { className: "field-head" }, [label]);
  const block = el("div", { className: "field", attrs: { "data-campo": field.campo } }, [head, input]);
  const described = [];

  const error = el("p", { className: "field-error", attrs: { id: `${id}-erro` } });
  error.hidden = true;
  block.append(error);
  described.push(error.id);
  if (doubt) {
    block.dataset.doubt = doubt.nivel;
    const level = LEVEL_NAMES[doubt.nivel];
    const reason = `Motivo do modelo: ${doubt.motivo}`;
    const badge = el("span", { className: `badge badge-${doubt.nivel}`, text: level });
    badge.id = `${id}-nivel`;
    head.append(badge);
    block.append(el("p", { className: "doubt-reason", text: reason, attrs: { id: `${id}-duvida` } }));
    described.push(`${id}-nivel`, `${id}-duvida`);
  }
  const changed = el("span", { className: "badge badge-changed", text: "alterado" });
  changed.hidden = true;
  head.append(changed);
  if (field.descricao) {
    block.append(el("p", { className: "field-help", text: field.descricao, attrs: { id: `${id}-ajuda` } }));
    described.push(`${id}-ajuda`);
  }
  input.setAttribute("aria-describedby", described.join(" "));
  return block;
}

// Um botão com o rótulo do campo, que leva até ele (na lista de dúvidas e na de erros).
function jumpTo(campo) {
  const button = el("button", { className: "link", text: fieldLabel(campo), attrs: { type: "button" } });
  button.addEventListener("click", () => $(inputId(campo)).focus());
  return button;
}

function renderForm(sheet) {
  const doubts = Object.fromEntries(sheet.duvidas.map((doubt) => [doubt.campo, doubt]));
  $("fields").replaceChildren(...sheet.formulario.map((field) => fieldBlock(field, doubts[field.campo])));
  state.initial = readForm();
  const note = $("form-note");
  if (sheet.duvidas.length === 0) {
    note.textContent = "O modelo não marcou dúvida em nenhum campo.";
  } else {
    note.replaceChildren("O modelo marcou dúvida em: ");
    sheet.duvidas.forEach((doubt, index) => {
      if (index > 0) note.append(", ");
      note.append(jumpTo(doubt.campo), ` (${LEVEL_NAMES[doubt.nivel]})`);
    });
    note.append(". Confira com a ficha.");
  }
  $("form").hidden = false;
  updateActions();
}

function updateActions() {
  const changed = changedFields();
  const empty = Object.keys(state.sheet.campos).length === 0;
  const approve = $("approve");
  const save = $("save-approve");
  // Aprovar manda os campos como estão; com edição, só "Salvar e aprovar" faz sentido.
  approve.disabled = changed.length > 0 || empty;
  save.disabled = changed.length === 0;
  $("reject").disabled = false;
  approve.classList.toggle("button-primary", !approve.disabled);
  save.classList.toggle("button-primary", !save.disabled);
  for (const block of document.querySelectorAll(".field")) {
    block.querySelector(".badge-changed").hidden = !changed.includes(block.dataset.campo);
  }
  let hint = "Aprovar manda os campos como estão para o catálogo.";
  if (changed.length) {
    const count = changed.length === 1 ? "1 campo alterado" : `${changed.length} campos alterados`;
    hint = `${count}: "Salvar e aprovar" grava as mudanças e manda para o catálogo.`;
  } else if (empty) {
    hint = 'A extração não trouxe campos: preencha o formulário e use "Salvar e aprovar".';
  }
  $("actions-hint").textContent = hint;
}

function onEdit(event) {
  // O erro do campo some quando ele é editado; a API confere de novo ao aprovar.
  const block = event.target.closest(".field");
  if (block) setFieldError(block, null);
  updateActions();
}

// --- Revisão: erros. O erro de um campo do formulário aparece no campo; os outros ('categoria',
// 'max_tokens', 'lote', 'body.*'...) aparecem acima do formulário. A lista do topo também leva a
// cada campo com erro.

function setFieldError(block, message) {
  const error = block.querySelector(".field-error");
  error.textContent = message ?? "";
  error.hidden = !message;
  block.classList.toggle("has-error", Boolean(message));
  const input = block.querySelector("input, select");
  if (message) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
}

function showErrors(errors, title) {
  const fields = [...document.querySelectorAll(".field")];
  const blocks = new Map(fields.map((block) => [block.dataset.campo, block]));
  const messages = new Map();
  const items = [];
  for (const { campo, mensagem } of errors) {
    const block = blocks.get(campo);
    if (!block) {
      items.push(el("li", { text: mensagem }));
      continue;
    }
    // A mensagem começa pelo campo ("tdp_w: ..."), que o rótulo já mostra.
    const text = mensagem.startsWith(`${campo}: `) ? mensagem.slice(campo.length + 2) : mensagem;
    messages.set(campo, [...(messages.get(campo) ?? []), text]);
    items.push(el("li", {}, [jumpTo(campo), `: ${text}`]));
  }
  for (const [campo, block] of blocks) setFieldError(block, messages.get(campo)?.join(" "));

  $("form-errors").hidden = items.length === 0;
  $("form-errors-title").textContent = title;
  $("form-errors-list").replaceChildren(...items);
  return messages;
}

// --- Revisão: aprovar e rejeitar.

async function decide(action) {
  const sheet = state.sheet;
  const body = action === "save" ? { campos: readForm() } : {};
  const endpoint = action === "reject" ? "reject" : "approve";
  const buttons = [$("approve"), $("save-approve"), $("reject")];
  const enabled = buttons.map((button) => !button.disabled);
  buttons.forEach((button) => (button.disabled = true));
  try {
    const { ok, status, data } = await request(`/api/sheets/${sheet.id}/${endpoint}`, body);
    if (ok) return finish(sheet, endpoint);
    if (status === 422 && Array.isArray(data?.detail)) {
      const fieldErrors = showErrors(data.detail, "A aprovação não passou na validação:");
      announce("A aprovação não passou: corrija os erros marcados.", "error");
      const first = sheet.formulario.find((field) => fieldErrors.has(field.campo));
      if (first) $(inputId(first.campo)).focus();
      else $("form-errors").scrollIntoView({ block: "nearest" });
    } else if (status === 409 || status === 404) {
      announce(errorText(status, data), "error");
      state.sheet = null;
      go("#revisao");
    } else {
      announce(errorText(status, data), "error");
    }
  } catch (error) {
    showError(error);
  } finally {
    if (state.sheet === sheet) buttons.forEach((button, index) => (button.disabled = !enabled[index]));
  }
}

function finish(sheet, endpoint) {
  if (endpoint === "approve") {
    state.approvedNow.add(sheet.id);
    announce(`${sheet.id_origem} aprovada: entrou no catálogo.`);
  } else {
    announce(`${sheet.id_origem} rejeitada: saiu da fila.`);
  }
  // Abre a próxima da fila (ou a anterior, se era a última).
  const index = state.queue.findIndex((item) => item.id === sheet.id);
  const next = state.queue[index + 1] ?? state.queue[index - 1];
  state.sheet = null;
  loadSummary().catch(showError);
  go(next ? `#revisao/${next.id}` : "#revisao");
}

// --- Catálogo.

async function showCatalog() {
  const navigation = ++state.navigation;
  const catalog = await getJSON("/api/catalog");
  if (navigation !== state.navigation) return;
  state.catalog = catalog;
  renderCatalog();
}

function renderCatalog() {
  const filter = normalize($("catalog-filter").value.trim());
  const rows = state.catalog
    .filter((product) => {
      const { nome, marca } = product.campos;
      const text = [nome, marca, categoryName(product.categoria), product.id_origem].join(" ");
      return normalize(text).includes(filter);
    })
    // As aprovadas agora primeiro (o sort é estável: o resto fica na ordem das fichas).
    .sort((a, b) => state.approvedNow.has(b.id) - state.approvedNow.has(a.id));

  $("catalog").replaceChildren(
    ...rows.map((product) => {
      const { nome, marca, preco, garantia_meses: warranty } = product.campos;
      const justApproved = state.approvedNow.has(product.id);
      const name = el("td", {}, [
        el("span", { className: "product-name", text: nome ?? NO_NAME }),
        el("span", { className: "origin", text: product.id_origem }),
      ]);
      if (justApproved) name.append(el("span", { className: "badge badge-changed", text: "aprovado agora" }));
      return el("tr", { className: justApproved ? "just-approved" : "", attrs: { "data-id": product.id } }, [
        name,
        el("td", { text: categoryName(product.categoria) }),
        el("td", { className: "optional", text: marca ?? "–" }),
        el("td", { className: "num", text: preco === null ? "sem preço" : price.format(preco) }),
        el("td", { className: "num optional", text: warranty === null ? "–" : `${warranty} meses` }),
      ]);
    }),
  );
  $("catalog-count").textContent = state.catalog.length;
  $("catalog-empty").hidden = state.catalog.length > 0;
}

// --- Assistente. A conversa fica só na memória da página: cada pergunta vai ao servidor com as
// últimas trocas (pergunta e resposta), e ele refaz a busca no catálogo.

const HISTORY_TURNS = 6; // o MAX_HISTORY_TURNS da API: ela recusa um histórico maior

async function showAssistant(_id, userAction) {
  if (userAction) $("question").focus();
}

function answerBubble({ resposta, chamadas, custo }) {
  const calls = chamadas === 1 ? "1 chamada ao modelo" : `${chamadas} chamadas ao modelo`;
  return el("div", { className: "bubble bubble-answer" }, [
    // "R$" e o número na mesma linha: o espaço vira um espaço que não quebra.
    el("p", { className: "answer-text", text: resposta.replace(/R\$ (?=\d)/g, "R$\u00a0") }),
    el("p", { className: "answer-cost", text: `${money(custo)} · ${calls}` }),
  ]);
}

function setAsking(asking) {
  state.asking = asking;
  $("ask-send").disabled = asking;
  $("ask-send").textContent = asking ? "Pensando..." : "Perguntar";
  $("chat-clear").disabled = asking || state.chat.length === 0;
  $("chat-empty").hidden = asking || state.chat.length > 0;
}

async function ask(question) {
  const historico = state.chat
    .slice(-HISTORY_TURNS)
    .map(({ pergunta, resposta }) => ({ pergunta, resposta }));
  const pending = el("p", { className: "bubble bubble-answer bubble-pending", text: "pensando..." });
  const turn = el("li", { className: "turn" }, [
    el("p", { className: "bubble bubble-question", text: question }),
    pending,
  ]);
  $("chat").append(turn);
  $("question").value = "";
  setAsking(true);
  // A troca nova no alto da tela (ou o fim da conversa, se ela couber): a caixa da pergunta, presa
  // embaixo, não cobre a resposta curta, e a longa aparece do começo.
  turn.scrollIntoView({ block: "start" });
  try {
    const { ok, status, data } = await request("/api/chat", { pergunta: question, historico });
    if (!ok) throw new Error(errorText(status, data));
    state.chat.push({ pergunta: question, resposta: data.resposta });
    pending.replaceWith(answerBubble(data));
    turn.scrollIntoView({ block: "start" });
  } catch (error) {
    // A pergunta volta para a caixa, para tentar de novo.
    turn.remove();
    if (!$("question").value) $("question").value = question;
    showError(error);
  } finally {
    setAsking(false);
  }
}

function onAsk(event) {
  event.preventDefault();
  const question = $("question").value.trim();
  if (question && !state.asking) ask(question);
}

function clearChat() {
  state.chat = [];
  $("chat").replaceChildren();
  setAsking(false);
  $("question").focus();
}

// --- Início.

$("approve").addEventListener("click", () => decide("approve"));
$("save-approve").addEventListener("click", () => decide("save"));
$("reject").addEventListener("click", () => decide("reject"));
$("form").addEventListener("input", onEdit);
$("form").addEventListener("submit", (event) => event.preventDefault());
$("catalog-filter").addEventListener("input", renderCatalog);
$("ask").addEventListener("submit", onAsk);
$("question").addEventListener("keydown", (event) => {
  // Enter envia; Shift+Enter quebra a linha.
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("ask").requestSubmit();
  }
});
$("chat-clear").addEventListener("click", clearChat);
for (const button of document.querySelectorAll(".suggestion")) {
  // A sugestão vai para a caixa; quem envia é a pessoa (cada pergunta custa uma chamada real).
  button.addEventListener("click", () => {
    $("question").value = button.textContent;
    $("question").focus();
  });
}
// O "pular para o conteúdo" só leva o foco: mudar o endereço trocaria de aba.
document.querySelector(".skip-link").addEventListener("click", (event) => {
  event.preventDefault();
  $("conteudo").focus();
});
window.addEventListener("hashchange", () => route());

loadSummary().catch(showError);
route({ userAction: false });

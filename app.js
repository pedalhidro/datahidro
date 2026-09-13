'use strict';

/* datahidro — levantamento Pedal Hidrográfico de candidatas às eleições de 2026.
 *
 * PWA estática sem build. Um deck por rota de hash:
 *   #/inicio → #/<slug do cargo> (×5) → #/enviar → #/obrigada
 * Os decks de cargo saem de data/candidates.json (gerado por
 * tools/ingest_tse.py a partir dos dados abertos do TSE; cargos, rótulos e
 * limites vêm do data/vocab.ttl — não duplicar aqui). As escolhas vivem num
 * rascunho no localStorage; o envio é JSON anônimo pro backend
 * (POST /api/responses), que monta o RDF e valida com SHACL. O selo é
 * desenhado no canvas: a foto nunca sai do aparelho.
 *
 * Ordem dos cards: pelas mais escolhidas (placar ao vivo de /api/tally, só
 * totais anônimos) ou sorteio ponderado pelo partido (GPS Partidário; semente
 * nova a cada carregamento; tocar de novo sorteia outra). A marcação mostra ✓,
 * não posição — a ordem em que alguém marca não é registrada.
 */

// ===================== constantes =====================

const CONSENT_VERSION = '2026-09-v2'; // v2: resposta anônima (sem nome/contato)
const STORAGE_KEYS = {
  draft: 'datahidro:draft:v1',
  sent: 'datahidro:sent:v1',
  womenOnly: 'datahidro:women-only:v1',
};
// cmocean.phase (mesmas âncoras do style.css e do cameratopo/render.py)
const PALETTE = [
  '#a8780d', '#be6828', '#cf5643', '#db4066', '#df2a93', '#d529c4', '#c041e5', '#a25cf3',
  '#7d73f0', '#5285dc', '#2c90bc', '#19959c', '#0c987c', '#249a52', '#5e9420', '#8b860d',
];
const CONNECTIVES = /^(da|das|de|do|dos|e|di|du)$/i;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

// ===================== estado =====================

const state = {
  catalog: null,
  offices: [], // em ordem de tela
  bySq: new Map(), // sq → { ...candidatura, office, haystack }
  routes: [], // ['inicio', ...slugs, 'enviar', 'obrigada']
  choices: {}, // slug → [sq, …]
  decks: new Map(), // slug → deck montado (ver buildOfficeDeck)
  currentRoute: null,
  scrollY: new Map(), // rota → scrollY ao sair
  responseId: null, // UUID v4 do aparelho: reenviar substitui a resposta
  sent: null, // { id, at, choices } do último envio aceito
  sending: false,
  tally: null, // última resposta de /api/tally ({ open, available, counts, … })
  womenOnly: false, // chip "só mulheres": esconde DS_GENERO MASCULINO em todos os cargos
};

const $ = (id) => document.getElementById(id);

// ===================== utilidades =====================

function load(key) {
  try {
    return JSON.parse(localStorage.getItem(key));
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* modo privado / cota: segue só em memória */
  }
}

function esc(s) {
  return String(s ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
  );
}

function normalize(s) {
  return String(s || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase();
}

function initials(name) {
  const parts = name.split(/\s+/).filter((p) => p && !CONNECTIVES.test(p));
  if (!parts.length) return '?';
  const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
  return (parts[0][0] + last).toUpperCase();
}

function colorFor(sq) {
  return PALETTE[Number(String(sq).slice(-5)) % PALETTE.length];
}

function plural(n, one, many) {
  return `${n.toLocaleString('pt-BR')} ${n === 1 ? one : many}`;
}

function newId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// Semente do sorteio: nova a cada carregamento da página (DECISÃO do Danilo,
// 2026-09-13) — recarregar sorteia outra ordem; durante a visita ela fica parada.
function randomSeed() {
  return crypto.getRandomValues(new Uint32Array(1))[0];
}

// mulberry32: mesma semente → mesma sequência.
function seededRandom(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Sorteio ponderado sem reposição (Efraimidis–Spirakis como corrida de
// exponenciais): cada item tira um tempo −ln(u)/peso e a lista sai do menor pro
// maior. A 1ª posição cai com cada item na proporção do seu peso; a 2ª, idem
// entre os que sobraram; e assim por diante. Pesos iguais → embaralhamento
// uniforme. Mesma semente → mesma ordem.
function weightedShuffle(list, seed, weightOf) {
  const rand = seededRandom(seed);
  return list
    .map((item) => ({ item, time: -Math.log(1 - rand()) / weightOf(item) }))
    .sort((a, b) => a.time - b.time)
    .map((x) => x.item);
}

// Peso do sorteio por partido (DECISÃO do Danilo, 2026-09-13): 1 + (z − z_max)²,
// z = media_z do partido no GPS Partidário 2026 da Folha (negativo = esquerda) e
// z_max = a maior do catálogo. O partido mais à direita pesa 1 (NOVO) e o peso
// cresce com o quadrado da distância até ele (PSTU ~17). O ingest já resolve
// siglas e substitutos (PCO = PSTU); partido que ainda assim falte entra com
// z = 0, a média. Sem a tabela (catálogo de exemplo), todos pesam 1.
function partyWeigher(catalog) {
  const z = catalog.party_lean?.media_z;
  if (!z) return () => 1;
  const zMax = Math.max(...Object.values(z));
  return (party) => 1 + ((z[party] ?? 0) - zMax) ** 2;
}

let toastTimer = null;
function toast(message, ms = 3200) {
  const el = $('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), ms);
}

// Foto ou iniciais sobre uma cor da paleta (cor via CSSOM: a CSP barra style="").
// [n] <small>nome</small>, PARTIDO, <strong>N votos</strong> — um item do
// placar da tela de início; reusa faceHtml (mesma miniatura de foto/iniciais
// dos cards e da barra inferior).
function leaderboardItemHtml(c, votes) {
  return (
    `<a class="leaderboard-item" href="#/${esc(c.office)}">` +
    faceHtml(c, 'leaderboard-photo') +
    `<span class="leaderboard-text"><b>${esc(c.number)}</b> <small>${esc(c.name)}</small>, ${esc(c.party)}, ` +
    `<strong>${esc(plural(votes, 'voto', 'votos'))}</strong></span></a>`
  );
}

function faceHtml(c, className) {
  if (c.photo) {
    return `<span class="${className}"><img src="${esc(c.photo)}" alt="" loading="lazy" decoding="async" width="180" height="240"></span>`;
  }
  return `<span class="${className} no-photo" data-sq="${esc(c.sq)}" data-initials="${esc(initials(c.name))}"></span>`;
}

function paintNoPhoto(root) {
  for (const el of root.querySelectorAll('.no-photo[data-sq]')) {
    el.style.setProperty('--no-photo', colorFor(el.dataset.sq));
  }
}

// foto que falhar (404, offline sem cache) vira iniciais
function replaceWithInitials(img) {
  const box = img.parentElement;
  const owner = img.closest('[data-sq]');
  const c = owner && state.bySq.get(owner.dataset.sq);
  if (!c || !box) return;
  box.classList.add('no-photo');
  box.dataset.sq = c.sq;
  box.dataset.initials = initials(c.name);
  box.style.setProperty('--no-photo', colorFor(c.sq));
  img.remove();
}

// ===================== rascunho =====================

function saveDraft() {
  save(STORAGE_KEYS.draft, {
    id: state.responseId,
    choices: state.choices,
    consent: $('consent').checked,
  });
}

// ===================== catálogo =====================

async function loadCatalog() {
  const resp = await fetch('data/candidates.json');
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function prepareCatalog(draft) {
  const cat = state.catalog;
  state.offices = cat.offices;
  let removed = 0;
  for (const office of cat.offices) {
    for (const c of cat.candidates[office.slug] || []) {
      state.bySq.set(c.sq, {
        ...c,
        office: office.slug,
        haystack: normalize(`${c.name} ${c.full_name || ''} ${c.party}`),
      });
    }
    const saved = draft.choices?.[office.slug] || [];
    const valid = saved.filter((sq) => state.bySq.get(sq)?.office === office.slug);
    removed += saved.length - valid.length;
    state.choices[office.slug] = valid.slice(0, office.max);
  }
  state.routes = ['inicio', ...cat.offices.map((o) => o.slug), 'enviar', 'obrigada'];
  $('sample-banner').hidden = !cat.sample;
  if (removed) {
    toast(
      removed === 1
        ? '1 candidatura saiu da lista do TSE e foi desmarcada.'
        : `${removed} candidaturas saíram da lista do TSE e foram desmarcadas.`,
      6000
    );
  }
}

// ===================== decks de cargo =====================

function question(office) {
  return `Quem você considera pra ${office.label.toLowerCase()}? Marque até ${office.max}.`;
}

function cardHtml(c) {
  const party = state.catalog.parties[c.party] || c.party;
  return (
    `<button type="button" class="card" data-sq="${esc(c.sq)}" aria-pressed="false" ` +
    `aria-label="${esc(`${c.name}, número ${c.number}, ${party}`)}">` +
    faceHtml(c, 'card-photo') +
    '<span class="card-check" aria-hidden="true">✓</span>' +
    `<span class="card-name">${esc(c.name)}</span>` +
    `<span class="card-meta"><span class="card-number">${esc(c.number)}</span>` +
    `<span class="card-party">${esc(c.party)}</span><span class="card-votes" hidden></span></span>` +
    '</button>'
  );
}

function buildOfficeDeck(office) {
  const el = $('tpl-office').content.firstElementChild.cloneNode(true);
  el.id = `deck-${office.slug}`;
  el.dataset.route = office.slug;
  const title = el.querySelector('.office-title');
  title.textContent = office.title;
  title.tabIndex = -1;
  el.querySelector('.counter-max').textContent = `/${office.max}`;
  el.querySelector('.office-question').textContent = question(office);

  const list = state.catalog.candidates[office.slug] || [];
  const deck = {
    office,
    el,
    grid: el.querySelector('.grid'),
    search: el.querySelector('.search'),
    chips: el.querySelector('.chips'),
    resultText: el.querySelector('.result-text'),
    orderButtons: el.querySelectorAll('.order-btn'),
    empty: el.querySelector('.empty'),
    cards: new Map(),
    filter: { query: '', parties: new Set() },
    total: list.length,
    seed: randomSeed(),
    order: null, // 'votes' | 'random'; null = padrão (votes se o placar estiver disponível)
  };
  deck.search.setAttribute('aria-label', `Buscar candidatas a ${office.label.toLowerCase()}`);
  deck.chips.setAttribute('aria-label', 'Filtrar por partido');

  const acronyms = [...new Set(list.map((c) => c.party))].sort((a, b) => a.localeCompare(b, 'pt-BR'));
  const hasMen = list.some((c) => c.gender === 'MASCULINO');
  deck.chips.innerHTML =
    (hasMen
      ? `<button type="button" class="chip chip-women" data-women aria-pressed="${state.womenOnly}">só mulheres</button>` +
        '<span class="chips-divider" aria-hidden="true"></span>'
      : '') +
    '<button type="button" class="chip" data-party="" aria-pressed="true">todos</button>' +
    acronyms
      .map(
        (p) =>
          `<button type="button" class="chip" data-party="${esc(p)}" aria-pressed="false" ` +
          `title="${esc(state.catalog.parties[p] || p)}">${esc(p)}</button>`
      )
      .join('');
  deck.chips.hidden = acronyms.length < 2 && !hasMen;
  deck.search.hidden = list.length < 2;
  el.querySelector('.order').hidden = list.length < 2;

  deck.grid.innerHTML = list.map(cardHtml).join(''); // a ordem vem do sortDeck
  for (const card of deck.grid.children) deck.cards.set(card.dataset.sq, card);
  paintNoPhoto(deck.grid);

  // error não borbulha: captura
  deck.grid.addEventListener('error', (ev) => ev.target.tagName === 'IMG' && replaceWithInitials(ev.target), true);
  deck.grid.addEventListener('click', (ev) => {
    const card = ev.target.closest('.card');
    if (card) toggleChoice(deck, card.dataset.sq);
  });
  deck.chips.addEventListener('click', (ev) => {
    const chip = ev.target.closest('.chip');
    if (!chip) return;
    if (chip.hasAttribute('data-women')) {
      setWomenOnly(!state.womenOnly);
      return;
    }
    const { parties } = deck.filter;
    const party = chip.dataset.party;
    if (!party) parties.clear();
    else if (parties.has(party)) parties.delete(party);
    else parties.add(party);
    for (const b of deck.chips.querySelectorAll('[data-party]')) {
      const p = b.dataset.party;
      b.setAttribute('aria-pressed', String(p ? parties.has(p) : !parties.size));
    }
    applyFilter(deck);
  });
  let debounce = null;
  deck.search.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      deck.filter.query = deck.search.value;
      applyFilter(deck);
    }, 120);
  });
  deck.search.addEventListener('keydown', (ev) => ev.key === 'Enter' && deck.search.blur());
  el.querySelector('.order').addEventListener('click', (ev) => {
    const btn = ev.target.closest('.order-btn');
    if (!btn) return;
    if (btn.dataset.order === 'random' && effectiveOrder(deck) === 'random') {
      deck.seed = randomSeed(); // tocou de novo: novo sorteio
    }
    deck.order = btn.dataset.order;
    sortDeck(deck);
    window.scrollTo({ top: 0 });
  });

  $('office-decks').append(el);
  state.decks.set(office.slug, deck);
  updateDeckSelection(deck);
  sortDeck(deck);
  return deck;
}

function applyFilter(deck) {
  const { parties } = deck.filter;
  const terms = normalize(deck.filter.query).split(/\s+/).filter(Boolean);
  let visible = 0;
  for (const [sq, card] of deck.cards) {
    const c = state.bySq.get(sq);
    const match =
      (!state.womenOnly || c.gender !== 'MASCULINO') &&
      (!parties.size || parties.has(c.party)) &&
      terms.every((t) => (/^\d+$/.test(t) ? c.number.startsWith(t) : c.haystack.includes(t)));
    if (card.hidden === match) card.hidden = !match;
    if (match) visible++;
  }
  const filtering = terms.length || parties.size || (state.womenOnly && deck.chips.querySelector('[data-women]'));
  deck.resultText.textContent = !deck.total
    ? ''
    : filtering
      ? `${visible.toLocaleString('pt-BR')} de ${plural(deck.total, 'candidata', 'candidatas')}`
      : plural(deck.total, 'candidata', 'candidatas');
  deck.empty.hidden = visible > 0;
  deck.empty.textContent = deck.total
    ? 'Nenhuma candidata encontrada. Tente outro nome, número ou partido.'
    : `Nenhuma candidatura a ${deck.office.label.toLowerCase()} na lista do TSE usada aqui.`;
}

// Top 5 de um cargo pelo placar ao vivo (mesmos totais de sortDeck) — só
// candidaturas com ao menos 1 escolha; cargo sem nenhuma some da lista.
function topCandidates(officeSlug, n = 5) {
  const counts = state.tally?.counts;
  if (!counts) return [];
  return (state.catalog.candidates[officeSlug] || [])
    .map((c) => ({ c: state.bySq.get(c.sq), votes: counts[c.sq] || 0 }))
    .filter((x) => x.votes > 0)
    .sort((a, b) => b.votes - a.votes)
    .slice(0, n);
}

function renderLeaderboard() {
  const el = $('intro-leaderboard');
  if (!tallyAvailable()) {
    el.hidden = true;
    return;
  }
  const sections = state.offices
    .map((office) => {
      const top = topCandidates(office.slug);
      if (!top.length) return '';
      const items = top
        .map(({ c, votes }) => leaderboardItemHtml(c, votes))
        .join('<span class="leaderboard-sep" aria-hidden="true">|</span>');
      return (
        `<div class="leaderboard-office"><p class="leaderboard-title">${esc(office.title)}</p>` +
        `<div class="leaderboard-row">${items}</div></div>`
      );
    })
    .join('');
  el.querySelectorAll('.leaderboard-office').forEach((n) => n.remove());
  el.insertAdjacentHTML('beforeend', sections);
  el.hidden = !sections;
  $('intro-votes').textContent = votesSummary();
  paintNoPhoto(el);
}

// Votos por cargo = escolhas somadas (uma resposta marca até 10 por cargo), não
// respostas. "1.234 votos totais, sendo 500 em deputadas estaduais, …, 100 em
// governadoras e 34 em presidentas".
function votesSummary() {
  const counts = state.tally?.counts || {};
  const perOffice = state.offices.map((office) => ({
    office,
    votes: (state.catalog.candidates[office.slug] || []).reduce((sum, c) => sum + (counts[c.sq] || 0), 0),
  }));
  const total = perOffice.reduce((sum, x) => sum + x.votes, 0);
  const parts = perOffice.map(
    ({ office, votes }) => `${votes.toLocaleString('pt-BR')} em ${(office.plural || office.title).toLocaleLowerCase('pt-BR')}`
  );
  const last = parts.pop();
  return `${plural(total, 'voto total', 'votos totais')}, sendo ${parts.length ? `${parts.join(', ')} e ` : ''}${last}`;
}

function setWomenOnly(on) {
  state.womenOnly = on;
  save(STORAGE_KEYS.womenOnly, on);
  for (const deck of state.decks.values()) {
    deck.chips.querySelector('[data-women]')?.setAttribute('aria-pressed', String(on));
    applyFilter(deck);
  }
}

// ===================== ordem + placar =====================
// Placar = totais anônimos de /api/tally. O backend só manda números a partir
// de um mínimo de respostas (e de DATAHIDRO_TALLY_OPENS_AT, se definido); antes
// disso a única ordem é a sorteada. Números atualizam ao vivo; a grade só
// reordena ao entrar no cargo ou ao tocar na ordem — nada pula debaixo do dedo.

function tallyAvailable() {
  return Boolean(state.tally?.available && state.tally.counts);
}

function effectiveOrder(deck) {
  return tallyAvailable() ? deck.order || 'votes' : 'random';
}

function sortDeck(deck) {
  const order = effectiveOrder(deck);
  const weight = partyWeigher(state.catalog);
  const sorted = weightedShuffle(state.catalog.candidates[deck.office.slug] || [], deck.seed, (c) => weight(c.party));
  if (order === 'votes') {
    const position = new Map(sorted.map((c, i) => [c.sq, i])); // empate: vale o sorteio
    const counts = state.tally.counts;
    sorted.sort((a, b) => (counts[b.sq] || 0) - (counts[a.sq] || 0) || position.get(a.sq) - position.get(b.sq));
  }
  deck.grid.append(...sorted.map((c) => deck.cards.get(c.sq)));
  for (const btn of deck.orderButtons) {
    btn.hidden = btn.dataset.order === 'votes' && !tallyAvailable();
    btn.setAttribute('aria-pressed', String(btn.dataset.order === order));
  }
  updateVotes(deck);
  applyFilter(deck);
}

function updateVotes(deck) {
  const available = tallyAvailable();
  for (const [sq, card] of deck.cards) {
    const el = card.querySelector('.card-votes');
    el.hidden = !available;
    if (available) el.textContent = plural(state.tally.counts[sq] || 0, 'escolha', 'escolhas');
  }
}

async function loadTally() {
  const wasAvailable = tallyAvailable();
  try {
    const resp = await fetch('api/tally');
    if (!resp.ok) return;
    state.tally = await resp.json();
  } catch {
    return; // offline: fica o que já tinha
  }
  if (state.currentRoute === 'inicio') fillIntro();
  if (state.currentRoute === 'obrigada') drawBoardStory();
  for (const deck of state.decks.values()) {
    if (tallyAvailable() !== wasAvailable) sortDeck(deck);
    else updateVotes(deck);
  }
}

// ===================== seleção =====================

function toggleChoice(deck, sq) {
  const list = state.choices[deck.office.slug];
  const i = list.indexOf(sq);
  if (i >= 0) {
    list.splice(i, 1);
  } else if (list.length >= deck.office.max) {
    const card = deck.cards.get(sq);
    card.classList.remove('refuse');
    void card.offsetWidth; // reinicia a animação
    card.classList.add('refuse');
    navigator.vibrate?.(40);
    toast(`Máximo de ${deck.office.max} pra ${deck.office.label.toLowerCase()}. Desmarque alguém pra trocar.`);
    return;
  } else {
    list.push(sq);
  }
  saveDraft();
  updateDeckSelection(deck);
  updateBar();
}

function updateDeckSelection(deck) {
  const list = state.choices[deck.office.slug];
  const chosen = new Set(list);
  for (const [sq, card] of deck.cards) {
    const pressed = String(chosen.has(sq));
    if (card.getAttribute('aria-pressed') !== pressed) card.setAttribute('aria-pressed', pressed);
  }
  deck.el.querySelector('.counter-n').textContent = list.length;
  deck.grid.classList.toggle('full', list.length >= deck.office.max);
}

// ===================== rotas =====================

function routeName(route) {
  const office = state.offices.find((o) => o.slug === route);
  return office ? office.title : { inicio: 'Início', enviar: 'Enviar', obrigada: 'Obrigada' }[route];
}

// Rótulo curto da barra de etapas do topo (intro | dep. est. | … | obrigada);
// o dos cargos vem do vocab.ttl (dh:shortLabel).
function stepLabel(route) {
  const office = state.offices.find((o) => o.slug === route);
  return office ? office.short || office.label.toLowerCase() : { inicio: 'intro', enviar: 'enviar', obrigada: 'obrigada' }[route];
}

function goTo(route) {
  if (location.hash === `#/${route}`) handleRoute();
  else location.hash = `#/${route}`;
}

function deckElement(route) {
  if (state.decks.has(route)) return state.decks.get(route).el;
  const office = state.offices.find((o) => o.slug === route);
  return office ? buildOfficeDeck(office).el : $(`deck-${route}`);
}

function handleRoute() {
  const requested = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  let route = state.routes.includes(requested) ? requested : 'inicio';
  if (route === 'obrigada' && !state.sent) route = 'enviar'; // brinde só depois de enviar
  if (requested !== route) history.replaceState(null, '', `#/${route}`);
  if (state.currentRoute === route) return;

  if (state.currentRoute) {
    state.scrollY.set(state.currentRoute, window.scrollY);
    deckElement(state.currentRoute).hidden = true;
  }
  const el = deckElement(route);
  el.hidden = false;
  const deck = state.decks.get(route);
  if (deck && effectiveOrder(deck) === 'votes') sortDeck(deck); // totais novos
  state.currentRoute = route;
  document.body.dataset.deck = String(state.routes.indexOf(route) + 1);

  if (route === 'inicio') fillIntro();
  if (route === 'enviar') renderSummary();
  if (route === 'obrigada') prepareBadge();
  updateProgress();
  updateBar();

  window.scrollTo(0, state.scrollY.get(route) || 0);
  el.querySelector('h1')?.focus({ preventScroll: true });
  document.title =
    route === 'inicio' ? 'datahidro 2026 · levantamento Pedal Hidrográfico' : `${routeName(route)} · datahidro 2026`;
}

function goNext() {
  const i = state.routes.indexOf(state.currentRoute);
  if (state.currentRoute === 'obrigada') goTo('inicio');
  else if (state.currentRoute === 'enviar') $('submit-form').requestSubmit();
  else goTo(state.routes[i + 1]);
}

function goBack() {
  const i = state.routes.indexOf(state.currentRoute);
  if (i > 0) goTo(state.routes[i - 1]);
}

function buildProgress() {
  const ol = $('progress');
  const n = state.routes.length;
  // o nome acessível começa pelo rótulo visível (quem usa comando de voz fala o que vê)
  ol.innerHTML = state.routes
    .map((r, i) => {
      const label = stepLabel(r);
      return (
        `<li data-route="${esc(r)}"><button type="button" aria-label="${esc(label)}: etapa ${i + 1} de ${n}, ${esc(routeName(r))}">` +
        `<span class="progress-label">${esc(label)}</span></button></li>`
      );
    })
    .join('');
  ol.addEventListener('click', (ev) => {
    const li = ev.target.closest('li');
    if (li && !li.firstElementChild.disabled) goTo(li.dataset.route);
  });
}

function updateProgress() {
  const current = state.routes.indexOf(state.currentRoute);
  $('progress')
    .querySelectorAll('li')
    .forEach((li, i) => {
      const btn = li.firstElementChild;
      li.classList.toggle('current', i === current);
      li.classList.toggle('done', i < current);
      btn.disabled = li.dataset.route === 'obrigada' && !state.sent;
      if (i === current) btn.setAttribute('aria-current', 'step');
      else btn.removeAttribute('aria-current');
    });
}

// ===================== barra inferior =====================

function miniHtml(c, { button = false } = {}) {
  const tag = button ? 'button' : 'span';
  const attrs = button ? ` type="button" aria-label="Desmarcar ${esc(c.name)}"` : ' aria-hidden="true"';
  return c.photo
    ? `<${tag} class="mini" data-sq="${esc(c.sq)}"${attrs}><img src="${esc(c.photo)}" alt="" decoding="async"></${tag}>`
    : `<${tag} class="mini no-photo" data-sq="${esc(c.sq)}" data-initials="${esc(initials(c.name))}"${attrs}></${tag}>`;
}

function totalChoices() {
  return Object.values(state.choices).reduce((n, list) => n + list.length, 0);
}

function updateBar() {
  const route = state.currentRoute;
  const deck = state.decks.get(route);
  const back = $('btn-back');
  const next = $('btn-next');
  const middle = $('bottombar-middle');
  back.hidden = route === 'inicio' || route === 'obrigada';
  next.hidden = false;
  next.disabled = false;
  middle.textContent = '';

  if (route === 'inicio') {
    next.textContent = state.sent ? 'Revisar respostas' : 'Começar';
    middle.textContent = state.sent ? '' : 'uns 3 minutos · anônimo';
  } else if (deck) {
    const list = state.choices[route];
    if (list.length) {
      middle.innerHTML = list.map((sq) => miniHtml(state.bySq.get(sq), { button: true })).join('');
      paintNoPhoto(middle);
      middle.scrollLeft = middle.scrollWidth;
    } else {
      middle.textContent = deck.total ? 'Toque nas fotos pra escolher.' : '';
    }
    next.textContent = list.length ? 'Próximo' : 'Pular';
  } else if (route === 'enviar') {
    middle.textContent = `${plural(totalChoices(), 'escolha', 'escolhas')} no total`;
    next.textContent = state.sent ? 'Enviar de novo' : 'Enviar';
    next.disabled = state.sending;
  } else if (route === 'obrigada') {
    next.textContent = 'Voltar ao início';
  }
}

// ===================== início =====================

function fillIntro() {
  renderLeaderboard();
  $('intro-resubmit').hidden = !state.sent;
}

// ===================== enviar =====================

function renderSummary() {
  $('summary').innerHTML = state.offices
    .map((office) => {
      const list = state.choices[office.slug];
      const items = list
        .map((sq) => {
          const c = state.bySq.get(sq);
          return `<li class="summary-item">${miniHtml(c)}<span>${esc(c.name)}</span><b>${esc(c.number)}</b></li>`;
        })
        .join('');
      return (
        `<section class="summary-office"><div class="summary-top"><h2>${esc(office.title)}</h2>` +
        `<a class="edit" href="#/${esc(office.slug)}">${list.length ? 'editar' : 'escolher'}</a></div>` +
        (list.length ? `<ul class="summary-list">${items}</ul>` : '<p class="summary-none">Nenhuma escolha — cargo pulado.</p>') +
        '</section>'
      );
    })
    .join('');
  paintNoPhoto($('summary'));
  $('btn-submit').textContent = state.sent ? 'Enviar de novo' : 'Enviar respostas';
}

function showSubmitError(message) {
  const el = $('submit-error');
  el.textContent = message;
  el.hidden = false;
}

async function submitResponse(ev) {
  ev.preventDefault();
  if (state.sending) return;
  const consent = $('consent');
  $('submit-error').hidden = true;

  if (!consent.checked) {
    const box = consent.closest('.consent');
    box.classList.add('missing');
    showSubmitError('Pra enviar, marque a autorização acima — sem ela não podemos guardar a resposta.');
    box.scrollIntoView({ block: 'center', behavior: 'smooth' });
    consent.focus({ preventScroll: true });
    return;
  }
  if (!totalChoices() && !confirm('Você não marcou nenhuma candidata. Enviar mesmo assim?')) return;

  const button = $('btn-submit');
  state.sending = true;
  button.disabled = true;
  button.textContent = 'Enviando…';
  updateBar();
  const body = {
    id: state.responseId,
    choices: Object.fromEntries(Object.entries(state.choices).filter(([, list]) => list.length)),
    consent: true,
    consent_version: CONSENT_VERSION,
  };

  try {
    const resp = await fetch('api/responses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const json = await resp.json().catch(() => ({}));
    if (resp.ok) {
      state.sent = { id: state.responseId, at: new Date().toISOString(), choices: totalChoices() };
      save(STORAGE_KEYS.sent, state.sent);
      saveDraft();
      state.sending = false;
      loadTally(); // o placar já inclui esta resposta
      goTo('obrigada');
      return;
    }
    if (resp.status === 422 && Array.isArray(json.unknown)) {
      // o catálogo mudou desde que a página abriu: desmarca e pede revisão
      for (const list of Object.values(state.choices)) {
        for (const sq of json.unknown) {
          const i = list.indexOf(sq);
          if (i >= 0) list.splice(i, 1);
        }
      }
      state.decks.forEach((deck) => updateDeckSelection(deck));
      saveDraft();
      renderSummary();
      showSubmitError('Algumas candidaturas saíram da lista do TSE desde que você abriu a página e foram desmarcadas. Confira o resumo e envie de novo.');
    } else if (resp.status === 422) {
      showSubmitError(`Não deu pra guardar: ${(json.violations || [json.error]).join('; ')}`);
    } else if (resp.status === 429) {
      showSubmitError('Muitos envios seguidos deste endereço. Espere uns minutos e tente de novo.');
    } else {
      showSubmitError(json.error ? `Não deu pra guardar: ${json.error}` : `Erro ${resp.status} no servidor. Tente de novo em instantes.`);
    }
  } catch {
    showSubmitError('Sem conexão com o servidor. Suas escolhas continuam guardadas neste aparelho — tente de novo quando a internet voltar.');
  } finally {
    if (state.sending) {
      state.sending = false;
      button.disabled = false;
      button.textContent = state.sent ? 'Enviar de novo' : 'Enviar respostas';
      updateBar();
    }
  }
}

async function invite() {
  const data = {
    title: 'datahidro 2026',
    text: 'Levantamento Pedal Hidrográfico de candidatas às eleições de 2026 em São Paulo. Participe:',
    url: `${location.origin}/`,
  };
  if (navigator.share) {
    try {
      await navigator.share(data);
    } catch {
      /* cancelado */
    }
    return;
  }
  try {
    await navigator.clipboard.writeText(data.url);
    toast('Link copiado!');
  } catch {
    toast(data.url, 6000);
  }
}

// ===================== selo "eu participei" =====================
// Quadrado 1080 px: anel com o ciclo inteiro da cmocean.phase (cíclica, então
// emenda sem costura — é a mesma paleta do relevo do Câmera Topográfica), foto
// recortada no círculo interno e o texto em arco. O quadrado todo é pintado:
// plataformas que recortam avatar em círculo mostram exatamente o anel.
// `drawBadgeArt` desenha esse anel em qualquer tamanho — reusado tanto no
// selo quadrado (perfil) quanto, encolhido, dentro do Story do Instagram
// (STORY, 1080×1920): mesma foto, mesmo enquadramento, dois formatos.

const BADGE = { size: 1080, ring: 128, maxZoom: 4 };
const STORY = { width: 1080, height: 1920, badgeSize: 760, badgeCenterY: 860 };
const badge = { img: null, url: null, zoom: 1, x: 0, y: 0, pointers: new Map(), pinch: null, frame: 0 };

const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

async function prepareBadge() {
  try {
    await Promise.all([
      document.fonts.load('700 58px "IBM Plex Mono"'),
      document.fonts.load('600 30px "IBM Plex Mono"'), // eyebrow do Story
      document.fonts.load('400 28px "IBM Plex Mono"'), // totais do Story do placar
    ]);
  } catch {
    /* sem a fonte cai no monospace do sistema */
  }
  drawBadge();
  drawStoryBadge();
  drawBoardStory();
}

function photoDiameter() {
  return BADGE.size - 2 * BADGE.ring;
}

function photoSize() {
  const d = photoDiameter();
  const scale = Math.max(d / badge.img.naturalWidth, d / badge.img.naturalHeight) * badge.zoom;
  return { w: badge.img.naturalWidth * scale, h: badge.img.naturalHeight * scale };
}

function clampOffset() {
  const d = photoDiameter();
  const { w, h } = photoSize();
  badge.x = clamp(badge.x, -(w - d) / 2, (w - d) / 2);
  badge.y = clamp(badge.y, -(h - d) / 2, (h - d) / 2);
}

// Texto centrado em `center` (radianos, canvas: 0 = direita, horário).
// Em cima corre no sentido horário; embaixo, anti-horário — os dois de pé.
function arcText(ctx, text, cx, cy, radius, center, bottom, spacing = 5) {
  const widths = [...text].map((ch) => ctx.measureText(ch).width + spacing);
  const total = widths.reduce((a, b) => a + b, 0) - spacing;
  let advance = 0;
  [...text].forEach((ch, i) => {
    const mid = (advance + (widths[i] - spacing) / 2) / radius;
    const angle = bottom ? center + total / radius / 2 - mid : center - total / radius / 2 + mid;
    ctx.save();
    ctx.translate(cx + radius * Math.cos(angle), cy + radius * Math.sin(angle));
    ctx.rotate(bottom ? angle - Math.PI / 2 : angle + Math.PI / 2);
    ctx.fillText(ch, 0, 0);
    ctx.restore();
    advance += widths[i];
  });
}

function drawBadgeArt(ctx, size) {
  const k = size / BADGE.size; // escala traço, texto e foto a partir do tamanho canônico
  const c = size / 2;
  const ring = BADGE.ring * k;
  const photoRadius = c - ring;

  if (ctx.createConicGradient) {
    const g = ctx.createConicGradient(-Math.PI / 2, c, c);
    PALETTE.forEach((color, i) => g.addColorStop(i / PALETTE.length, color));
    g.addColorStop(1, PALETTE[0]);
    ctx.fillStyle = g;
  } else {
    ctx.fillStyle = PALETTE[4];
  }
  ctx.fillRect(0, 0, size, size);

  ctx.save();
  ctx.beginPath();
  ctx.arc(c, c, photoRadius, 0, Math.PI * 2);
  ctx.clip();
  if (badge.img) {
    const { w, h } = photoSize(); // sempre no espaço canônico (BADGE.size) — escala com k abaixo
    ctx.drawImage(badge.img, c - (w * k) / 2 + badge.x * k, c - (h * k) / 2 + badge.y * k, w * k, h * k);
  } else {
    ctx.fillStyle = '#ebe8df';
    ctx.fillRect(0, 0, size, size);
    ctx.fillStyle = '#cfcbbf';
    ctx.beginPath();
    ctx.arc(c, c - 70 * k, 150 * k, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(c, c + 330 * k, 290 * k, 250 * k, 0, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  ctx.beginPath();
  ctx.arc(c, c, photoRadius, 0, Math.PI * 2);
  ctx.lineWidth = 10 * k;
  ctx.strokeStyle = '#ffffff';
  ctx.stroke();

  const textRadius = photoRadius + ring / 2;
  ctx.font = `700 ${58 * k}px "IBM Plex Mono", ui-monospace, monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = '#ffffff';
  ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
  ctx.shadowBlur = 10 * k;
  arcText(ctx, 'eu participei do', c, c, textRadius, -Math.PI / 2, false, 5 * k);
  arcText(ctx, 'datahidro 2026', c, c, textRadius, Math.PI / 2, true, 5 * k);
  for (const side of [-1, 1]) {
    ctx.beginPath();
    ctx.arc(c + side * textRadius, c, 9 * k, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.shadowColor = 'transparent';
}

function drawBadge() {
  drawBadgeArt($('badge-canvas').getContext('2d'), BADGE.size);
}

// Story do Instagram (1080×1920): o mesmo selo redondo, encolhido, sobre um
// fundo claro FIXO (não segue o tema do aparelho — é uma imagem pra postar
// fora do app, precisa ficar igual pra todo mundo) + um convite a responder.
function drawStoryBadge() {
  const canvas = $('story-canvas');
  const ctx = canvas.getContext('2d');
  const { width: W, height: H, badgeSize, badgeCenterY } = STORY;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#f7f5ef';
  ctx.fillRect(0, 0, W, H);

  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = '#6f6d66';
  ctx.font = '600 30px "IBM Plex Mono", ui-monospace, monospace';
  ctx.fillText('LEVANTAMENTO PEDAL HIDROGRÁFICO', W / 2, 320);

  // o anel é desenhado à parte (mesmo drawBadgeArt do selo quadrado, só que
  // menor) e composto aqui — assim os dois formatos nunca podem divergir.
  const off = document.createElement('canvas');
  off.width = off.height = badgeSize;
  drawBadgeArt(off.getContext('2d'), badgeSize);
  const bx = (W - badgeSize) / 2;
  const by = badgeCenterY - badgeSize / 2;
  ctx.save();
  ctx.shadowColor = 'rgba(0, 0, 0, 0.18)';
  ctx.shadowBlur = 44;
  ctx.shadowOffsetY = 18;
  ctx.drawImage(off, bx, by);
  ctx.restore();

  ctx.fillStyle = '#121210';
  ctx.font = '700 42px "IBM Plex Mono", ui-monospace, monospace';
  ctx.fillText('responda também:', W / 2, by + badgeSize + 96);
  ctx.fillStyle = '#2c6fd6';
  ctx.font = '700 48px "IBM Plex Mono", ui-monospace, monospace';
  ctx.fillText('pesquisa.pedalhidrografi.co', W / 2, by + badgeSize + 158);
}

// Story do placar (1080×1920): o "Quem está na frente" da tela de início —
// totais + top 5 de cada cargo — no mesmo fundo claro fixo do Story do selo,
// pra divulgar o andamento do levantamento. As fotos vêm do próprio domínio
// (photos/), então o canvas continua exportável (toBlob).
const BOARD_STORY = { width: 1080, height: 1920, side: 48, photo: 64, block: 240, areaBottom: 1650 };
const facePhotos = new Map(); // sq → Promise<HTMLImageElement | null>
let boardGeneration = 0;

function loadFacePhoto(c) {
  if (!c.photo) return Promise.resolve(null);
  if (!facePhotos.has(c.sq)) {
    const img = new Image();
    img.src = c.photo;
    facePhotos.set(c.sq, img.decode().then(() => img, () => null));
  }
  return facePhotos.get(c.sq);
}

function ellipsize(ctx, text, maxWidth) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let s = text;
  while (s.length > 1 && ctx.measureText(`${s}…`).width > maxWidth) s = s.slice(0, -1);
  return `${s.trimEnd()}…`;
}

function wrapLines(ctx, text, maxWidth) {
  const lines = [''];
  for (const word of text.split(' ')) {
    const line = lines[lines.length - 1];
    const next = line ? `${line} ${word}` : word;
    if (line && ctx.measureText(next).width > maxWidth) lines.push(word);
    else lines[lines.length - 1] = next;
  }
  return lines;
}

const canvasFont = (weight, px) => `${weight} ${px}px "IBM Plex Mono", ui-monospace, monospace`;

// Miniatura redonda: foto em "cover" (como o .leaderboard-photo) ou iniciais
// sobre a cor da candidatura (como o .no-photo).
function drawFace(ctx, c, img, cx, cy, d) {
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, d / 2, 0, Math.PI * 2);
  ctx.clip();
  if (img) {
    const scale = Math.max(d / img.naturalWidth, d / img.naturalHeight);
    const w = img.naturalWidth * scale;
    const h = img.naturalHeight * scale;
    ctx.drawImage(img, cx - w / 2, cy - h / 2, w, h);
  } else {
    ctx.fillStyle = colorFor(c.sq);
    ctx.fillRect(cx - d / 2, cy - d / 2, d, d);
    ctx.fillStyle = '#ffffff';
    ctx.font = canvasFont(700, Math.round(d * 0.34));
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(initials(c.name), cx, cy);
  }
  ctx.restore();
}

async function drawBoardStory() {
  const generation = ++boardGeneration;
  const boards = tallyAvailable()
    ? state.offices.map((office) => ({ office, top: topCandidates(office.slug) })).filter((b) => b.top.length)
    : [];
  if (!boards.length) {
    $('board-story').hidden = true;
    return;
  }
  const faces = new Map(
    await Promise.all(boards.flatMap((b) => b.top.map(async ({ c }) => [c.sq, await loadFacePhoto(c)])))
  );
  if (generation !== boardGeneration) return; // o placar mudou enquanto as fotos carregavam

  const { width: W, height: H, side, photo: D, block, areaBottom } = BOARD_STORY;
  const ctx = $('board-story-canvas').getContext('2d');
  ctx.fillStyle = '#f7f5ef';
  ctx.fillRect(0, 0, W, H);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';

  ctx.fillStyle = '#6f6d66';
  ctx.font = canvasFont(600, 30);
  ctx.fillText('LEVANTAMENTO PEDAL HIDROGRÁFICO', W / 2, 170);
  ctx.fillStyle = '#121210';
  ctx.font = canvasFont(700, 66);
  ctx.fillText('Quem está na frente', W / 2, 250);
  const rule = ctx.createLinearGradient(W / 2 - 260, 0, W / 2 + 260, 0);
  PALETTE.forEach((color, i) => rule.addColorStop(i / (PALETTE.length - 1), color));
  ctx.fillStyle = rule;
  ctx.fillRect(W / 2 - 260, 276, 520, 8);

  // os totais: a mesma frase da tela de início
  ctx.fillStyle = '#4d4b45';
  ctx.font = canvasFont(400, 28);
  const lines = wrapLines(ctx, votesSummary(), W - 2 * side);
  lines.forEach((line, i) => ctx.fillText(line, W / 2, 336 + i * 38));

  // top 5 por cargo, centrado no espaço entre os totais e o convite
  const cellW = (W - 2 * side) / 5;
  const areaTop = 336 + (lines.length - 1) * 38 + 40;
  let y = areaTop + Math.max(0, (areaBottom - areaTop - boards.length * block) / 2);
  for (const { office, top } of boards) {
    ctx.textAlign = 'left';
    ctx.fillStyle = '#6f6d66';
    ctx.font = canvasFont(600, 24);
    ctx.fillText(office.title.toLocaleUpperCase('pt-BR'), side, y + 24);
    const x0 = W / 2 - (top.length * cellW) / 2;
    top.forEach(({ c, votes }, i) => {
      const cx = x0 + cellW * (i + 0.5);
      const photoTop = y + 44;
      drawFace(ctx, c, faces.get(c.sq), cx, photoTop + D / 2, D);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.fillStyle = '#121210';
      ctx.font = canvasFont(700, 28);
      ctx.fillText(c.number, cx, photoTop + D + 34);
      ctx.fillStyle = '#4d4b45';
      ctx.font = canvasFont(400, 20);
      ctx.fillText(ellipsize(ctx, c.name, cellW - 12), cx, photoTop + D + 60);
      ctx.fillStyle = '#6f6d66';
      ctx.font = canvasFont(600, 18);
      ctx.fillText(ellipsize(ctx, c.party, cellW - 12), cx, photoTop + D + 84);
      ctx.fillStyle = '#121210';
      ctx.font = canvasFont(700, 21);
      ctx.fillText(plural(votes, 'voto', 'votos'), cx, photoTop + D + 110);
    });
    y += block;
  }

  ctx.textAlign = 'center';
  ctx.fillStyle = '#121210';
  ctx.font = canvasFont(700, 40);
  ctx.fillText('responda também:', W / 2, 1722);
  ctx.fillStyle = '#2c6fd6';
  ctx.font = canvasFont(700, 46);
  ctx.fillText('pesquisa.pedalhidrografi.co', W / 2, 1780);

  $('board-story').hidden = false;
  setSaveButtons('board-story-share', 'board-story-download');
}

function scheduleDraw() {
  if (badge.frame) return;
  badge.frame = requestAnimationFrame(() => {
    badge.frame = 0;
    drawBadge();
    drawStoryBadge();
  });
}

async function pickPhoto(file) {
  if (!file) return;
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.src = url;
  try {
    await img.decode();
  } catch {
    URL.revokeObjectURL(url);
    toast('Não deu pra abrir essa imagem. Tente outra (JPG ou PNG).');
    return;
  }
  if (badge.url) URL.revokeObjectURL(badge.url);
  Object.assign(badge, { img, url, zoom: 1, x: 0, y: 0 });
  $('badge-zoom').value = '1';
  $('badge-zoom-label').hidden = false;
  $('badge-hint').hidden = false;
  $('badge-canvas').classList.add('draggable');
  // Salvar direto no álbum de fotos (iOS/Android) só é possível via Web
  // Share: o <a download> do Safari em iOS não tem acesso ao álbum, só aos
  // Arquivos. Onde o navegador suporta, "Salvar no álbum" vira a ação
  // primária; sem suporte, "Baixar arquivo" assume o lugar (única opção).
  setSaveButtons('story-share', 'story-download');
  $('badge-save-hint').hidden = !setSaveButtons('badge-share', 'badge-download');
  await prepareBadge();
}

function setSaveButtons(shareId, downloadId) {
  const canShare = canShareFiles();
  const shareBtn = $(shareId);
  const downloadBtn = $(downloadId);
  shareBtn.hidden = !canShare;
  shareBtn.disabled = false;
  downloadBtn.disabled = false;
  downloadBtn.classList.toggle('btn-primary', !canShare);
  downloadBtn.classList.toggle('btn-secondary', canShare);
  return canShare;
}

function pointerDistance() {
  const [a, b] = [...badge.pointers.values()];
  return Math.hypot(a.x - b.x, a.y - b.y) || 1;
}

function canvasBlob(canvas) {
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));
}

function canShareFiles() {
  try {
    return Boolean(navigator.canShare?.({ files: [new File([new Blob()], 'selo.jpg', { type: 'image/jpeg' })] }));
  } catch {
    return false;
  }
}

async function downloadImage(canvas, filename) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(await canvasBlob(canvas));
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
}

// navigator.share com um único arquivo de imagem: o menu que abre tem
// "Salvar Imagem" (iOS) — vai direto pro álbum de fotos, ao contrário do
// <a download>, que no Safari cai nos Arquivos (motivo de existir esta função).
async function shareImage(canvas, filename, text = 'eu participei do datahidro 2026 · pesquisa.pedalhidrografi.co') {
  const file = new File([await canvasBlob(canvas)], filename, { type: 'image/jpeg' });
  try {
    await navigator.share({ files: [file], title: 'datahidro 2026', text });
  } catch {
    /* cancelado */
  }
}

function bindBadge() {
  const canvas = $('badge-canvas');
  $('badge-file').addEventListener('change', (ev) => {
    pickPhoto(ev.target.files[0]);
    ev.target.value = '';
  });
  $('badge-zoom').addEventListener('input', (ev) => {
    if (!badge.img) return;
    badge.zoom = Number(ev.target.value);
    clampOffset();
    scheduleDraw();
  });
  canvas.addEventListener('pointerdown', (ev) => {
    if (!badge.img) return;
    canvas.setPointerCapture(ev.pointerId);
    badge.pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (badge.pointers.size === 2) badge.pinch = { distance: pointerDistance(), zoom: badge.zoom };
  });
  canvas.addEventListener('pointermove', (ev) => {
    const previous = badge.pointers.get(ev.pointerId);
    if (!previous || !badge.img) return;
    const current = { x: ev.clientX, y: ev.clientY };
    badge.pointers.set(ev.pointerId, current);
    if (badge.pinch && badge.pointers.size >= 2) {
      badge.zoom = clamp((badge.pinch.zoom * pointerDistance()) / badge.pinch.distance, 1, BADGE.maxZoom);
      $('badge-zoom').value = String(badge.zoom);
    } else {
      const scale = BADGE.size / canvas.getBoundingClientRect().width;
      badge.x += (current.x - previous.x) * scale;
      badge.y += (current.y - previous.y) * scale;
    }
    clampOffset();
    scheduleDraw();
  });
  const release = (ev) => {
    badge.pointers.delete(ev.pointerId);
    if (badge.pointers.size < 2) badge.pinch = null;
  };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);
  canvas.addEventListener(
    'wheel',
    (ev) => {
      if (!badge.img) return;
      ev.preventDefault();
      badge.zoom = clamp(badge.zoom * Math.exp(-ev.deltaY / 400), 1, BADGE.maxZoom);
      $('badge-zoom').value = String(badge.zoom);
      clampOffset();
      scheduleDraw();
    },
    { passive: false }
  );
  $('badge-download').addEventListener('click', () => downloadImage($('badge-canvas'), 'selo-datahidro-2026.jpg'));
  $('badge-share').addEventListener('click', () => shareImage($('badge-canvas'), 'selo-datahidro-2026.jpg'));
  $('story-download').addEventListener('click', () => downloadImage($('story-canvas'), 'selo-datahidro-2026-story.jpg'));
  $('story-share').addEventListener('click', () => shareImage($('story-canvas'), 'selo-datahidro-2026-story.jpg'));
  $('board-story-download').addEventListener('click', () =>
    downloadImage($('board-story-canvas'), 'placar-datahidro-2026-story.jpg')
  );
  $('board-story-share').addEventListener('click', () =>
    shareImage($('board-story-canvas'), 'placar-datahidro-2026-story.jpg', 'quem está na frente no datahidro 2026 · pesquisa.pedalhidrografi.co')
  );
}

// ===================== início do app =====================

function bindEvents() {
  $('btn-next').addEventListener('click', goNext);
  $('btn-back').addEventListener('click', goBack);
  $('bottombar-middle').addEventListener('click', (ev) => {
    const mini = ev.target.closest('.mini');
    const deck = state.decks.get(state.currentRoute);
    if (mini && deck) toggleChoice(deck, mini.dataset.sq);
  });
  for (const id of ['bottombar-middle', 'summary', 'intro-leaderboard']) {
    $(id).addEventListener('error', (ev) => ev.target.tagName === 'IMG' && replaceWithInitials(ev.target), true);
  }
  $('submit-form').addEventListener('submit', submitResponse);
  $('consent').addEventListener('change', () => {
    if ($('consent').checked) $('consent').closest('.consent').classList.remove('missing');
    saveDraft();
  });
  $('btn-invite').addEventListener('click', invite);
  bindBadge();
}

async function init() {
  history.scrollRestoration = 'manual';
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});

  const draft = load(STORAGE_KEYS.draft) || {};
  state.responseId = UUID_RE.test(draft.id) ? draft.id : newId();
  state.sent = load(STORAGE_KEYS.sent);
  state.womenOnly = load(STORAGE_KEYS.womenOnly) === true;
  for (const id of ['deck-inicio', 'deck-enviar', 'deck-obrigada']) $(id).querySelector('h1').tabIndex = -1;

  try {
    state.catalog = await loadCatalog();
  } catch {
    $('deck-inicio').hidden = false;
    $('intro-error').textContent = 'Não deu pra carregar a lista de candidatas. Confira a conexão e recarregue a página.';
    $('intro-error').hidden = false;
    $('bottombar').hidden = true;
    return;
  }
  prepareCatalog(draft);
  buildProgress();
  $('consent').checked = draft.consent === true;
  bindEvents();
  window.addEventListener('hashchange', handleRoute);
  handleRoute();
  saveDraft(); // persiste o id do aparelho
  loadTally();
  setInterval(() => {
    if (document.visibilityState === 'visible' && state.tally?.open) loadTally();
  }, 30_000);
}

init();

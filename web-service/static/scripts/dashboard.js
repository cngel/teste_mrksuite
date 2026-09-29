/* === API config ===
   Passo intermédio de diagnóstico: usa sempre o mesmo protocolo/hostname
   pelo qual a página foi carregada, com a porta directa de cada serviço.
   Testando via IP (ex: http://212.47.74.132:5002) evita o bloqueio de
   "mixed content" porque tudo fica em HTTP simples. Testando via
   https://marksuite.ao isto volta a falhar até o nginx ter rotas /api/*
   (ver conversa) — é um passo seguinte, ainda por aplicar aqui. */
const API_BASE = {
    AUTH: "/api/auth",
    CRM: "/api/crm",
    RH: "/api/rh",
    FIN: "/api/finance",
    PROJECTS: "/api/projects",
    DOCS: "/api/documents",
    ACCOUNTING: "/api/accounting",
    STOCK: "/api/stock",
};

/* === Auth guard: exige sessão iniciada para aceder ao dashboard === */
if (!localStorage.getItem('access_token')) {
    window.location.replace('/login');
}

function clearSession() {
    localStorage.removeItem('access_token');
    // O refresh_token já não vive no localStorage — está num cookie HttpOnly
    // gerido pelo auth-service (ver /logout_refresh, que o revoga e apaga).
}

async function logout() {
    const access = localStorage.getItem('access_token');
    try {
        if (access) await fetch(`${API_BASE.AUTH}/logout`, { method: 'POST', headers: { Authorization: `Bearer ${access}` } });
        await fetch(`${API_BASE.AUTH}/logout_refresh`, { method: 'POST', credentials: 'include' });
    } catch (e) {}
    clearSession();
    window.location.replace('/login');
}

document.querySelector('.user-avatar')?.addEventListener('click', async () => {
    if (await UIModal.confirm('Terminar sessão?')) logout();
});

/* === Refresh automático do access token quando expira ===
   Devolve true (renovado), false (sessão mesmo inválida/expirada — só
   nesse caso faz sentido forçar logout) ou null (falha transitória de
   rede/servidor, que não deve por si só deitar a sessão abaixo). */
async function tryRefreshToken() {
    try {
        const res = await fetch(`${API_BASE.AUTH}/refresh`, {
            method: 'POST',
            credentials: 'include', // envia o cookie HttpOnly com o refresh token
        });
        if (res.status === 401 || res.status === 403) return false;
        if (!res.ok) return null;
        const data = await res.json();
        localStorage.setItem('access_token', data.access_token);
        return true;
    } catch (e) {
        return null;
    }
}

/* === Fetch autenticado (anexa o token e faz refresh automático em 401) === */
async function apiFetch(url, options = {}) {
    const token = localStorage.getItem('access_token');
    const headers = { ...(options.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    let res = await fetch(url, { ...options, headers });

    if (res.status === 401) {
        const refreshed = await tryRefreshToken();
        if (refreshed === false) {
            clearSession();
            window.location.replace('/login');
            throw new Error('Sessão expirada');
        }
        if (refreshed === null) {
            // Falha transitória (rede/servidor) a renovar o token — não é
            // prova de sessão inválida, por isso não força logout: devolve
            // a resposta 401 original e deixa o chamador lidar com ela.
            return res;
        }
        headers['Authorization'] = `Bearer ${localStorage.getItem('access_token')}`;
        res = await fetch(url, { ...options, headers });
    }

    return res;
}

/* Renova o access token pouco antes de expirar (TTL de 15min no
   auth-service — ver auth-service/app.py), para o utilizador nunca sentir
   o token a "morrer" a meio do uso normal. O refresh reativo em apiFetch
   acima fica como rede de segurança para o intervalo entre renovações. */
setInterval(() => {
    if (localStorage.getItem('access_token')) tryRefreshToken();
}, 10 * 60 * 1000);

/* ============================================================
   UIMODAL — substitui confirm()/alert()/prompt() nativos do browser
   por um modal consistente com o resto da aplicação. Usa a marcação
   genérica #uiModal (dashboard.html) e reaproveita as classes
   .modal / .modal-card / .form-input já existentes.
   ============================================================ */
const UIModal = (() => {
    const modalEl = () => document.getElementById('uiModal');
    const titleEl = () => document.getElementById('uiModalTitle');
    const msgEl = () => document.getElementById('uiModalMessage');
    const promptFieldEl = () => document.getElementById('uiModalPromptField');
    const promptInputEl = () => document.getElementById('uiModalPromptInput');
    const cancelBtnEl = () => document.getElementById('uiModalCancelBtn');
    const okBtnEl = () => document.getElementById('uiModalOkBtn');

    let activeResolve = null;
    let activeMode = 'alert'; // 'alert' | 'confirm' | 'prompt'

    function openEl() {
        modalEl()?.classList.add('is-open');
        modalEl()?.setAttribute('aria-hidden', 'false');
    }
    function closeEl() {
        modalEl()?.classList.remove('is-open');
        modalEl()?.setAttribute('aria-hidden', 'true');
    }

    function settle(value) {
        if (!activeResolve) return;
        closeEl();
        const resolve = activeResolve;
        activeResolve = null;
        resolve(value);
    }

    function handleDismiss() {
        // fechar via backdrop/Escape conta como "cancelar"
        if (activeMode === 'confirm') settle(false);
        else if (activeMode === 'prompt') settle(null);
        else settle(undefined);
    }

    function wire() {
        cancelBtnEl()?.addEventListener('click', handleDismiss);
        document.getElementById('uiModalBackdrop')?.addEventListener('click', handleDismiss);
        okBtnEl()?.addEventListener('click', () => {
            if (activeMode === 'confirm') settle(true);
            else if (activeMode === 'prompt') settle(promptInputEl().value);
            else settle(undefined);
        });
        promptInputEl()?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); okBtnEl()?.click(); }
        });
        document.addEventListener('keydown', (e) => {
            if (!modalEl()?.classList.contains('is-open')) return;
            if (e.key === 'Escape') handleDismiss();
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wire);
    } else {
        wire();
    }

    function show({ title, message, mode, okLabel, cancelLabel, danger, defaultValue }) {
        return new Promise((resolve) => {
            activeResolve = resolve;
            activeMode = mode;

            titleEl().textContent = title;
            msgEl().textContent = message;

            cancelBtnEl().hidden = mode === 'alert';
            okBtnEl().textContent = okLabel || (mode === 'confirm' ? 'Confirmar' : 'OK');
            cancelBtnEl().textContent = cancelLabel || 'Cancelar';
            okBtnEl().classList.toggle('btn-danger-solid', !!danger);

            const showPrompt = mode === 'prompt';
            promptFieldEl().hidden = !showPrompt;
            if (showPrompt) {
                promptInputEl().value = defaultValue || '';
                setTimeout(() => promptInputEl()?.focus(), 60);
            }

            openEl();
        });
    }

    /** Substitui o confirm() nativo. Resolve para true/false. opts: { title, okLabel, cancelLabel, danger } */
    function confirmModal(message, opts = {}) {
        return show({
            title: opts.title || 'Confirmar acção',
            message,
            mode: 'confirm',
            okLabel: opts.okLabel,
            cancelLabel: opts.cancelLabel,
            danger: opts.danger,
        });
    }

    /** Substitui o alert() nativo. Resolve quando o utilizador fecha o modal. opts: { title, okLabel } */
    function alertModal(message, opts = {}) {
        return show({
            title: opts.title || 'Aviso',
            message,
            mode: 'alert',
            okLabel: opts.okLabel || 'OK',
        });
    }

    /** Substitui o prompt() nativo. Resolve para string, ou null se cancelado. */
    function promptModal(message, defaultValue = '', opts = {}) {
        return show({
            title: opts.title || 'Introduzir valor',
            message,
            mode: 'prompt',
            defaultValue,
            okLabel: opts.okLabel || 'OK',
        });
    }

    return { confirm: confirmModal, alert: alertModal, prompt: promptModal };
})();

/* ============================================================
   UITOAST — confirmações de sucesso/erro não-bloqueantes no canto
   do ecrã (ex: "Funcionário criado com sucesso"). Não interrompe
   o fluxo do utilizador como um alert()/modal exigiria clique.
   ============================================================ */
const UIToast = (() => {
    const ICONS = {
        success: '<path d="M3 8.5 L6.5 12 L13 4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
        error: '<path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
        info: '<path d="M8 7 V11.5 M8 4.6 V4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="8" r="6.3" stroke="currentColor" stroke-width="1.3" fill="none"/>',
    };

    function host() {
        return document.getElementById('uiToastHost');
    }

    function show(message, type = 'success', duration = 4000) {
        const h = host();
        if (!h) return;

        const el = document.createElement('div');
        el.className = `ui-toast ui-toast--${type}`;
        el.setAttribute('role', 'status');
        el.innerHTML = `
            <svg class="ui-toast-icon" viewBox="0 0 16 16" aria-hidden="true">${ICONS[type] || ICONS.success}</svg>
            <span class="ui-toast-msg"></span>
            <svg class="ui-toast-close" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        `;
        el.querySelector('.ui-toast-msg').textContent = message;

        let dismissed = false;
        const dismiss = () => {
            if (dismissed) return;
            dismissed = true;
            el.classList.add('is-leaving');
            el.addEventListener('animationend', () => el.remove(), { once: true });
        };

        el.querySelector('.ui-toast-close').addEventListener('click', dismiss);
        h.appendChild(el);
        setTimeout(dismiss, duration);
    }

    return {
        success: (message, duration) => show(message, 'success', duration),
        error: (message, duration) => show(message, 'error', duration),
        info: (message, duration) => show(message, 'info', duration),
    };
})();

/* ============================================================
   THEME TOGGLE — modo claro/escuro, persistido em localStorage.
   Sem preferência guardada, a aparência é sempre clara — o modo escuro
   é sempre uma escolha explícita do utilizador (não segue prefers-color-
   scheme do sistema). O <head> de cada página já aplica a preferência
   guardada antes do primeiro paint (script inline anti-flash).
   ============================================================ */
const ThemeToggle = (() => {
    const STORAGE_KEY = 'mksTheme';

    function apply(theme) {
        if (theme === 'dark' || theme === 'light') {
            document.documentElement.setAttribute('data-theme', theme);
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
    }

    function current() {
        return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    }

    function toggle() {
        const next = current() === 'dark' ? 'light' : 'dark';
        apply(next);
        try { localStorage.setItem(STORAGE_KEY, next); } catch (e) {}
    }

    function init() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            apply(saved);
        } catch (e) {}
        document.getElementById('themeToggleBtn')?.addEventListener('click', toggle);
    }

    return { init, toggle };
})();

document.addEventListener('DOMContentLoaded', ThemeToggle.init);

/* === Dashboard: toggle do sidebar em mobile === */
const dash = document.querySelector('.dash');
const sidebarToggle = document.getElementById('sidebarToggle');
const dashOverlay = document.getElementById('dashOverlay');

if (dash && sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
        dash.classList.toggle('is-sidebar-open');
    });
}
if (dashOverlay) {
    dashOverlay.addEventListener('click', () => {
        dash.classList.remove('is-sidebar-open');
    });
}

/* === Dashboard: contadores das stat tiles === */
    /* === Router de views do dashboard === */
const navItems = document.querySelectorAll('[data-target]');
const views = document.querySelectorAll('.view[data-view]');
const STORAGE_KEY = 'mksActiveView';

function runCountersInScope(scope) {
    scope.querySelectorAll('[data-count-to]').forEach(el => {
        if (el.dataset.counted === 'true') return;
        const target = parseFloat(el.dataset.countTo);
        if (isNaN(target)) return;

        const start = performance.now();
        const dur = 1400;
        const tick = (now) => {
            const p = Math.min((now - start) / dur, 1);
            const eased = 1 - Math.pow(1 - p, 3);
            const value = target % 1 === 0
                ? Math.round(target * eased)
                : (target * eased).toFixed(1);
            el.textContent = value;
            if (p < 1) requestAnimationFrame(tick);
            else el.textContent = target % 1 === 0 ? target : target.toFixed(1);
        };
        requestAnimationFrame(tick);
        el.dataset.counted = 'true';
    });
}

function activateView(target) {
    const view = document.querySelector(`.view[data-view="${target}"]`);
    if (!view) return;

    document.querySelectorAll('.nav-item[data-target]').forEach(item => {
        item.classList.toggle('is-active', item.dataset.target === target);
    });

    views.forEach(v => v.classList.toggle('is-active', v === view));

    history.replaceState(null, '', `#${target}`);
    try { sessionStorage.setItem(STORAGE_KEY, target); } catch (e) {}

    runCountersInScope(view);
    runProgressBarsInScope(view);

    document.querySelector('.dash')?.classList.remove('is-sidebar-open');
    document.querySelector('.dash-main')?.scrollTo({ top: 0, behavior: 'smooth' });
}

function runProgressBarsInScope(scope) {
    scope.querySelectorAll('.bar-fill[data-progress]').forEach((bar, i) => {
        if (bar.dataset.animated === 'true') return;
        const pct = parseFloat(bar.dataset.progress) || 0;
        const clamped = Math.min(100, Math.max(0, pct));
        bar.style.setProperty('--p-static', clamped + '%');
        setTimeout(() => {
            bar.style.width = clamped + '%';
        }, 250 + i * 100);
        bar.dataset.animated = 'true';
    });
}

navItems.forEach(item => {
    item.addEventListener('click', (e) => {
        const target = item.dataset.target;
        if (!target) return;
        e.preventDefault();
        activateView(target);
    });
});

window.addEventListener('hashchange', () => {
    const target = window.location.hash.slice(1) || 'home';
    activateView(target);
});

const dashRoot = document.querySelector('.dash');
if (dashRoot && views.length) {
    let initial = window.location.hash.slice(1);
    if (!initial) {
        try { initial = sessionStorage.getItem(STORAGE_KEY); } catch (e) {}
    }
    if (initial && document.querySelector(`.view[data-view="${initial}"]`)) {
        activateView(initial);
    } else {
        const activeView = document.querySelector('.view.is-active') || document;
        runCountersInScope(activeView);
        runProgressBarsInScope(activeView);
    }
}

/* === Kanban: drag & drop entre colunas === */
let draggedCard = null;

document.addEventListener('dragstart', (e) => {
    const card = e.target.closest?.('.lead-card');
    if (!card) return;
    draggedCard = card;
    card.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', ''); } catch (err) {}
});

document.addEventListener('dragend', () => {
    if (draggedCard) draggedCard.classList.remove('is-dragging');
    draggedCard = null;
    document.querySelectorAll('.kanban-list.is-drop-target')
        .forEach(l => l.classList.remove('is-drop-target'));
});

document.querySelectorAll('[data-drop]').forEach(list => {
    list.addEventListener('dragover', (e) => {
        if (!draggedCard) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        list.classList.add('is-drop-target');
    });

    list.addEventListener('dragleave', (e) => {
        if (!list.contains(e.relatedTarget)) {
            list.classList.remove('is-drop-target');
        }
    });

    list.addEventListener('drop', (e) => {
        e.preventDefault();
        if (!draggedCard) return;
        const origin = draggedCard.closest('.kanban-list');
        list.appendChild(draggedCard);
        list.classList.remove('is-drop-target');
        bumpCount(list);
        if (origin && origin !== list) bumpCount(origin);
    });
});

function bumpCount(list) {
    const col = list.closest('.kanban-col');
    if (!col) return;
    const badge = col.querySelector('.col-count');
    if (!badge) return;
    const n = list.querySelectorAll('.lead-card').length;
    badge.textContent = n;
    badge.classList.remove('is-bumped');
    void badge.offsetWidth;
    badge.classList.add('is-bumped');
}


/* === Dashboard: segmented control (6M / 1A / ALL) === */
document.querySelectorAll('.seg-control').forEach(group => {
    group.addEventListener('click', (e) => {
        const btn = e.target.closest('.seg-btn');
        if (!btn) return;
        group.querySelectorAll('.seg-btn').forEach(b => {
            b.classList.remove('is-active');
            b.setAttribute('aria-selected', 'false');
        });
        btn.classList.add('is-active');
        btn.setAttribute('aria-selected', 'true');
    });
});

/* === Dashboard: atalho ⌘K / Ctrl+K focar pesquisa === */
const searchInput = document.querySelector('.search input');
if (searchInput) {
    document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            searchInput.focus();
            searchInput.select();
        }
    });
}


/* === Kora AI: agente real (erp-agent) — chat + tarefas agendadas ===
   O erp-agent corre como serviço próprio no docker-compose (ver
   erp-agent/Dockerfile) e é alcançado através do mesmo proxy /api/{serviço}/...
   que os restantes módulos usam (ver web-service/app.py), em vez de um URL
   fixo — assim funciona a partir de qualquer máquina, não só localhost.
   Sem ecrã de login próprio: sincroniza automaticamente com a sessão já
   autenticada no dashboard, reaproveitando o mesmo Bearer token — a Kora
   cria a sua sessão (ver erp-agent/app/web.py) na primeira chamada. */
const KORA_AGENT_BASE = "/api/kora";

const koraConnectError = document.getElementById('koraConnectError');
const koraLayout = document.getElementById('koraLayout');
const koraComposer = document.getElementById('koraComposer');
const koraInput = document.getElementById('koraInput');
const koraThread = document.getElementById('koraThread');
const koraPrompts = document.getElementById('koraPrompts');
const koraTasksList = document.getElementById('koraTasksList');
const koraTaskModal = document.getElementById('koraTaskModal');
const koraTaskForm = document.getElementById('koraTaskForm');

let koraConnected = false;
let koraTasksCache = [];

const KORA_MODULE_LABELS = { crm: 'CRM', books: 'Books', projects: 'Projects', desk: 'Desk', people: 'People', accounting: 'Contabilidade', stock: 'Stock', creator: 'Creator', kora: 'Kora' };
const KORA_WEEKDAYS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];
const KORA_STATUS_LABEL = { ativa: 'Activa', pausada: 'Pausada', cancelada: 'Cancelada' };
const KORA_STATUS_CLASS = { ativa: 'status-pill--online', pausada: 'status-pill--meeting', cancelada: 'status-pill--off' };

function koraEscapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function koraFetch(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    const withToken = () => {
        const token = localStorage.getItem('access_token');
        if (token) headers['Authorization'] = `Bearer ${token}`;
    };
    withToken();

    let res = await fetch(`${KORA_AGENT_BASE}${path}`, { ...options, headers });

    if (res.status === 401) {
        // A Kora sincroniza pelo mesmo Bearer token do dashboard (ver
        // erp-agent/app/web.py) — se expirou, renova-o tal como o apiFetch
        // faz para os outros módulos, em vez de deixar a conversa morrer.
        const refreshed = await tryRefreshToken();
        if (refreshed) {
            withToken();
            res = await fetch(`${KORA_AGENT_BASE}${path}`, { ...options, headers });
        }
    }

    return res;
}

/* ---------- Sincronização automática ---------- */
async function koraConnect() {
    if (koraConnectError) koraConnectError.textContent = '';
    try {
        const res = await koraFetch('/api/session');
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            if (koraConnectError) koraConnectError.textContent = data.detail || 'Não foi possível ligar à Kora.';
            return;
        }
        koraConnected = true;
        koraLoadTasks();
    } catch (err) {
        if (koraConnectError) koraConnectError.textContent = 'Não foi possível contactar a Kora. Verifica se o serviço está a correr.';
    }
}
koraConnect();

/* ---------- Chat ---------- */

/* Conversor minimalista de Markdown -> HTML seguro (sem dependências
   externas): negrito, itálico, código inline/bloco, links, títulos,
   listas e parágrafos. Tudo passa primeiro por koraEscapeHtml, por isso
   o resultado nunca injeta HTML vindo da resposta do modelo. */
function koraRenderMarkdown(raw) {
    const text = String(raw ?? '');
    if (!text.trim()) return '';

    const codeBlocks = [];
    const withPlaceholders = text.replace(/```[^\n]*\n?([\s\S]*?)```/g, (_, code) => {
        codeBlocks.push(code.replace(/\n+$/, ''));
        return ` CODEBLOCK${codeBlocks.length - 1} `;
    });

    const escapeInline = (s) => koraEscapeHtml(s)
        .replace(/`([^`\n]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>')
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

    const htmlParts = [];
    let listBuffer = [];
    let listType = null;
    let paraBuffer = [];

    const flushList = () => {
        if (!listBuffer.length) return;
        htmlParts.push(`<${listType}>${listBuffer.map(li => `<li>${escapeInline(li)}</li>`).join('')}</${listType}>`);
        listBuffer = [];
        listType = null;
    };
    const flushPara = () => {
        if (!paraBuffer.length) return;
        htmlParts.push(`<p>${paraBuffer.map(escapeInline).join('<br>')}</p>`);
        paraBuffer = [];
    };

    for (const rawLine of withPlaceholders.split('\n')) {
        const line = rawLine.trim();
        const codePlaceholder = line.match(/^ CODEBLOCK(\d+) $/);
        const heading = line.match(/^(#{1,4})\s+(.*)$/);
        const ul = line.match(/^[-*]\s+(.*)$/);
        const ol = line.match(/^\d+\.\s+(.*)$/);

        if (codePlaceholder) {
            flushPara(); flushList();
            htmlParts.push(`<pre class="kora-code"><code>${koraEscapeHtml(codeBlocks[Number(codePlaceholder[1])])}</code></pre>`);
        } else if (heading) {
            flushPara(); flushList();
            const tag = `h${Math.min(heading[1].length + 3, 6)}`;
            htmlParts.push(`<${tag}>${escapeInline(heading[2])}</${tag}>`);
        } else if (ul) {
            flushPara();
            if (listType && listType !== 'ul') flushList();
            listType = 'ul';
            listBuffer.push(ul[1]);
        } else if (ol) {
            flushPara();
            if (listType && listType !== 'ol') flushList();
            listType = 'ol';
            listBuffer.push(ol[1]);
        } else if (line === '') {
            flushPara(); flushList();
        } else {
            flushList();
            paraBuffer.push(line);
        }
    }
    flushPara();
    flushList();
    return htmlParts.join('');
}

function koraAppendMessage(text, role) {
    const msg = document.createElement('div');
    msg.className = `msg msg--${role}`;
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    if (role === 'bot') {
        bubble.innerHTML = koraRenderMarkdown(text);
    } else {
        const p = document.createElement('p');
        p.textContent = text;
        bubble.appendChild(p);
    }
    msg.appendChild(bubble);
    koraThread.appendChild(msg);
    koraThread.scrollTop = koraThread.scrollHeight;
    return msg;
}

/* Mensagens da Kora aparecem com efeito de "a escrever" — revela o texto
   progressivamente, sempre reinterpretado como Markdown, em vez de surgir
   tudo de uma vez. Duração proporcional ao tamanho da resposta, com um
   teto para não fazer o utilizador esperar em respostas longas. */
function koraTypeMessage(text, role) {
    const raw = String(text ?? '');
    const msg = document.createElement('div');
    msg.className = `msg msg--${role}`;
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    msg.appendChild(bubble);
    koraThread.appendChild(msg);
    koraThread.scrollTop = koraThread.scrollHeight;

    const total = raw.length;
    if (total === 0) return msg;

    const durationMs = Math.min(1400, Math.max(350, total * 12));
    const start = performance.now();

    function tick(now) {
        const progress = Math.min(1, (now - start) / durationMs);
        const shown = Math.max(1, Math.round(total * progress));
        bubble.innerHTML = koraRenderMarkdown(raw.slice(0, shown));
        koraThread.scrollTop = koraThread.scrollHeight;
        if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
    return msg;
}

function koraShowTyping() {
    const msg = document.createElement('div');
    msg.className = 'msg msg--bot msg--typing';
    msg.id = 'koraTyping';
    msg.innerHTML = '<div class="msg-bubble"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>';
    koraThread.appendChild(msg);
    koraThread.scrollTop = koraThread.scrollHeight;
}

function koraHideTyping() {
    document.getElementById('koraTyping')?.remove();
}

function koraAppendPending(id, description) {
    const div = document.createElement('div');
    div.className = 'kora-pending';
    div.id = `koraPending-${id}`;
    div.innerHTML = `
        <p>⏳ ${koraEscapeHtml(description)}</p>
        <div class="kora-pending-actions">
            <button type="button" class="btn-kora" data-pending-approve="${id}">Confirmar</button>
            <button type="button" class="btn-soft" data-pending-reject="${id}">Rejeitar</button>
        </div>`;
    koraThread.appendChild(div);
    koraThread.scrollTop = koraThread.scrollHeight;
}

koraThread?.addEventListener('click', async (e) => {
    const approveBtn = e.target.closest('[data-pending-approve]');
    const rejectBtn = e.target.closest('[data-pending-reject]');
    const btn = approveBtn || rejectBtn;
    if (!btn) return;
    const id = btn.dataset.pendingApprove || btn.dataset.pendingReject;
    try {
        const res = await koraFetch(`/api/confirm/${id}`, { method: 'POST', body: JSON.stringify({ approve: !!approveBtn }) });
        const data = await res.json();
        document.getElementById(`koraPending-${id}`)?.remove();
        if (data.status === 'confirmada') koraTypeMessage('Ação executada com sucesso.', 'bot');
        else if (data.status === 'rejeitada') koraTypeMessage('Ação rejeitada.', 'bot');
        else koraTypeMessage(data.mensagem || 'Não foi possível concluir a acção.', 'bot');
        koraLoadTasks();
    } catch (err) {
        koraTypeMessage('Não foi possível processar a acção.', 'bot');
    }
});

async function koraSend(text) {
    const clean = text.trim();
    if (!clean || !koraConnected) return;
    koraAppendMessage(clean, 'user');
    koraInput.value = '';
    koraPrompts?.classList.add('is-hidden');
    koraShowTyping();
    try {
        const res = await koraFetch('/api/chat', { method: 'POST', body: JSON.stringify({ message: clean }) });
        koraHideTyping();
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            koraTypeMessage(data.detail || 'Ocorreu um erro ao falar com a Kora.', 'bot');
            return;
        }
        const data = await res.json();
        koraTypeMessage(data.reply, 'bot');
        (data.pending || []).forEach(p => koraAppendPending(p.id, p.description));
        koraLoadTasks();
    } catch (err) {
        koraHideTyping();
        koraTypeMessage('Não foi possível contactar a Kora.', 'bot');
    }
}

if (koraComposer) {
    koraComposer.addEventListener('submit', (e) => {
        e.preventDefault();
        koraSend(koraInput.value);
    });
}

if (koraPrompts) {
    koraPrompts.addEventListener('click', (e) => {
        const chip = e.target.closest('.prompt-chip');
        if (!chip) return;
        koraSend(chip.dataset.prompt || chip.textContent);
    });
}

/* ---------- Tarefas agendadas ---------- */
function koraScheduleSummary(t) {
    if (t.frequency === 'diaria') return `Diária às ${t.time_of_day}`;
    if (t.frequency === 'semanal') return `Semanal à ${KORA_WEEKDAYS[t.weekday] ?? '—'} às ${t.time_of_day}`;
    if (t.frequency === 'unica') return `Em ${t.run_date} às ${t.time_of_day}`;
    return t.time_of_day || '—';
}

async function koraLoadTasks() {
    if (!koraTasksList) return;
    try {
        const res = await koraFetch('/api/tasks?include_cancelled=true');
        koraTasksCache = res.ok ? await res.json() : [];
    } catch (e) {
        koraTasksCache = [];
    }
    koraRenderTasks();
}

function koraRenderTasks() {
    if (!koraTasksList) return;
    if (!koraTasksCache.length) {
        koraTasksList.innerHTML = '<p class="hr-empty-state">Sem tarefas agendadas. Cria uma com o botão "+" ou pede à Kora no chat.</p>';
        return;
    }
    koraTasksList.innerHTML = koraTasksCache.map(t => `
        <article class="kora-task-card" data-task-id="${t.id}">
            <div class="kora-task-head">
                <span class="badge badge--empresa">${koraEscapeHtml(KORA_MODULE_LABELS[t.module] || t.module)}</span>
                <span class="status-pill ${KORA_STATUS_CLASS[t.status] || ''}">${koraEscapeHtml(KORA_STATUS_LABEL[t.status] || t.status)}</span>
            </div>
            <h3 class="kora-task-title">${koraEscapeHtml(t.title)}</h3>
            <p class="kora-task-schedule">${koraEscapeHtml(koraScheduleSummary(t))}</p>
            <div class="kora-task-actions">
                <button type="button" class="btn-link-sm" data-task-edit="${t.id}">Editar</button>
                ${t.status === 'cancelada' ? '' : (t.status === 'pausada'
                    ? `<button type="button" class="btn-link-sm" data-task-resume="${t.id}">Retomar</button>`
                    : `<button type="button" class="btn-link-sm" data-task-pause="${t.id}">Pausar</button>`)}
                ${t.status === 'cancelada' ? '' : `<button type="button" class="btn-link-sm" data-task-cancel="${t.id}">Cancelar</button>`}
                <button type="button" class="btn-link-sm" data-task-delete="${t.id}" style="color:#ef4444;">Apagar</button>
            </div>
        </article>
    `).join('');
}

koraTasksList?.addEventListener('click', async (e) => {
    const editBtn = e.target.closest('[data-task-edit]');
    if (editBtn) { openKoraTaskModal(Number(editBtn.dataset.taskEdit)); return; }

    const pauseBtn = e.target.closest('[data-task-pause]');
    if (pauseBtn) {
        await koraFetch(`/api/tasks/${pauseBtn.dataset.taskPause}/pause`, { method: 'POST' });
        UIToast.success('Tarefa pausada.');
        koraLoadTasks();
        return;
    }

    const resumeBtn = e.target.closest('[data-task-resume]');
    if (resumeBtn) {
        await koraFetch(`/api/tasks/${resumeBtn.dataset.taskResume}/resume`, { method: 'POST' });
        UIToast.success('Tarefa retomada.');
        koraLoadTasks();
        return;
    }

    const cancelBtn = e.target.closest('[data-task-cancel]');
    if (cancelBtn) {
        if (!await UIModal.confirm('Cancelar esta tarefa?')) return;
        await koraFetch(`/api/tasks/${cancelBtn.dataset.taskCancel}/cancel`, { method: 'POST' });
        UIToast.success('Tarefa cancelada.');
        koraLoadTasks();
        return;
    }

    const deleteBtn = e.target.closest('[data-task-delete]');
    if (deleteBtn) {
        if (!await UIModal.confirm('Apagar esta tarefa definitivamente?', { danger: true, okLabel: 'Apagar' })) return;
        await koraFetch(`/api/tasks/${deleteBtn.dataset.taskDelete}`, { method: 'DELETE' });
        UIToast.success('Tarefa apagada.');
        koraLoadTasks();
    }
});

/* ---------- Modal de criar/editar tarefa ---------- */
function koraUpdateTaskFieldVisibility() {
    const freq = document.getElementById('koraTaskFrequency').value;
    document.getElementById('koraTaskWeekdayField').hidden = freq !== 'semanal';
    document.getElementById('koraTaskDateField').hidden = freq !== 'unica';
}

document.getElementById('koraTaskFrequency')?.addEventListener('change', koraUpdateTaskFieldVisibility);
document.getElementById('koraTaskAddBtn')?.addEventListener('click', () => openKoraTaskModal(null));
koraTaskModal?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => {
    koraTaskModal.classList.remove('is-open');
    koraTaskModal.setAttribute('aria-hidden', 'true');
}));

function openKoraTaskModal(id) {
    koraTaskForm.reset();
    document.getElementById('koraTaskId').value = '';
    document.getElementById('koraTaskModule').disabled = false;
    document.getElementById('koraTaskModalTitle').textContent = 'Nova tarefa';

    if (id) {
        const t = koraTasksCache.find(x => x.id === id);
        if (t) {
            document.getElementById('koraTaskId').value = t.id;
            document.getElementById('koraTaskModule').value = t.module;
            document.getElementById('koraTaskModule').disabled = true;
            document.getElementById('koraTaskTitle').value = t.title;
            document.getElementById('koraTaskFrequency').value = t.frequency;
            document.getElementById('koraTaskTime').value = t.time_of_day;
            if (t.weekday !== null && t.weekday !== undefined) document.getElementById('koraTaskWeekday').value = String(t.weekday);
            if (t.run_date) document.getElementById('koraTaskDate').value = t.run_date;
            document.getElementById('koraTaskModalTitle').textContent = 'Editar tarefa';
        }
    }
    koraUpdateTaskFieldVisibility();
    koraTaskModal.classList.add('is-open');
    koraTaskModal.setAttribute('aria-hidden', 'false');
}

koraTaskForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!koraTaskForm.checkValidity()) { koraTaskForm.reportValidity(); return; }
    const id = document.getElementById('koraTaskId').value;
    const frequency = document.getElementById('koraTaskFrequency').value;
    const body = {
        title: document.getElementById('koraTaskTitle').value.trim(),
        frequency,
        time_of_day: document.getElementById('koraTaskTime').value,
        weekday: frequency === 'semanal' ? Number(document.getElementById('koraTaskWeekday').value) : null,
        run_date: frequency === 'unica' ? document.getElementById('koraTaskDate').value : null,
    };
    if (!id) body.module = document.getElementById('koraTaskModule').value;

    try {
        const res = await koraFetch(id ? `/api/tasks/${id}` : '/api/tasks', {
            method: id ? 'PUT' : 'POST',
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            await UIModal.alert(data.detail || 'Não foi possível guardar a tarefa.');
            return;
        }
    } catch (err) {
        await UIModal.alert('Não foi possível guardar a tarefa.');
        return;
    }
    koraTaskModal.classList.remove('is-open');
    koraTaskModal.setAttribute('aria-hidden', 'true');
    UIToast.success(id ? 'Tarefa actualizada com sucesso!' : 'Tarefa criada com sucesso!');
    koraLoadTasks();
});

/* ============================================
   CRM MODULE — tabs + table + reports + modal
   ============================================ */

const CRM = (() => {
    const state = { leads: [] };
   const STAGE_LABEL = {
    novo: 'Novo',
    qualificado: 'Qualificado',
    proposta: 'Proposta',
    fechado: 'Fechado'
};
const CHANNEL_LABEL = {
    whatsapp: 'WhatsApp',
    facebook: 'Facebook',
    instagram: 'Instagram',
    linkedin: 'LinkedIn',
    email: 'Email',
    outros: 'Outros'
};
const CHANNEL_COLOR = {
    whatsapp:  '#10B981',
    facebook:  '#3B82F6',
    instagram: '#EC4899',
    linkedin:  '#4F46E5',
    email:     '#6B7280',
    outros:    '#F59E0B'
};
const CHANNEL_ICON = {
    whatsapp:  '<svg viewBox="0 0 16 16"><path d="M8 1.5 a6.5 6.5 0 0 0 -5.5 9.9 L1.5 14.5 L4.7 13.5 A6.5 6.5 0 1 0 8 1.5 Z" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linejoin="round"/></svg>',
    facebook:  '<svg viewBox="0 0 16 16"><path d="M9 14 V8.5 H10.7 L11 6.5 H9 V5.3 c0 -0.6 0.2 -1 1 -1 H11 V2.5 a14 14 0 0 0 -1.6 -0.1 c-1.6 0 -2.6 1 -2.6 2.7 V6.5 H5 V8.5 H6.8 V14 Z" fill="currentColor"/></svg>',
    instagram: '<svg viewBox="0 0 16 16"><rect x="2" y="2" width="12" height="12" rx="3.5" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="8" cy="8" r="2.6" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="11.5" cy="4.5" r="0.7" fill="currentColor"/></svg>',
    linkedin:  '<svg viewBox="0 0 16 16"><rect x="2" y="2" width="12" height="12" rx="2" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="5" cy="6" r="0.9" fill="currentColor"/><path d="M4.3 8 H5.7 V12 H4.3 z M7.3 8 H8.6 V8.7 C8.9 8.2 9.4 7.9 10 7.9 c1 0 1.7 0.6 1.7 1.9 V12 H10.3 V10.1 c0 -0.6 -0.2 -1 -0.7 -1 c-0.5 0 -0.8 0.4 -0.8 1 V12 H7.3 z" fill="currentColor"/></svg>',
    email:     '<svg viewBox="0 0 16 16"><rect x="2" y="3.5" width="12" height="9" rx="1.2" stroke="currentColor" stroke-width="1.3" fill="none"/><path d="M2.5 4.5 L8 9 L13.5 4.5" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/></svg>',
    outros:    '<svg viewBox="0 0 16 16"><circle cx="4" cy="8" r="1.2" fill="currentColor"/><circle cx="8" cy="8" r="1.2" fill="currentColor"/><circle cx="12" cy="8" r="1.2" fill="currentColor"/></svg>'
};

   function mapContact(c) {
    return {
        id: c.id,
        name: c.name,
        contact: c.phone || '',
        email: c.email || '',
        company: c.company || '',
        notes: c.notes || '',
        service: c.service_type || '',
        value: Number(c.pipeline_value) || 0,
        date: c.lead_date || todayIso(),
        channel: c.channel || 'outros',
        owner: c.owner || '',
        stage: c.stage || 'novo'
    };
}

async function loadLeads() {
    try {
        const res = await apiFetch(`${API_BASE.CRM}/contacts`);
        if (!res.ok) throw new Error('Falha ao carregar leads');
        const contacts = await res.json();
        state.leads = contacts.map(mapContact);
    } catch (e) {
        state.leads = [];
    }
    renderKanban();
    refreshStats();
    renderTable();
    renderReports();
}

function todayIso() {
    const d = new Date();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${d.getFullYear()}-${m}-${day}`;
}

function fmtDate(iso) {
    if (!iso) return '—';
    const [y, m, d] = iso.split('-');
    return `${d}/${m}/${y}`;
}

function initialsFor(name) {
    return (name || '').split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() || '').join('') || '?';
}
function ownerTone(name) {
    const tones = ['avatar--blue','avatar--teal','avatar--purple'];
    let h = 0;
    for (let i = 0; i < (name || '').length; i++) h = (h + name.charCodeAt(i)) % 999;
    return tones[h % tones.length];
}

    function fmtKz(n) {
        if (n >= 1000000) return 'Kz ' + (n / 1000000).toFixed(1).replace('.0', '') + 'M';
        if (n >= 1000) return 'Kz ' + (n / 1000).toFixed(0) + 'K';
        return 'Kz ' + (n || 0);
    }

    /* ---------- Stat tiles ---------- */
    function refreshStats() {
        const total = state.leads.length;
        const novos = state.leads.filter(l => l.stage === 'novo').length;
        const comprados = state.leads.filter(l => l.stage === 'fechado').length;
        const pipelineK = Math.round(state.leads.reduce((s, l) => s + l.value, 0) / 1000);

        setText('crmTotal', total);
        setText('crmNovos', novos);
        setText('crmComprados', comprados);
        setText('crmPipeline', pipelineK);
    }
    function setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    /* ---------- Tabs ---------- */
    function initTabs() {
        document.querySelectorAll('.crm-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.dataset.crmtab;
                document.querySelectorAll('.crm-tab').forEach(t => {
                    const active = t === tab;
                    t.classList.toggle('is-active', active);
                    t.setAttribute('aria-selected', String(active));
                });
                document.querySelectorAll('.crm-pane').forEach(p => {
                    p.classList.toggle('is-active', p.dataset.crmpane === target);
                });
                if (target === 'tabela') renderTable();
                if (target === 'relatorios') renderReports();
            });
        });
    }

    /* ---------- Tabela ---------- */
    const tableBody = () => document.getElementById('crmTableBody');
    const searchInput = () => document.getElementById('crmSearch');
    const stageFilter = () => document.getElementById('crmStageFilter');

    function renderTable() {
        const tbody = tableBody();
        if (!tbody) return;
        const q = (searchInput()?.value || '').trim().toLowerCase();
        const stage = stageFilter()?.value || '';

      const filtered = state.leads.filter(l => {
        const matchQ = !q ||
            l.name.toLowerCase().includes(q) ||
            l.contact.toLowerCase().includes(q) ||
            l.email.toLowerCase().includes(q) ||
            (l.service || '').toLowerCase().includes(q) ||
            (l.owner || '').toLowerCase().includes(q) ||
            (CHANNEL_LABEL[l.channel] || '').toLowerCase().includes(q);
        const matchStage = !stage || l.stage === stage;
        return matchQ && matchStage;
    });

        const empty = document.getElementById('crmEmpty');
        if (!filtered.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;

       tbody.innerHTML = filtered.map(l => `
            <tr class="inv-row" data-id="${l.id}">
                <td class="inv-client">${escapeHtml(l.name)}</td>
                <td class="col-date">${fmtDate(l.date)}</td>
                <td>
                    <span class="channel channel--${l.channel}">
                        ${CHANNEL_ICON[l.channel] || CHANNEL_ICON.outros}
                        ${CHANNEL_LABEL[l.channel] || 'Outros'}
                    </span>
                </td>
                <td>
                    ${l.owner ? `
                        <span class="col-owner">
                            <span class="avatar ${ownerTone(l.owner)}">${initialsFor(l.owner)}</span>
                            ${escapeHtml(l.owner)}
                        </span>
                    ` : '<span style="color:var(--color-text-muted)">—</span>'}
                </td>
                <td>${escapeHtml(l.contact) || '<span style="color:var(--color-text-muted)">—</span>'}</td>
                <td>${escapeHtml(l.service) || '<span style="color:var(--color-text-muted)">—</span>'}</td>
                <td class="num">${l.value ? fmtKz(l.value) : '<span style="color:var(--color-text-muted)">—</span>'}</td>
                <td><span class="lead-state lead-state--${l.stage}">${STAGE_LABEL[l.stage]}</span></td>
                <td class="action-col">
                    <button class="row-action" data-lead-edit="${l.id}" aria-label="Ver / editar">
                        <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                    </button>
                    <button class="row-action" data-delete="${l.id}" aria-label="Apagar">
                        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 5 H13 M6 5 V3 H10 V5 M5 5 L6 14 H10 L11 5" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    </button>
                </td>
            </tr>
        `).join('');
    }

    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        }[m]));
    }

    function initTableEvents() {
        searchInput()?.addEventListener('input', renderTable);
        stageFilter()?.addEventListener('change', renderTable);
        tableBody()?.addEventListener('click', async (e) => {
            const delBtn = e.target.closest('[data-delete]');
            if (delBtn) {
                const id = Number(delBtn.dataset.delete);
                if (await UIModal.confirm('Apagar este lead?', { danger: true })) deleteLead(id);
                return;
            }
            const editBtn = e.target.closest('[data-lead-edit]');
            if (editBtn) { openModal(Number(editBtn.dataset.leadEdit)); return; }
            const row = e.target.closest('tr[data-id]');
            if (row) openModal(Number(row.dataset.id));
        });
    }

    async function deleteLead(id) {
        try {
            const res = await apiFetch(`${API_BASE.CRM}/contacts/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('Falha ao apagar lead');
        } catch (e) {
            await UIModal.alert('Não foi possível apagar o lead.');
            return;
        }
        state.leads = state.leads.filter(l => l.id !== id);
        renderKanban();
        refreshStats();
        renderTable();
        renderReports();
        UIToast.success('Lead apagado com sucesso!');
    }

    /* ---------- Relatórios ---------- */
   function renderReports() {
    renderFunnel();
    renderDonut();
    renderRevenueBars();
    renderTeam();
}

function renderTeam() {
    const list = document.getElementById('teamBars');
    if (!list) return;
    const counts = {};
    state.leads.forEach(l => {
        const o = l.owner || '— não atribuído';
        counts[o] = (counts[o] || 0) + 1;
    });
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const max = Math.max(1, ...entries.map(e => e[1]));

    if (!entries.length) {
        list.innerHTML = '<li style="color:var(--color-text-soft); font-size:13px;">Sem leads atribuídos.</li>';
        return;
    }

    list.innerHTML = entries.map(([owner, n]) => {
        const w = Math.round(n / max * 100);
        return `
            <li class="team-row">
                <span class="avatar ${ownerTone(owner)}">${initialsFor(owner)}</span>
                <span class="team-row-name">${escapeHtml(owner)}</span>
                <div class="team-row-track"><span class="team-row-fill" data-w="${w}"></span></div>
                <span class="team-row-count">${n}</span>
            </li>
        `;
    }).join('');

    requestAnimationFrame(() => {
        list.querySelectorAll('.team-row-fill').forEach(f => f.style.width = f.dataset.w + '%');
    });
}

    function renderFunnel() {
        const list = document.getElementById('funnelList');
        if (!list) return;
        const total = state.leads.length;
        const stages = ['novo','qualificado','proposta','fechado'];
        const max = Math.max(1, ...stages.map(s => state.leads.filter(l => l.stage === s).length));

        list.innerHTML = stages.map(s => {
            const count = state.leads.filter(l => l.stage === s).length;
            const pct = total ? Math.round(count / total * 100) : 0;
            const w = Math.round(count / max * 100);
            return `
                <li class="funnel-row">
                    <span class="funnel-label">${STAGE_LABEL[s]}</span>
                    <div class="funnel-track"><span class="funnel-fill funnel-fill--${s}" data-w="${w}"></span></div>
                    <span class="funnel-count">${count}</span>
                    <span class="funnel-pct">${pct}%</span>
                </li>
            `;
        }).join('');

        requestAnimationFrame(() => {
            list.querySelectorAll('.funnel-fill').forEach(f => f.style.width = f.dataset.w + '%');
        });

        const fechados = state.leads.filter(l => l.stage === 'fechado').length;
        const rate = total ? Math.round(fechados / total * 100) : 0;
        const rateEl = document.getElementById('funnelRate');
        if (rateEl) rateEl.textContent = rate + '%';
    }

   function renderDonut() {
    const segsHost = document.getElementById('donutSegs');
    if (!segsHost) return;

    const channels = ['whatsapp','facebook','instagram','linkedin','email','outros'];
    const total = state.leads.length;

    const items = channels
        .map(ch => ({
            key: ch,
            label: CHANNEL_LABEL[ch],
            color: CHANNEL_COLOR[ch],
            n: state.leads.filter(l => l.channel === ch).length
        }))
        .filter(it => it.n > 0);

    segsHost.innerHTML = items.map((it, i) => `
        <circle class="donut-seg seg-${i}"
                cx="60" cy="60" r="48" fill="none"
                stroke="${it.color}" stroke-width="14" stroke-linecap="butt"
                pathLength="100" stroke-dasharray="0 100"
                transform="rotate(-90 60 60)"/>
    `).join('');

    let offset = 0;
    items.forEach((it, i) => {
        const pct = total ? (it.n / total) * 100 : 0;
        const seg = segsHost.querySelector('.seg-' + i);
        const curOffset = offset;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                seg.setAttribute('stroke-dasharray', `${pct} ${100 - pct}`);
                seg.setAttribute('stroke-dashoffset', `${-curOffset}`);
            });
        });
        offset += pct;
    });

    document.getElementById('donutTotal').textContent = total;

    const legend = document.getElementById('donutLegend');
    if (legend) {
        legend.innerHTML = items.length
            ? items.map(it => `
                <li>
                    <span class="dot" style="background:${it.color}"></span>
                    <span>${it.label}</span>
                    <span class="num">${it.n}</span>
                </li>
            `).join('')
            : '<li style="color:var(--color-text-soft); font-size:13px;">Sem dados ainda.</li>';
    }
}

    function renderRevenueBars() {
        const list = document.getElementById('revenueBars');
        if (!list) return;
        const stages = ['novo','qualificado','proposta','fechado'];
        const sums = {};
        stages.forEach(s => sums[s] = state.leads.filter(l => l.stage === s).reduce((a, l) => a + l.value, 0));
        const max = Math.max(1, ...Object.values(sums));

        list.innerHTML = stages.map(s => {
            const v = sums[s];
            const w = Math.round(v / max * 100);
            return `
                <li class="bar-row">
                    <span class="bar-row-label">${STAGE_LABEL[s]}</span>
                    <div class="bar-row-track"><span class="bar-row-fill" data-w="${w}"></span></div>
                    <span class="bar-row-value">${fmtKz(v)}</span>
                </li>
            `;
        }).join('');

        requestAnimationFrame(() => {
            list.querySelectorAll('.bar-row-fill').forEach(f => f.style.width = f.dataset.w + '%');
        });
    }

    /* ---------- Modal (novo lead) ---------- */
    const modal = () => document.getElementById('leadModal');
    const form = () => document.getElementById('leadForm');

    async function loadOwners() {
        const sel = document.getElementById('leadOwner');
        if (!sel) return;
        const cur = sel.value;
        try {
            const [deptRes, empRes] = await Promise.all([
                apiFetch(`${API_BASE.RH}/departments`),
                apiFetch(`${API_BASE.RH}/employees`),
            ]);
            if (!deptRes.ok) throw new Error('Falha ao carregar departamentos');
            if (!empRes.ok) throw new Error('Falha ao carregar funcionários');
            const departments = await deptRes.json();
            const employees = await empRes.json();

            const comercialIds = new Set(
                departments.filter(d => d.name.trim().toLowerCase() === 'comercial').map(d => d.id)
            );
            const comercialEmployees = employees.filter(e => comercialIds.has(e.department_id));

            sel.innerHTML = '<option value="">— Escolher —</option>' +
                comercialEmployees.map(e => `<option value="${escapeHtml(e.full_name)}">${escapeHtml(e.full_name)}</option>`).join('');
        } catch (e) {
            sel.innerHTML = '<option value="">— Escolher —</option>';
        }
        sel.value = cur;
    }

    async function openModal(id = null) {
        const m = modal();
        if (!m) return;
        await loadOwners();
        const f = form();
        f.reset();
        document.getElementById('leadId').value = '';
        document.getElementById('leadModalTitle').textContent = 'Novo lead';

        if (id) {
            const lead = state.leads.find(l => l.id === id);
            if (lead) {
                document.getElementById('leadId').value = lead.id;
                document.getElementById('leadName').value = lead.name || '';
                document.getElementById('leadContact').value = lead.contact || '';
                document.getElementById('leadEmail').value = lead.email || '';
                document.getElementById('leadCompany').value = lead.company || '';
                document.getElementById('leadService').value = lead.service || '';
                document.getElementById('leadValue').value = lead.value || '';
                document.getElementById('leadDate').value = lead.date || '';
                document.getElementById('leadChannel').value = lead.channel || 'outros';
                document.getElementById('leadOwner').value = lead.owner || '';
                document.getElementById('leadStage').value = lead.stage || 'novo';
                document.getElementById('leadNotes').value = lead.notes || '';
                document.getElementById('leadModalTitle').textContent = 'Editar lead';
            }
        } else {
            const dateInput = document.getElementById('leadDate');
            if (dateInput && !dateInput.value) dateInput.value = todayIso();
        }

        m.classList.add('is-open');
        m.setAttribute('aria-hidden', 'false');
        setTimeout(() => document.getElementById('leadName')?.focus(), 100);
    }
    function closeModal() {
        const m = modal();
        if (!m) return;
        m.classList.remove('is-open');
        m.setAttribute('aria-hidden', 'true');
        form()?.reset();
    }

  function initModal() {
    document.getElementById('crmAddBtn')?.addEventListener('click', () => openModal());
    modal()?.querySelectorAll('[data-close]').forEach(el => {
        el.addEventListener('click', closeModal);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal()?.classList.contains('is-open')) {
            closeModal();
        }
    });
    form()?.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!form().checkValidity()) { form().reportValidity(); return; }
        const fd = new FormData(form());
        const id = fd.get('id');
        const data = {
            name: fd.get('name').trim(),
            contact: (fd.get('contact') || '').trim(),
            email: (fd.get('email') || '').trim(),
            company: (fd.get('company') || '').trim(),
            service: (fd.get('service') || '').trim(),
            value: Number(fd.get('value')) || 0,
            date: fd.get('date') || todayIso(),
            channel: fd.get('channel') || 'outros',
            owner: fd.get('owner') || '',
            stage: fd.get('stage') || 'novo',
            notes: (fd.get('notes') || '').trim()
        };
        if (id) {
            await updateLead(Number(id), data);
        } else {
            await addLead(data);
        }
        closeModal();
    });
}

    function contactBody(data) {
        return {
            name: data.name,
            email: data.email || null,
            phone: data.contact || null,
            company: data.company || null,
            notes: data.notes || null,
            stage: data.stage,
            pipeline_value: data.value,
            channel: data.channel,
            owner: data.owner || null,
            service_type: data.service || null,
            lead_date: data.date,
        };
    }

    async function addLead(data) {
        try {
            const res = await apiFetch(`${API_BASE.CRM}/contacts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(contactBody(data)),
            });
            if (!res.ok) throw new Error('Falha ao criar lead');
            const contact = await res.json();
            state.leads.push(mapContact(contact));
        } catch (e) {
            await UIModal.alert('Não foi possível criar o lead.');
            return;
        }
        renderKanban();
        refreshStats();
        renderTable();
        renderReports();
        UIToast.success('Lead criado com sucesso!');
    }

    async function updateLead(id, data) {
        try {
            const res = await apiFetch(`${API_BASE.CRM}/contacts/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(contactBody(data)),
            });
            if (!res.ok) throw new Error('Falha ao guardar lead');
            const contact = await res.json();
            const idx = state.leads.findIndex(l => l.id === id);
            if (idx !== -1) state.leads[idx] = mapContact(contact);
        } catch (e) {
            await UIModal.alert('Não foi possível guardar as alterações do lead.');
            return;
        }
        renderKanban();
        refreshStats();
        renderTable();
        renderReports();
        UIToast.success('Lead actualizado com sucesso!');
    }

    function clearKanbanColumns() {
        document.querySelectorAll('.kanban-list').forEach(list => list.innerHTML = '');
    }

    function renderKanban() {
        clearKanbanColumns();
        state.leads.forEach(appendCardToKanban);
        recountAllColumns();
    }

    function appendCardToKanban(lead) {
        const col = document.querySelector(`.kanban-col[data-stage="${lead.stage}"] .kanban-list`);
        if (!col) return;
        const initials = initialsFor(lead.name);
        const tone = ownerTone(lead.owner || lead.name);

        const card = document.createElement('article');
        card.className = 'lead-card';
        card.draggable = true;
        card.dataset.id = lead.id;
        card.innerHTML = `
            <div class="lead-info">
                <h4 class="lead-name">${escapeHtml(lead.name)}</h4>
                <p class="lead-value">${lead.value ? fmtKz(lead.value) : '—'}</p>
            </div>
            <button class="row-action" type="button" data-attach="${lead.id}" aria-label="Anexos" title="Anexos">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11 5 L6.5 9.5 a1.8 1.8 0 0 0 2.5 2.5 L13.5 7.5 a3 3 0 0 0 -4.2 -4.2 L4.8 7.8 a4.2 4.2 0 0 0 5.9 5.9 L14 10.4" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <span class="avatar ${tone}">${initials}</span>
        `;
        col.appendChild(card);
    }

    /* ---------- Sync Kanban → API on drop ---------- */
    function watchKanbanMoves() {
        document.querySelectorAll('[data-drop]').forEach(list => {
            list.addEventListener('drop', () => {
                setTimeout(syncStageFromDOM, 50);
            });
        });
    }
    async function syncStageFromDOM() {
        const updates = [];
        document.querySelectorAll('.kanban-col').forEach(col => {
            const stage = col.dataset.stage;
            col.querySelectorAll('.lead-card').forEach(card => {
                const id = Number(card.dataset.id);
                const lead = state.leads.find(l => l.id === id);
                if (lead && lead.stage !== stage) {
                    lead.stage = stage;
                    updates.push({ id, stage });
                }
            });
        });
        refreshStats();
        renderTable();
        renderReports();
        for (const u of updates) {
            try {
                await apiFetch(`${API_BASE.CRM}/contacts/${u.id}/stage`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ stage: u.stage }),
                });
            } catch (e) {}
        }
    }

    function recountAllColumns() {
        document.querySelectorAll('.kanban-col').forEach(col => {
            const n = col.querySelectorAll('.lead-card').length;
            const badge = col.querySelector('.col-count');
            if (badge) badge.textContent = n;
        });
    }

    /* ---------- Anexos (MinIO via crm-service) ---------- */
    const attachModal = () => document.getElementById('attachModal');
    const attachList = () => document.getElementById('attachList');
    let currentAttachContactId = null;

    function openAttachModal(contactId) {
        currentAttachContactId = contactId;
        const m = attachModal();
        if (!m) return;
        m.classList.add('is-open');
        m.setAttribute('aria-hidden', 'false');
        loadAttachments(contactId);
    }
    function closeAttachModal() {
        const m = attachModal();
        if (!m) return;
        m.classList.remove('is-open');
        m.setAttribute('aria-hidden', 'true');
        currentAttachContactId = null;
    }

    async function loadAttachments(contactId) {
        const list = attachList();
        if (!list) return;
        list.innerHTML = '<li class="attach-empty">A carregar...</li>';
        try {
            const res = await apiFetch(`${API_BASE.CRM}/contacts/${contactId}/attachments`);
            if (!res.ok) throw new Error('Falha ao carregar anexos');
            const items = await res.json();
            if (!items.length) {
                list.innerHTML = '<li class="attach-empty">Sem anexos ainda.</li>';
                return;
            }
            list.innerHTML = items.map(a => `
                <li class="attach-item">
                    <span class="attach-name">${escapeHtml(a.filename)}</span>
                    <a class="attach-download" href="${a.url}" target="_blank" rel="noopener">Transferir</a>
                </li>
            `).join('');
        } catch (e) {
            list.innerHTML = '<li class="attach-empty">Não foi possível carregar os anexos.</li>';
        }
    }

    function initAttachModal() {
        document.getElementById('kanban')?.addEventListener('click', (e) => {
            const attachBtn = e.target.closest('[data-attach]');
            if (attachBtn) { openAttachModal(Number(attachBtn.dataset.attach)); return; }
            const card = e.target.closest('.lead-card');
            if (card) openModal(Number(card.dataset.id));
        });
        attachModal()?.querySelectorAll('[data-close]').forEach(el => {
            el.addEventListener('click', closeAttachModal);
        });
        document.getElementById('attachUploadForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (!currentAttachContactId) return;
            const fileInput = document.getElementById('attachFile');
            const file = fileInput.files[0];
            if (!file) return;
            const fd = new FormData();
            fd.append('file', file);
            const btn = e.target.querySelector('button[type="submit"]');
            btn?.classList.add('is-loading');
            try {
                const res = await apiFetch(`${API_BASE.CRM}/contacts/${currentAttachContactId}/attachments`, {
                    method: 'POST',
                    body: fd,
                });
                if (!res.ok) throw new Error('Falha ao enviar ficheiro');
                fileInput.value = '';
                await loadAttachments(currentAttachContactId);
                UIToast.success('Anexo enviado com sucesso!');
            } catch (e) {
                await UIModal.alert('Falha ao enviar o ficheiro.');
            } finally {
                btn?.classList.remove('is-loading');
            }
        });
    }

    /* ---------- Init ---------- */
    async function init() {
        if (!document.querySelector('[data-view="crm"]')) return;
        initTabs();
        initTableEvents();
        initModal();
        initAttachModal();
        watchKanbanMoves();
        await loadLeads();
    }

    return { init, renderTable, renderReports };
})();

document.addEventListener('DOMContentLoaded', CRM.init);

function renderReports() {
    renderFunnel();
    renderDonut();
    renderRevenueBars();
    renderTeam();
}

function renderTeam() {
    const list = document.getElementById('teamBars');
    if (!list) return;

    const counts = {};
    state.leads.forEach(l => {
        const o = l.owner || '— não atribuído';
        counts[o] = (counts[o] || 0) + 1;
    });
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const max = Math.max(1, ...entries.map(e => e[1]));

    if (!entries.length) {
        list.innerHTML = '<li style="color:var(--color-text-soft); font-size:13px;">Sem leads atribuídos.</li>';
        return;
    }

    list.innerHTML = entries.map(([owner, n]) => {
        const w = Math.round(n / max * 100);
        return `
            <li class="team-row">
                <span class="avatar ${ownerTone(owner)}">${initialsFor(owner)}</span>
                <span class="team-row-name">${escapeHtml(owner)}</span>
                <div class="team-row-track"><span class="team-row-fill" data-w="${w}"></span></div>
                <span class="team-row-count">${n}</span>
            </li>
        `;
    }).join('');

    requestAnimationFrame(() => {
        list.querySelectorAll('.team-row-fill').forEach(f => f.style.width = f.dataset.w + '%');
    });
}

/* ============================================
   HR MODULE — Equipa + Departamentos (Bloco 1)
   ============================================ */


const HR = (() => {
    const LS_KEY = 'mksHR.v1';
    const VAC_LIMIT_PER_YEAR = 22;  // LGT Angola
    let prTickInterval = null;
    const state = {
        employees: [],
        departments: [],
        absences: [],
        vacations: [],
        attendance: {},     // { 'YYYY-MM-DD': { employeeId: { in: 'HH:MM', out: 'HH:MM' } } }
        documents: [],      // { id, name, category, employee, notes, filename, date }
        evaluations: [],    // carregado do rh-service (/evaluations)
        training: [],       // carregado do rh-service (/trainings)
        payslips: [],       // carregado do rh-service (/payslips)
        contracts: [],      // carregado do rh-service (/contracts) — lista completa
        onboarding: null,   // { checklist_id, employee_id, items: [...] } do funcionário seleccionado no filtro
        attSeen: {},        // { employeeId: true } — quem já foi marcado presente nesta sessão (sem endpoint de listagem)
        ui: {
            absMonth: null  // ISO 'YYYY-MM-01'
        }
    };

    const STATUS_LABEL = {
        online: 'Presente',
        meeting: 'Em reunião',
        lunch: 'Almoço',
        remote: 'Remoto',
        training: 'Formação',
        vacation: 'Férias',
        off: 'Off'
    };

    /* ---------- Storage (apenas para os módulos ainda sem backend) ---------- */
   function load() {
    try {
        const raw = localStorage.getItem(LS_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            state.attendance = parsed.attendance || {};
            state.attSeen = parsed.attSeen || {};
            state.documents = parsed.documents || [];
        }
    } catch (e) {}
}
    function save() {
    try {
        localStorage.setItem(LS_KEY, JSON.stringify({
            attendance: state.attendance || {},
            attSeen: state.attSeen || {},
            documents: state.documents || []
        }));
    } catch (e) {}
}

    /* ---------- Funcionários e Departamentos (rh-service real) ---------- */
    function mapEmployee(e) {
        return {
            id: e.id,
            name: e.full_name,
            role: e.role || '',
            department: e.department_id,
            status: e.status || 'online',
            email: e.email || '',
            phone: e.phone || '',
            startDate: e.hire_date ? e.hire_date.slice(0, 10) : '',
            birth: e.birth_date || '',
            reportsTo: e.reports_to,
            contractType: e.contract_type || '',
            employeeNumber: e.employee_number || '',
            firstName: e.first_name || '',
            lastName: e.last_name || '',
            gender: e.gender || '',
            nationality: e.nationality || '',
            maritalStatus: e.marital_status || '',
            mobile: e.mobile || '',
            address: e.address || '',
            province: e.province || '',
            city: e.city || '',
            biNumber: e.bi_number || '',
            biExpiry: e.bi_expiry || '',
            nif: e.nif || '',
            niss: e.niss || '',
            passportNumber: e.passport_number || '',
            passportExpiry: e.passport_expiry || '',
            bankName: e.bank_name || '',
            bankAccount: e.bank_account || '',
            iban: e.iban || '',
            photoUrl: e.photo_url || ''
        };
    }

    async function loadEmployees() {
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees`);
            if (!res.ok) throw new Error('Falha ao carregar funcionários');
            const employees = await res.json();
            state.employees = employees.map(mapEmployee);
        } catch (e) {
            state.employees = [];
        }
    }

    async function loadDepartments() {
        try {
            const res = await apiFetch(`${API_BASE.RH}/departments`);
            if (!res.ok) throw new Error('Falha ao carregar departamentos');
            state.departments = await res.json();
        } catch (e) {
            state.departments = [];
        }
    }

    /* ---------- Helpers ---------- */
    function nextId(list) {
        return list.length ? Math.max(...list.map(x => x.id)) + 1 : 1;
    }
    function dept(id) { return state.departments.find(d => d.id === Number(id)); }
    function deptName(id) { return dept(id)?.name || '—'; }
    function deptColor(id) { return dept(id)?.color || '#6B7280'; }
    function initials(name) {
        return (name || '').trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() || '').join('') || '?';
    }
    function avatarTone(id) {
        const tones = ['avatar--blue','avatar--teal','avatar--purple'];
        return tones[Number(id) % tones.length];
    }
    function avatarInner(nameOrEmp) {
        const emp = (nameOrEmp && typeof nameOrEmp === 'object') ? nameOrEmp : null;
        const name = emp ? emp.name : nameOrEmp;
        if (emp && emp.photoUrl) {
            return `<img src="${emp.photoUrl}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:inherit;">`;
        }
        return initials(name);
    }
    function avatarInnerById(id) {
        return avatarInner(state.employees.find(e => e.id === Number(id)));
    }
    function fmtDate(iso) {
        if (!iso) return '—';
        const [y, m, d] = iso.split('-');
        return `${d}/${m}/${y}`;
    }
    function tenureYears(startDate) {
        if (!startDate) return 0;
        const start = new Date(startDate);
        const now = new Date();
        return Math.max(0, (now - start) / (365.25 * 24 * 3600 * 1000));
    }
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
        }[m]));
    }
    function todayIso() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    }


    function pad(n) { return String(n).padStart(2, '0'); }
function addDaysIso(iso, n) {
    const d = new Date(iso);
    d.setDate(d.getDate() + n);
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
}
function lastBusinessDay(offset) {
    const d = new Date();
    d.setDate(d.getDate() + offset);
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
}
function isoToDate(iso) { const [y,m,d] = iso.split('-').map(Number); return new Date(y, m-1, d); }
function monthIso(year, month) { return `${year}-${pad(month+1)}-01`; }
function businessDaysBetween(startIso, endIso) {
    if (!startIso || !endIso) return 0;
    const s = isoToDate(startIso);
    const e = isoToDate(endIso);
    if (e < s) return 0;
    let count = 0;
    const cur = new Date(s);
    while (cur <= e) {
        const dow = cur.getDay();
        if (dow !== 0 && dow !== 6) count++;
        cur.setDate(cur.getDate() + 1);
    }
    return count;
}
function monthLabel(iso) {
    const months = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
    const d = isoToDate(iso);
    return `${months[d.getMonth()]} ${d.getFullYear()}`;
}

    /* ---------- KPIs ---------- */
    function renderKPIs() {
        document.getElementById('hrKpiTotal').textContent = state.employees.length;
        document.getElementById('hrKpiPresent').textContent = state.employees.filter(e => e.status === 'online' || e.status === 'meeting' || e.status === 'remote').length;
        document.getElementById('hrKpiDept').textContent = state.departments.length;
        const avgTenure = state.employees.length
            ? state.employees.reduce((s, e) => s + tenureYears(e.startDate), 0) / state.employees.length
            : 0;
        document.getElementById('hrKpiTenure').textContent = avgTenure.toFixed(1);
    }

    /* ---------- Tabs + view toggle ---------- */
   function initTabs() {
    document.querySelectorAll('[data-hrtab]').forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.hrtab;
            document.querySelectorAll('[data-hrtab]').forEach(t => {
                const active = t === tab;
                t.classList.toggle('is-active', active);
                t.setAttribute('aria-selected', String(active));
            });
            document.querySelectorAll('[data-hrpane]').forEach(p => {
                p.classList.toggle('is-active', p.dataset.hrpane === target);
            });
            if (target === 'equipa')        renderEmployees();
            if (target === 'documentos')    ScopedDocs.mount('hrDocsWidget', 'Recursos Humanos');
            if (target === 'onboarding')    renderOnboarding();
            if (target === 'contratos')     renderContracts();
            if (target === 'avaliacoes')    renderEvaluations();
            if (target === 'formacao')      renderTraining();
            if (target === 'organograma')   renderOrganogram();
        });
    });

    document.querySelectorAll('[data-hrview]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('[data-hrview]').forEach(b => b.classList.toggle('is-active', b === btn));
            const view = btn.dataset.hrview;
            document.getElementById('hrGrid').classList.toggle('is-active', view === 'grid');
            document.querySelector('.hr-list').classList.toggle('is-active', view === 'list');
        });
    });
}

/* ---------- Sub-tabs (scoped a um único .crm-pane pai) ---------- */
function initSubTabs() {
    document.querySelectorAll('[data-hrsubtab]').forEach(tab => {
        tab.addEventListener('click', () => {
            const parentPane = tab.closest('.crm-pane[data-hrpane]');
            if (!parentPane) return;
            const target = tab.dataset.hrsubtab;
            parentPane.querySelectorAll('[data-hrsubtab]').forEach(t => {
                const active = t === tab;
                t.classList.toggle('is-active', active);
                t.setAttribute('aria-selected', String(active));
            });
            parentPane.querySelectorAll(':scope > [data-hrsubpane]').forEach(p => {
                p.classList.toggle('is-active', p.dataset.hrsubpane === target);
            });
            if (target === 'departamentos') renderDepartments();
            if (target === 'ausencias')     renderAbsences();
            if (target === 'ferias')        renderVacations();
            if (target === 'presenca')      renderAttendance();
            if (target === 'equipa')        renderEmployees();
            if (target === 'recibos')       renderPayslips();
            if (target === 'contratos')     renderContracts();
        });
    });
}

    /* ---------- Filtros ---------- */
    function populateDeptFilter() {
        const sel = document.getElementById('hrDeptFilter');
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">Todos os departamentos</option>' +
            state.departments.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
        sel.value = current;
    }
    function populateDeptSelect() {
        const sel = document.getElementById('empDept');
        if (!sel) return;
        sel.innerHTML = state.departments.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
    }

    function filtered() {
        const q = (document.getElementById('hrSearch')?.value || '').trim().toLowerCase();
        const d = document.getElementById('hrDeptFilter')?.value || '';
        const s = document.getElementById('hrStatusFilter')?.value || '';
        return state.employees.filter(e => {
            const matchQ = !q ||
                e.name.toLowerCase().includes(q) ||
                e.role.toLowerCase().includes(q) ||
                deptName(e.department).toLowerCase().includes(q);
            const matchD = !d || String(e.department) === d;
            const matchS = !s || e.status === s;
            return matchQ && matchD && matchS;
        });
    }

    /* ---------- Render grid ---------- */
    function renderGrid() {
        const host = document.getElementById('hrGrid');
        if (!host) return;
        const list = filtered();
        if (!list.length) {
            host.innerHTML = `
                <div class="hr-empty-state">
                    <svg viewBox="0 0 48 48" fill="none"><circle cx="18" cy="16" r="6" stroke="currentColor" stroke-width="2"/><path d="M6 38 c0-6 5-10 12-10 s12 4 12 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="36" cy="18" r="5" stroke="currentColor" stroke-width="2"/></svg>
                    <h3>Nenhum funcionário encontrado</h3>
                    <p>Tente outro filtro ou adicione um novo funcionário.</p>
                </div>
            `;
            return;
        }
        host.innerHTML = list.map(e => {
            const d = dept(e.department);
            const presence = ['online','meeting','remote','training','lunch','vacation'].includes(e.status)
                ? `<span class="presence presence--${e.status === 'online' ? 'online' : e.status}"></span>`
                : '';
            return `
                <article class="emp-card" data-id="${e.id}">
                    <div class="emp-status-bar">
                        <div class="status-quick" data-status-quick="${e.id}">
                            <button type="button" class="status-quick-trigger" aria-label="Mudar estado">
                                <span class="status-pill status-pill--${e.status}">${STATUS_LABEL[e.status]}</span>
                            </button>
                            <div class="status-quick-menu">
                                ${Object.entries(STATUS_LABEL).map(([k, v]) => `
                                    <button type="button" data-status="${k}" data-emp="${e.id}">${v}</button>
                                `).join('')}
                            </div>
                        </div>
                        <div class="emp-card-actions">
                            <button class="btn-icon-mini" data-emp-docs="${e.id}" aria-label="Documentos">
                                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11 5 L6.5 9.5 a1.8 1.8 0 0 0 2.5 2.5 L13.5 7.5 a3 3 0 0 0 -4.2 -4.2 L4.8 7.8 a4.2 4.2 0 0 0 5.9 5.9 L14 10.4" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
                            </button>
                            <button class="btn-icon-mini" data-edit="${e.id}" aria-label="Editar">
                                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linejoin="round"/></svg>
                            </button>
                        </div>
                    </div>

                    <div class="emp-card-top">
                        <div class="avatar ${avatarTone(e.id)}">
                            ${avatarInner(e)}
                            ${presence}
                        </div>
                        <div class="emp-card-info">
                            <div class="emp-name">${escapeHtml(e.name)}</div>
                            <div class="emp-role">${escapeHtml(e.role)}</div>
                        </div>
                    </div>

                    ${d ? `<span class="emp-dept-tag" style="--dept-color: ${d.color}; --dept-bg: ${d.color}1A;">${escapeHtml(d.name)}</span>` : ''}

                    <div class="emp-meta">
                        ${e.email ? `<div class="emp-meta-row"><svg viewBox="0 0 16 16" fill="none"><rect x="2" y="3.5" width="12" height="9" rx="1.2" stroke="currentColor" stroke-width="1.3"/><path d="M2.5 4.5 L8 9 L13.5 4.5" stroke="currentColor" stroke-width="1.3"/></svg>${escapeHtml(e.email)}</div>` : ''}
                        ${e.phone ? `<div class="emp-meta-row"><svg viewBox="0 0 16 16" fill="none"><path d="M3 4 c0 -1 1 -2 2 -2 L7 2 L8 5 L6.5 6.5 c1 2 2.5 3.5 4.5 4.5 L12.5 9.5 L15.5 10.5 L15.5 12 c0 1 -1 2 -2 2 c-6 0 -10.5 -4.5 -10.5 -10 z" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>${escapeHtml(e.phone)}</div>` : ''}
                        ${e.startDate ? `<div class="emp-meta-row"><svg viewBox="0 0 16 16" fill="none"><rect x="2" y="3" width="12" height="11" rx="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M2 6 L14 6 M5 1.5 L5 4 M11 1.5 L11 4" stroke="currentColor" stroke-width="1.3"/></svg>Desde ${fmtDate(e.startDate)}</div>` : ''}
                    </div>
                </article>
            `;
        }).join('');
    }

    /* ---------- Render list ---------- */
    function renderList() {
        const tbody = document.getElementById('hrTableBody');
        const empty = document.getElementById('hrEmpty');
        if (!tbody) return;
        const list = filtered();
        if (!list.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = list.map(e => {
            const d = dept(e.department);
            return `
                <tr class="inv-row" data-id="${e.id}">
                    <td>
                        <span class="col-owner">
                            <span class="avatar ${avatarTone(e.id)}">${avatarInner(e)}</span>
                            ${escapeHtml(e.name)}
                        </span>
                    </td>
                    <td>${escapeHtml(e.role)}</td>
                    <td>${d ? `<span class="emp-dept-tag" style="--dept-color: ${d.color}; --dept-bg: ${d.color}1A;">${escapeHtml(d.name)}</span>` : '—'}</td>
                    <td>${escapeHtml(e.email) || '—'}</td>
                    <td>${escapeHtml(e.phone) || '—'}</td>
                    <td><span class="status-pill status-pill--${e.status}">${STATUS_LABEL[e.status]}</span></td>
                    <td class="action-col">
                        <button class="btn-icon-mini" data-edit="${e.id}" aria-label="Editar">
                            <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
    }

    function renderEmployees() {
        renderGrid();
        renderList();
        renderKPIs();
    }

    /* ---------- Render departments ---------- */
    function renderDepartments() {
        const host = document.getElementById('deptGrid');
        if (!host) return;
        if (!state.departments.length) {
            host.innerHTML = `
                <div class="hr-empty-state">
                    <svg viewBox="0 0 48 48" fill="none"><rect x="6" y="9" width="14" height="14" rx="2" stroke="currentColor" stroke-width="2"/><rect x="28" y="9" width="14" height="14" rx="2" stroke="currentColor" stroke-width="2"/></svg>
                    <h3>Sem departamentos</h3>
                    <p>Crie o primeiro departamento para organizar a equipa.</p>
                </div>
            `;
            return;
        }
        host.innerHTML = state.departments.map(d => {
            const members = state.employees.filter(e => e.department === d.id);
            const visible = members.slice(0, 5);
            const more = members.length - visible.length;
            return `
                <article class="dept-card" data-dept="${d.id}" style="--dept-color: ${d.color}">
                    <div class="dept-actions">
                        <button class="btn-icon-mini" data-edit-dept="${d.id}" aria-label="Editar">
                            <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                        </button>
                    </div>
                    <h3 class="dept-name">${escapeHtml(d.name)}</h3>
                    <p class="dept-count">${members.length} ${members.length === 1 ? 'pessoa' : 'pessoas'}</p>
                    <div class="dept-avatars">
                        ${visible.length
                            ? visible.map(e => `<span class="avatar ${avatarTone(e.id)}" title="${escapeHtml(e.name)}">${avatarInner(e)}</span>`).join('')
                            : '<span class="dept-empty">Ainda sem membros</span>'
                        }
                        ${more > 0 ? `<span class="dept-avatars-more">+${more}</span>` : ''}
                    </div>
                </article>
            `;
        }).join('');
    }

    /* ---------- Status quick change ---------- */
    function initStatusQuick() {
        document.addEventListener('click', (e) => {
            const trigger = e.target.closest('.status-quick-trigger');
            if (trigger) {
                const parent = trigger.closest('.status-quick');
                document.querySelectorAll('.status-quick.is-open').forEach(el => {
                    if (el !== parent) el.classList.remove('is-open');
                });
                parent.classList.toggle('is-open');
                e.stopPropagation();
                return;
            }
            const opt = e.target.closest('.status-quick-menu button');
            if (opt) {
                const empId = Number(opt.dataset.emp);
                const newStatus = opt.dataset.status;
                const emp = state.employees.find(x => x.id === empId);
                if (emp) {
                    emp.status = newStatus;
                    renderEmployees();
                    apiFetch(`${API_BASE.RH}/employees/${empId}/status`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: newStatus }),
                    }).catch(() => {});
                }
                e.stopPropagation();
                return;
            }
            document.querySelectorAll('.status-quick.is-open').forEach(el => el.classList.remove('is-open'));
        });
    }

    /* ---------- Employee modal ---------- */
    const empModal = () => document.getElementById('employeeModal');
    const empForm = () => document.getElementById('employeeForm');

    const EMP_WIZARD_STEPS = ['pessoais', 'documentos', 'profissional', 'financeiro'];
    let empWizardIndex = 0;
    let empWizardMaxReached = 0;
    let empWizardMode = 'create';

    function empStepButtons() {
        const form = empForm();
        return form ? Array.from(form.querySelectorAll('[data-emptab]')) : [];
    }
    function empStepPanes() {
        const form = empForm();
        return form ? Array.from(form.querySelectorAll('[data-emptabpane]')) : [];
    }
    function validateEmpStep(index) {
        const pane = empStepPanes()[index];
        if (!pane) return true;
        const invalid = Array.from(pane.querySelectorAll('[required]')).find(el => !el.checkValidity());
        if (invalid) { invalid.reportValidity(); return false; }
        return true;
    }
    function renderEmpWizardUI() {
        empStepButtons().forEach((btn, i) => {
            const active = i === empWizardIndex;
            btn.classList.toggle('is-active', active);
            btn.classList.toggle('is-done', empWizardMode === 'edit' || i < empWizardIndex);
            btn.setAttribute('aria-selected', String(active));
            btn.disabled = empWizardMode !== 'edit' && i > empWizardMaxReached;
        });
        empStepPanes().forEach((pane, i) => pane.classList.toggle('is-active', i === empWizardIndex));

        const fill = document.getElementById('empWizardProgressFill');
        if (fill) fill.style.width = `${((empWizardIndex + 1) / EMP_WIZARD_STEPS.length) * 100}%`;

        const isLast = empWizardIndex === EMP_WIZARD_STEPS.length - 1;
        const backBtn = document.getElementById('empWizardBackBtn');
        const nextBtn = document.getElementById('empWizardNextBtn');
        const submitBtn = document.getElementById('empWizardSubmitBtn');
        if (backBtn) backBtn.hidden = empWizardIndex === 0;
        if (nextBtn) nextBtn.hidden = isLast;
        if (submitBtn) {
            submitBtn.hidden = !isLast;
            submitBtn.textContent = empWizardMode === 'edit' ? 'Guardar alterações' : 'Concluir onboarding';
        }
    }
    function goToEmpStep(index, { validate = true } = {}) {
        if (validate && index > empWizardIndex && !validateEmpStep(empWizardIndex)) return;
        empWizardIndex = Math.max(0, Math.min(EMP_WIZARD_STEPS.length - 1, index));
        empWizardMaxReached = Math.max(empWizardMaxReached, empWizardIndex);
        renderEmpWizardUI();
    }
    function initEmpTabs() {
        const form = empForm();
        if (!form) return;
        empStepButtons().forEach((btn, i) => {
            btn.addEventListener('click', () => goToEmpStep(i));
        });
        document.getElementById('empWizardBackBtn')?.addEventListener('click', () => goToEmpStep(empWizardIndex - 1, { validate: false }));
        document.getElementById('empWizardNextBtn')?.addEventListener('click', () => goToEmpStep(empWizardIndex + 1));
        form.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && empWizardIndex < EMP_WIZARD_STEPS.length - 1) {
                e.preventDefault();
                goToEmpStep(empWizardIndex + 1);
            }
        });
    }
    function resetEmpTabs(mode = 'create') {
        empWizardMode = mode;
        empWizardIndex = 0;
        empWizardMaxReached = mode === 'edit' ? EMP_WIZARD_STEPS.length - 1 : 0;
        renderEmpWizardUI();
    }

    async function refreshEmpActiveContract(id) {
        const typeInput  = document.getElementById('empActiveContractType');
        const hint       = document.getElementById('empContractHint');
        const salaryEl   = document.getElementById('empActiveSalary');
        const mealEl     = document.getElementById('empActiveMeal');
        const transEl    = document.getElementById('empActiveTransport');
        const dedEl      = document.getElementById('empActiveDeductions');
        if (!id) {
            typeInput.value = '';
            typeInput.placeholder = '— Sem contrato activo —';
            hint.textContent = 'Sem contrato activo — crie um em Contratos.';
            salaryEl.value = ''; mealEl.value = ''; transEl.value = '';
            dedEl.textContent = '—';
            return;
        }
        const c = await fetchActiveContract(id);
        if (!c) {
            typeInput.value = '';
            typeInput.placeholder = '— Sem contrato activo —';
            hint.textContent = 'Sem contrato activo — crie um em Contratos.';
            salaryEl.value = ''; mealEl.value = ''; transEl.value = '';
            dedEl.textContent = '—';
            return;
        }
        typeInput.value = CONTRACT_TYPE_LABEL(c.contract_type);
        hint.textContent = `Contrato activo desde ${fmtDate((c.start_date || '').slice(0, 10))}.`;
        salaryEl.value = fmtAoa(c.base_salary);
        mealEl.value = fmtAoa(c.meal_allowance || 0);
        transEl.value = fmtAoa(c.transport_allowance || 0);
        const parts = [];
        if (c.apply_inss) parts.push('Segurança Social (INSS 3%)');
        if (c.apply_irt) parts.push('IRT');
        dedEl.textContent = parts.length ? parts.join(' · ') : 'Nenhum desconto aplicado';
    }

    let empPhotoSelectedFile = null;

    function setEmpPhotoAvatar(url, fallbackInitials) {
        const el = document.getElementById('empPhotoAvatar');
        if (!el) return;
        if (url) {
            el.innerHTML = `<img src="${url}" alt="Foto do colaborador" style="width:100%;height:100%;object-fit:cover;border-radius:inherit;">`;
        } else {
            el.innerHTML = '';
            el.textContent = fallbackInitials || '?';
        }
    }

    async function uploadEmpPhotoIfSelected(empId) {
        if (!empPhotoSelectedFile || !empId) return;
        const fd = new FormData();
        fd.append('file', empPhotoSelectedFile);
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/photo`, {
                method: 'POST',
                body: fd,
            });
            if (!res.ok) throw new Error('Falha ao enviar foto');
        } catch (err) {
            await UIModal.alert('Funcionário guardado, mas não foi possível carregar a foto.');
        } finally {
            empPhotoSelectedFile = null;
        }
    }

    function initEmpPhoto() {
        const btn = document.getElementById('empPhotoBtn');
        const fileInput = document.getElementById('empPhotoFile');
        btn?.addEventListener('click', () => fileInput?.click());
        fileInput?.addEventListener('change', () => {
            const file = fileInput.files[0];
            if (!file) return;
            empPhotoSelectedFile = file;
            setEmpPhotoAvatar(URL.createObjectURL(file), '?');
            document.getElementById('empPhotoHint').textContent = 'A foto será guardada ao gravar o funcionário.';
        });
    }

    function openEmpModal(id = null) {
        populateDeptSelect();
        const form = empForm();
        form.reset();
        resetEmpTabs(id ? 'edit' : 'create');
        document.getElementById('empId').value = '';
        document.getElementById('empDeleteBtn').hidden = true;
        document.getElementById('employeeModalTitle').textContent = 'Adicionar funcionário';
        empPhotoSelectedFile = null;
        setEmpPhotoAvatar(null, '?');

        if (id) {
            const emp = state.employees.find(e => e.id === id);
            if (emp) {
                document.getElementById('empId').value = emp.id;
                document.getElementById('empFirstName').value = emp.firstName || '';
                document.getElementById('empLastName').value = emp.lastName || '';
                document.getElementById('empNumber').value = emp.employeeNumber || '';
                document.getElementById('empGender').value = emp.gender || '';
                document.getElementById('empRole').value = emp.role;
                document.getElementById('empDept').value = emp.department;
                document.getElementById('empStatus').value = emp.status;
                document.getElementById('empEmail').value = emp.email || '';
                document.getElementById('empPhone').value = emp.phone || '';
                document.getElementById('empMobile').value = emp.mobile || '';
                document.getElementById('empStartDate').value = emp.startDate || '';
                document.getElementById('empBirth').value = emp.birth || '';
                document.getElementById('empNationality').value = emp.nationality || '';
                document.getElementById('empMaritalStatus').value = emp.maritalStatus || '';
                document.getElementById('empAddress').value = emp.address || '';
                document.getElementById('empProvince').value = emp.province || '';
                document.getElementById('empCity').value = emp.city || '';
                document.getElementById('empBiNumber').value = emp.biNumber || '';
                document.getElementById('empBiExpiry').value = emp.biExpiry || '';
                document.getElementById('empNif').value = emp.nif || '';
                document.getElementById('empNiss').value = emp.niss || '';
                document.getElementById('empPassportNumber').value = emp.passportNumber || '';
                document.getElementById('empPassportExpiry').value = emp.passportExpiry || '';
                document.getElementById('empBankName').value = emp.bankName || '';
                document.getElementById('empBankAccount').value = emp.bankAccount || '';
                document.getElementById('empIban').value = emp.iban || '';
                setEmpPhotoAvatar(emp.photoUrl || null, initials(emp.name));
                document.getElementById('empPhotoHint').textContent = 'Carregue uma nova foto para substituir a actual.';
                populateReportsToSelect(emp.id);
                document.getElementById('empReportsTo').value = emp.reportsTo || '';
                document.getElementById('empDeleteBtn').hidden = false;
                document.getElementById('employeeModalTitle').textContent = 'Editar funcionário';
                refreshEmpActiveContract(emp.id);
            }
        } else {
            document.getElementById('empStartDate').value = todayIso();
            document.getElementById('empPhotoHint').textContent = 'Pode carregar a foto agora ou depois de gravar.';
            populateReportsToSelect(null);
            refreshEmpActiveContract(null);
        }

        empModal().classList.add('is-open');
        empModal().setAttribute('aria-hidden', 'false');
        setTimeout(() => document.getElementById('empFirstName').focus(), 100);
    }
    function closeEmpModal() {
    if (document.activeElement && empModal()?.contains(document.activeElement)) {
        document.activeElement.blur();
    }
    empModal().classList.remove('is-open');
    empModal().setAttribute('aria-hidden', 'true');
}

    function initEmpModal() {
        initEmpTabs();
        initEmpPhoto();
        document.getElementById('hrAddBtn')?.addEventListener('click', () => openEmpModal());
        empModal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeEmpModal));

        empForm()?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = empForm();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const idVal = fd.get('id');
            const body = {
                first_name: (fd.get('first_name') || '').trim(),
                last_name: (fd.get('last_name') || '').trim(),
                employee_number: (fd.get('employee_number') || '').trim() || null,
                gender: fd.get('gender') || null,
                nationality: (fd.get('nationality') || '').trim() || null,
                marital_status: fd.get('marital_status') || null,
                role: fd.get('role').trim(),
                department_id: fd.get('department') ? Number(fd.get('department')) : null,
                status: fd.get('status') || 'online',
                email: (fd.get('email') || '').trim(),
                phone: (fd.get('phone') || '').trim() || null,
                mobile: (fd.get('mobile') || '').trim() || null,
                address: (fd.get('address') || '').trim() || null,
                province: fd.get('province') || null,
                city: (fd.get('city') || '').trim() || null,
                birth_date: fd.get('birth') || null,
                reports_to: fd.get('reportsTo') ? Number(fd.get('reportsTo')) : null,
                bi_number: (fd.get('bi_number') || '').trim() || null,
                bi_expiry: fd.get('bi_expiry') || null,
                nif: (fd.get('nif') || '').trim() || null,
                niss: (fd.get('niss') || '').trim() || null,
                passport_number: (fd.get('passport_number') || '').trim() || null,
                passport_expiry: fd.get('passport_expiry') || null,
                bank_name: (fd.get('bank_name') || '').trim() || null,
                bank_account: (fd.get('bank_account') || '').trim() || null,
                iban: (fd.get('iban') || '').trim() || null
            };
            try {
                const res = await apiFetch(
                    idVal ? `${API_BASE.RH}/employees/${idVal}` : `${API_BASE.RH}/employees`,
                    {
                        method: idVal ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    }
                );
                if (!res.ok) {
                    const errBody = await res.json().catch(() => ({}));
                    throw new Error(errBody.detail || 'Falha ao guardar funcionário');
                }
                const savedEmp = await res.json();
                await uploadEmpPhotoIfSelected(savedEmp.id);
                if (!idVal) {
                    try {
                        await apiFetch(`${API_BASE.DOCS}/employees/${savedEmp.id}/ensure-folder`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ employee_name: savedEmp.full_name }),
                        });
                    } catch (e) {}
                }
                await loadEmployees();
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível guardar o funcionário.');
                return;
            }
            renderEmployees();
            renderDepartments();
            closeEmpModal();
            UIToast.success(idVal ? 'Funcionário actualizado com sucesso!' : 'Funcionário criado com sucesso!');
        });

        document.getElementById('empDeleteBtn')?.addEventListener('click', async () => {
            const id = Number(document.getElementById('empId').value);
            if (!id) return;
            if (!await UIModal.confirm('Apagar este funcionário? Esta acção não pode ser desfeita.', { danger: true, okLabel: 'Apagar' })) return;
            try {
                const res = await apiFetch(`${API_BASE.RH}/employees/${id}`, { method: 'DELETE' });
                if (!res.ok) throw new Error('Falha ao apagar funcionário');
                await loadEmployees();
            } catch (err) {
                await UIModal.alert('Não foi possível apagar o funcionário.');
                return;
            }
            renderEmployees();
            renderDepartments();
            closeEmpModal();
            UIToast.success('Funcionário apagado com sucesso!');
        });
    }

    /* ---------- Documentos do funcionário (MinIO via rh-service) ---------- */
    const empDocsModal = () => document.getElementById('empDocsModal');
    const empDocsList = () => document.getElementById('empDocsList');
    let currentEmpDocsId = null;

    function openEmpDocsModal(empId) {
        currentEmpDocsId = empId;
        const m = empDocsModal();
        if (!m) return;
        m.classList.add('is-open');
        m.setAttribute('aria-hidden', 'false');
        loadEmpDocs(empId);
    }
    function closeEmpDocsModal() {
        const m = empDocsModal();
        if (!m) return;
        m.classList.remove('is-open');
        m.setAttribute('aria-hidden', 'true');
        currentEmpDocsId = null;
    }

    async function loadEmpDocs(empId) {
        const list = empDocsList();
        if (!list) return;
        list.innerHTML = '<li class="attach-empty">A carregar...</li>';
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/documents`);
            if (!res.ok) throw new Error('Falha ao carregar documentos');
            const items = await res.json();
            if (!items.length) {
                list.innerHTML = '<li class="attach-empty">Sem documentos ainda.</li>';
                return;
            }
            const withUrls = await Promise.all(items.map(async (d) => {
                try {
                    const r = await apiFetch(`${API_BASE.RH}/documents/${d.id}/presigned-url`);
                    const j = r.ok ? await r.json() : {};
                    return { ...d, url: j.url || null };
                } catch (e) {
                    return { ...d, url: null };
                }
            }));
            list.innerHTML = withUrls.map(d => `
                <li class="attach-item">
                    <span class="attach-name">${escapeHtml(d.filename)} <span class="attach-tag">${escapeHtml(d.document_type)}</span></span>
                    <span class="attach-item-actions">
                        ${d.url ? `<a class="attach-download" href="${d.url}" target="_blank" rel="noopener">Transferir</a>` : ''}
                        <button type="button" class="attach-remove" data-doc-remove="${d.id}" aria-label="Apagar documento">×</button>
                    </span>
                </li>
            `).join('');
        } catch (e) {
            list.innerHTML = '<li class="attach-empty">Não foi possível carregar os documentos.</li>';
        }
    }

    function initEmpDocsModal() {
        empDocsModal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeEmpDocsModal));

        empDocsList()?.addEventListener('click', async (e) => {
            const rm = e.target.closest('[data-doc-remove]');
            if (!rm || !currentEmpDocsId) return;
            if (!await UIModal.confirm('Apagar este documento?', { danger: true, okLabel: 'Apagar' })) return;
            try {
                const res = await apiFetch(`${API_BASE.RH}/documents/${rm.dataset.docRemove}`, { method: 'DELETE' });
                if (!res.ok) throw new Error('Falha ao apagar documento');
                await loadEmpDocs(currentEmpDocsId);
                UIToast.success('Documento apagado com sucesso!');
            } catch (err) {
                await UIModal.alert('Não foi possível apagar o documento.');
            }
        });

        document.getElementById('empDocsUploadForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (!currentEmpDocsId) return;
            const fileInput = document.getElementById('empDocsFile');
            const typeInput = document.getElementById('empDocsType');
            const file = fileInput.files[0];
            if (!file) return;
            const docType = (typeInput.value || 'outro').trim() || 'outro';
            const fd = new FormData();
            fd.append('file', file);
            const btn = e.target.querySelector('button[type="submit"]');
            btn?.classList.add('is-loading');
            try {
                const res = await apiFetch(`${API_BASE.RH}/employees/${currentEmpDocsId}/documents?document_type=${encodeURIComponent(docType)}`, {
                    method: 'POST',
                    body: fd,
                });
                if (!res.ok) throw new Error('Falha ao enviar ficheiro');
                fileInput.value = '';
                typeInput.value = '';
                await loadEmpDocs(currentEmpDocsId);
                UIToast.success('Documento enviado com sucesso!');
            } catch (err) {
                await UIModal.alert('Falha ao enviar o ficheiro.');
            } finally {
                btn?.classList.remove('is-loading');
            }
        });
    }

    /* ---------- Department modal ---------- */
    const deptModal = () => document.getElementById('deptModal');
    const deptForm = () => document.getElementById('deptForm');

    function openDeptModal(id = null) {
        const form = deptForm();
        form.reset();
        document.getElementById('deptId').value = '';
        document.getElementById('deptDeleteBtn').hidden = true;
        document.getElementById('deptModalTitle').textContent = 'Novo departamento';
        setColorPicker('#3B82F6');

        if (id) {
            const d = dept(id);
            if (d) {
                document.getElementById('deptId').value = d.id;
                document.getElementById('deptName').value = d.name;
                setColorPicker(d.color);
                document.getElementById('deptDeleteBtn').hidden = false;
                document.getElementById('deptModalTitle').textContent = 'Editar departamento';
            }
        }
        deptModal().classList.add('is-open');
        deptModal().setAttribute('aria-hidden', 'false');
        setTimeout(() => document.getElementById('deptName').focus(), 100);
    }
    function closeDeptModal() {
    if (document.activeElement && deptModal()?.contains(document.activeElement)) {
        document.activeElement.blur();
    }
    deptModal().classList.remove('is-open');
    deptModal().setAttribute('aria-hidden', 'true');
}
    function setColorPicker(color) {
        document.getElementById('deptColor').value = color;
        document.querySelectorAll('#deptColorPicker .color-dot').forEach(dot => {
            dot.classList.toggle('is-selected', dot.dataset.color === color);
        });
    }

    function initDeptModal() {
        document.getElementById('hrDeptBtn')?.addEventListener('click', () => openDeptModal());
        deptModal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeDeptModal));

        document.getElementById('deptColorPicker')?.addEventListener('click', (e) => {
            const dot = e.target.closest('.color-dot');
            if (!dot) return;
            setColorPicker(dot.dataset.color);
        });

        deptForm()?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = deptForm();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const idVal = fd.get('id');
            const body = {
                name: fd.get('name').trim(),
                color: fd.get('color') || '#6B7280'
            };
            try {
                const res = await apiFetch(
                    idVal ? `${API_BASE.RH}/departments/${idVal}` : `${API_BASE.RH}/departments`,
                    {
                        method: idVal ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    }
                );
                if (!res.ok) throw new Error('Falha ao guardar departamento');
                const savedDept = await res.json();
                if (!idVal) {
                    try {
                        await apiFetch(`${API_BASE.DOCS}/departments/${savedDept.id}/ensure-folder`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ department_name: savedDept.name }),
                        });
                    } catch (e) {}
                }
                await loadDepartments();
            } catch (err) {
                await UIModal.alert('Não foi possível guardar o departamento.');
                return;
            }
            populateDeptFilter();
            populateDeptSelect();
            renderEmployees();
            renderDepartments();
            renderKPIs();
            closeDeptModal();
            UIToast.success(idVal ? 'Departamento actualizado com sucesso!' : 'Departamento criado com sucesso!');
        });

        document.getElementById('deptDeleteBtn')?.addEventListener('click', async () => {
            const id = Number(document.getElementById('deptId').value);
            if (!id) return;
            if (!await UIModal.confirm('Apagar este departamento?', { danger: true, okLabel: 'Apagar' })) return;
            try {
                const res = await apiFetch(`${API_BASE.RH}/departments/${id}`, { method: 'DELETE' });
                if (!res.ok) {
                    if (res.status === 409) {
                        await UIModal.alert('Não pode apagar este departamento: há funcionários atribuídos. Mude-os primeiro para outro departamento.');
                    } else {
                        throw new Error('Falha ao apagar departamento');
                    }
                    return;
                }
                await loadDepartments();
            } catch (err) {
                await UIModal.alert('Não foi possível apagar o departamento.');
                return;
            }
            populateDeptFilter();
            populateDeptSelect();
            renderEmployees();
            renderDepartments();
            renderKPIs();
            closeDeptModal();
            UIToast.success('Departamento apagado com sucesso!');
        });
    }

    /* ---------- Click handlers (edit) ---------- */
    function initRowClicks() {
        document.addEventListener('click', (e) => {
            const editBtn = e.target.closest('[data-edit]');
            if (editBtn) {
                e.stopPropagation();
                openEmpModal(Number(editBtn.dataset.edit));
                return;
            }
            const docsBtn = e.target.closest('[data-emp-docs]');
            if (docsBtn) {
                e.stopPropagation();
                openEmpDocsModal(Number(docsBtn.dataset.empDocs));
                return;
            }
            const editDept = e.target.closest('[data-edit-dept]');
            if (editDept) {
                e.stopPropagation();
                openDeptModal(Number(editDept.dataset.editDept));
                return;
            }
            const card = e.target.closest('.emp-card');
            if (card && !e.target.closest('.status-quick')) {
                openEmpModal(Number(card.dataset.id));
            }
        });

        document.getElementById('hrSearch')?.addEventListener('input', renderEmployees);
        document.getElementById('hrDeptFilter')?.addEventListener('change', renderEmployees);
        document.getElementById('hrStatusFilter')?.addEventListener('change', renderEmployees);

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                empModal()?.classList.remove('is-open');
                deptModal()?.classList.remove('is-open');
                document.querySelectorAll('.status-quick.is-open').forEach(el => el.classList.remove('is-open'));
            }
        });
    }


    
/* ---------- AUSÊNCIAS / FÉRIAS (rh-service: /employees/{id}/leave) ----------
   O backend não distingue "ausência" de "férias": ambos os painéis leem e
   escrevem no mesmo endpoint de leave_requests. O tipo (falta justificada,
   injustificada, atestado médico, outro) é guardado como prefixo textual em
   `reason`, ex: "[Falta injustificada] assuntos pessoais". Pedidos vindos do
   painel de Férias não usam prefixo (mostram-se como "Outro" na Ausências). */
const ABS_TYPE = {
    justified: { label: 'Falta justificada', color: '#EF4444', cls: 'justified' },
    unjustified: { label: 'Falta injustificada', color: '#F59E0B', cls: 'unjustified' },
    medical: { label: 'Atestado médico', color: '#3B82F6', cls: 'medical' },
    other: { label: 'Outro', color: '#8B5CF6', cls: 'other' }
};

let leavesCache = [];

function currentMonthIso() {
    if (state.ui.absMonth) return state.ui.absMonth;
    const d = new Date();
    state.ui.absMonth = monthIso(d.getFullYear(), d.getMonth());
    return state.ui.absMonth;
}

function encodeLeaveReason(type, reason) {
    const label = ABS_TYPE[type]?.label || ABS_TYPE.other.label;
    return reason ? `[${label}] ${reason}` : `[${label}]`;
}
function decodeLeaveReason(full) {
    const m = /^\[([^\]]+)\]\s*(.*)$/.exec(full || '');
    if (!m) return { type: 'other', reason: full || '' };
    const key = Object.keys(ABS_TYPE).find(k => ABS_TYPE[k].label === m[1]) || 'other';
    return { type: key, reason: m[2] || '' };
}

async function loadAllLeaves() {
    if (!state.employees.length) { leavesCache = []; return; }
    try {
        const perEmployee = await Promise.all(state.employees.map(async (emp) => {
            try {
                const res = await apiFetch(`${API_BASE.RH}/employees/${emp.id}/leave`);
                if (!res.ok) return [];
                const items = await res.json();
                return items.map(l => ({
                    id: l.id,
                    employee: emp.id,
                    start: (l.start_date || '').slice(0, 10),
                    end: (l.end_date || l.start_date || '').slice(0, 10),
                    status: l.status || 'pending',
                    ...decodeLeaveReason(l.reason)
                }));
            } catch (e) { return []; }
        }));
        leavesCache = perEmployee.flat();
    } catch (e) {
        leavesCache = [];
    }
}

function leaveDaysInMonth(leave, year, month) {
    const days = [];
    if (!leave.start) return days;
    const s = isoToDate(leave.start);
    const e = leave.end ? isoToDate(leave.end) : s;
    const cur = new Date(s);
    while (cur <= e) {
        if (cur.getFullYear() === year && cur.getMonth() === month) days.push(cur.getDate());
        cur.setDate(cur.getDate() + 1);
    }
    return days;
}

function renderAbsences() {
    loadAllLeaves().then(() => {
        renderAbsCalendar();
        renderAbsList();
    });
}

function renderAbsCalendar() {
    const host = document.getElementById('absCalendar');
    const label = document.getElementById('absMonthLabel');
    const counter = document.getElementById('absMonthCount');
    if (!host) return;

    const monthStart = isoToDate(currentMonthIso());
    label.textContent = monthLabel(currentMonthIso());

    const y = monthStart.getFullYear(), m = monthStart.getMonth();
    const firstDow = (monthStart.getDay() + 6) % 7;
    const daysInMonth = new Date(y, m+1, 0).getDate();
    const todayStr = todayIso();
    const cells = [];

    for (let i = 0; i < firstDow; i++) cells.push('<div class="cal-day cal-day--empty"></div>');

    let monthAbsCount = 0;
    for (let d = 1; d <= daysInMonth; d++) {
        const iso = `${y}-${pad(m+1)}-${pad(d)}`;
        const dayDate = new Date(y, m, d);
        const isWeekend = dayDate.getDay() === 0 || dayDate.getDay() === 6;
        const dayAbs = leavesCache.filter(l => leaveDaysInMonth(l, y, m).includes(d));
        monthAbsCount += dayAbs.length;
        const dots = dayAbs.slice(0, 4).map(a => `<span class="dot dot--${ABS_TYPE[a.type]?.cls || 'other'}"></span>`).join('');
        const more = dayAbs.length > 4 ? `<span class="dot" style="background:#9CA3AF">+${dayAbs.length - 4}</span>` : '';
        const cls = [
            'cal-day',
            iso === todayStr ? 'cal-day--today' : '',
            isWeekend ? 'cal-day--weekend' : ''
        ].filter(Boolean).join(' ');
        cells.push(`
            <div class="${cls}" data-date="${iso}">
                <span class="cal-day-num">${d}</span>
                ${dots ? `<div class="cal-day-dots">${dots}${more}</div>` : ''}
            </div>
        `);
    }
    host.innerHTML = cells.join('');
    counter.textContent = `${monthAbsCount} ${monthAbsCount === 1 ? 'ausência' : 'ausências'}`;
}

function renderAbsList() {
    const host = document.getElementById('absList');
    if (!host) return;
    const monthDate = isoToDate(currentMonthIso());
    const items = leavesCache
        .filter(a => leaveDaysInMonth(a, monthDate.getFullYear(), monthDate.getMonth()).length > 0)
        .sort((a, b) => b.start.localeCompare(a.start));

    if (!items.length) {
        host.innerHTML = '<li class="abs-empty">Nenhuma ausência este mês.</li>';
        return;
    }

    host.innerHTML = items.map(a => {
        const emp = state.employees.find(e => e.id === a.employee);
        const type = ABS_TYPE[a.type] || ABS_TYPE.other;
        return `
            <li class="abs-item" data-abs="${a.id}" style="--abs-color: ${type.color}">
                <span class="avatar ${avatarTone(emp?.id || 0)}">${avatarInner(emp)}</span>
                <div class="abs-item-info">
                    <div class="abs-item-name">${escapeHtml(emp?.name || 'Funcionário removido')}</div>
                    <div class="abs-item-meta">${type.label}${a.reason ? ' · ' + escapeHtml(a.reason) : ''}</div>
                </div>
                <div class="abs-item-date">${fmtDate(a.start)}${a.end && a.end !== a.start ? ' → ' + fmtDate(a.end) : ''}</div>
            </li>
        `;
    }).join('');
}

function initAbsModal() {
    document.getElementById('absMonthPrev')?.addEventListener('click', () => {
        const d = isoToDate(currentMonthIso());
        d.setMonth(d.getMonth() - 1);
        state.ui.absMonth = monthIso(d.getFullYear(), d.getMonth());
        renderAbsences();
    });
    document.getElementById('absMonthNext')?.addEventListener('click', () => {
        const d = isoToDate(currentMonthIso());
        d.setMonth(d.getMonth() + 1);
        state.ui.absMonth = monthIso(d.getFullYear(), d.getMonth());
        renderAbsences();
    });

    const modal = () => document.getElementById('absenceModal');
    const form = () => document.getElementById('absenceForm');

    function open(prefillDate = null) {
        populateEmployeeSelect('absEmployee');
        const f = form();
        f.reset();
        document.getElementById('absId').value = '';
        document.getElementById('absDeleteBtn').hidden = true;
        document.getElementById('absenceModalTitle').textContent = 'Marcar falta';
        document.getElementById('absDate').value = prefillDate || todayIso();
        modal().classList.add('is-open');
        modal().setAttribute('aria-hidden', 'false');
        setTimeout(() => document.getElementById('absEmployee').focus(), 100);
    }
    function close() {
        if (document.activeElement && modal()?.contains(document.activeElement)) document.activeElement.blur();
        modal().classList.remove('is-open');
        modal().setAttribute('aria-hidden', 'true');
    }

    document.getElementById('absAddBtn')?.addEventListener('click', () => open());
    modal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', close));

    document.getElementById('absCalendar')?.addEventListener('click', (e) => {
        const day = e.target.closest('[data-date]');
        if (day && !day.classList.contains('cal-day--empty')) open(day.dataset.date);
    });

    form()?.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!form().checkValidity()) { form().reportValidity(); return; }
        const fd = new FormData(form());
        const empId = Number(fd.get('employee'));
        const date = fd.get('date');
        const type = fd.get('type') || 'other';
        const reason = (fd.get('reason') || '').trim();
        if (!empId) return;
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/leave`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ start_date: date, end_date: date, reason: encodeLeaveReason(type, reason) })
            });
            if (!res.ok) throw new Error('Falha ao marcar falta');
        } catch (err) {
            await UIModal.alert('Não foi possível marcar a falta.');
            return;
        }
        renderAbsences();
        close();
        UIToast.success('Falta registada com sucesso!');
    });
}


/* ---------- FÉRIAS ---------- */
function vacUsedThisYear(empId) {
    const yr = new Date().getFullYear();
    return leavesCache
        .filter(v => v.employee === empId && v.status === 'approved' && isoToDate(v.start).getFullYear() === yr)
        .reduce((sum, v) => sum + businessDaysBetween(v.start, v.end), 0);
}

function renderVacations() {
    loadAllLeaves().then(() => {
        renderVacKPIs();
        renderVacBalance();
        renderVacList();
    });
}

function renderVacKPIs() {
    const todayStr = todayIso();
    const onVac = leavesCache.filter(v => v.status === 'approved' && v.start <= todayStr && v.end >= todayStr).length;
    const yr = new Date().getFullYear();
    const used = leavesCache
        .filter(v => v.status === 'approved' && isoToDate(v.start).getFullYear() === yr)
        .reduce((s, v) => s + businessDaysBetween(v.start, v.end), 0);
    const pending = leavesCache.filter(v => v.status === 'pending').length;
    document.getElementById('vacKpiPending').textContent = pending;
    document.getElementById('vacKpiOn').textContent = onVac;
    document.getElementById('vacKpiUsed').textContent = used;
}

function renderVacBalance() {
    const host = document.getElementById('vacBalance');
    if (!host) return;
    if (!state.employees.length) {
        host.innerHTML = '<li class="abs-empty">Sem funcionários.</li>';
        return;
    }
    host.innerHTML = state.employees.map(e => {
        const used = vacUsedThisYear(e.id);
        const left = Math.max(0, VAC_LIMIT_PER_YEAR - used);
        const pct = Math.min(100, Math.round(used / VAC_LIMIT_PER_YEAR * 100));
        const cls = pct < 50 ? 'low' : pct < 80 ? 'mid' : 'high';
        return `
            <li class="vac-row">
                <span class="avatar ${avatarTone(e.id)}">${avatarInner(e)}</span>
                <span class="vac-row-name">${escapeHtml(e.name)}</span>
                <div class="vac-bar"><span class="vac-bar-fill vac-bar-fill--${cls}" data-w="${pct}"></span></div>
                <span class="vac-row-text">${used}/${VAC_LIMIT_PER_YEAR}</span>
            </li>
        `;
    }).join('');
    requestAnimationFrame(() => {
        host.querySelectorAll('.vac-bar-fill').forEach(f => f.style.width = f.dataset.w + '%');
    });
}

function renderVacList() {
    const host = document.getElementById('vacList');
    if (!host) return;
    const items = leavesCache.slice().sort((a, b) => b.start.localeCompare(a.start));

    if (!items.length) {
        host.innerHTML = '<li class="abs-empty">Nenhum pedido registado.</li>';
        return;
    }

    const VAC_STATUS_LABEL = { pending: 'Pendente', approved: 'Aprovado', rejected: 'Rejeitado' };

    host.innerHTML = items.map(v => {
        const emp = state.employees.find(e => e.id === v.employee);
        const days = businessDaysBetween(v.start, v.end);
        const status = v.status || 'pending';
        return `
            <li class="vac-card" data-vac="${v.id}" data-vac-employee="${v.employee}">
                <span class="avatar ${avatarTone(emp?.id || 0)}">${avatarInner(emp)}</span>
                <div class="vac-card-info">
                    <div class="vac-card-name">${escapeHtml(emp?.name || '—')}</div>
                    <div class="vac-card-period">${fmtDate(v.start)} → ${fmtDate(v.end)}${v.reason ? ' · ' + escapeHtml(v.reason) : ''}</div>
                </div>
                <span class="vac-card-days">${days} ${days === 1 ? 'dia' : 'dias'}</span>
                <span class="vac-status vac-status--${status}">${VAC_STATUS_LABEL[status] || status}</span>
                ${status === 'pending' ? `
                    <div class="vac-card-actions">
                        <button type="button" class="vac-action vac-action--approve" data-vac-approve="${v.id}" aria-label="Aprovar" title="Aprovar">
                            <svg viewBox="0 0 16 16" fill="none"><path d="M3 8.5 L6.5 12 L13 4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        </button>
                        <button type="button" class="vac-action vac-action--reject" data-vac-reject="${v.id}" aria-label="Rejeitar" title="Rejeitar">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
                        </button>
                    </div>
                ` : ''}
            </li>
        `;
    }).join('');
}

function initVacModal() {
    const modal = () => document.getElementById('vacationModal');
    const form = () => document.getElementById('vacationForm');

    function updateDaysHint() {
        const s = document.getElementById('vacStart').value;
        const e = document.getElementById('vacEnd').value;
        const hint = document.getElementById('vacDaysHint');
        if (s && e) {
            const days = businessDaysBetween(s, e);
            hint.innerHTML = days > 0
                ? `Total: <strong>${days} ${days === 1 ? 'dia útil' : 'dias úteis'}</strong> (fins-de-semana excluídos).`
                : 'A data de fim deve ser igual ou posterior à de início.';
        } else {
            hint.textContent = 'Selecione as datas para calcular os dias úteis.';
        }
    }

    function open() {
        populateEmployeeSelect('vacEmployee');
        const f = form();
        f.reset();
        document.getElementById('vacId').value = '';
        document.getElementById('vacDeleteBtn').hidden = true;
        document.getElementById('vacationModalTitle').textContent = 'Novo pedido de férias';
        document.getElementById('vacStart').value = todayIso();
        updateDaysHint();
        modal().classList.add('is-open');
        modal().setAttribute('aria-hidden', 'false');
        setTimeout(() => document.getElementById('vacEmployee').focus(), 100);
    }
    function close() {
        if (document.activeElement && modal()?.contains(document.activeElement)) document.activeElement.blur();
        modal().classList.remove('is-open');
        modal().setAttribute('aria-hidden', 'true');
    }

    document.getElementById('vacAddBtn')?.addEventListener('click', () => open());
    modal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', close));
    document.getElementById('vacStart')?.addEventListener('change', updateDaysHint);
    document.getElementById('vacEnd')?.addEventListener('change', updateDaysHint);

    form()?.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!form().checkValidity()) { form().reportValidity(); return; }
        const fd = new FormData(form());
        const empId = Number(fd.get('employee'));
        const start = fd.get('start'), end = fd.get('end');
        const note = (fd.get('note') || '').trim();
        if (end < start) { await UIModal.alert('A data de fim deve ser igual ou posterior à de início.'); return; }
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/leave`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ start_date: start, end_date: end, reason: note || 'Férias' })
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || 'Falha ao criar pedido');
            }
        } catch (err) {
            await UIModal.alert(err.message || 'Não foi possível criar o pedido de férias.');
            return;
        }
        renderVacations();
        close();
        UIToast.success('Pedido de férias criado com sucesso! Aguarda aprovação do RH.');
    });

    document.getElementById('vacList')?.addEventListener('click', async (e) => {
        const approveBtn = e.target.closest('[data-vac-approve]');
        const rejectBtn = e.target.closest('[data-vac-reject]');
        const btn = approveBtn || rejectBtn;
        if (!btn) return;

        const card = btn.closest('[data-vac]');
        const leaveId = card?.dataset.vac;
        const empId = card?.dataset.vacEmployee;
        if (!leaveId || !empId) return;

        const status = approveBtn ? 'approved' : 'rejected';
        if (status === 'rejected' && !await UIModal.confirm('Rejeitar este pedido de férias?')) return;

        try {
            const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/leave/${leaveId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status })
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || 'Falha ao actualizar pedido');
            }
        } catch (err) {
            await UIModal.alert(err.message || 'Não foi possível actualizar o pedido de férias.');
            return;
        }
        renderVacations();
        UIToast.success(status === 'approved' ? 'Pedido de férias aprovado com sucesso!' : 'Pedido de férias rejeitado.');
    });
}

/* ---------- PRESENÇA ---------- */
function todayAttendance() {
    const day = todayIso();
    if (!state.attendance[day]) state.attendance[day] = {};
    return state.attendance[day];
}

function isOnLeaveToday(empId) {
    const today = todayIso();
    return leavesCache.some(l => l.employee === empId && l.status !== 'rejected' && l.start <= today && l.end >= today);
}

function renderAttendance() {
    renderPRDate();
    loadAllLeaves().then(() => {
        renderPRList();
        renderPRKPIs();
    });
    startPRTick();
}

function renderPRDate() {
    const el = document.getElementById('prClockDate');
    if (!el) return;
    const d = new Date();
    const days = ['Domingo','Segunda','Terça','Quarta','Quinta','Sexta','Sábado'];
    const months = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
    el.textContent = `${days[d.getDay()]}, ${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
}

function renderPRList() {
    const host = document.getElementById('prList');
    if (!host) return;
    if (!state.employees.length) {
        host.innerHTML = '<li class="abs-empty">Sem funcionários.</li>';
        return;
    }
    const today = todayAttendance();
    host.innerHTML = state.employees.map(e => {
        const att = today[e.id] || {};
        const hasIn = !!att.in;
        const hasOut = !!att.out;
        const working = hasIn && !hasOut;
        const done = hasIn && hasOut;
        const onLeave = !hasIn && isOnLeaveToday(e.id);

        let durationHtml = '';
        if (working) {
            durationHtml = `<span class="pr-duration pr-duration--live" data-live-in="${att.in}">${liveDuration(att.in)}</span>`;
        } else if (done) {
            durationHtml = `<span class="pr-duration">${staticDuration(att.in, att.out)}</span>`;
        } else {
            durationHtml = `<span class="pr-duration" style="color: var(--color-text-muted)">—</span>`;
        }

        return `
            <li class="pr-row" data-emp="${e.id}">
                <span class="avatar ${avatarTone(e.id)}">${avatarInner(e)}</span>
                <span class="pr-name">${escapeHtml(e.name)}</span>
                <span class="pr-time ${hasIn ? '' : 'pr-time--off'}">
                    <svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M8 4.5 V8 L10.5 9.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>
                    ${hasIn ? `<strong>${att.in}</strong>` : '—'}
                </span>
                <span class="pr-time ${hasOut ? '' : 'pr-time--off'}">
                    <svg viewBox="0 0 16 16"><path d="M2 8 H12 M9 5 L12 8 L9 11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>
                    ${hasOut ? `<strong>${att.out}</strong>` : '—'}
                </span>
                ${durationHtml}
                ${working
                    ? `<button class="pr-btn pr-btn--out" data-pr-out="${e.id}">Saída</button>`
                    : (done
                        ? `<span class="pr-status-pill pr-status-pill--out">Saiu</span>`
                        : (onLeave
                            ? `<span class="pr-status-pill pr-status-pill--absent">Falta</span>`
                            : `<button class="pr-btn pr-btn--in" data-pr-in="${e.id}">Entrada</button>`
                        )
                    )
                }
            </li>
        `;
    }).join('');
}

function nowHm() {
    const d = new Date();
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function hmToMinutes(hm) {
    const [h, m] = hm.split(':').map(Number);
    return h * 60 + m;
}
function liveDuration(inHm) {
    const start = hmToMinutes(inHm);
    const now = hmToMinutes(nowHm());
    return formatMins(Math.max(0, now - start));
}
function staticDuration(inHm, outHm) {
    return formatMins(Math.max(0, hmToMinutes(outHm) - hmToMinutes(inHm)));
}
function formatMins(mins) {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return `${h}h${m.toString().padStart(2,'0')}`;
}

function renderPRKPIs() {
    const today = todayAttendance();
    let active = 0, totalMins = 0;
    state.employees.forEach(e => {
        const att = today[e.id];
        if (!att) return;
        if (att.in && !att.out) { active++; totalMins += hmToMinutes(nowHm()) - hmToMinutes(att.in); }
        else if (att.in && att.out) totalMins += hmToMinutes(att.out) - hmToMinutes(att.in);
    });
    document.getElementById('prKpiActive').textContent = active;
    document.getElementById('prKpiHours').textContent = formatMins(Math.max(0, totalMins));

    // Média semanal: últimos 7 dias
    let weekMins = 0, weekCount = 0;
    for (let i = 0; i < 7; i++) {
        const iso = addDaysIso(todayIso(), -i);
        const day = state.attendance[iso];
        if (!day) continue;
        Object.values(day).forEach(a => {
            if (a.in && a.out) { weekMins += hmToMinutes(a.out) - hmToMinutes(a.in); weekCount++; }
        });
    }
    document.getElementById('prKpiAvg').textContent = weekCount ? formatMins(Math.round(weekMins / weekCount)) : '0h00';
}

function startPRTick() {
    if (prTickInterval) clearInterval(prTickInterval);
    prTickInterval = setInterval(() => {
        const pane = document.querySelector('[data-hrsubpane="presenca"]');
        if (!pane?.classList.contains('is-active')) { clearInterval(prTickInterval); prTickInterval = null; return; }
        document.querySelectorAll('[data-live-in]').forEach(el => {
            el.textContent = liveDuration(el.dataset.liveIn);
        });
        renderPRKPIs();
    }, 30000);
}

function initAttendance() {
    document.getElementById('prList')?.addEventListener('click', async (e) => {
        const inBtn = e.target.closest('[data-pr-in]');
        if (inBtn) {
            const id = Number(inBtn.dataset.prIn);
            if (isOnLeaveToday(id)) {
                await UIModal.alert('Este funcionário tem uma falta registada para hoje — não é possível marcar presença.');
                return;
            }
            try {
                const res = await apiFetch(`${API_BASE.RH}/employees/${id}/checkin`, { method: 'POST' });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    throw new Error(data.detail || 'Falha no check-in');
                }
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível registar a entrada.');
                return;
            }
            const day = todayAttendance();
            day[id] = { in: nowHm(), out: null };
            state.attSeen[id] = true;
            save();
            renderPRList();
            renderPRKPIs();
            return;
        }
        const outBtn = e.target.closest('[data-pr-out]');
        if (outBtn) {
            const id = Number(outBtn.dataset.prOut);
            try {
                const res = await apiFetch(`${API_BASE.RH}/employees/${id}/checkout`, { method: 'POST' });
                if (!res.ok) throw new Error('Falha no check-out');
            } catch (err) {
                await UIModal.alert('Não foi possível registar a saída.');
                return;
            }
            const day = todayAttendance();
            if (day[id]?.in) day[id].out = nowHm();
            save();
            renderPRList();
            renderPRKPIs();
            return;
        }
    });
}



function populateEmployeeSelect(selectId) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = state.employees.map(e => `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join('');
    if (current) sel.value = current;
}

/* ============================================================
   HELPERS COMUNS AOS NOVOS MÓDULOS
   ============================================================ */

function empName(id) {
    return state.employees.find(e => e.id === Number(id))?.name || '—';
}

function fmtKz(val) {
    return Number(val || 0).toLocaleString('pt-AO') + ' Kz';
}

function calcIRT(gross) {
    if (gross <= 70000)  return 0;
    if (gross <= 100000) return (gross - 70000) * 0.10;
    if (gross <= 150000) return 3000  + (gross - 100000) * 0.13;
    if (gross <= 200000) return 9500  + (gross - 150000) * 0.16;
    if (gross <= 300000) return 17500 + (gross - 200000) * 0.18;
    if (gross <= 500000) return 35500 + (gross - 300000) * 0.19;
    return 73500 + (gross - 500000) * 0.20;
}

function populateReportsToSelect(excludeId) {
    const sel = document.getElementById('empReportsTo');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">— Topo da hierarquia —</option>' +
        state.employees
            .filter(e => e.id !== excludeId)
            .map(e => `<option value="${e.id}">${escapeHtml(e.name)}</option>`)
            .join('');
    sel.value = cur;
}

function populateYearFilter() {
    const sel = document.getElementById('payYearFilter');
    if (!sel) return;
    const cur = new Date().getFullYear();
    sel.innerHTML = [cur + 1, cur, cur - 1, cur - 2]
        .map(y => `<option value="${y}"${y === cur ? ' selected' : ''}>${y}</option>`)
        .join('');
}

function populateAllEmpSelects() {
    ['evalEmpFilter', 'evalEmpSel',
     'trainEmpFilter', 'trainEmpSel', 'onbEmpFilter',
     'payEmpFilter', 'payEmpSel', 'ctrEmpFilter', 'ctrEmpSel'].forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const cur = sel.value;
        const blank = id.endsWith('Filter') ? '<option value="">Todos</option>' : '<option value="">— Escolher —</option>';
        sel.innerHTML = blank + state.employees.map(e =>
            `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join('');
        if (cur) sel.value = cur;
    });
    ['onbEmpFilter', 'evalEmpFilter', 'trainEmpFilter', 'payEmpFilter', 'ctrEmpFilter'].forEach(id => {
        const sel = document.getElementById(id);
        if (sel && !sel.querySelector('option[value=""]')) {
            sel.insertAdjacentHTML('afterbegin', '<option value="">Todos os funcionários</option>');
        }
    });
}


/* ============================================================
   ONBOARDING
   ============================================================ */

async function loadOnboardingFor(empId) {
    try {
        const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/onboarding`);
        if (!res.ok) throw new Error('Falha ao carregar onboarding');
        state.onboarding = await res.json();
    } catch (e) {
        state.onboarding = null;
    }
}

function renderOnboarding() {
    populateAllEmpSelects();
    const host = document.getElementById('onbList');
    if (!host) return;
    const empId = Number(document.getElementById('onbEmpFilter')?.value || 0);
    if (!empId) {
        host.innerHTML = `<div class="hr-empty-state"><svg viewBox="0 0 48 48" fill="none"><rect x="8" y="9" width="32" height="30" rx="2" stroke="currentColor" stroke-width="2"/><path d="M15 22 L21 28 L33 16" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg><h3>Seleccione um funcionário</h3><p>Escolha um funcionário no filtro para ver ou completar a checklist de onboarding.</p></div>`;
        return;
    }
    host.innerHTML = '<div class="hr-empty-state"><p>A carregar...</p></div>';
    loadOnboardingFor(empId).then(() => {
        const ob = state.onboarding;
        if (!ob) {
            host.innerHTML = `<div class="hr-empty-state"><h3>Não foi possível carregar</h3></div>`;
            return;
        }
        const items = ob.items || [];
        const done  = items.filter(i => i.done).length;
        const total = items.length;
        const pct   = total ? Math.round((done / total) * 100) : 0;
        host.innerHTML = `
        <article class="onb-card">
            <div class="onb-card-head">
                <span class="avatar ${avatarTone(empId)}">${avatarInnerById(empId)}</span>
                <div>
                    <div class="onb-emp-name">${escapeHtml(empName(empId))}</div>
                    <div class="onb-progress-label">${done}/${total} concluídos · ${pct}%</div>
                </div>
                <div class="onb-progress-bar-wrap">
                    <div class="onb-progress-bar" style="width:${pct}%"></div>
                </div>
            </div>
            <ul class="onb-checklist">
                ${items.map(item => `
                    <li class="onb-item${item.done ? ' is-done' : ''}">
                        <button class="onb-check" data-onb-item="${item.id}" aria-label="${item.done ? 'Desmarcar' : 'Marcar como feito'}">
                            <svg viewBox="0 0 16 16" fill="none">${item.done ? '<path d="M3 8 L6.5 11.5 L13 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' : ''}</svg>
                        </button>
                        <span>${escapeHtml(item.label)}</span>
                        <button class="onb-remove" data-onb-remove="${item.id}" aria-label="Remover item">×</button>
                    </li>`).join('')}
            </ul>
        </article>`;
    });
}

function initOnboarding() {
    document.getElementById('onbEmpFilter')?.addEventListener('change', renderOnboarding);
    document.getElementById('onbAddBtn')?.addEventListener('click', async () => {
        const empId = Number(document.getElementById('onbEmpFilter')?.value);
        if (!empId) { await UIModal.alert('Seleccione um funcionário no filtro para adicionar um item.'); return; }
        const label = (await UIModal.prompt('Novo item da checklist:') || '').trim();
        if (!label) return;
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/onboarding/items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label })
            });
            if (!res.ok) throw new Error('Falha ao adicionar item');
        } catch (err) {
            await UIModal.alert('Não foi possível adicionar o item.');
            return;
        }
        renderOnboarding();
    });
    document.addEventListener('click', async e => {
        const chk = e.target.closest('[data-onb-item]');
        if (chk) {
            const itemId = Number(chk.dataset.onbItem);
            const item = (state.onboarding?.items || []).find(i => i.id === itemId);
            if (!item) return;
            try {
                const res = await apiFetch(`${API_BASE.RH}/onboarding/items/${itemId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ done: !item.done })
                });
                if (!res.ok) throw new Error('Falha ao actualizar item');
            } catch (err) {
                await UIModal.alert('Não foi possível actualizar o item.');
                return;
            }
            renderOnboarding();
            return;
        }
        const rm = e.target.closest('[data-onb-remove]');
        if (rm) {
            if (!await UIModal.confirm('Remover este item da checklist?')) return;
            try {
                const res = await apiFetch(`${API_BASE.RH}/onboarding/items/${rm.dataset.onbRemove}`, { method: 'DELETE' });
                if (!res.ok) throw new Error('Falha ao remover item');
            } catch (err) {
                await UIModal.alert('Não foi possível remover o item.');
                return;
            }
            renderOnboarding();
        }
    });
}

/* ============================================================
   CONTRATOS
   ============================================================ */

function fmtAoa(val) {
    return Number(val || 0).toLocaleString('pt-AO') + ' AOA';
}

function CONTRACT_TYPE_LABEL(type) {
    return {
        tempo_indeterminado: 'Tempo Indeterminado',
        termo_certo: 'Termo Certo',
        prestacao: 'Prestação de Serviços',
        estagio: 'Estágio'
    }[type] || type || '—';
}

let contractsCache = [];

async function loadAllContracts() {
    try {
        const res = await apiFetch(`${API_BASE.RH}/contracts`);
        if (!res.ok) throw new Error('Falha ao carregar contratos');
        contractsCache = await res.json();
    } catch (e) {
        contractsCache = [];
    }
}

async function fetchActiveContract(empId) {
    try {
        const res = await apiFetch(`${API_BASE.RH}/employees/${empId}/contracts/active`);
        if (res.status === 404) return null;
        if (!res.ok) throw new Error('Falha ao obter contrato activo');
        return await res.json();
    } catch (e) {
        return null;
    }
}

function renderContracts() {
    populateAllEmpSelects();
    const host = document.getElementById('ctrList');
    if (!host) return;
    host.innerHTML = '<div class="hr-empty-state"><p>A carregar contratos...</p></div>';
    loadAllContracts().then(() => {
        const empF  = document.getElementById('ctrEmpFilter')?.value  || '';
        const typeF = document.getElementById('ctrTypeFilter')?.value || '';
        const list = contractsCache.filter(c =>
            (!empF  || String(c.employee_id) === empF) &&
            (!typeF || c.contract_type === typeF)
        ).sort((a, b) => (b.start_date || '').localeCompare(a.start_date || ''));
        if (!list.length) {
            host.innerHTML = `<div class="hr-empty-state"><svg viewBox="0 0 48 48" fill="none"><path d="M8 2 V14 M5 5 H10 a1.5 1.5 0 0 1 0 3 H6 a1.5 1.5 0 0 0 0 3 H11" transform="scale(3)" stroke="currentColor" stroke-width="0.5" stroke-linecap="round" fill="none"/></svg><h3>Sem contratos</h3><p>Crie o primeiro contrato de um funcionário.</p></div>`;
            return;
        }
        host.innerHTML = list.map(c => {
            const isActive = (c.status || 'ativo') === 'ativo';
            return `
            <article class="train-card">
                <div class="train-head">
                    <div class="train-info">
                        <div class="train-title">${escapeHtml(empName(c.employee_id))}</div>
                        <div class="train-meta">
                            <span class="avatar ${avatarTone(c.employee_id)}" style="width:24px;height:24px;font-size:10px">${avatarInnerById(c.employee_id)}</span>
                            ${CONTRACT_TYPE_LABEL(c.contract_type)}
                            · <strong>${fmtAoa(c.base_salary)}</strong>
                        </div>
                    </div>
                    <div class="train-aside">
                        <span class="status-pill ${isActive ? 'status-pill--online' : 'status-pill--off'}">${isActive ? 'Activo' : 'Terminado'}</span>
                        <button class="btn-icon-mini" data-ctr-edit="${c.id}" aria-label="Editar">
                            <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                        </button>
                    </div>
                </div>
                <div class="train-dates">${fmtDate((c.start_date || '').slice(0,10))} → ${c.end_date ? fmtDate(c.end_date.slice(0,10)) : 'em curso'}</div>
            </article>`;
        }).join('');
    });
}

function initContractModal() {
    const modal = document.getElementById('contractModal');
    const form  = document.getElementById('contractForm');
    if (!modal || !form) return;

    document.getElementById('ctrAddBtn')?.addEventListener('click', () => openContractModal());
    modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
    document.getElementById('ctrEmpFilter')?.addEventListener('change', renderContracts);
    document.getElementById('ctrTypeFilter')?.addEventListener('change', renderContracts);

    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-ctr-edit]');
        if (btn) openContractModal(Number(btn.dataset.ctrEdit));
    });

    document.getElementById('ctrDeleteBtn')?.addEventListener('click', async () => {
        const id = Number(document.getElementById('ctrId').value);
        if (!id || !await UIModal.confirm('Apagar este contrato?', { danger: true, okLabel: 'Apagar' })) return;
        try {
            const res = await apiFetch(`${API_BASE.RH}/contracts/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('Falha ao apagar contrato');
        } catch (err) {
            await UIModal.alert('Não foi possível apagar o contrato.');
            return;
        }
        renderContracts();
        closeModal(modal);
        UIToast.success('Contrato apagado com sucesso!');
    });

    form.addEventListener('submit', async e => {
        e.preventDefault();
        if (!form.checkValidity()) { form.reportValidity(); return; }
        const fd = new FormData(form);
        const id = fd.get('id');
        const body = {
            employee_id: Number(fd.get('employee_id')),
            contract_type: fd.get('contract_type'),
            start_date: fd.get('start_date'),
            end_date: fd.get('end_date') || null,
            trial_period_days: fd.get('trial_period_days') ? Number(fd.get('trial_period_days')) : null,
            weekly_hours: fd.get('weekly_hours') ? Number(fd.get('weekly_hours')) : null,
            base_salary: Number(fd.get('base_salary')) || 0,
            meal_allowance: Number(fd.get('meal_allowance')) || 0,
            transport_allowance: Number(fd.get('transport_allowance')) || 0,
            apply_inss: document.getElementById('ctrApplyInss').checked,
            apply_irt: document.getElementById('ctrApplyIrt').checked,
            notes: (fd.get('notes') || '').trim()
        };
        try {
            const res = await apiFetch(
                id ? `${API_BASE.RH}/contracts/${id}` : `${API_BASE.RH}/contracts`,
                {
                    method: id ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                }
            );
            if (!res.ok) throw new Error('Falha ao guardar contrato');
        } catch (err) {
            await UIModal.alert('Não foi possível guardar o contrato.');
            return;
        }
        renderContracts();
        closeModal(modal);
        UIToast.success(id ? 'Contrato actualizado com sucesso!' : 'Contrato criado com sucesso!');
    });
}

function openContractModal(id = null) {
    const modal = document.getElementById('contractModal');
    const form  = document.getElementById('contractForm');
    form.reset();
    document.getElementById('ctrId').value = '';
    document.getElementById('ctrDeleteBtn').hidden = true;
    document.getElementById('contractModalTitle').textContent = 'Novo contrato';
    document.getElementById('ctrApplyInss').checked = true;
    document.getElementById('ctrApplyIrt').checked = true;
    populateAllEmpSelects();
    if (id) {
        const c = contractsCache.find(x => x.id === id);
        if (c) {
            document.getElementById('ctrId').value = c.id;
            document.getElementById('ctrEmpSel').value = c.employee_id;
            document.getElementById('ctrType').value = c.contract_type;
            document.getElementById('ctrStart').value = (c.start_date || '').slice(0, 10);
            document.getElementById('ctrEnd').value = (c.end_date || '').slice(0, 10);
            document.getElementById('ctrTrial').value = c.trial_period_days || '';
            document.getElementById('ctrWeeklyHours').value = c.weekly_hours || '';
            document.getElementById('ctrBaseSalary').value = c.base_salary || '';
            document.getElementById('ctrMeal').value = c.meal_allowance || '';
            document.getElementById('ctrTransport').value = c.transport_allowance || '';
            document.getElementById('ctrApplyInss').checked = c.apply_inss !== false;
            document.getElementById('ctrApplyIrt').checked = c.apply_irt !== false;
            document.getElementById('ctrNotes').value = c.notes || '';
            document.getElementById('ctrDeleteBtn').hidden = false;
            document.getElementById('contractModalTitle').textContent = 'Editar contrato';
        }
    }
    openModal(modal);
    setTimeout(() => document.getElementById('ctrEmpSel')?.focus(), 100);
}

/* ============================================================
   AVALIAÇÕES
   ============================================================ */

async function loadAllEvaluations() {
    try {
        const res = await apiFetch(`${API_BASE.RH}/evaluations`);
        if (!res.ok) throw new Error('Falha ao carregar avaliações');
        state.evaluations = (await res.json()).map(ev => ({
            id: ev.id,
            employee: ev.employee_id,
            cycle: ev.cycle,
            period: ev.period || '',
            date: (ev.eval_date || '').slice(0, 10),
            rating: ev.rating,
            comments: ev.comments || ''
        }));
    } catch (e) {
        state.evaluations = [];
    }
}

function renderEvaluations() {
    populateAllEmpSelects();
    const host  = document.getElementById('evalList');
    if (!host) return;
    host.innerHTML = '<div class="hr-empty-state"><p>A carregar avaliações...</p></div>';
    loadAllEvaluations().then(() => {
        const empF   = document.getElementById('evalEmpFilter')?.value   || '';
        const cycleF = document.getElementById('evalCycleFilter')?.value || '';
        const list   = state.evaluations.filter(ev =>
            (!empF   || String(ev.employee) === empF) &&
            (!cycleF || ev.cycle === cycleF)
        ).sort((a, b) => (b.date || '').localeCompare(a.date || ''));
        if (!list.length) {
            host.innerHTML = `<div class="hr-empty-state"><svg viewBox="0 0 48 48" fill="none"><path d="M24 6 L28.5 18 L42 18 L31.5 27 L34.5 40.5 L24 33 L13.5 40.5 L16.5 27 L6 18 L19.5 18 Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg><h3>Sem avaliações</h3><p>Inicie o primeiro ciclo de avaliações de desempenho.</p></div>`;
            return;
        }
        host.innerHTML = list.map(ev => {
            const stars = '★'.repeat(ev.rating) + '☆'.repeat(5 - ev.rating);
            return `
            <article class="eval-card" data-eval-id="${ev.id}">
                <div class="eval-head">
                    <span class="avatar ${avatarTone(ev.employee)}">${avatarInnerById(ev.employee)}</span>
                    <div class="eval-meta">
                        <div class="eval-emp">${escapeHtml(empName(ev.employee))}</div>
                        <div class="eval-sub">
                            <span class="badge badge--cycle">${ev.cycle}</span>
                            ${ev.period ? `<span class="eval-period">${escapeHtml(ev.period)}</span>` : ''}
                            <span class="eval-date">${fmtDate(ev.date)}</span>
                        </div>
                    </div>
                    <div class="eval-stars">${stars}</div>
                    <button class="btn-icon-mini" data-eval-edit="${ev.id}" aria-label="Editar">
                        <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                    </button>
                </div>
                ${ev.comments ? `<p class="eval-comment">${escapeHtml(ev.comments)}</p>` : ''}
            </article>`;
        }).join('');
    });
}

function initEvalModal() {
    const modal = document.getElementById('evalModal');
    const form  = document.getElementById('evalForm');
    if (!modal || !form) return;

    document.getElementById('evalAddBtn')?.addEventListener('click', () => openEvalModal());
    modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
    document.getElementById('evalEmpFilter')?.addEventListener('change', renderEvaluations);
    document.getElementById('evalCycleFilter')?.addEventListener('change', renderEvaluations);

    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-eval-edit]');
        if (btn) openEvalModal(Number(btn.dataset.evalEdit));
    });

    document.getElementById('evalStarPicker')?.addEventListener('click', e => {
        const star = e.target.closest('[data-star]');
        if (!star) return;
        const val = Number(star.dataset.star);
        document.getElementById('evalRating').value = val;
        updateStarPicker(val);
    });

    document.getElementById('evalDeleteBtn')?.addEventListener('click', async () => {
        const id = Number(document.getElementById('evalId').value);
        if (!id || !await UIModal.confirm('Apagar esta avaliação?', { danger: true, okLabel: 'Apagar' })) return;
        try {
            const res = await apiFetch(`${API_BASE.RH}/evaluations/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('Falha ao apagar avaliação');
        } catch (err) {
            await UIModal.alert('Não foi possível apagar a avaliação.');
            return;
        }
        renderEvaluations(); closeModal(modal);
        UIToast.success('Avaliação apagada com sucesso!');
    });

    form.addEventListener('submit', async e => {
        e.preventDefault();
        if (!form.checkValidity()) { form.reportValidity(); return; }
        const fd  = new FormData(form);
        const id  = fd.get('id');
        const body = {
            employee_id: Number(fd.get('employee')),
            cycle:       fd.get('cycle'),
            period:      (fd.get('period') || '').trim(),
            eval_date:   fd.get('date'),
            rating:      Number(fd.get('rating') || 3),
            comments:    (fd.get('comments') || '').trim()
        };
        try {
            const res = await apiFetch(
                id ? `${API_BASE.RH}/evaluations/${id}` : `${API_BASE.RH}/evaluations`,
                {
                    method: id ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                }
            );
            if (!res.ok) throw new Error('Falha ao guardar avaliação');
        } catch (err) {
            await UIModal.alert('Não foi possível guardar a avaliação.');
            return;
        }
        renderEvaluations(); closeModal(modal);
        UIToast.success(id ? 'Avaliação actualizada com sucesso!' : 'Avaliação criada com sucesso!');
    });
}

function openEvalModal(id = null) {
    const modal = document.getElementById('evalModal');
    const form  = document.getElementById('evalForm');
    form.reset();
    document.getElementById('evalId').value = '';
    document.getElementById('evalDeleteBtn').hidden = true;
    document.getElementById('evalModalTitle').textContent = 'Nova avaliação';
    document.getElementById('evalDate').value = todayIso();
    document.getElementById('evalRating').value = 3;
    populateAllEmpSelects();
    updateStarPicker(3);
    if (id) {
        const ev = state.evaluations.find(x => x.id === id);
        if (ev) {
            document.getElementById('evalId').value       = ev.id;
            document.getElementById('evalEmpSel').value   = ev.employee;
            document.getElementById('evalCycle').value    = ev.cycle;
            document.getElementById('evalPeriod').value   = ev.period;
            document.getElementById('evalDate').value     = ev.date;
            document.getElementById('evalRating').value   = ev.rating;
            document.getElementById('evalComments').value = ev.comments;
            document.getElementById('evalDeleteBtn').hidden = false;
            document.getElementById('evalModalTitle').textContent = 'Editar avaliação';
            updateStarPicker(ev.rating);
        }
    }
    openModal(modal);
}

function updateStarPicker(val) {
    document.querySelectorAll('#evalStarPicker [data-star]').forEach(btn => {
        btn.classList.toggle('is-active', Number(btn.dataset.star) <= val);
    });
}

/* ============================================================
   FORMAÇÃO
   ============================================================ */

async function loadAllTraining() {
    try {
        const res = await apiFetch(`${API_BASE.RH}/trainings`);
        if (!res.ok) throw new Error('Falha ao carregar formações');
        state.training = (await res.json()).map(t => ({
            id: t.id,
            employee: t.employee_id,
            title: t.title,
            provider: t.provider || '',
            status: t.status || 'ongoing',
            startDate: (t.start_date || '').slice(0, 10),
            endDate: (t.end_date || '').slice(0, 10),
            hours: t.hours || 0,
            certUrl: t.cert_url || ''
        }));
    } catch (e) {
        state.training = [];
    }
}

function renderTraining() {
    populateAllEmpSelects();
    const host = document.getElementById('trainList');
    if (!host) return;
    host.innerHTML = '<div class="hr-empty-state"><p>A carregar formações...</p></div>';
    loadAllTraining().then(() => {
        const q      = (document.getElementById('trainSearch')?.value || '').toLowerCase();
        const empF   = document.getElementById('trainEmpFilter')?.value   || '';
        const statF  = document.getElementById('trainStatusFilter')?.value || '';
        const list   = state.training.filter(t =>
            (!q     || t.title.toLowerCase().includes(q) || (t.provider||'').toLowerCase().includes(q)) &&
            (!empF  || String(t.employee) === empF) &&
            (!statF || t.status === statF)
        ).sort((a, b) => (b.startDate || '').localeCompare(a.startDate || ''));
        if (!list.length) {
            host.innerHTML = `<div class="hr-empty-state"><svg viewBox="0 0 48 48" fill="none"><path d="M6 18 L24 9 L42 18 L24 27 L6 18 z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" fill="none"/><path d="M15 24 V36 c0 3 3 6 9 6 s9-3 9-6 V24" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg><h3>Sem formações</h3><p>Registe as formações concluídas e em curso da equipa.</p></div>`;
            return;
        }
        host.innerHTML = list.map(t => {
            const statusCls = t.status === 'completed' ? 'status-pill--online' : 'status-pill--training';
            const statusLbl = t.status === 'completed' ? 'Concluída' : 'Em curso';
            return `
            <article class="train-card">
                <div class="train-head">
                    <div class="train-info">
                        <div class="train-title">${escapeHtml(t.title)}</div>
                        <div class="train-meta">
                            <span class="avatar ${avatarTone(t.employee)}" style="width:24px;height:24px;font-size:10px">${avatarInnerById(t.employee)}</span>
                            ${escapeHtml(empName(t.employee))}
                            ${t.provider ? `· <em>${escapeHtml(t.provider)}</em>` : ''}
                            ${t.hours    ? `· <strong>${t.hours}h</strong>` : ''}
                        </div>
                    </div>
                    <div class="train-aside">
                        <span class="status-pill ${statusCls}">${statusLbl}</span>
                        ${t.certUrl ? `<a class="train-cert-link" href="${escapeHtml(t.certUrl)}" target="_blank" rel="noopener" title="Ver certificado">🎓</a>` : ''}
                        <button class="btn-icon-mini" data-train-edit="${t.id}" aria-label="Editar">
                            <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                        </button>
                    </div>
                </div>
                ${(t.startDate || t.endDate) ? `<div class="train-dates">${t.startDate ? fmtDate(t.startDate) : '?'} → ${t.endDate ? fmtDate(t.endDate) : 'em curso'}</div>` : ''}
            </article>`;
        }).join('');
    });
}

function initTrainModal() {
    const modal = document.getElementById('trainModal');
    const form  = document.getElementById('trainForm');
    if (!modal || !form) return;

    document.getElementById('trainAddBtn')?.addEventListener('click', () => openTrainModal());
    modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
    document.getElementById('trainSearch')?.addEventListener('input', renderTraining);
    document.getElementById('trainEmpFilter')?.addEventListener('change', renderTraining);
    document.getElementById('trainStatusFilter')?.addEventListener('change', renderTraining);

    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-train-edit]');
        if (btn) openTrainModal(Number(btn.dataset.trainEdit));
    });

    document.getElementById('trainDeleteBtn')?.addEventListener('click', async () => {
        const id = Number(document.getElementById('trainId').value);
        if (!id || !await UIModal.confirm('Apagar este registo de formação?', { danger: true, okLabel: 'Apagar' })) return;
        try {
            const res = await apiFetch(`${API_BASE.RH}/trainings/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('Falha ao apagar formação');
        } catch (err) {
            await UIModal.alert('Não foi possível apagar o registo de formação.');
            return;
        }
        renderTraining(); closeModal(modal);
        UIToast.success('Formação apagada com sucesso!');
    });

    form.addEventListener('submit', async e => {
        e.preventDefault();
        const fd  = new FormData(form);
        const id  = fd.get('id');
        const body = {
            employee_id: Number(fd.get('employee')),
            title:       fd.get('title').trim(),
            provider:    (fd.get('provider') || '').trim(),
            status:      fd.get('status'),
            start_date:  fd.get('startDate') || null,
            end_date:    fd.get('endDate')   || null,
            hours:       Number(fd.get('hours') || 0),
            cert_url:    (fd.get('certUrl') || '').trim()
        };
        try {
            const res = await apiFetch(
                id ? `${API_BASE.RH}/trainings/${id}` : `${API_BASE.RH}/trainings`,
                {
                    method: id ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                }
            );
            if (!res.ok) throw new Error('Falha ao guardar formação');
        } catch (err) {
            await UIModal.alert('Não foi possível guardar a formação.');
            return;
        }
        renderTraining(); closeModal(modal);
        UIToast.success(id ? 'Formação actualizada com sucesso!' : 'Formação criada com sucesso!');
    });
}

function openTrainModal(id = null) {
    const modal = document.getElementById('trainModal');
    const form  = document.getElementById('trainForm');
    form.reset();
    document.getElementById('trainId').value = '';
    document.getElementById('trainDeleteBtn').hidden = true;
    document.getElementById('trainModalTitle').textContent = 'Nova formação';
    populateAllEmpSelects();
    if (id) {
        const t = state.training.find(x => x.id === id);
        if (t) {
            document.getElementById('trainId').value       = t.id;
            document.getElementById('trainEmpSel').value   = t.employee;
            document.getElementById('trainTitle').value    = t.title;
            document.getElementById('trainProvider').value = t.provider;
            document.getElementById('trainStatus').value   = t.status;
            document.getElementById('trainStart').value    = t.startDate;
            document.getElementById('trainEnd').value      = t.endDate;
            document.getElementById('trainHours').value    = t.hours || '';
            document.getElementById('trainCertUrl').value  = t.certUrl;
            document.getElementById('trainDeleteBtn').hidden = false;
            document.getElementById('trainModalTitle').textContent = 'Editar formação';
        }
    }
    openModal(modal);
    setTimeout(() => document.getElementById('trainTitle')?.focus(), 100);
}

/* ============================================================
   ORGANOGRAMA
   ============================================================ */

function renderOrganogram() {
    const host = document.getElementById('orgWrap');
    if (!host) return;
    const roots = state.employees.filter(e => !e.reportsTo);
    if (!roots.length && !state.employees.length) {
        host.innerHTML = '<div class="hr-empty-state"><h3>Sem funcionários</h3><p>Adicione funcionários para ver o organograma.</p></div>';
        return;
    }
    function buildTree(parentId) {
        const children = state.employees.filter(e => e.reportsTo === parentId);
        if (!children.length) return '';
        return `<ul class="org-children">${children.map(e => `
            <li class="org-node">
                <div class="org-card" data-edit="${e.id}">
                    <span class="avatar ${avatarTone(e.id)}">${avatarInner(e)}</span>
                    <div class="org-card-info">
                        <div class="org-name">${escapeHtml(e.name)}</div>
                        <div class="org-role">${escapeHtml(e.role)}</div>
                    </div>
                </div>
                ${buildTree(e.id)}
            </li>`).join('')}
        </ul>`;
    }
    host.innerHTML = `<ul class="org-root">${roots.map(e => `
        <li class="org-node">
            <div class="org-card org-card--root" data-edit="${e.id}">
                <span class="avatar ${avatarTone(e.id)}">${avatarInner(e)}</span>
                <div class="org-card-info">
                    <div class="org-name">${escapeHtml(e.name)}</div>
                    <div class="org-role">${escapeHtml(e.role)}</div>
                </div>
            </div>
            ${buildTree(e.id)}
        </li>`).join('')}
    </ul>`;
}

/* ============================================================
   RECIBOS DE VENCIMENTO
   ============================================================ */

async function loadAllPayslips() {
    try {
        const res = await apiFetch(`${API_BASE.RH}/payslips`);
        if (!res.ok) throw new Error('Falha ao carregar recibos');
        state.payslips = (await res.json()).map(p => ({
            id: p.id,
            employee: p.employee_id,
            month: p.month,
            year: p.year,
            baseSalary: Number(p.base_salary || 0),
            extras: Number(p.extras || 0),
            deductions: Number(p.deductions || 0),
            irt: Number(p.irt || 0),
            ss: Number(p.social_security || 0),
            net: Number(p.net || 0),
            generated: (p.created_at || '').slice(0, 10)
        }));
    } catch (e) {
        state.payslips = [];
    }
}

function renderPayslips() {
    populateAllEmpSelects();
    populateYearFilter();
    const host  = document.getElementById('payList');
    if (!host) return;
    host.innerHTML = '<div class="hr-empty-state"><p>A carregar recibos...</p></div>';
    loadAllPayslips().then(() => {
    const empF  = document.getElementById('payEmpFilter')?.value || '';
    const yearF = document.getElementById('payYearFilter')?.value || '';
    const list  = state.payslips.filter(p =>
        (!empF  || String(p.employee) === empF) &&
        (!yearF || String(p.year)     === yearF)
    ).sort((a, b) => `${b.year}-${b.month}`.localeCompare(`${a.year}-${a.month}`));
    if (!list.length) {
        host.innerHTML = `<div class="hr-empty-state"><svg viewBox="0 0 48 48" fill="none"><path d="M8 6 H40 V38 L35 44 L30 38 L25 44 L20 38 L15 44 L10 38 L8 44 z M15 18 H33 M15 25 H27" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg><h3>Sem recibos</h3><p>Gere o primeiro recibo de vencimento usando o botão acima.</p></div>`;
        return;
    }
    const MONTHS = ['','Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
    host.innerHTML = list.map(p => {
        return `
        <article class="pay-card">
            <div class="pay-card-head">
                <span class="avatar ${avatarTone(p.employee)}">${avatarInnerById(p.employee)}</span>
                <div class="pay-info">
                    <div class="pay-emp">${escapeHtml(empName(p.employee))}</div>
                    <div class="pay-period">${MONTHS[Number(p.month)]} ${p.year}</div>
                </div>
                <div class="pay-amounts">
                    <span class="pay-gross">${fmtKz(p.baseSalary + (p.extras || 0))}</span>
                    <span class="pay-net">${fmtKz(p.net)}</span>
                </div>
                <button class="btn-icon-mini pay-print" data-pay-print="${p.id}" aria-label="Imprimir / exportar">
                    <svg viewBox="0 0 16 16" fill="none"><path d="M4 5 V2 H12 V5 M4 12 H2 V6 H14 V12 H12 M4 9 H12 V14 H4 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                </button>
            </div>
        </article>`;
    }).join('');
    });
}

function initPayModal() {
    const modal = document.getElementById('payModal');
    const form  = document.getElementById('payForm');
    if (!modal || !form) return;

    document.getElementById('payGenBtn')?.addEventListener('click', () => {
        populateAllEmpSelects();
        const empF = document.getElementById('payEmpFilter')?.value;
        if (empF) document.getElementById('payEmpSel').value = empF;
        const now = new Date();
        document.getElementById('payMonth').value = String(now.getMonth() + 1).padStart(2, '0');
        document.getElementById('payYear').value  = now.getFullYear();
        document.getElementById('payPreview').innerHTML = '';
        updatePayPreview();
        openModal(modal);
    });
    modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
    document.getElementById('payEmpFilter')?.addEventListener('change', renderPayslips);
    document.getElementById('payYearFilter')?.addEventListener('change', renderPayslips);

    ['payEmpSel', 'payExtras', 'payDeductions'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', updatePayPreview);
        document.getElementById(id)?.addEventListener('input', updatePayPreview);
    });

    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-pay-print]');
        if (btn) printPayslip(Number(btn.dataset.payPrint));
    });

    form.addEventListener('submit', async e => {
        e.preventDefault();
        const fd    = new FormData(form);
        const empId = Number(fd.get('employee'));
        if (!empId) return;
        const c      = await fetchActiveContract(empId) || {};
        const gross  = Number(c.base_salary || 0);
        const extras = Number(fd.get('extras') || 0);
        const deds   = Number(fd.get('deductions') || 0);
        const total  = gross + extras;
        const irt    = Math.round(calcIRT(total));
        const ss     = Math.round(total * 0.03);
        const net    = total - irt - ss - deds;
        const body = {
            employee_id:     empId,
            month:           fd.get('month'),
            year:            Number(fd.get('year')),
            base_salary:     gross,
            extras:          extras,
            deductions:      deds,
            irt:             irt,
            social_security: ss,
            net:             net,
        };
        try {
            const res = await apiFetch(`${API_BASE.RH}/payslips`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!res.ok) throw new Error('Falha ao gerar recibo');
        } catch (err) {
            await UIModal.alert('Não foi possível gerar o recibo.');
            return;
        }
        renderPayslips();
        closeModal(modal);
        UIToast.success('Recibo de vencimento gerado com sucesso!');
    });
}

async function updatePayPreview() {
    const host  = document.getElementById('payPreview');
    if (!host) return;
    const empId = Number(document.getElementById('payEmpSel')?.value);
    if (!empId) { host.innerHTML = ''; return; }
    host.innerHTML = '<p class="field-hint">A carregar contrato activo...</p>';
    const c      = await fetchActiveContract(empId) || {};
    const gross  = Number(c.base_salary || 0);
    const extras = Number(document.getElementById('payExtras')?.value || 0);
    const deds   = Number(document.getElementById('payDeductions')?.value || 0);
    const total  = gross + extras;
    const irt    = Math.round(calcIRT(total));
    const ss     = Math.round(total * 0.03);
    const net    = total - irt - ss - deds;
    host.innerHTML = `
        <div class="sal-preview-inner">
            <div class="sal-row"><span>Salário base</span><span>${fmtKz(gross)}</span></div>
            ${extras ? `<div class="sal-row"><span>Subsídios/extras</span><span>+ ${fmtKz(extras)}</span></div>` : ''}
            <div class="sal-row sal-deduction"><span>IRT</span><span>- ${fmtKz(irt)}</span></div>
            <div class="sal-row sal-deduction"><span>Seg. Social 3%</span><span>- ${fmtKz(ss)}</span></div>
            ${deds ? `<div class="sal-row sal-deduction"><span>Deduções extra</span><span>- ${fmtKz(deds)}</span></div>` : ''}
            <div class="sal-row sal-net"><span>Líquido a pagar</span><span>${fmtKz(net)}</span></div>
        </div>`;
}

function printPayslip(id) {
    const p = state.payslips.find(x => x.id === id);
    if (!p) return;
    const MONTHS = ['','Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
    const w = window.open('', '_blank', 'width=700,height=600');
    w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Recibo — ${empName(p.employee)}</title>
    <style>body{font-family:sans-serif;padding:40px;color:#111;max-width:600px;margin:auto}h1{font-size:20px;margin-bottom:4px}h2{font-size:14px;font-weight:400;color:#666;margin:0 0 24px}table{width:100%;border-collapse:collapse;margin-top:12px}td{padding:8px 0;border-bottom:1px solid #eee;font-size:14px}td:last-child{text-align:right}.total td{font-weight:700;font-size:15px;border-top:2px solid #111;border-bottom:none}.footer{margin-top:32px;font-size:12px;color:#999}</style>
    </head><body>
    <h1>Recibo de Vencimento</h1>
    <h2>${escapeHtml(empName(p.employee))} · ${MONTHS[Number(p.month)]} ${p.year}</h2>
    <table>
        <tr><td>Salário base</td><td>${fmtKz(p.baseSalary)}</td></tr>
        ${p.extras ? `<tr><td>Subsídios / Extras</td><td>${fmtKz(p.extras)}</td></tr>` : ''}
        <tr><td>IRT</td><td>- ${fmtKz(p.irt)}</td></tr>
        <tr><td>Segurança Social (3%)</td><td>- ${fmtKz(p.ss)}</td></tr>
        ${p.deductions ? `<tr><td>Outras deduções</td><td>- ${fmtKz(p.deductions)}</td></tr>` : ''}
        <tr class="total"><td>Total líquido</td><td>${fmtKz(p.net)}</td></tr>
    </table>
    <p class="footer">Emitido em ${fmtDate(p.generated)} · MarkSuite RHRM</p>
    </body></html>`);
    w.document.close();
    w.print();
}

/* ============================================================
   MODAL HELPERS
   ============================================================ */

function openModal(el) {
    if (!el) return;
    el.classList.add('is-open');
    el.setAttribute('aria-hidden', 'false');
}

function closeModal(el) {
    if (!el) return;
    el.classList.remove('is-open');
    el.setAttribute('aria-hidden', 'true');
}

/* ---------- Init ---------- */
async function init() {
    if (!document.querySelector('[data-view="people"]')) return;
    load();

    initTabs();
    initSubTabs();
    initEmpModal();
    initDeptModal();
    initStatusQuick();
    initRowClicks();
    initAbsModal();
    initVacModal();
    initAttendance();
    initEmpDocsModal();
    initContractModal();
    initEvalModal();
    initTrainModal();
    initPayModal();
    initOnboarding();
    populateYearFilter();

    await loadDepartments();
    await loadEmployees();
    populateDeptFilter();
    populateDeptSelect();

    renderEmployees();
    renderDepartments();
}

return { init };

})();

document.addEventListener('DOMContentLoaded', HR.init);
/* ============================================================
   BOOKS MODULE — facturação, despesas, fluxo de caixa
   ============================================================ */

const BOOKS = (() => {
    const state = { invoices: [], expenses: [], departments: [], employees: [], suppliers: [] };

    /* ---------- Helpers locais (o módulo é um IIFE isolado) ---------- */
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    function fmtKz(val) {
        return Number(val || 0).toLocaleString('pt-AO') + ' Kz';
    }
    function openModal(el) {
        if (!el) return;
        el.classList.add('is-open');
        el.setAttribute('aria-hidden', 'false');
    }
    function closeModal(el) {
        if (!el) return;
        el.classList.remove('is-open');
        el.setAttribute('aria-hidden', 'true');
    }

    const STATUS_LABEL_INV = {
        emitida: 'Emitida', parcial: 'Parcial', paga: 'Paga',
        vencida: 'Vencida', anulada: 'Anulada'
    };
    const STATUS_CLASS_INV = {
        emitida: 'inv-status--pending', parcial: 'inv-status--pending',
        paga: 'inv-status--paid', vencida: 'inv-status--overdue', anulada: 'inv-status--overdue'
    };
    const STATUS_LABEL_EXP = {
        pendente: 'Pendente', aprovada: 'Aprovada', rejeitada: 'Rejeitada', paga: 'Paga'
    };
    const STATUS_CLASS_EXP = {
        pendente: 'status-pill--meeting', aprovada: 'status-pill--remote',
        rejeitada: 'status-pill--off', paga: 'status-pill--online'
    };

    function mapInvoice(i) {
        return {
            id: i.id, docNumber: i.doc_number, clientName: i.client_name,
            clientNif: i.client_nif || '', clientEmail: i.client_email || '',
            description: i.description || '', subtotal: Number(i.subtotal) || 0,
            ivaRate: Number(i.iva_rate) || 0, ivaAmount: Number(i.iva_amount) || 0,
            total: Number(i.total) || 0, paidAmount: Number(i.paid_amount) || 0,
            status: i.status, issueDate: i.issue_date || '', dueDate: i.due_date || '',
            notes: i.notes || ''
        };
    }

    function mapExpense(e) {
        return {
            id: e.id, description: e.description, supplierId: e.supplier_id || null, supplierName: e.supplier_name || '',
            category: e.category || '', departmentId: e.department_id,
            requestedBy: e.requested_by, approverId: e.approver_id,
            amount: Number(e.amount) || 0, dueDate: e.due_date || '', status: e.status,
            approvalNote: e.approval_note || ''
        };
    }

    function deptName(id) {
        const d = state.departments.find(x => x.id === Number(id));
        return d ? d.name : '—';
    }
    function empName(id) {
        const e = state.employees.find(x => x.id === Number(id));
        return e ? e.full_name : '—';
    }

    async function loadDepartments() {
        try {
            const res = await apiFetch(`${API_BASE.RH}/departments`);
            state.departments = res.ok ? await res.json() : [];
        } catch (e) { state.departments = []; }
    }
    async function loadEmployees() {
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees`);
            state.employees = res.ok ? await res.json() : [];
        } catch (e) { state.employees = []; }
    }

    function populateSelects() {
        const deptSel = document.getElementById('expDept');
        if (deptSel) {
            const cur = deptSel.value;
            deptSel.innerHTML = '<option value="">— Escolher —</option>' +
                state.departments.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
            deptSel.value = cur;
        }
        const empSel = document.getElementById('expRequester');
        if (empSel) {
            const cur = empSel.value;
            empSel.innerHTML = '<option value="">— Escolher —</option>' +
                state.employees.map(e => `<option value="${e.id}">${escapeHtml(e.full_name)}</option>`).join('');
            empSel.value = cur;
        }
    }

    /* ---------- Summary / KPIs ---------- */
    async function loadSummary() {
        try {
            const res = await apiFetch(`${API_BASE.FIN}/summary`);
            if (!res.ok) throw new Error('Falha ao carregar resumo');
            const s = await res.json();
            setText('booksKpiRecebido', fmtKz(s.recebido_mes));
            setText('booksKpiAReceber', fmtKz(s.a_receber));
            setText('booksKpiVencidas', s.vencidas);
            setText('booksKpiDespPendentes', s.despesas_pendentes);
            setText('booksKpiSaldo', fmtKz(s.saldo_caixa_mes));
        } catch (e) {
            setText('booksKpiRecebido', 'Kz 0');
            setText('booksKpiAReceber', 'Kz 0');
            setText('booksKpiVencidas', '0');
            setText('booksKpiDespPendentes', '0');
            setText('booksKpiSaldo', 'Kz 0');
        }
    }
    function setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    /* ---------- Facturas ---------- */
    async function loadInvoices() {
        try {
            const params = new URLSearchParams();
            const status = document.getElementById('invFilterStatus')?.value || '';
            const client = (document.getElementById('invFilterClient')?.value || '').trim();
            if (status) params.set('status', status);
            if (client) params.set('client', client);
            const qs = params.toString() ? `?${params.toString()}` : '';
            const res = await apiFetch(`${API_BASE.FIN}/invoices${qs}`);
            if (!res.ok) throw new Error('Falha ao carregar facturas');
            state.invoices = (await res.json()).map(mapInvoice);
        } catch (e) { state.invoices = []; }
    }

    function renderInvoices() {
        const tbody = document.getElementById('invTableBody');
        const empty = document.getElementById('invEmpty');
        if (!tbody) return;
        if (!state.invoices.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        const today = todayIsoBooks();
        tbody.innerHTML = state.invoices.map(i => {
            const isOverdue = i.dueDate && i.dueDate < today && i.status !== 'paga' && i.status !== 'anulada';
            return `
            <tr class="inv-row" data-id="${i.id}">
                <td><code class="inv-doc">${escapeHtml(i.docNumber)}</code></td>
                <td class="inv-client">${escapeHtml(i.clientName)}</td>
                <td class="${isOverdue ? 'text-danger' : ''}">${i.dueDate ? fmtDateBooks(i.dueDate) : '—'}</td>
                <td class="num">${fmtKz(i.total)}</td>
                <td><span class="inv-status ${STATUS_CLASS_INV[i.status] || ''}">${STATUS_LABEL_INV[i.status] || i.status}</span></td>
                <td class="action-col">
                    <button class="row-action" data-inv-detail="${i.id}" aria-label="Ver detalhes" title="Ver detalhes">
                        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8 C3 4.8 5.3 3 8 3 s5 1.8 6.5 5 C13 11.2 10.7 13 8 13 s-5-1.8-6.5-5 z" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>
                    </button>
                    ${i.status !== 'paga' && i.status !== 'anulada' ? `
                        <button class="row-action" data-inv-receipt="${i.id}" aria-label="Emitir recibo" title="Emitir recibo">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3 V13 M5 6 H10 a1.5 1.5 0 0 1 0 3 H6 a1.5 1.5 0 0 0 0 3 H11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>
                        </button>
                        <button class="row-action" data-inv-cancel="${i.id}" aria-label="Anular" title="Anular">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
                        </button>
                    ` : ''}
                    ${i.paidAmount === 0 && (i.status === 'emitida' || i.status === 'anulada' || i.status === 'vencida') ? `
                        <button class="row-action" data-inv-del="${i.id}" aria-label="Apagar" title="Apagar">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4.5 H13 M6.5 4.5 V3 H9.5 V4.5 M5 4.5 L5.5 13.5 H10.5 L11 4.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                        </button>
                    ` : ''}
                </td>
            </tr>`;
        }).join('');
    }

    function openInvoiceModal() {
        const modal = document.getElementById('invoiceModal');
        const form = document.getElementById('invoiceForm');
        if (!modal || !form) return;
        form.reset();
        document.getElementById('invIssueDate').value = todayIsoBooks();
        document.getElementById('invPreview').innerHTML = '';
        openModal(modal);
    }

    function todayIsoBooks() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    }

    function updateInvoicePreview() {
        const host = document.getElementById('invPreview');
        if (!host) return;
        const subtotal = Number(document.getElementById('invSubtotal')?.value || 0);
        const rate = Number(document.getElementById('invIvaRate')?.value || 0);
        const iva = subtotal * rate / 100;
        const total = subtotal + iva;
        host.innerHTML = `
            <div class="sal-preview-inner">
                <div class="sal-row"><span>Subtotal</span><span>${fmtKz(subtotal)}</span></div>
                <div class="sal-row"><span>IVA (${rate || 0}%)</span><span>+ ${fmtKz(iva)}</span></div>
                <div class="sal-row sal-net"><span>Total</span><span>${fmtKz(total)}</span></div>
            </div>`;
    }

    function initInvoiceModal() {
        const modal = document.getElementById('invoiceModal');
        const form = document.getElementById('invoiceForm');
        if (!modal || !form) return;

        document.getElementById('booksAddInvoiceBtn')?.addEventListener('click', openInvoiceModal);
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
        ['invSubtotal', 'invIvaRate'].forEach(id => {
            document.getElementById(id)?.addEventListener('input', updateInvoicePreview);
        });

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const body = {
                client_name: fd.get('client_name').trim(),
                client_nif: (fd.get('client_nif') || '').trim() || null,
                client_email: (fd.get('client_email') || '').trim() || null,
                description: (fd.get('description') || '').trim() || null,
                subtotal: Number(fd.get('subtotal')) || 0,
                iva_rate: Number(fd.get('iva_rate')) || 0,
                issue_date: fd.get('issue_date') || null,
                due_date: fd.get('due_date') || null,
                notes: (fd.get('notes') || '').trim() || null,
            };
            try {
                const res = await apiFetch(`${API_BASE.FIN}/invoices`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error('Falha ao emitir factura');
            } catch (err) {
                await UIModal.alert('Não foi possível emitir a factura.');
                return;
            }
            await refreshInvoices();
            closeModal(modal);
            UIToast.success('Factura emitida com sucesso!');
        });
    }

    let currentReceiptInvoiceId = null;

    function openReceiptModal(invoiceId) {
        const invoice = state.invoices.find(i => i.id === invoiceId);
        if (!invoice) return;
        currentReceiptInvoiceId = invoiceId;
        const modal = document.getElementById('receiptModal');
        const form = document.getElementById('receiptForm');
        if (!modal || !form) return;
        form.reset();
        const outstanding = Math.max(0, invoice.total - invoice.paidAmount);
        document.getElementById('recInvoiceId').value = invoiceId;
        document.getElementById('recAmount').value = outstanding;
        document.getElementById('recDate').value = todayIsoBooks();
        document.getElementById('recOutstandingHint').textContent =
            `Saldo em dívida: ${fmtKz(outstanding)} de ${fmtKz(invoice.total)} (${invoice.docNumber})`;
        openModal(modal);
    }

    function initReceiptModal() {
        const modal = document.getElementById('receiptModal');
        const form = document.getElementById('receiptForm');
        if (!modal || !form) return;
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const invoiceId = Number(fd.get('invoice_id'));
            if (!invoiceId) return;
            const body = {
                amount: Number(fd.get('amount')) || 0,
                payment_date: fd.get('payment_date') || null,
                payment_method: fd.get('payment_method') || 'transferencia',
            };
            try {
                const res = await apiFetch(`${API_BASE.FIN}/invoices/${invoiceId}/receipts`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error('Falha ao registar recibo');
            } catch (err) {
                await UIModal.alert('Não foi possível registar o recibo.');
                return;
            }
            await refreshInvoices();
            closeModal(modal);
            UIToast.success('Recibo registado com sucesso!');
        });
    }

    function initInvoiceTableEvents() {
        document.getElementById('invTableBody')?.addEventListener('click', async e => {
            const recBtn = e.target.closest('[data-inv-receipt]');
            if (recBtn) { openReceiptModal(Number(recBtn.dataset.invReceipt)); return; }
            const detailBtn = e.target.closest('[data-inv-detail]');
            if (detailBtn) { openInvoiceDetail(Number(detailBtn.dataset.invDetail)); return; }
            const delBtn = e.target.closest('[data-inv-del]');
            if (delBtn) {
                if (!await UIModal.confirm('Apagar esta factura definitivamente?', { danger: true, okLabel: 'Apagar' })) return;
                try {
                    const res = await apiFetch(`${API_BASE.FIN}/invoices/${delBtn.dataset.invDel}`, { method: 'DELETE' });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || 'Falha ao apagar');
                    }
                } catch (err) {
                    await UIModal.alert(err.message || 'Não foi possível apagar a factura.');
                    return;
                }
                await refreshInvoices();
                UIToast.success('Factura apagada com sucesso!');
                return;
            }
            const cancelBtn = e.target.closest('[data-inv-cancel]');
            if (cancelBtn) {
                if (!await UIModal.confirm('Anular esta factura? A numeração fica reservada e não pode ser reutilizada.', { danger: true, okLabel: 'Anular' })) return;
                try {
                    const res = await apiFetch(`${API_BASE.FIN}/invoices/${cancelBtn.dataset.invCancel}/cancel`, { method: 'PATCH' });
                    if (!res.ok) throw new Error('Falha ao anular');
                } catch (err) {
                    await UIModal.alert('Não foi possível anular a factura.');
                    return;
                }
                await refreshInvoices();
                UIToast.success('Factura anulada com sucesso!');
            }
        });

        let invFilterTimer = null;
        document.getElementById('invFilterClient')?.addEventListener('input', () => {
            clearTimeout(invFilterTimer);
            invFilterTimer = setTimeout(async () => { await loadInvoices(); renderInvoices(); }, 300);
        });
        document.getElementById('invFilterStatus')?.addEventListener('change', async () => {
            await loadInvoices();
            renderInvoices();
        });
    }

    async function refreshInvoices() {
        await loadInvoices();
        renderInvoices();
        await loadSummary();
    }

    /* ---------- Detalhe da factura (recibos + anexos) ---------- */
    const PAYMENT_METHOD_LABEL = {
        transferencia: 'Transferência', numerario: 'Numerário', multicaixa: 'Multicaixa', outro: 'Outro'
    };
    let currentDetailInvoiceId = null;

    async function openInvoiceDetail(invoiceId) {
        const modal = document.getElementById('invoiceDetailModal');
        if (!modal) return;
        currentDetailInvoiceId = invoiceId;

        const infoHost = document.getElementById('invDetInfo');
        const recHost = document.getElementById('invDetReceipts');
        const docsHost = document.getElementById('invDetDocs');
        infoHost.innerHTML = '<p class="field-hint">A carregar...</p>';
        recHost.innerHTML = '';
        docsHost.innerHTML = '';
        openModal(modal);

        let inv = state.invoices.find(i => i.id === invoiceId);
        try {
            const res = await apiFetch(`${API_BASE.FIN}/invoices/${invoiceId}`);
            if (res.ok) inv = mapInvoice(await res.json());
        } catch (e) {}
        if (!inv) { infoHost.innerHTML = '<p class="field-hint">Factura não encontrada.</p>'; return; }

        document.getElementById('invoiceDetailTitle').textContent = `Factura ${inv.docNumber}`;
        infoHost.innerHTML = `
            <div class="sal-preview-inner">
                <div class="sal-row"><span>Cliente</span><span>${escapeHtml(inv.clientName)}${inv.clientNif ? ` · NIF ${escapeHtml(inv.clientNif)}` : ''}</span></div>
                ${inv.description ? `<div class="sal-row"><span>Descrição</span><span>${escapeHtml(inv.description)}</span></div>` : ''}
                <div class="sal-row"><span>Emissão / Vencimento</span><span>${fmtDateBooks((inv.issueDate || '').slice(0,10))} → ${inv.dueDate ? fmtDateBooks(inv.dueDate.slice(0,10)) : '—'}</span></div>
                <div class="sal-row"><span>Subtotal</span><span>${fmtKz(inv.subtotal)}</span></div>
                <div class="sal-row"><span>IVA (${inv.ivaRate}%)</span><span>+ ${fmtKz(inv.ivaAmount)}</span></div>
                <div class="sal-row sal-net"><span>Total</span><span>${fmtKz(inv.total)}</span></div>
                <div class="sal-row"><span>Pago</span><span>${fmtKz(inv.paidAmount)} · <span class="inv-status ${STATUS_CLASS_INV[inv.status] || ''}">${STATUS_LABEL_INV[inv.status] || inv.status}</span></span></div>
            </div>`;

        loadInvoiceReceipts(invoiceId);
        loadFinDocs('invoice', invoiceId, docsHost);
    }

    async function loadInvoiceReceipts(invoiceId) {
        const host = document.getElementById('invDetReceipts');
        if (!host) return;
        host.innerHTML = '<li class="attach-empty">A carregar recibos...</li>';
        try {
            const res = await apiFetch(`${API_BASE.FIN}/invoices/${invoiceId}/receipts`);
            if (!res.ok) throw new Error();
            const receipts = await res.json();
            if (!receipts.length) {
                host.innerHTML = '<li class="attach-empty">Sem recibos emitidos.</li>';
                return;
            }
            host.innerHTML = receipts.map(r => `
                <li class="attach-item">
                    <span class="attach-name"><code class="inv-doc">${escapeHtml(r.doc_number)}</code> · ${fmtDateBooks((r.payment_date || '').slice(0,10))} · ${PAYMENT_METHOD_LABEL[r.payment_method] || escapeHtml(r.payment_method || '—')}</span>
                    <span class="attach-item-actions"><strong>${fmtKz(r.amount)}</strong></span>
                </li>
            `).join('');
        } catch (e) {
            host.innerHTML = '<li class="attach-empty">Não foi possível carregar os recibos.</li>';
        }
    }

    async function loadFinDocs(entityType, entityId, host) {
        if (!host) return;
        host.innerHTML = '<li class="attach-empty">A carregar anexos...</li>';
        try {
            const res = await apiFetch(`${API_BASE.FIN}/${entityType === 'invoice' ? 'invoices' : 'expenses'}/${entityId}/documents`);
            if (!res.ok) throw new Error();
            const docs = await res.json();
            if (!docs.length) {
                host.innerHTML = '<li class="attach-empty">Sem anexos ainda.</li>';
                return;
            }
            const withUrls = await Promise.all(docs.map(async d => {
                try {
                    const r = await apiFetch(`${API_BASE.FIN}/documents/${d.id}/presigned-url`);
                    const j = r.ok ? await r.json() : {};
                    return { ...d, url: j.url || null };
                } catch (e) { return { ...d, url: null }; }
            }));
            host.innerHTML = withUrls.map(d => `
                <li class="attach-item">
                    <span class="attach-name">${escapeHtml(d.filename)} <span class="attach-tag">${escapeHtml(d.document_type || 'anexo')}</span></span>
                    <span class="attach-item-actions">
                        ${d.url ? `<a class="attach-download" href="${d.url}" target="_blank" rel="noopener">Transferir</a>` : ''}
                        <button type="button" class="btn-icon-mini" data-fin-doc-del="${d.id}" data-fin-doc-entity="${entityType}" data-fin-doc-entity-id="${entityId}" aria-label="Apagar anexo">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4.5 H13 M6.5 4.5 V3 H9.5 V4.5 M5 4.5 L5.5 13.5 H10.5 L11 4.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                        </button>
                    </span>
                </li>
            `).join('');
        } catch (e) {
            host.innerHTML = '<li class="attach-empty">Não foi possível carregar os anexos.</li>';
        }
    }

    async function uploadFinDoc(entityType, entityId, fileInput, typeInput, host) {
        const file = fileInput?.files?.[0];
        if (!file || !entityId) return;
        const docType = (typeInput?.value || '').trim() || 'anexo';
        const fd = new FormData();
        fd.append('file', file);
        try {
            const res = await apiFetch(
                `${API_BASE.FIN}/${entityType === 'invoice' ? 'invoices' : 'expenses'}/${entityId}/documents?document_type=${encodeURIComponent(docType)}`,
                { method: 'POST', body: fd }
            );
            if (!res.ok) throw new Error();
        } catch (e) {
            await UIModal.alert('Não foi possível enviar o anexo.');
            return;
        }
        fileInput.value = '';
        if (typeInput) typeInput.value = '';
        loadFinDocs(entityType, entityId, host);
        UIToast.success('Anexo enviado com sucesso!');
    }

    function initInvoiceDetailModal() {
        const modal = document.getElementById('invoiceDetailModal');
        if (!modal) return;
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        document.getElementById('invDetUploadForm')?.addEventListener('submit', e => {
            e.preventDefault();
            uploadFinDoc('invoice', currentDetailInvoiceId,
                document.getElementById('invDetDocFile'),
                document.getElementById('invDetDocType'),
                document.getElementById('invDetDocs'));
        });

        document.addEventListener('click', async e => {
            const delBtn = e.target.closest('[data-fin-doc-del]');
            if (!delBtn) return;
            if (!await UIModal.confirm('Apagar este anexo?', { danger: true, okLabel: 'Apagar' })) return;
            try {
                const res = await apiFetch(`${API_BASE.FIN}/documents/${delBtn.dataset.finDocDel}`, { method: 'DELETE' });
                if (!res.ok) throw new Error();
            } catch (err) {
                await UIModal.alert('Não foi possível apagar o anexo.');
                return;
            }
            const entityType = delBtn.dataset.finDocEntity;
            const entityId = Number(delBtn.dataset.finDocEntityId);
            const host = entityType === 'invoice'
                ? document.getElementById('invDetDocs')
                : document.getElementById('expDocsList');
            loadFinDocs(entityType, entityId, host);
            UIToast.success('Anexo apagado com sucesso!');
        });
    }

    /* ---------- Recibos (listagem global) ---------- */
    async function renderReceipts() {
        const tbody = document.getElementById('recTableBody');
        const empty = document.getElementById('recEmpty');
        if (!tbody) return;
        const from = document.getElementById('recFrom')?.value;
        const to = document.getElementById('recTo')?.value;
        const params = new URLSearchParams();
        if (from) params.set('date_from', from);
        if (to) params.set('date_to', to);
        const qs = params.toString() ? `?${params.toString()}` : '';
        let rows = [];
        try {
            const res = await apiFetch(`${API_BASE.FIN}/receipts${qs}`);
            if (res.ok) rows = await res.json();
        } catch (e) { rows = []; }
        if (!rows.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = rows.map(r => `
            <tr class="inv-row">
                <td><code class="inv-doc">${escapeHtml(r.doc_number)}</code></td>
                <td>#${r.invoice_id}</td>
                <td>${fmtDateBooks((r.payment_date || '').slice(0,10))}</td>
                <td>${PAYMENT_METHOD_LABEL[r.payment_method] || escapeHtml(r.payment_method || '—')}</td>
                <td class="num">${fmtKz(r.amount)}</td>
            </tr>
        `).join('');
    }

    function initReceiptsTab() {
        const d = new Date();
        const monthStart = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-01`;
        const fromEl = document.getElementById('recFrom');
        const toEl = document.getElementById('recTo');
        if (fromEl) fromEl.value = monthStart;
        if (toEl) toEl.value = todayIsoBooks();
        document.getElementById('recApplyBtn')?.addEventListener('click', renderReceipts);
    }

    /* ---------- Despesas ---------- */
    async function loadExpenses() {
        try {
            const res = await apiFetch(`${API_BASE.FIN}/expenses`);
            if (!res.ok) throw new Error('Falha ao carregar despesas');
            state.expenses = (await res.json()).map(mapExpense);
        } catch (e) { state.expenses = []; }
    }

    function renderExpenses() {
        const host = document.getElementById('expList');
        if (!host) return;
        const statusFilter = document.getElementById('expFilterStatus')?.value || '';
        const list = statusFilter ? state.expenses.filter(x => x.status === statusFilter) : state.expenses;
        if (!list.length) {
            host.innerHTML = `<div class="hr-empty-state"><h3>Sem despesas</h3><p>${state.expenses.length ? 'Nenhuma despesa corresponde a este filtro.' : 'Submeta a primeira despesa usando o botão "Nova despesa".'}</p></div>`;
            return;
        }
        host.innerHTML = list.map(x => `
            <article class="train-card" data-id="${x.id}">
                <div class="train-head">
                    <div class="train-info">
                        <div class="train-title">${escapeHtml(x.description)}</div>
                        <div class="train-meta">
                            ${x.category ? `<span class="badge badge--outro">${escapeHtml(x.category)}</span> · ` : ''}
                            ${x.supplierName ? `${escapeHtml(x.supplierName)} · ` : ''}
                            ${escapeHtml(deptName(x.departmentId))}
                            · Solicitado por <strong>${escapeHtml(empName(x.requestedBy))}</strong>
                            ${x.approverId ? `· Aprovador: <strong>${escapeHtml(empName(x.approverId))}</strong>` : ''}
                            · <strong>${fmtKz(x.amount)}</strong>
                        </div>
                    </div>
                    <div class="train-aside">
                        <span class="status-pill ${STATUS_CLASS_EXP[x.status] || ''}">${STATUS_LABEL_EXP[x.status] || x.status}</span>
                        <button class="btn-icon-mini" data-exp-docs="${x.id}" aria-label="Anexos" title="Anexos">
                            <svg viewBox="0 0 16 16" fill="none"><path d="M12.5 7.5 L8 12 a3 3 0 0 1-4.2-4.2 L9 2.6 a2 2 0 0 1 2.8 2.8 L6.6 10.6 a1 1 0 0 1-1.4-1.4 L9.5 4.9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
                        </button>
                        ${x.status === 'pendente' ? `
                            <button class="btn-icon-mini" data-exp-approve="${x.id}" aria-label="Aprovar" title="Aprovar">
                                <svg viewBox="0 0 16 16" fill="none"><path d="M3 8.5 L6.5 12 L13 4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
                            </button>
                            <button class="btn-icon-mini" data-exp-reject="${x.id}" aria-label="Rejeitar" title="Rejeitar">
                                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
                            </button>
                            <button class="btn-icon-mini" data-exp-del="${x.id}" aria-label="Apagar" title="Apagar">
                                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4.5 H13 M6.5 4.5 V3 H9.5 V4.5 M5 4.5 L5.5 13.5 H10.5 L11 4.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                            </button>
                        ` : ''}
                        ${x.status === 'aprovada' ? `
                            <button class="btn-icon-mini" data-exp-pay="${x.id}" aria-label="Marcar como paga" title="Marcar como paga">
                                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3 V13 M5 6 H10 a1.5 1.5 0 0 1 0 3 H6 a1.5 1.5 0 0 0 0 3 H11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>
                            </button>
                        ` : ''}
                    </div>
                </div>
            </article>
        `).join('');
    }

    function openExpenseModal() {
        const modal = document.getElementById('expenseModal');
        const form = document.getElementById('expenseForm');
        if (!modal || !form) return;
        form.reset();
        populateSelects();
        populateSupplierSelect();
        openModal(modal);
    }

    function initExpenseModal() {
        const modal = document.getElementById('expenseModal');
        const form = document.getElementById('expenseForm');
        if (!modal || !form) return;

        document.getElementById('booksAddExpenseBtn')?.addEventListener('click', openExpenseModal);
        document.getElementById('booksAddExpenseTopBtn')?.addEventListener('click', openExpenseModal);
        document.getElementById('expFilterStatus')?.addEventListener('change', renderExpenses);
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const body = {
                description: fd.get('description').trim(),
                supplier_id: fd.get('supplier_id') ? Number(fd.get('supplier_id')) : null,
                supplier_name: (fd.get('supplier_name') || '').trim() || null,
                category: (fd.get('category') || '').trim() || null,
                department_id: fd.get('department_id') ? Number(fd.get('department_id')) : null,
                requested_by: fd.get('requested_by') ? Number(fd.get('requested_by')) : null,
                amount: Number(fd.get('amount')) || 0,
                due_date: fd.get('due_date') || null,
            };
            try {
                const res = await apiFetch(`${API_BASE.FIN}/expenses`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error('Falha ao submeter despesa');
            } catch (err) {
                await UIModal.alert('Não foi possível submeter a despesa.');
                return;
            }
            await refreshExpenses();
            closeModal(modal);
            UIToast.success('Despesa submetida com sucesso!');
        });
    }

    let currentExpenseDocsId = null;

    function initExpenseDocsModal() {
        const modal = document.getElementById('expenseDocsModal');
        if (!modal) return;
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
        document.getElementById('expDocsUploadForm')?.addEventListener('submit', e => {
            e.preventDefault();
            uploadFinDoc('expense', currentExpenseDocsId,
                document.getElementById('expDocsFile'),
                document.getElementById('expDocsType'),
                document.getElementById('expDocsList'));
        });
    }

    function openExpenseDocsModal(expenseId) {
        currentExpenseDocsId = expenseId;
        const modal = document.getElementById('expenseDocsModal');
        if (!modal) return;
        openModal(modal);
        loadFinDocs('expense', expenseId, document.getElementById('expDocsList'));
    }

    function initExpenseListEvents() {
        document.getElementById('expList')?.addEventListener('click', async e => {
            const docsBtn = e.target.closest('[data-exp-docs]');
            if (docsBtn) { openExpenseDocsModal(Number(docsBtn.dataset.expDocs)); return; }
            const delBtn = e.target.closest('[data-exp-del]');
            if (delBtn) {
                if (!await UIModal.confirm('Apagar esta despesa pendente?', { danger: true, okLabel: 'Apagar' })) return;
                try {
                    const res = await apiFetch(`${API_BASE.FIN}/expenses/${delBtn.dataset.expDel}`, { method: 'DELETE' });
                    if (!res.ok) throw new Error('Falha ao apagar');
                } catch (err) {
                    await UIModal.alert('Não foi possível apagar a despesa.');
                    return;
                }
                await refreshExpenses();
                UIToast.success('Despesa apagada com sucesso!');
                return;
            }
            const approveBtn = e.target.closest('[data-exp-approve]');
            const rejectBtn = e.target.closest('[data-exp-reject]');
            const payBtn = e.target.closest('[data-exp-pay]');
            let successMsg = '';
            try {
                if (approveBtn) {
                    const res = await apiFetch(`${API_BASE.FIN}/expenses/${approveBtn.dataset.expApprove}/approve`, {
                        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
                    });
                    if (!res.ok) throw new Error('Falha ao aprovar');
                    successMsg = 'Despesa aprovada com sucesso!';
                } else if (rejectBtn) {
                    if (!await UIModal.confirm('Rejeitar esta despesa?', { danger: true, okLabel: 'Rejeitar' })) return;
                    const res = await apiFetch(`${API_BASE.FIN}/expenses/${rejectBtn.dataset.expReject}/reject`, {
                        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
                    });
                    if (!res.ok) throw new Error('Falha ao rejeitar');
                    successMsg = 'Despesa rejeitada.';
                } else if (payBtn) {
                    const res = await apiFetch(`${API_BASE.FIN}/expenses/${payBtn.dataset.expPay}/pay`, { method: 'PATCH' });
                    if (!res.ok) throw new Error('Falha ao marcar como paga');
                    successMsg = 'Despesa marcada como paga com sucesso!';
                } else {
                    return;
                }
            } catch (err) {
                await UIModal.alert('Não foi possível actualizar a despesa.');
                return;
            }
            await refreshExpenses();
            UIToast.success(successMsg);
        });
    }

    async function refreshExpenses() {
        await loadExpenses();
        renderExpenses();
        await loadSummary();
    }

    /* ---------- Fornecedores ---------- */
    function mapSupplier(s) {
        return {
            id: s.id, name: s.name, nif: s.nif || '', email: s.email || '',
            phone: s.phone || '', category: s.category || '', address: s.address || '',
            notes: s.notes || '', active: s.active !== false,
        };
    }

    async function loadSuppliers() {
        try {
            const res = await apiFetch(`${API_BASE.FIN}/suppliers`);
            if (!res.ok) throw new Error('Falha ao carregar fornecedores');
            state.suppliers = (await res.json()).map(mapSupplier);
        } catch (e) { state.suppliers = []; }
    }

    function populateSupplierSelect() {
        const sel = document.getElementById('expSupplierSelect');
        if (!sel) return;
        const cur = sel.value;
        sel.innerHTML = '<option value="">— Nenhum (fornecedor avulso) —</option>' +
            state.suppliers.filter(s => s.active).map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('');
        if (cur) sel.value = cur;
    }

    function renderSuppliers() {
        const tbody = document.getElementById('supTableBody');
        const empty = document.getElementById('supEmpty');
        if (!tbody) return;
        const q = (document.getElementById('supFilterSearch')?.value || '').trim().toLowerCase();
        const activeFilter = document.getElementById('supFilterActive')?.value || '';
        const list = state.suppliers.filter(s => {
            const matchQ = !q || s.name.toLowerCase().includes(q) || s.nif.toLowerCase().includes(q);
            const matchActive = !activeFilter || String(s.active) === activeFilter;
            return matchQ && matchActive;
        });
        if (!list.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = list.map(s => `
            <tr class="inv-row" data-id="${s.id}">
                <td class="inv-client">${escapeHtml(s.name)}</td>
                <td>${escapeHtml(s.nif) || '—'}</td>
                <td>${s.category ? `<span class="badge badge--outro">${escapeHtml(s.category)}</span>` : '—'}</td>
                <td>${escapeHtml(s.phone || s.email) || '—'}</td>
                <td><span class="status-pill ${s.active ? 'status-pill--online' : 'status-pill--off'}">${s.active ? 'Activo' : 'Inactivo'}</span></td>
                <td class="action-col">
                    <button class="row-action" data-sup-edit="${s.id}" aria-label="Editar" title="Editar">
                        <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                    </button>
                </td>
            </tr>
        `).join('');
    }

    function openSupplierModal(supplier) {
        const modal = document.getElementById('supplierModal');
        const form = document.getElementById('supplierForm');
        if (!modal || !form) return;
        form.reset();
        document.getElementById('supId').value = supplier?.id || '';
        document.getElementById('supplierModalTitle').textContent = supplier ? 'Editar fornecedor' : 'Novo fornecedor';
        document.getElementById('supActiveField').hidden = !supplier;
        document.getElementById('supDeleteBtn').hidden = !supplier;
        if (supplier) {
            document.getElementById('supName').value = supplier.name;
            document.getElementById('supNif').value = supplier.nif;
            document.getElementById('supCategory').value = supplier.category;
            document.getElementById('supEmail').value = supplier.email;
            document.getElementById('supPhone').value = supplier.phone;
            document.getElementById('supAddress').value = supplier.address;
            document.getElementById('supNotes').value = supplier.notes;
            document.getElementById('supActive').checked = supplier.active;
        }
        openModal(modal);
    }

    function initSupplierModal() {
        const modal = document.getElementById('supplierModal');
        const form = document.getElementById('supplierForm');
        if (!modal || !form) return;

        document.getElementById('supAddBtn')?.addEventListener('click', () => openSupplierModal(null));
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
        document.getElementById('supFilterSearch')?.addEventListener('input', renderSuppliers);
        document.getElementById('supFilterActive')?.addEventListener('change', renderSuppliers);

        document.getElementById('supTableBody')?.addEventListener('click', e => {
            const btn = e.target.closest('[data-sup-edit]');
            if (!btn) return;
            const supplier = state.suppliers.find(s => s.id === Number(btn.dataset.supEdit));
            if (supplier) openSupplierModal(supplier);
        });

        document.getElementById('supDeleteBtn')?.addEventListener('click', async () => {
            const id = document.getElementById('supId').value;
            if (!id) return;
            if (!await UIModal.confirm('Apagar este fornecedor? Se tiver despesas associadas, desactive-o em vez disso.', { danger: true })) return;
            try {
                const res = await apiFetch(`${API_BASE.FIN}/suppliers/${id}`, { method: 'DELETE' });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao apagar');
                }
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível apagar o fornecedor.');
                return;
            }
            await refreshSuppliers();
            closeModal(modal);
            UIToast.success('Fornecedor apagado com sucesso!');
        });

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const id = document.getElementById('supId').value;
            const fd = new FormData(form);
            const body = {
                name: fd.get('name').trim(),
                nif: (fd.get('nif') || '').trim() || null,
                email: (fd.get('email') || '').trim() || null,
                phone: (fd.get('phone') || '').trim() || null,
                category: (fd.get('category') || '').trim() || null,
                address: (fd.get('address') || '').trim() || null,
                notes: (fd.get('notes') || '').trim() || null,
            };
            if (id) body.active = document.getElementById('supActive').checked;
            try {
                const res = await apiFetch(`${API_BASE.FIN}/suppliers${id ? '/' + id : ''}`, {
                    method: id ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error('Falha ao guardar fornecedor');
            } catch (err) {
                await UIModal.alert('Não foi possível guardar o fornecedor.');
                return;
            }
            await refreshSuppliers();
            closeModal(modal);
            UIToast.success(id ? 'Fornecedor actualizado com sucesso!' : 'Fornecedor criado com sucesso!');
        });
    }

    async function refreshSuppliers() {
        await loadSuppliers();
        renderSuppliers();
        populateSupplierSelect();
    }

    /* ---------- Fluxo de Caixa ---------- */
    async function renderCashflow() {
        const tbody = document.getElementById('cfTableBody');
        const empty = document.getElementById('cfEmpty');
        if (!tbody) return;
        const from = document.getElementById('cfFrom')?.value;
        const to = document.getElementById('cfTo')?.value;
        if (!from || !to) return;
        let rows = [];
        try {
            const res = await apiFetch(`${API_BASE.FIN}/cashflow?date_from=${from}&date_to=${to}`);
            if (res.ok) rows = await res.json();
        } catch (e) { rows = []; }
        if (!rows.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = rows.map(r => `
            <tr class="inv-row">
                <td>${fmtDateBooks(r.day)}</td>
                <td class="num">${fmtKz(r.income)}</td>
                <td class="num">${fmtKz(r.expense)}</td>
                <td class="num">${fmtKz(r.balance)}</td>
                <td class="num">${fmtKz(r.running_balance)}</td>
            </tr>
        `).join('');
    }
    function fmtDateBooks(iso) {
        if (!iso) return '—';
        const [y, m, d] = iso.split('-');
        return `${d}/${m}/${y}`;
    }

    function initCashflow() {
        const d = new Date();
        const monthStart = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-01`;
        const today = todayIsoBooks();
        const fromEl = document.getElementById('cfFrom');
        const toEl = document.getElementById('cfTo');
        if (fromEl) fromEl.value = monthStart;
        if (toEl) toEl.value = today;
        document.getElementById('cfApplyBtn')?.addEventListener('click', renderCashflow);
    }

    /* ---------- Relatórios ---------- */
    const MONTH_ABBR_BOOKS = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];

    function last6MonthsBooks() {
        const now = new Date();
        const months = [];
        for (let i = 5; i >= 0; i--) {
            const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
            months.push({ year: d.getFullYear(), month: d.getMonth() });
        }
        return months;
    }

    async function renderReports() {
        const months = last6MonthsBooks();
        const dateFrom = `${months[0].year}-${String(months[0].month + 1).padStart(2, '0')}-01`;
        const dateTo = todayIsoBooks();

        let receipts = [], expensesAll = [], invoicesAll = [];
        try {
            const [rRes, eRes, iRes] = await Promise.all([
                apiFetch(`${API_BASE.FIN}/receipts?date_from=${dateFrom}&date_to=${dateTo}`),
                apiFetch(`${API_BASE.FIN}/expenses`),
                apiFetch(`${API_BASE.FIN}/invoices`),
            ]);
            receipts = rRes.ok ? await rRes.json() : [];
            expensesAll = eRes.ok ? await eRes.json() : [];
            invoicesAll = iRes.ok ? await iRes.json() : [];
        } catch (e) {}

        renderRevenueExpenseChart(receipts, expensesAll, months);
        renderCategoryBreakdown(expensesAll);
        renderAgingReport(invoicesAll);
        renderTopClients(invoicesAll);
    }

    function renderRevenueExpenseChart(receipts, expenses, months) {
        const revTotals = months.map(() => 0);
        receipts.forEach(r => {
            const d = new Date(r.payment_date);
            if (isNaN(d)) return;
            const idx = months.findIndex(mo => mo.year === d.getFullYear() && mo.month === d.getMonth());
            if (idx >= 0) revTotals[idx] += Number(r.amount) || 0;
        });
        const expTotals = months.map(() => 0);
        expenses.filter(e => e.status === 'paga' && e.paid_at).forEach(e => {
            const d = new Date(e.paid_at);
            if (isNaN(d)) return;
            const idx = months.findIndex(mo => mo.year === d.getFullYear() && mo.month === d.getMonth());
            if (idx >= 0) expTotals[idx] += Number(e.amount) || 0;
        });

        const xs = [40, 168, 296, 424, 552, 680];
        const yTop = 30, yBottom = 210, baseline = 230;
        const max = Math.max(...revTotals, ...expTotals, 0);
        const denom = max || 1;
        const scaleY = v => yBottom - (v / denom) * (yBottom - yTop);

        const revYs = revTotals.map(scaleY);
        const expYs = expTotals.map(scaleY);

        const revLine = 'M ' + xs.map((x, i) => `${x} ${revYs[i].toFixed(1)}`).join(' L ');
        const revArea = `${revLine} L ${xs[5]} ${baseline} L ${xs[0]} ${baseline} Z`;
        const expLine = 'M ' + xs.map((x, i) => `${x} ${expYs[i].toFixed(1)}`).join(' L ');
        const expArea = `${expLine} L ${xs[5]} ${baseline} L ${xs[0]} ${baseline} Z`;

        document.getElementById('repRevLine')?.setAttribute('d', revLine);
        document.getElementById('repRevArea')?.setAttribute('d', revArea);
        document.getElementById('repExpLine')?.setAttribute('d', expLine);
        document.getElementById('repExpArea')?.setAttribute('d', expArea);

        const dotsHost = document.getElementById('repRevDots');
        if (dotsHost) {
            dotsHost.innerHTML = xs.map((x, i) => `
                <circle class="chart-dot" cx="${x}" cy="${revYs[i].toFixed(1)}" r="${i === xs.length - 1 ? 5.5 : 4.5}">
                    <title>${escapeHtml(MONTH_ABBR_BOOKS[months[i].month])} — Receita ${fmtKz(revTotals[i])} · Despesa ${fmtKz(expTotals[i])}</title>
                </circle>
            `).join('');
        }
        const labelsHost = document.getElementById('repChartXLabels');
        if (labelsHost) labelsHost.innerHTML = months.map(mo => `<span>${MONTH_ABBR_BOOKS[mo.month]}</span>`).join('');
    }

    function renderCategoryBreakdown(expenses) {
        const host = document.getElementById('repCategoryList');
        if (!host) return;
        const totals = {};
        expenses.filter(e => e.status !== 'rejeitada').forEach(e => {
            const cat = (e.category || '').trim() || 'Sem categoria';
            totals[cat] = (totals[cat] || 0) + (Number(e.amount) || 0);
        });
        const entries = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 8);
        if (!entries.length) {
            host.innerHTML = '<li class="attach-empty">Sem despesas registadas.</li>';
            return;
        }
        const max = entries[0][1] || 1;
        host.innerHTML = entries.map(([cat, total]) => `
            <li class="report-bar-row">
                <div class="report-bar-label"><span>${escapeHtml(cat)}</span><strong>${fmtKz(total)}</strong></div>
                <div class="report-bar-track"><div class="report-bar-fill" style="width:${Math.max(4, (total / max) * 100)}%"></div></div>
            </li>
        `).join('');
    }

    function renderAgingReport(invoices) {
        const host = document.getElementById('repAgingList');
        if (!host) return;
        const today = todayIsoBooks();
        const todayDate = new Date(today);
        const buckets = [
            { label: 'Por vencer', total: 0 },
            { label: '1–30 dias', total: 0 },
            { label: '31–60 dias', total: 0 },
            { label: '61–90 dias', total: 0 },
            { label: '90+ dias', total: 0 },
        ];
        invoices
            .filter(i => ['emitida', 'parcial', 'vencida'].includes(i.status) && i.due_date)
            .forEach(inv => {
                const amountLeft = Number(inv.total) - Number(inv.paid_amount || 0);
                if (amountLeft <= 0) return;
                if (inv.due_date >= today) { buckets[0].total += amountLeft; return; }
                const days = Math.floor((todayDate - new Date(inv.due_date)) / (1000 * 60 * 60 * 24));
                if (days <= 30) buckets[1].total += amountLeft;
                else if (days <= 60) buckets[2].total += amountLeft;
                else if (days <= 90) buckets[3].total += amountLeft;
                else buckets[4].total += amountLeft;
            });
        const totalOutstanding = buckets.reduce((s, b) => s + b.total, 0);
        if (totalOutstanding <= 0) {
            host.innerHTML = '<li class="attach-empty">Sem facturas por cobrar.</li>';
            return;
        }
        const max = Math.max(...buckets.map(b => b.total), 1);
        host.innerHTML = buckets.map(b => `
            <li class="report-bar-row">
                <div class="report-bar-label"><span>${b.label}</span><strong>${fmtKz(b.total)}</strong></div>
                <div class="report-bar-track"><div class="report-bar-fill ${b.label !== 'Por vencer' && b.total > 0 ? 'report-bar-fill--danger' : ''}" style="width:${b.total ? Math.max(4, (b.total / max) * 100) : 0}%"></div></div>
            </li>
        `).join('');
    }

    function renderTopClients(invoices) {
        const host = document.getElementById('repTopClientsList');
        if (!host) return;
        const totals = {};
        invoices.filter(i => i.status !== 'anulada').forEach(i => {
            totals[i.client_name] = (totals[i.client_name] || 0) + (Number(i.total) || 0);
        });
        const entries = Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 6);
        if (!entries.length) {
            host.innerHTML = '<li class="attach-empty">Sem facturas emitidas.</li>';
            return;
        }
        host.innerHTML = entries.map(([name, total], i) => `
            <li class="report-rank-row">
                <span class="report-rank-num">${i + 1}</span>
                <span class="report-rank-name">${escapeHtml(name)}</span>
                <strong>${fmtKz(total)}</strong>
            </li>
        `).join('');
    }

    /* ---------- Tabs ---------- */
    function initTabs() {
        document.querySelectorAll('[data-bktab]').forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.dataset.bktab;
                document.querySelectorAll('[data-bktab]').forEach(t => {
                    const active = t === tab;
                    t.classList.toggle('is-active', active);
                    t.setAttribute('aria-selected', String(active));
                });
                document.querySelectorAll('[data-bkpane]').forEach(p => {
                    p.classList.toggle('is-active', p.dataset.bkpane === target);
                });
                if (target === 'fluxo') renderCashflow();
                if (target === 'recibos') renderReceipts();
                if (target === 'fornecedores') renderSuppliers();
                if (target === 'relatorios') renderReports();
                if (target === 'documentos') ScopedDocs.mount('booksDocsWidget', 'Financeiro');
            });
        });
    }

    /* ---------- Init ---------- */
    async function init() {
        if (!document.querySelector('[data-view="books"]')) return;

        initTabs();
        initInvoiceModal();
        initReceiptModal();
        initInvoiceTableEvents();
        initInvoiceDetailModal();
        initExpenseModal();
        initExpenseListEvents();
        initExpenseDocsModal();
        initSupplierModal();
        initCashflow();
        initReceiptsTab();

        await Promise.all([loadDepartments(), loadEmployees(), loadSuppliers()]);
        populateSelects();
        populateSupplierSelect();

        await refreshInvoices();
        await refreshExpenses();
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', BOOKS.init);

/* ============================================================
   STOCK MODULE — inventário, armazéns, custeio (CMP/FIFO),
   guias de transporte, câmbio e relatórios (stock-service)
   ============================================================ */

const STOCK = (() => {
    const state = { items: [], warehouses: [], movements: [], batches: [], guides: [], rates: [], alerts: [] };

    /* ---------- Helpers locais (o módulo é um IIFE isolado) ---------- */
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    function fmtKz(val) {
        return Number(val || 0).toLocaleString('pt-AO') + ' Kz';
    }
    function fmtNum(val) {
        return Number(val || 0).toLocaleString('pt-AO', { maximumFractionDigits: 3 });
    }
    function fmtDateStk(iso) {
        if (!iso) return '—';
        const s = String(iso).slice(0, 10);
        const [y, m, d] = s.split('-');
        return y && m && d ? `${d}/${m}/${y}` : s;
    }
    function fmtDateTimeStk(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        if (isNaN(d)) return fmtDateStk(iso);
        return d.toLocaleString('pt-AO', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    }
    function todayIsoStk() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    function openModal(el) {
        if (!el) return;
        el.classList.add('is-open');
        el.setAttribute('aria-hidden', 'false');
    }
    function closeModal(el) {
        if (!el) return;
        el.classList.remove('is-open');
        el.setAttribute('aria-hidden', 'true');
    }
    function setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }
    function warehouseName(id) {
        const w = state.warehouses.find(x => x.id === Number(id));
        return w ? w.name : '—';
    }

    const MOVEMENT_TYPE_LABEL = {
        entrada: 'Entrada', saida: 'Saída', transferencia: 'Transferência',
        ajuste: 'Ajuste', devolucao: 'Devolução',
    };
    const GUIDE_STATUS_LABEL = {
        rascunho: 'Rascunho', emitida: 'Emitida', validada_agt: 'Validada (AGT)', anulada: 'Anulada',
    };
    const GUIDE_STATUS_CLASS = {
        rascunho: 'inv-status--pending', emitida: 'inv-status--pending',
        validada_agt: 'inv-status--paid', anulada: 'inv-status--overdue',
    };
    const WAREHOUSE_TYPE_LABEL = { armazem: 'Armazém', loja: 'Loja' };

    /* ---------- Summary / KPIs ---------- */
    async function loadSummary() {
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/summary`);
            if (!res.ok) throw new Error('Falha ao carregar resumo');
            const s = await res.json();
            setText('stkKpiItems', s.total_items);
            setText('stkKpiValue', fmtKz(s.stock_value_aoa));
            setText('stkKpiAlerts', s.alerts_count);
            setText('stkKpiGuides', s.pending_guides);
            setText('stkKpiWarehouses', s.total_warehouses);
        } catch (e) {
            setText('stkKpiItems', '0');
            setText('stkKpiValue', 'Kz 0');
            setText('stkKpiAlerts', '0');
            setText('stkKpiGuides', '0');
            setText('stkKpiWarehouses', '0');
        }
    }

    /* ---------- Selects partilhados entre modais ---------- */
    function itemOptionsHtml() {
        return state.items.filter(i => i.active).map(i =>
            `<option value="${i.id}">${escapeHtml(i.name)}${i.sku ? ' (' + escapeHtml(i.sku) + ')' : ''}</option>`
        ).join('');
    }
    function warehouseOptionsHtml() {
        return state.warehouses.filter(w => w.active).map(w =>
            `<option value="${w.id}">${escapeHtml(w.name)}</option>`
        ).join('');
    }
    function populateSelects() {
        const filterSel = document.getElementById('stkMovFilterItem');
        if (filterSel) {
            const cur = filterSel.value;
            filterSel.innerHTML = '<option value="">Todos os artigos</option>' + itemOptionsHtml();
            filterSel.value = cur;
        }
        const movItemSel = document.getElementById('stkMovItem');
        if (movItemSel) {
            const cur = movItemSel.value;
            movItemSel.innerHTML = '<option value="">— Escolher —</option>' + itemOptionsHtml();
            movItemSel.value = cur;
        }
        ['stkMovWarehouse', 'stkGuideOrigin'].forEach(id => {
            const sel = document.getElementById(id);
            if (!sel) return;
            const cur = sel.value;
            sel.innerHTML = '<option value="">— Escolher —</option>' + warehouseOptionsHtml();
            sel.value = cur;
        });
        const destSel = document.getElementById('stkMovDestination');
        if (destSel) {
            const cur = destSel.value;
            destSel.innerHTML = '<option value="">— Escolher —</option>' + warehouseOptionsHtml();
            destSel.value = cur;
        }
        const destWhSel = document.getElementById('stkGuideDestinationWarehouse');
        if (destWhSel) {
            const cur = destWhSel.value;
            destWhSel.innerHTML = '<option value="">— Destino externo —</option>' + warehouseOptionsHtml();
            destWhSel.value = cur;
        }
        const currSel = document.getElementById('stkMovCurrency');
        if (currSel) {
            const cur = currSel.value;
            const codes = Array.from(new Set(state.rates.map(r => r.currency_code)));
            currSel.innerHTML = '<option value="AOA">AOA (Kwanza)</option>' +
                codes.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
            currSel.value = cur || 'AOA';
        }
    }

    /* ---------- Artigos ---------- */
    async function loadItems() {
        try {
            const q = (document.getElementById('stkItemSearch')?.value || '').trim();
            const qs = q ? `?q=${encodeURIComponent(q)}` : '';
            const res = await apiFetch(`${API_BASE.STOCK}/items${qs}`);
            state.items = res.ok ? await res.json() : [];
        } catch (e) { state.items = []; }
    }

    function renderItems() {
        const tbody = document.getElementById('stkItemsTableBody');
        const empty = document.getElementById('stkItemsEmpty');
        if (!tbody) return;
        if (!state.items.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.items.map(i => `
            <tr>
                <td><code class="inv-doc">${escapeHtml(i.sku || i.barcode || '—')}</code></td>
                <td>${escapeHtml(i.name)}${i.track_batches ? ' <span class="badge badge--outro">Lotes</span>' : ''}</td>
                <td>${escapeHtml(i.category || '—')}</td>
                <td>${escapeHtml(i.unit)}</td>
                <td class="num">${fmtNum(i.min_stock)}</td>
                <td>${i.active ? '<span class="badge badge--empresa">Activo</span>' : '<span class="badge badge--outro">Inactivo</span>'}</td>
                <td class="action-col" style="white-space:nowrap;">
                    <button type="button" class="btn-link-sm" data-item-edit="${i.id}">Editar</button>
                    <button type="button" class="btn-link-sm" data-item-del="${i.id}" style="color:#DC2626;">Apagar</button>
                </td>
            </tr>
        `).join('');
    }

    function openItemModal(item) {
        const modal = document.getElementById('stkItemModal');
        const form = document.getElementById('stkItemForm');
        if (!modal || !form) return;
        form.reset();
        document.getElementById('stkItemId').value = item?.id || '';
        document.getElementById('stkItemModalTitle').textContent = item ? 'Editar artigo' : 'Novo artigo';
        document.getElementById('stkItemActiveField').hidden = !item;
        if (item) {
            document.getElementById('stkItemName').value = item.name || '';
            document.getElementById('stkItemSku').value = item.sku || '';
            document.getElementById('stkItemBarcode').value = item.barcode || '';
            document.getElementById('stkItemCategory').value = item.category || '';
            document.getElementById('stkItemUnit').value = item.unit || 'un';
            document.getElementById('stkItemMinStock').value = item.min_stock ?? 0;
            document.getElementById('stkItemTrackBatches').checked = !!item.track_batches;
            document.getElementById('stkItemActive').checked = item.active !== false;
        } else {
            document.getElementById('stkItemUnit').value = 'un';
        }
        openModal(modal);
    }

    function initItemModal() {
        const modal = document.getElementById('stkItemModal');
        const form = document.getElementById('stkItemForm');
        if (!modal || !form) return;
        document.getElementById('stkAddItemBtn')?.addEventListener('click', () => openItemModal(null));
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const id = fd.get('id');
            const body = {
                name: fd.get('name').trim(),
                sku: (fd.get('sku') || '').trim() || null,
                barcode: (fd.get('barcode') || '').trim() || null,
                category: (fd.get('category') || '').trim() || null,
                unit: (fd.get('unit') || 'un').trim() || 'un',
                min_stock: Number(fd.get('min_stock')) || 0,
                track_batches: document.getElementById('stkItemTrackBatches').checked,
            };
            if (id) body.active = document.getElementById('stkItemActive').checked;
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/items${id ? '/' + id : ''}`, {
                    method: id ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error('Falha ao guardar artigo');
            } catch (err) {
                await UIModal.alert('Não foi possível guardar o artigo.');
                return;
            }
            await refreshItems();
            closeModal(modal);
            UIToast.success('Artigo guardado com sucesso!');
        });
    }

    function initItemTableEvents() {
        document.getElementById('stkItemsTableBody')?.addEventListener('click', async e => {
            const editBtn = e.target.closest('[data-item-edit]');
            if (editBtn) {
                const item = state.items.find(i => i.id === Number(editBtn.dataset.itemEdit));
                openItemModal(item);
                return;
            }
            const delBtn = e.target.closest('[data-item-del]');
            if (delBtn) {
                if (!await UIModal.confirm('Apagar este artigo definitivamente?', { danger: true, okLabel: 'Apagar' })) return;
                try {
                    const res = await apiFetch(`${API_BASE.STOCK}/items/${delBtn.dataset.itemDel}`, { method: 'DELETE' });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || 'Falha ao apagar');
                    }
                } catch (err) {
                    await UIModal.alert(err.message || 'Não foi possível apagar o artigo.');
                    return;
                }
                await refreshItems();
                UIToast.success('Artigo apagado com sucesso!');
            }
        });

        let searchTimer = null;
        document.getElementById('stkItemSearch')?.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(refreshItems, 300);
        });
    }

    async function refreshItems() {
        await loadItems();
        renderItems();
        populateSelects();
    }

    /* ---------- Armazéns ---------- */
    async function loadWarehouses() {
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/warehouses`);
            state.warehouses = res.ok ? await res.json() : [];
        } catch (e) { state.warehouses = []; }
    }

    function renderWarehouses() {
        const tbody = document.getElementById('stkWarehousesTableBody');
        const empty = document.getElementById('stkWarehousesEmpty');
        if (!tbody) return;
        if (!state.warehouses.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.warehouses.map(w => `
            <tr>
                <td>${escapeHtml(w.name)}</td>
                <td>${escapeHtml(w.code || '—')}</td>
                <td>${WAREHOUSE_TYPE_LABEL[w.type] || escapeHtml(w.type)}</td>
                <td>${escapeHtml(w.address || '—')}</td>
                <td>${w.active ? '<span class="badge badge--empresa">Activo</span>' : '<span class="badge badge--outro">Inactivo</span>'}</td>
                <td class="action-col" style="white-space:nowrap;">
                    <button type="button" class="btn-link-sm" data-wh-edit="${w.id}">Editar</button>
                    <button type="button" class="btn-link-sm" data-wh-del="${w.id}" style="color:#DC2626;">Apagar</button>
                </td>
            </tr>
        `).join('');
    }

    function openWarehouseModal(wh) {
        const modal = document.getElementById('stkWarehouseModal');
        const form = document.getElementById('stkWarehouseForm');
        if (!modal || !form) return;
        form.reset();
        document.getElementById('stkWarehouseId').value = wh?.id || '';
        document.getElementById('stkWarehouseModalTitle').textContent = wh ? 'Editar armazém' : 'Novo armazém';
        document.getElementById('stkWarehouseActiveField').hidden = !wh;
        if (wh) {
            document.getElementById('stkWarehouseName').value = wh.name || '';
            document.getElementById('stkWarehouseCode').value = wh.code || '';
            document.getElementById('stkWarehouseType').value = wh.type || 'armazem';
            document.getElementById('stkWarehouseAddress').value = wh.address || '';
            document.getElementById('stkWarehouseActive').checked = wh.active !== false;
        }
        openModal(modal);
    }

    function initWarehouseModal() {
        const modal = document.getElementById('stkWarehouseModal');
        const form = document.getElementById('stkWarehouseForm');
        if (!modal || !form) return;
        document.getElementById('stkAddWarehouseBtn')?.addEventListener('click', () => openWarehouseModal(null));
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const id = fd.get('id');
            const body = {
                name: fd.get('name').trim(),
                code: (fd.get('code') || '').trim() || null,
                type: fd.get('type') || 'armazem',
                address: (fd.get('address') || '').trim() || null,
            };
            if (id) body.active = document.getElementById('stkWarehouseActive').checked;
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/warehouses${id ? '/' + id : ''}`, {
                    method: id ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error('Falha ao guardar armazém');
            } catch (err) {
                await UIModal.alert('Não foi possível guardar o armazém.');
                return;
            }
            await refreshWarehouses();
            closeModal(modal);
            UIToast.success('Armazém guardado com sucesso!');
        });
    }

    function initWarehouseTableEvents() {
        document.getElementById('stkWarehousesTableBody')?.addEventListener('click', async e => {
            const editBtn = e.target.closest('[data-wh-edit]');
            if (editBtn) {
                const wh = state.warehouses.find(w => w.id === Number(editBtn.dataset.whEdit));
                openWarehouseModal(wh);
                return;
            }
            const delBtn = e.target.closest('[data-wh-del]');
            if (delBtn) {
                if (!await UIModal.confirm('Apagar este armazém definitivamente?', { danger: true, okLabel: 'Apagar' })) return;
                try {
                    const res = await apiFetch(`${API_BASE.STOCK}/warehouses/${delBtn.dataset.whDel}`, { method: 'DELETE' });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || 'Falha ao apagar');
                    }
                } catch (err) {
                    await UIModal.alert(err.message || 'Não foi possível apagar o armazém.');
                    return;
                }
                await refreshWarehouses();
                UIToast.success('Armazém apagado com sucesso!');
            }
        });
    }

    async function refreshWarehouses() {
        await loadWarehouses();
        renderWarehouses();
        populateSelects();
    }

    /* ---------- Movimentos ---------- */
    async function loadMovements() {
        try {
            const params = new URLSearchParams();
            const itemId = document.getElementById('stkMovFilterItem')?.value;
            const type = document.getElementById('stkMovFilterType')?.value;
            if (itemId) params.set('item_id', itemId);
            if (type) params.set('movement_type', type);
            const qs = params.toString() ? `?${params.toString()}` : '';
            const res = await apiFetch(`${API_BASE.STOCK}/movements${qs}`);
            state.movements = res.ok ? await res.json() : [];
        } catch (e) { state.movements = []; }
    }

    function renderMovements() {
        const tbody = document.getElementById('stkMovementsTableBody');
        const empty = document.getElementById('stkMovementsEmpty');
        if (!tbody) return;
        if (!state.movements.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.movements.map(m => `
            <tr>
                <td>${fmtDateTimeStk(m.created_at)}</td>
                <td><span class="badge badge--outro">${MOVEMENT_TYPE_LABEL[m.movement_type] || m.movement_type}</span></td>
                <td>${escapeHtml(m.item_name)}</td>
                <td>${escapeHtml(m.warehouse_name)}${m.destination_warehouse_id ? ' → ' + escapeHtml(warehouseName(m.destination_warehouse_id)) : ''}</td>
                <td class="num">${fmtNum(m.quantity)}</td>
                <td class="num">${m.unit_cost_aoa ? fmtKz(m.unit_cost_aoa) : '—'}</td>
                <td class="num">${m.total_cost_aoa ? fmtKz(m.total_cost_aoa) : '—'}</td>
                <td>${escapeHtml(m.reason || '—')}</td>
            </tr>
        `).join('');
    }

    function updateMovementFormVisibility() {
        const type = document.getElementById('stkMovType')?.value;
        const destField = document.getElementById('stkMovDestinationField');
        const destSel = document.getElementById('stkMovDestination');
        const adjustField = document.getElementById('stkMovAdjustDirectionField');
        const unitCostField = document.getElementById('stkMovUnitCostField');
        const unitCostInput = document.getElementById('stkMovUnitCost');
        const salePriceField = document.getElementById('stkMovSalePriceField');
        const batchFields = document.getElementById('stkMovBatchFields');

        if (destField) destField.hidden = type !== 'transferencia';
        if (destSel) destSel.required = type === 'transferencia';

        if (adjustField) adjustField.hidden = type !== 'ajuste';

        const needsCost = type === 'entrada' || type === 'devolucao' || type === 'ajuste';
        if (unitCostField) unitCostField.hidden = !needsCost;
        if (unitCostInput) unitCostInput.required = type === 'entrada';

        if (salePriceField) salePriceField.hidden = type !== 'saida';

        const itemId = Number(document.getElementById('stkMovItem')?.value);
        const item = state.items.find(i => i.id === itemId);
        if (batchFields) batchFields.hidden = !(type === 'entrada' && item?.track_batches);
    }

    function openMovementModal() {
        const modal = document.getElementById('stkMovementModal');
        const form = document.getElementById('stkMovementForm');
        if (!modal || !form) return;
        form.reset();
        populateSelects();
        document.getElementById('stkMovType').value = 'entrada';
        document.getElementById('stkMovAdjustDirection').value = '1';
        updateMovementFormVisibility();
        openModal(modal);
    }

    function initMovementModal() {
        const modal = document.getElementById('stkMovementModal');
        const form = document.getElementById('stkMovementForm');
        if (!modal || !form) return;
        document.getElementById('stkAddMovementBtn')?.addEventListener('click', openMovementModal);
        document.getElementById('stkAddMovementTopBtn')?.addEventListener('click', openMovementModal);
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));
        document.getElementById('stkMovType')?.addEventListener('change', updateMovementFormVisibility);
        document.getElementById('stkMovItem')?.addEventListener('change', updateMovementFormVisibility);

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const type = fd.get('movement_type');
            let quantity = Math.abs(Number(fd.get('quantity')) || 0);
            if (type === 'ajuste') {
                const direction = Number(document.getElementById('stkMovAdjustDirection').value) || 1;
                quantity = quantity * direction;
            }
            const body = {
                movement_type: type,
                item_id: Number(fd.get('item_id')),
                warehouse_id: Number(fd.get('warehouse_id')),
                destination_warehouse_id: type === 'transferencia' && fd.get('destination_warehouse_id')
                    ? Number(fd.get('destination_warehouse_id')) : null,
                quantity,
                currency_code: fd.get('currency_code') || 'AOA',
                unit_cost: fd.get('unit_cost') ? Number(fd.get('unit_cost')) : null,
                sale_price: fd.get('sale_price') ? Number(fd.get('sale_price')) : null,
                batch_number: (fd.get('batch_number') || '').trim() || null,
                expiry_date: fd.get('expiry_date') || null,
                reason: (fd.get('reason') || '').trim() || null,
            };
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/movements`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao registar movimento');
                }
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível registar o movimento.');
                return;
            }
            await refreshMovements();
            await loadSummary();
            closeModal(modal);
            UIToast.success('Movimento registado com sucesso!');
        });

        document.getElementById('stkMovFilterItem')?.addEventListener('change', refreshMovements);
        document.getElementById('stkMovFilterType')?.addEventListener('change', refreshMovements);
    }

    async function refreshMovements() {
        await loadMovements();
        renderMovements();
    }

    /* ---------- Lotes / Validade ---------- */
    async function loadBatches() {
        try {
            const days = document.getElementById('stkBatchFilterExpiry')?.value || '';
            const qs = days ? `?expiring_within_days=${days}` : '';
            const res = await apiFetch(`${API_BASE.STOCK}/batches${qs}`);
            state.batches = res.ok ? await res.json() : [];
        } catch (e) { state.batches = []; }
    }

    function renderBatches() {
        const tbody = document.getElementById('stkBatchesTableBody');
        const empty = document.getElementById('stkBatchesEmpty');
        if (!tbody) return;
        if (!state.batches.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.batches.map(b => `
            <tr>
                <td>${escapeHtml(b.item_name)}${b.item_sku ? ' (' + escapeHtml(b.item_sku) + ')' : ''}</td>
                <td>${escapeHtml(b.warehouse_name)}</td>
                <td>${escapeHtml(b.batch_number || '—')}</td>
                <td>${b.expiry_date ? fmtDateStk(b.expiry_date) : '—'}</td>
                <td class="num">${fmtNum(b.quantity_remaining)}</td>
                <td class="num">${fmtKz(b.unit_cost)}</td>
            </tr>
        `).join('');
    }

    async function refreshBatches() {
        await loadBatches();
        renderBatches();
    }

    /* ---------- Câmbio ---------- */
    async function loadRates() {
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/exchange-rates`);
            state.rates = res.ok ? await res.json() : [];
        } catch (e) { state.rates = []; }
    }

    function renderRates() {
        const tbody = document.getElementById('stkRatesTableBody');
        const empty = document.getElementById('stkRatesEmpty');
        if (!tbody) return;
        if (!state.rates.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.rates.map(r => `
            <tr>
                <td>${escapeHtml(r.currency_code)}</td>
                <td class="num">${fmtNum(r.rate_to_aoa)}</td>
                <td>${fmtDateStk(r.effective_date)}</td>
            </tr>
        `).join('');
    }

    function openRateModal() {
        const modal = document.getElementById('stkRateModal');
        const form = document.getElementById('stkRateForm');
        if (!modal || !form) return;
        form.reset();
        document.getElementById('stkRateDate').value = todayIsoStk();
        openModal(modal);
    }

    function initRateModal() {
        const modal = document.getElementById('stkRateModal');
        const form = document.getElementById('stkRateForm');
        if (!modal || !form) return;
        document.getElementById('stkAddRateBtn')?.addEventListener('click', openRateModal);
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const body = {
                currency_code: fd.get('currency_code').trim().toUpperCase(),
                rate_to_aoa: Number(fd.get('rate_to_aoa')) || 0,
                effective_date: fd.get('effective_date') || null,
            };
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/exchange-rates`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao guardar taxa');
                }
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível guardar a taxa.');
                return;
            }
            await refreshRates();
            closeModal(modal);
            UIToast.success('Taxa de câmbio guardada com sucesso!');
        });
    }

    async function refreshRates() {
        await loadRates();
        renderRates();
        populateSelects();
    }

    /* ---------- Alertas ---------- */
    async function loadAlerts() {
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/stock/alerts`);
            state.alerts = res.ok ? await res.json() : [];
        } catch (e) { state.alerts = []; }
    }

    function renderAlerts() {
        const host = document.getElementById('stkAlertsList');
        const empty = document.getElementById('stkAlertsEmpty');
        if (!host) return;
        if (!state.alerts.length) {
            host.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        host.innerHTML = state.alerts.map(a => `
            <article class="train-card">
                <div class="train-head">
                    <div class="train-info">
                        <div class="train-title">${escapeHtml(a.item_name)}</div>
                        <div class="train-meta">
                            ${escapeHtml(a.warehouse_name)}${a.sku ? ' · ' + escapeHtml(a.sku) : ''}
                            · Stock actual: <strong>${fmtNum(a.quantity)}</strong>
                            · Mínimo: <strong>${fmtNum(a.min_stock)}</strong>
                        </div>
                    </div>
                    <div class="train-aside">
                        <span class="status-pill status-pill--off">Abaixo do mínimo</span>
                    </div>
                </div>
            </article>
        `).join('');
    }

    async function refreshAlerts() {
        await loadAlerts();
        renderAlerts();
    }

    /* ---------- Relatórios ---------- */
    function renderTurnover(rows) {
        const host = document.getElementById('stkTurnoverList');
        if (!host) return;
        if (!rows.length) {
            host.innerHTML = '<li class="attach-empty">Sem saídas de stock neste período.</li>';
            return;
        }
        const max = Math.max(...rows.map(r => Number(r.quantity_saida) || 0), 1);
        host.innerHTML = rows.slice(0, 10).map(r => `
            <li class="report-bar-row">
                <div class="report-bar-label"><span>${escapeHtml(r.item_name)}</span><strong>${fmtNum(r.quantity_saida)}</strong></div>
                <div class="report-bar-track"><div class="report-bar-fill" style="width:${Math.max(4, (r.quantity_saida / max) * 100)}%"></div></div>
            </li>
        `).join('');
    }

    function renderMargins(rows) {
        const tbody = document.getElementById('stkMarginsTableBody');
        const empty = document.getElementById('stkMarginsEmpty');
        if (!tbody) return;
        if (!rows.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = rows.map(r => `
            <tr>
                <td>${escapeHtml(r.item_name)}</td>
                <td class="num">${fmtNum(r.quantity_vendida)}</td>
                <td class="num">${fmtKz(r.custo_total)}</td>
                <td class="num">${fmtKz(r.receita_total)}</td>
                <td class="num">${fmtKz(r.margem)}</td>
                <td class="num">${fmtNum(r.margem_percentual)}%</td>
            </tr>
        `).join('');
    }

    async function applyReports() {
        const from = document.getElementById('stkRepFrom')?.value;
        const to = document.getElementById('stkRepTo')?.value;
        if (!from || !to) return;
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/reports/turnover?date_from=${from}&date_to=${to}`);
            renderTurnover(res.ok ? await res.json() : []);
        } catch (e) { renderTurnover([]); }
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/reports/margins?date_from=${from}&date_to=${to}`);
            renderMargins(res.ok ? await res.json() : []);
        } catch (e) { renderMargins([]); }
    }

    async function exportSaft() {
        const from = document.getElementById('stkRepFrom')?.value;
        const to = document.getElementById('stkRepTo')?.value;
        if (!from || !to) { await UIModal.alert('Escolha o período (de/até) antes de exportar.'); return; }
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/saft/export?date_from=${from}&date_to=${to}`);
            if (!res.ok) throw new Error();
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `SAFT_AO_${from}_${to}.xml`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (e) {
            await UIModal.alert('Não foi possível exportar o SAF-T.');
        }
    }

    /* ---------- Guias de Transporte ---------- */
    async function loadGuides() {
        try {
            const status = document.getElementById('stkGuideFilterStatus')?.value || '';
            const qs = status ? `?status=${status}` : '';
            const res = await apiFetch(`${API_BASE.STOCK}/transport-guides${qs}`);
            state.guides = res.ok ? await res.json() : [];
        } catch (e) { state.guides = []; }
    }

    function renderGuides() {
        const tbody = document.getElementById('stkGuidesTableBody');
        const empty = document.getElementById('stkGuidesEmpty');
        if (!tbody) return;
        if (!state.guides.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.guides.map(g => `
            <tr>
                <td><code class="inv-doc">${escapeHtml(g.doc_number)}</code></td>
                <td>${fmtDateStk(g.issue_date)}</td>
                <td>${escapeHtml(warehouseName(g.origin_warehouse_id))}</td>
                <td>${g.destination_warehouse_id ? escapeHtml(warehouseName(g.destination_warehouse_id)) : escapeHtml(g.destination_name || '—')}</td>
                <td><span class="inv-status ${GUIDE_STATUS_CLASS[g.status] || ''}">${GUIDE_STATUS_LABEL[g.status] || g.status}</span></td>
                <td class="action-col">
                    <button class="row-action" data-guide-detail="${g.id}" aria-label="Ver detalhes" title="Ver detalhes">
                        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8 C3 4.8 5.3 3 8 3 s5 1.8 6.5 5 C13 11.2 10.7 13 8 13 s-5-1.8-6.5-5 z" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>
                    </button>
                </td>
            </tr>
        `).join('');
    }

    let guideLineCount = 0;

    function addGuideLineRow(itemId, quantity) {
        guideLineCount++;
        const host = document.getElementById('stkGuideLines');
        if (!host) return;
        const row = document.createElement('div');
        row.className = 'form-row';
        row.dataset.guideLine = String(guideLineCount);
        row.innerHTML = `
            <div class="form-field">
                <select class="form-input stk-guide-line-item" required>
                    <option value="">— Artigo —</option>
                    ${itemOptionsHtml()}
                </select>
            </div>
            <div class="form-field" style="max-width:140px;">
                <input class="form-input stk-guide-line-qty" type="number" min="0" step="0.001" placeholder="Qtd." required>
            </div>
            <button type="button" class="btn-icon-mini stk-guide-line-remove" aria-label="Remover linha">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
            </button>`;
        host.appendChild(row);
        if (itemId) row.querySelector('.stk-guide-line-item').value = itemId;
        if (quantity) row.querySelector('.stk-guide-line-qty').value = quantity;
        row.querySelector('.stk-guide-line-remove').addEventListener('click', () => row.remove());
    }

    function openGuideModal() {
        const modal = document.getElementById('stkGuideModal');
        const form = document.getElementById('stkGuideForm');
        if (!modal || !form) return;
        form.reset();
        populateSelects();
        document.getElementById('stkGuideIssueDate').value = todayIsoStk();
        const linesHost = document.getElementById('stkGuideLines');
        if (linesHost) linesHost.innerHTML = '';
        guideLineCount = 0;
        addGuideLineRow();
        openModal(modal);
    }

    function initGuideModal() {
        const modal = document.getElementById('stkGuideModal');
        const form = document.getElementById('stkGuideForm');
        if (!modal || !form) return;
        document.getElementById('stkAddGuideBtn')?.addEventListener('click', openGuideModal);
        document.getElementById('stkAddGuideTopBtn')?.addEventListener('click', openGuideModal);
        document.getElementById('stkGuideAddLineBtn')?.addEventListener('click', () => addGuideLineRow());
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const lines = Array.from(document.querySelectorAll('#stkGuideLines [data-guide-line]')).map(row => ({
                item_id: Number(row.querySelector('.stk-guide-line-item').value) || null,
                quantity: Number(row.querySelector('.stk-guide-line-qty').value) || 0,
            })).filter(l => l.item_id && l.quantity > 0);
            if (!lines.length) { await UIModal.alert('Adicione pelo menos um artigo à guia.'); return; }
            const body = {
                origin_warehouse_id: Number(fd.get('origin_warehouse_id')),
                destination_warehouse_id: fd.get('destination_warehouse_id') ? Number(fd.get('destination_warehouse_id')) : null,
                destination_name: (fd.get('destination_name') || '').trim() || null,
                destination_nif: (fd.get('destination_nif') || '').trim() || null,
                destination_address: (fd.get('destination_address') || '').trim() || null,
                transporter_name: (fd.get('transporter_name') || '').trim() || null,
                vehicle_plate: (fd.get('vehicle_plate') || '').trim() || null,
                issue_date: fd.get('issue_date') || null,
                notes: (fd.get('notes') || '').trim() || null,
                lines,
            };
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/transport-guides`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao emitir guia');
                }
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível emitir a guia.');
                return;
            }
            await refreshGuides();
            await loadSummary();
            closeModal(modal);
            UIToast.success('Guia de transporte emitida com sucesso!');
        });
    }

    async function refreshGuides() {
        await loadGuides();
        renderGuides();
    }

    let currentGuideId = null;

    async function openGuideDetail(guideId) {
        const modal = document.getElementById('stkGuideDetailModal');
        if (!modal) return;
        currentGuideId = guideId;
        const infoHost = document.getElementById('stkGuideDetInfo');
        const linesHost = document.getElementById('stkGuideDetLines');
        const docsHost = document.getElementById('stkGuideDetDocs');
        infoHost.innerHTML = '<p class="field-hint">A carregar...</p>';
        linesHost.innerHTML = '';
        docsHost.innerHTML = '';
        document.getElementById('stkGuideAtcud').value = '';
        document.getElementById('stkGuideValCode').value = '';
        document.getElementById('stkGuideHash').value = '';
        openModal(modal);

        let guide = null;
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/transport-guides/${guideId}`);
            if (res.ok) guide = await res.json();
        } catch (e) {}
        if (!guide) { infoHost.innerHTML = '<p class="field-hint">Guia não encontrada.</p>'; return; }

        document.getElementById('stkGuideDetailTitle').textContent = `Guia ${guide.doc_number}`;
        infoHost.innerHTML = `
            <div class="sal-preview-inner">
                <div class="sal-row"><span>Origem</span><span>${escapeHtml(warehouseName(guide.origin_warehouse_id))}</span></div>
                <div class="sal-row"><span>Destino</span><span>${guide.destination_warehouse_id
                    ? escapeHtml(warehouseName(guide.destination_warehouse_id))
                    : escapeHtml(guide.destination_name || '—')}</span></div>
                <div class="sal-row"><span>Data de emissão</span><span>${fmtDateStk(guide.issue_date)}</span></div>
                <div class="sal-row"><span>Estado</span><span class="inv-status ${GUIDE_STATUS_CLASS[guide.status] || ''}">${GUIDE_STATUS_LABEL[guide.status] || guide.status}</span></div>
                ${guide.transporter_name ? `<div class="sal-row"><span>Transportador</span><span>${escapeHtml(guide.transporter_name)}${guide.vehicle_plate ? ' · ' + escapeHtml(guide.vehicle_plate) : ''}</span></div>` : ''}
            </div>`;

        linesHost.innerHTML = (guide.lines || []).map(l => `
            <li class="attach-item">
                <span class="attach-name">${escapeHtml(l.item_name)}${l.item_sku ? ' (' + escapeHtml(l.item_sku) + ')' : ''}</span>
                <span>${fmtNum(l.quantity)}</span>
            </li>
        `).join('') || '<li class="attach-empty">Sem linhas.</li>';

        document.getElementById('stkGuideAtcud').value = guide.atcud || '';
        document.getElementById('stkGuideValCode').value = guide.agt_validation_code || '';
        document.getElementById('stkGuideHash').value = guide.agt_hash || '';

        const cancelBtn = document.getElementById('stkGuideCancelBtn');
        if (cancelBtn) cancelBtn.hidden = guide.status === 'anulada';

        loadStkDocs(guideId, docsHost);
    }

    function initGuideDetailModal() {
        const modal = document.getElementById('stkGuideDetailModal');
        if (!modal) return;
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        document.getElementById('stkGuideValidateForm')?.addEventListener('submit', async e => {
            e.preventDefault();
            if (!currentGuideId) return;
            const body = {
                atcud: document.getElementById('stkGuideAtcud').value.trim() || null,
                agt_validation_code: document.getElementById('stkGuideValCode').value.trim() || null,
                agt_hash: document.getElementById('stkGuideHash').value.trim() || null,
            };
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/transport-guides/${currentGuideId}/validate`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error();
            } catch (err) {
                await UIModal.alert('Não foi possível registar a validação.');
                return;
            }
            await refreshGuides();
            await openGuideDetail(currentGuideId);
            UIToast.success('Validação registada com sucesso!');
        });

        document.getElementById('stkGuideCancelBtn')?.addEventListener('click', async () => {
            if (!currentGuideId) return;
            if (!await UIModal.confirm('Anular esta guia de transporte?', { danger: true, okLabel: 'Anular' })) return;
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/transport-guides/${currentGuideId}/cancel`, { method: 'PATCH' });
                if (!res.ok) throw new Error();
            } catch (err) {
                await UIModal.alert('Não foi possível anular a guia.');
                return;
            }
            await refreshGuides();
            await openGuideDetail(currentGuideId);
            UIToast.success('Guia anulada com sucesso!');
        });

        document.getElementById('stkGuideDetUploadForm')?.addEventListener('submit', e => {
            e.preventDefault();
            uploadStkDoc(currentGuideId,
                document.getElementById('stkGuideDetDocFile'),
                document.getElementById('stkGuideDetDocType'),
                document.getElementById('stkGuideDetDocs'));
        });

        document.addEventListener('click', async e => {
            const delBtn = e.target.closest('[data-stk-doc-del]');
            if (!delBtn) return;
            if (!await UIModal.confirm('Apagar este anexo?', { danger: true, okLabel: 'Apagar' })) return;
            try {
                const res = await apiFetch(`${API_BASE.STOCK}/documents/${delBtn.dataset.stkDocDel}`, { method: 'DELETE' });
                if (!res.ok) throw new Error();
            } catch (err) {
                await UIModal.alert('Não foi possível apagar o anexo.');
                return;
            }
            if (currentGuideId) loadStkDocs(currentGuideId, document.getElementById('stkGuideDetDocs'));
            UIToast.success('Anexo apagado com sucesso!');
        });
    }

    function initGuideTableEvents() {
        document.getElementById('stkGuidesTableBody')?.addEventListener('click', e => {
            const btn = e.target.closest('[data-guide-detail]');
            if (btn) openGuideDetail(Number(btn.dataset.guideDetail));
        });
        document.getElementById('stkGuideFilterStatus')?.addEventListener('change', refreshGuides);
    }

    /* ---------- Documentos anexados a guias ---------- */
    async function loadStkDocs(guideId, host) {
        if (!host) return;
        host.innerHTML = '<li class="attach-empty">A carregar anexos...</li>';
        try {
            const res = await apiFetch(`${API_BASE.STOCK}/transport-guides/${guideId}/documents`);
            if (!res.ok) throw new Error();
            const docs = await res.json();
            if (!docs.length) { host.innerHTML = '<li class="attach-empty">Sem anexos ainda.</li>'; return; }
            const withUrls = await Promise.all(docs.map(async d => {
                try {
                    const r = await apiFetch(`${API_BASE.STOCK}/documents/${d.id}/presigned-url`);
                    const j = r.ok ? await r.json() : {};
                    return { ...d, url: j.url || null };
                } catch (e) { return { ...d, url: null }; }
            }));
            host.innerHTML = withUrls.map(d => `
                <li class="attach-item">
                    <span class="attach-name">${escapeHtml(d.filename)} <span class="attach-tag">${escapeHtml(d.document_type || 'anexo')}</span></span>
                    <span class="attach-item-actions">
                        ${d.url ? `<a class="attach-download" href="${d.url}" target="_blank" rel="noopener">Transferir</a>` : ''}
                        <button type="button" class="btn-icon-mini" data-stk-doc-del="${d.id}" aria-label="Apagar anexo">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4.5 H13 M6.5 4.5 V3 H9.5 V4.5 M5 4.5 L5.5 13.5 H10.5 L11 4.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                        </button>
                    </span>
                </li>
            `).join('');
        } catch (e) {
            host.innerHTML = '<li class="attach-empty">Não foi possível carregar os anexos.</li>';
        }
    }

    async function uploadStkDoc(guideId, fileInput, typeInput, host) {
        const file = fileInput?.files?.[0];
        if (!file || !guideId) return;
        const docType = (typeInput?.value || '').trim() || 'anexo';
        const fd = new FormData();
        fd.append('file', file);
        try {
            const res = await apiFetch(
                `${API_BASE.STOCK}/transport-guides/${guideId}/documents?document_type=${encodeURIComponent(docType)}`,
                { method: 'POST', body: fd }
            );
            if (!res.ok) throw new Error();
        } catch (e) {
            await UIModal.alert('Não foi possível enviar o anexo.');
            return;
        }
        fileInput.value = '';
        if (typeInput) typeInput.value = '';
        loadStkDocs(guideId, host);
        UIToast.success('Anexo enviado com sucesso!');
    }

    /* ---------- Tabs ---------- */
    function initTabs() {
        document.querySelectorAll('[data-stocktab]').forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.dataset.stocktab;
                document.querySelectorAll('[data-stocktab]').forEach(t => {
                    const active = t === tab;
                    t.classList.toggle('is-active', active);
                    t.setAttribute('aria-selected', String(active));
                });
                document.querySelectorAll('[data-stockpane]').forEach(p => {
                    p.classList.toggle('is-active', p.dataset.stockpane === target);
                });
                if (target === 'armazens') refreshWarehouses();
                if (target === 'movimentos') refreshMovements();
                if (target === 'lotes') refreshBatches();
                if (target === 'guias') refreshGuides();
                if (target === 'cambio') refreshRates();
                if (target === 'alertas') refreshAlerts();
                if (target === 'relatorios') applyReports();
                if (target === 'documentos') ScopedDocs.mount('stockDocsWidget', 'Stock');
            });
        });
    }

    /* ---------- Init ---------- */
    async function init() {
        if (!document.querySelector('[data-view="stock"]')) return;

        initTabs();
        initItemModal();
        initItemTableEvents();
        initWarehouseModal();
        initWarehouseTableEvents();
        initMovementModal();
        initRateModal();
        initGuideModal();
        initGuideDetailModal();
        initGuideTableEvents();
        document.getElementById('stkBatchFilterExpiry')?.addEventListener('change', refreshBatches);
        document.getElementById('stkRepApplyBtn')?.addEventListener('click', applyReports);
        document.getElementById('stkSaftExportBtn')?.addEventListener('click', exportSaft);

        const monthStart = `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, '0')}-01`;
        const fromEl = document.getElementById('stkRepFrom');
        const toEl = document.getElementById('stkRepTo');
        if (fromEl) fromEl.value = monthStart;
        if (toEl) toEl.value = todayIsoStk();

        await Promise.all([loadWarehouses(), loadRates()]);
        populateSelects();
        await refreshItems();
        await loadSummary();
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', STOCK.init);

/* ============================================================
   ACCOUNTING MODULE — plano de contas, lançamentos, livro razão,
   balancete, DRE e balanço patrimonial (accounting-service)
   ============================================================ */

const ACCOUNTING = (() => {
    const state = { accounts: [], entries: [] };

    /* ---------- Helpers locais ---------- */
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    function fmtKz(val) {
        return Number(val || 0).toLocaleString('pt-AO') + ' Kz';
    }
    function fmtNum(val) {
        return Number(val || 0).toLocaleString('pt-AO', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function fmtDate(iso) {
        if (!iso) return '—';
        const [y, m, d] = iso.split('-');
        return `${d}/${m}/${y}`;
    }
    function todayIso() {
        return new Date().toISOString().slice(0, 10);
    }
    function openModal(el) {
        if (!el) return;
        el.classList.add('is-open');
        el.setAttribute('aria-hidden', 'false');
    }
    function closeModal(el) {
        if (!el) return;
        el.classList.remove('is-open');
        el.setAttribute('aria-hidden', 'true');
    }
    function setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    const CLASS_LABEL = {
        ativo: 'Ativo', passivo: 'Passivo', patrimonio: 'Património Líquido',
        receita: 'Receita', despesa: 'Despesa',
    };
    const SOURCE_LABEL = { manual: 'Manual', finance: 'Financeiro' };
    const ENTRY_STATUS_LABEL = { lancado: 'Lançado', estornado: 'Estornado' };
    const ENTRY_STATUS_CLASS = { lancado: 'inv-status--paid', estornado: 'inv-status--overdue' };

    function mapAccount(a) {
        return {
            id: a.id, code: a.code, name: a.name, accountClass: a.account_class,
            accountType: a.account_type, parentId: a.parent_id, isSystem: !!a.is_system,
            active: !!a.active,
        };
    }
    function mapEntry(e) {
        const lines = (e.lines || []).map(l => ({
            id: l.id, accountId: l.account_id, accountCode: l.account_code, accountName: l.account_name,
            debit: Number(l.debit) || 0, credit: Number(l.credit) || 0, memo: l.memo || '',
        }));
        return {
            id: e.id, docNumber: e.doc_number, entryDate: e.entry_date, description: e.description,
            source: e.source, sourceType: e.source_type, sourceId: e.source_id, status: e.status,
            lines, total: lines.reduce((sum, l) => sum + l.debit, 0),
        };
    }

    /* ---------- Resumo / KPIs ---------- */
    async function loadSummary() {
        try {
            const res = await apiFetch(`${API_BASE.ACCOUNTING}/summary`);
            if (!res.ok) throw new Error('Falha ao carregar resumo');
            const s = await res.json();
            setText('accKpiSaldoCaixa', fmtKz(s.saldo_caixa_bancos));
            setText('accKpiAReceber', fmtKz(s.total_a_receber));
            setText('accKpiAPagar', fmtKz(s.total_a_pagar));
            setText('accKpiLancamentosMes', s.lancamentos_mes);
            setText('accKpiResultadoMes', fmtKz(s.resultado_mes));
        } catch (e) {
            setText('accKpiSaldoCaixa', 'Kz 0');
            setText('accKpiAReceber', 'Kz 0');
            setText('accKpiAPagar', 'Kz 0');
            setText('accKpiLancamentosMes', '0');
            setText('accKpiResultadoMes', 'Kz 0');
        }
    }

    /* ---------- Plano de Contas ---------- */
    async function loadAccounts() {
        try {
            const cls = document.getElementById('accFilterClass')?.value || '';
            const qs = cls ? `?account_class=${encodeURIComponent(cls)}` : '';
            const res = await apiFetch(`${API_BASE.ACCOUNTING}/accounts${qs}`);
            if (!res.ok) throw new Error('Falha ao carregar contas');
            state.accounts = (await res.json()).map(mapAccount);
        } catch (e) { state.accounts = []; }
    }

    function renderAccounts() {
        const tbody = document.getElementById('accAccountsTableBody');
        const empty = document.getElementById('accAccountsEmpty');
        if (!tbody) return;
        if (!state.accounts.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.accounts.map(a => `
            <tr data-id="${a.id}">
                <td><code class="inv-doc">${escapeHtml(a.code)}</code></td>
                <td>${escapeHtml(a.name)}</td>
                <td>${CLASS_LABEL[a.accountClass] || a.accountClass}</td>
                <td>${a.accountType === 'analitica' ? 'Analítica' : 'Sintética'}</td>
                <td><span class="status-pill ${a.active ? 'status-pill--online' : 'status-pill--off'}">${a.active ? 'Ativa' : 'Inativa'}</span></td>
                <td class="action-col">
                    ${!a.isSystem ? `
                        <button class="row-action" data-acc-del="${a.id}" aria-label="Apagar" title="Apagar">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4.5 H13 M6.5 4.5 V3 H9.5 V4.5 M5 4.5 L5.5 13.5 H10.5 L11 4.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                        </button>
                    ` : ''}
                </td>
            </tr>
        `).join('');
    }

    function populateAccountSelects() {
        const postable = state.accounts.filter(a => a.accountType === 'analitica' && a.active);
        const razaoSel = document.getElementById('razaoAccountSelect');
        if (razaoSel) {
            const cur = razaoSel.value;
            razaoSel.innerHTML = postable.map(a => `<option value="${a.id}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`).join('');
            if (cur) razaoSel.value = cur;
        }
        const parentSel = document.getElementById('accParent');
        if (parentSel) {
            parentSel.innerHTML = '<option value="">— Nenhuma —</option>' +
                state.accounts.map(a => `<option value="${a.id}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`).join('');
        }
    }

    async function refreshAccounts() {
        await loadAccounts();
        renderAccounts();
        populateAccountSelects();
    }

    function initAccountsTab() {
        document.getElementById('accFilterClass')?.addEventListener('change', refreshAccounts);
        document.getElementById('accAccountsTableBody')?.addEventListener('click', async e => {
            const delBtn = e.target.closest('[data-acc-del]');
            if (delBtn) {
                if (!(await UIModal.confirm('Apagar esta conta?'))) return;
                try {
                    const res = await apiFetch(`${API_BASE.ACCOUNTING}/accounts/${delBtn.dataset.accDel}`, { method: 'DELETE' });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || 'Falha ao apagar');
                    }
                    await refreshAccounts();
                    UIToast.success('Conta apagada.');
                } catch (err) {
                    await UIModal.alert(err.message);
                }
            }
        });
    }

    function initAccountModal() {
        const modal = document.getElementById('accAccountModal');
        const form = document.getElementById('accAccountForm');
        if (!modal || !form) return;

        document.getElementById('accAddAccountBtn')?.addEventListener('click', () => {
            form.reset();
            openModal(modal);
        });
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(form);
            const body = {
                code: fd.get('code').trim(),
                name: fd.get('name').trim(),
                account_class: fd.get('account_class'),
                parent_id: fd.get('parent_id') ? Number(fd.get('parent_id')) : null,
            };
            try {
                const res = await apiFetch(`${API_BASE.ACCOUNTING}/accounts`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao criar conta');
                }
            } catch (err) {
                await UIModal.alert(err.message);
                return;
            }
            await refreshAccounts();
            closeModal(modal);
            UIToast.success('Conta criada com sucesso!');
        });
    }

    /* ---------- Lançamentos ---------- */
    async function loadEntries() {
        try {
            const params = new URLSearchParams();
            const from = document.getElementById('accEntriesFrom')?.value || '';
            const to = document.getElementById('accEntriesTo')?.value || '';
            if (from) params.set('date_from', from);
            if (to) params.set('date_to', to);
            const qs = params.toString() ? `?${params.toString()}` : '';
            const res = await apiFetch(`${API_BASE.ACCOUNTING}/entries${qs}`);
            if (!res.ok) throw new Error('Falha ao carregar lançamentos');
            state.entries = (await res.json()).map(mapEntry);
        } catch (e) { state.entries = []; }
    }

    function renderEntries() {
        const tbody = document.getElementById('accEntriesTableBody');
        const empty = document.getElementById('accEntriesEmpty');
        if (!tbody) return;
        if (!state.entries.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.entries.map(en => `
            <tr data-id="${en.id}">
                <td><code class="inv-doc">${escapeHtml(en.docNumber)}</code></td>
                <td>${fmtDate(en.entryDate)}</td>
                <td>${escapeHtml(en.description)}</td>
                <td>${SOURCE_LABEL[en.source] || en.source}</td>
                <td class="num">${fmtKz(en.total)}</td>
                <td><span class="inv-status ${ENTRY_STATUS_CLASS[en.status] || ''}">${ENTRY_STATUS_LABEL[en.status] || en.status}</span></td>
                <td class="action-col">
                    ${en.status === 'lancado' ? `
                        <button class="row-action" data-entry-reverse="${en.id}" aria-label="Reverter" title="Reverter (estorno)">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 8 a4 4 0 1 1 1.2 2.8 M4 8 V5 M4 8 H7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                        </button>
                    ` : ''}
                </td>
            </tr>
        `).join('');
    }

    async function refreshEntries() {
        await loadEntries();
        renderEntries();
    }

    function initEntriesTab() {
        document.getElementById('accEntriesApplyBtn')?.addEventListener('click', refreshEntries);
        document.getElementById('accEntriesTableBody')?.addEventListener('click', async e => {
            const revBtn = e.target.closest('[data-entry-reverse]');
            if (revBtn) {
                if (!(await UIModal.confirm('Reverter este lançamento? Será criado um estorno.'))) return;
                try {
                    const res = await apiFetch(`${API_BASE.ACCOUNTING}/entries/${revBtn.dataset.entryReverse}/reverse`, { method: 'POST' });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || 'Falha ao reverter');
                    }
                    await refreshEntries();
                    await loadSummary();
                    UIToast.success('Lançamento revertido.');
                } catch (err) {
                    await UIModal.alert(err.message);
                }
            }
        });
    }

    /* ---------- Livro Razão ---------- */
    async function renderRazao() {
        const tbody = document.getElementById('accRazaoTableBody');
        const empty = document.getElementById('accRazaoEmpty');
        if (!tbody) return;
        const accountId = document.getElementById('razaoAccountSelect')?.value;
        if (!accountId) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        try {
            const params = new URLSearchParams();
            const from = document.getElementById('razaoFrom')?.value || '';
            const to = document.getElementById('razaoTo')?.value || '';
            if (from) params.set('date_from', from);
            if (to) params.set('date_to', to);
            const qs = params.toString() ? `?${params.toString()}` : '';
            const res = await apiFetch(`${API_BASE.ACCOUNTING}/ledger/${accountId}${qs}`);
            if (!res.ok) throw new Error('Falha ao carregar livro razão');
            const data = await res.json();
            if (!data.movements.length) {
                tbody.innerHTML = '';
                if (empty) empty.hidden = false;
                return;
            }
            if (empty) empty.hidden = true;
            tbody.innerHTML = data.movements.map(m => `
                <tr>
                    <td><code class="inv-doc">${escapeHtml(m.doc_number)}</code></td>
                    <td>${fmtDate(m.entry_date)}</td>
                    <td>${escapeHtml(m.description)}</td>
                    <td class="num">${m.debit ? fmtNum(m.debit) : ''}</td>
                    <td class="num">${m.credit ? fmtNum(m.credit) : ''}</td>
                    <td class="num">${fmtNum(m.running_balance)}</td>
                </tr>
            `).join('');
        } catch (e) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
        }
    }

    function initRazaoTab() {
        document.getElementById('razaoAccountSelect')?.addEventListener('change', renderRazao);
        document.getElementById('razaoApplyBtn')?.addEventListener('click', renderRazao);
    }

    /* ---------- Balancete ---------- */
    async function renderBalancete() {
        const tbody = document.getElementById('accBalanceteTableBody');
        const empty = document.getElementById('accBalanceteEmpty');
        if (!tbody) return;
        try {
            const params = new URLSearchParams();
            const from = document.getElementById('balanceteFrom')?.value || '';
            const to = document.getElementById('balanceteTo')?.value || '';
            if (from) params.set('date_from', from);
            if (to) params.set('date_to', to);
            const qs = params.toString() ? `?${params.toString()}` : '';
            const res = await apiFetch(`${API_BASE.ACCOUNTING}/balancete${qs}`);
            if (!res.ok) throw new Error('Falha ao carregar balancete');
            const data = await res.json();
            if (!data.accounts.length) {
                tbody.innerHTML = '';
                if (empty) empty.hidden = false;
                setText('balanceteTotalDebit', fmtKz(0));
                setText('balanceteTotalCredit', fmtKz(0));
                return;
            }
            if (empty) empty.hidden = true;
            tbody.innerHTML = data.accounts.map(a => `
                <tr>
                    <td><code class="inv-doc">${escapeHtml(a.code)}</code></td>
                    <td>${escapeHtml(a.name)}</td>
                    <td class="num">${fmtNum(a.debit_total)}</td>
                    <td class="num">${fmtNum(a.credit_total)}</td>
                    <td class="num">${fmtNum(a.balance)}</td>
                </tr>
            `).join('');
            setText('balanceteTotalDebit', fmtKz(data.total_debit));
            setText('balanceteTotalCredit', fmtKz(data.total_credit));
        } catch (e) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
        }
    }

    function initBalanceteTab() {
        document.getElementById('balanceteApplyBtn')?.addEventListener('click', renderBalancete);
    }

    /* ---------- DRE ---------- */
    async function renderDRE() {
        const host = document.getElementById('accDreContent');
        if (!host) return;
        try {
            const params = new URLSearchParams();
            const from = document.getElementById('dreFrom')?.value || '';
            const to = document.getElementById('dreTo')?.value || '';
            if (from) params.set('date_from', from);
            if (to) params.set('date_to', to);
            const qs = params.toString() ? `?${params.toString()}` : '';
            const res = await apiFetch(`${API_BASE.ACCOUNTING}/dre${qs}`);
            if (!res.ok) throw new Error('Falha ao carregar DRE');
            const data = await res.json();
            const rowsHtml = (rows) => rows.map(r => `
                <li class="report-rank-row"><span class="report-rank-name">${escapeHtml(r.code)} — ${escapeHtml(r.name)}</span><strong>${fmtKz(r.total)}</strong></li>
            `).join('') || '<li class="attach-empty">Sem movimentos.</li>';
            host.innerHTML = `
                <section class="mid-row">
                    <article class="panel">
                        <header class="panel-head"><h3 class="panel-title">Receitas</h3></header>
                        <ul class="report-rank">${rowsHtml(data.receitas)}</ul>
                    </article>
                    <article class="panel">
                        <header class="panel-head"><h3 class="panel-title">Despesas</h3></header>
                        <ul class="report-rank">${rowsHtml(data.despesas)}</ul>
                    </article>
                </section>
                <section class="mid-row">
                    <article class="panel">
                        <header class="panel-head"><h3 class="panel-title">Resultado Líquido</h3></header>
                        <div class="stat-big">${fmtKz(data.resultado_liquido)}</div>
                        <p class="panel-sub">Receitas ${fmtKz(data.total_receitas)} − Despesas ${fmtKz(data.total_despesas)}</p>
                    </article>
                </section>
            `;
        } catch (e) {
            host.innerHTML = '<p class="attach-empty">Não foi possível carregar a DRE.</p>';
        }
    }

    function initDreTab() {
        document.getElementById('dreApplyBtn')?.addEventListener('click', renderDRE);
    }

    /* ---------- Balanço Patrimonial ---------- */
    async function renderBalanco() {
        const host = document.getElementById('accBalancoContent');
        if (!host) return;
        try {
            const asOf = document.getElementById('balancoAsOf')?.value || todayIso();
            const res = await apiFetch(`${API_BASE.ACCOUNTING}/balanco?as_of=${encodeURIComponent(asOf)}`);
            if (!res.ok) throw new Error('Falha ao carregar balanço');
            const data = await res.json();
            const rowsHtml = (rows) => rows.map(r => `
                <li class="report-rank-row"><span class="report-rank-name">${escapeHtml(r.code)} — ${escapeHtml(r.name)}</span><strong>${fmtKz(r.total)}</strong></li>
            `).join('') || '<li class="attach-empty">Sem movimentos.</li>';
            host.innerHTML = `
                <section class="mid-row">
                    <article class="panel">
                        <header class="panel-head"><h3 class="panel-title">Ativo</h3><p class="panel-sub">Total: ${fmtKz(data.total_ativo)}</p></header>
                        <ul class="report-rank">${rowsHtml(data.ativo)}</ul>
                    </article>
                    <article class="panel">
                        <header class="panel-head"><h3 class="panel-title">Passivo</h3><p class="panel-sub">Total: ${fmtKz(data.total_passivo)}</p></header>
                        <ul class="report-rank">${rowsHtml(data.passivo)}</ul>
                    </article>
                    <article class="panel">
                        <header class="panel-head"><h3 class="panel-title">Património Líquido</h3><p class="panel-sub">Total: ${fmtKz(data.total_patrimonio)}</p></header>
                        <ul class="report-rank">${rowsHtml(data.patrimonio)}</ul>
                    </article>
                </section>
                ${!data.balanceado ? '<p class="text-danger">Aviso: o balanço não está equilibrado (Ativo ≠ Passivo + Património).</p>' : ''}
            `;
        } catch (e) {
            host.innerHTML = '<p class="attach-empty">Não foi possível carregar o balanço.</p>';
        }
    }

    function initBalancoTab() {
        const asOfInput = document.getElementById('balancoAsOf');
        if (asOfInput && !asOfInput.value) asOfInput.value = todayIso();
        document.getElementById('balancoApplyBtn')?.addEventListener('click', renderBalanco);
    }

    /* ---------- Modal de novo lançamento (linhas dinâmicas) ---------- */
    function lineRowHtml() {
        const options = state.accounts
            .filter(a => a.accountType === 'analitica' && a.active)
            .map(a => `<option value="${a.id}">${escapeHtml(a.code)} — ${escapeHtml(a.name)}</option>`).join('');
        return `
            <div class="form-row acc-entry-line" style="align-items:flex-end; gap:8px;">
                <div class="form-field" style="flex:2;">
                    <select class="form-input" data-role="account">${options}</select>
                </div>
                <div class="form-field" style="flex:1;">
                    <input class="form-input" type="number" min="0" step="0.01" data-role="debit" placeholder="Débito">
                </div>
                <div class="form-field" style="flex:1;">
                    <input class="form-input" type="number" min="0" step="0.01" data-role="credit" placeholder="Crédito">
                </div>
                <button type="button" class="btn-danger-link" data-role="remove-line" style="margin-bottom:10px;">×</button>
            </div>
        `;
    }

    function recalcBalance() {
        const container = document.getElementById('accEntryLinesContainer');
        const submitBtn = document.getElementById('accEntrySubmitBtn');
        const statusEl = document.getElementById('accBalanceStatus');
        if (!container) return;
        let totalDebit = 0, totalCredit = 0;
        container.querySelectorAll('.acc-entry-line').forEach(row => {
            totalDebit += Number(row.querySelector('[data-role="debit"]').value) || 0;
            totalCredit += Number(row.querySelector('[data-role="credit"]').value) || 0;
        });
        setText('accTotalDebit', fmtNum(totalDebit));
        setText('accTotalCredit', fmtNum(totalCredit));
        const balanced = totalDebit > 0 && Math.abs(totalDebit - totalCredit) < 0.005;
        if (statusEl) {
            statusEl.textContent = balanced ? 'Balanceado' : 'Desequilibrado';
            statusEl.style.color = balanced ? '#10B981' : '#DC2626';
            statusEl.style.fontWeight = '600';
        }
        if (submitBtn) submitBtn.disabled = !balanced;
    }

    function addLineRow(container) {
        container.insertAdjacentHTML('beforeend', lineRowHtml());
        const row = container.lastElementChild;
        row.querySelectorAll('[data-role="debit"], [data-role="credit"]').forEach(input => {
            input.addEventListener('input', () => {
                const other = input.dataset.role === 'debit'
                    ? row.querySelector('[data-role="credit"]')
                    : row.querySelector('[data-role="debit"]');
                if (Number(input.value) > 0) other.value = '';
                recalcBalance();
            });
        });
        row.querySelector('[data-role="remove-line"]')?.addEventListener('click', () => {
            if (container.querySelectorAll('.acc-entry-line').length <= 2) return;
            row.remove();
            recalcBalance();
        });
        updateRemoveButtons(container);
    }

    function updateRemoveButtons(container) {
        const rows = container.querySelectorAll('.acc-entry-line');
        rows.forEach(row => {
            const btn = row.querySelector('[data-role="remove-line"]');
            if (btn) btn.disabled = rows.length <= 2;
        });
    }

    function initEntryModal() {
        const modal = document.getElementById('accEntryModal');
        const form = document.getElementById('accEntryForm');
        const container = document.getElementById('accEntryLinesContainer');
        if (!modal || !form || !container) return;

        document.getElementById('accAddEntryBtn')?.addEventListener('click', () => {
            form.reset();
            container.innerHTML = '';
            addLineRow(container);
            addLineRow(container);
            document.getElementById('accEntryDate').value = todayIso();
            recalcBalance();
            openModal(modal);
        });
        document.getElementById('accAddLineBtn')?.addEventListener('click', () => {
            addLineRow(container);
            recalcBalance();
        });
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const lines = [...container.querySelectorAll('.acc-entry-line')].map(row => ({
                account_id: Number(row.querySelector('[data-role="account"]').value),
                debit: Number(row.querySelector('[data-role="debit"]').value) || 0,
                credit: Number(row.querySelector('[data-role="credit"]').value) || 0,
            })).filter(l => l.debit > 0 || l.credit > 0);

            const body = {
                entry_date: document.getElementById('accEntryDate').value,
                description: document.getElementById('accEntryDescription').value.trim(),
                lines,
            };
            try {
                const res = await apiFetch(`${API_BASE.ACCOUNTING}/entries`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao lançar');
                }
            } catch (err) {
                await UIModal.alert(err.message);
                return;
            }
            await refreshEntries();
            await loadSummary();
            closeModal(modal);
            UIToast.success('Lançamento registado com sucesso!');
        });
    }

    /* ---------- Tabs ---------- */
    function initTabs() {
        document.querySelectorAll('[data-actab]').forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.dataset.actab;
                document.querySelectorAll('[data-actab]').forEach(t => {
                    const active = t === tab;
                    t.classList.toggle('is-active', active);
                    t.setAttribute('aria-selected', String(active));
                });
                document.querySelectorAll('[data-acpane]').forEach(p => {
                    p.classList.toggle('is-active', p.dataset.acpane === target);
                });
                if (target === 'lancamentos') refreshEntries();
                if (target === 'razao') renderRazao();
                if (target === 'balancete') renderBalancete();
                if (target === 'dre') renderDRE();
                if (target === 'balanco') renderBalanco();
                if (target === 'documentos') ScopedDocs.mount('accDocsWidget', 'Contabilidade');
            });
        });
    }

    /* ---------- Init ---------- */
    async function init() {
        if (!document.querySelector('[data-view="accounting"]')) return;

        initTabs();
        initAccountsTab();
        initAccountModal();
        initEntriesTab();
        initRazaoTab();
        initBalanceteTab();
        initDreTab();
        initBalancoTab();
        initEntryModal();

        await refreshAccounts();
        await refreshEntries();
        await loadSummary();
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', ACCOUNTING.init);

/* ============================================================
   PROJECTS MODULE — projectos, tarefas, quadro, calendário (projects-service)
   ============================================================ */

const Projects = (() => {
    const state = {
        projects: [],
        tasks: [],
        employees: [],
        tags: [],
        filterProject: '',
        filterPriority: '',
        collapsedGroups: new Set(),
        calYear: new Date().getFullYear(),
        calMonth: new Date().getMonth(),
    };

    const STATUS_LABEL = { todo: 'A Fazer', in_progress: 'Em Curso', review: 'Em Revisão', done: 'Concluído' };
    const PRIORITY_LABEL = { baixa: 'Baixa', media: 'Média', alta: 'Alta', urgente: 'Urgente' };
    const PROJECT_STATUS_LABEL = { ativo: 'Activo', pausado: 'Pausado', concluido: 'Concluído', cancelado: 'Cancelado' };
    const SWATCH_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#0EA5E9', '#6B7280'];
    const MONTH_NAMES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
    const WEEKDAY_LABELS = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];

    /* ---------- Helpers ---------- */
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    function pad(n) { return String(n).padStart(2, '0'); }
    function todayIso() {
        const d = new Date();
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    }
    function addDaysIso(iso, n) {
        const d = new Date(iso);
        d.setDate(d.getDate() + n);
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    }
    function fmtDate(iso) {
        if (!iso) return '—';
        const [y, m, d] = iso.split('-');
        return `${d}/${m}/${y}`;
    }
    function initials(name) {
        return (name || '').trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() || '').join('') || '?';
    }
    function avatarTone(id) {
        const tones = ['avatar--blue', 'avatar--teal', 'avatar--purple'];
        return tones[Number(id) % tones.length];
    }
    function avatarInner(id, name, photoUrl) {
        if (photoUrl) return `<img class="avatar-img" src="${photoUrl}" alt="${escapeHtml(name || '')}">`;
        return initials(name);
    }

    function project(id) { return state.projects.find(p => p.id === Number(id)); }
    function projectName(id) { return project(id)?.name || '—'; }
    function employee(id) { return state.employees.find(e => e.id === Number(id)); }
    function employeeName(id) { return id ? (employee(id)?.name || '—') : '— Sem responsável —'; }
    function tasksForProject(id) { return state.tasks.filter(t => t.projectId === Number(id)); }

    function assigneeAvatarHtml(id) {
        if (!id) return `<span class="avatar avatar--blue" style="width:24px;height:24px;font-size:10px">?</span>`;
        const e = employee(id);
        return `<span class="avatar ${avatarTone(id)}" style="width:24px;height:24px;font-size:10px">${avatarInner(id, e?.name, e?.photoUrl)}</span>`;
    }

    function tagPillsHtml(tags) {
        if (!tags || !tags.length) return '';
        return tags.map(t => `<span class="tag-pill" style="--tag-color:${t.color}">${escapeHtml(t.name)}</span>`).join('');
    }

    function isOverdue(task) {
        return task.dueDate && task.dueDate < todayIso() && task.status !== 'done';
    }

    /* ---------- Mapping ---------- */
    function mapProject(p) {
        return {
            id: p.id,
            name: p.name,
            description: p.description || '',
            color: p.color || '#3B82F6',
            status: p.status || 'ativo',
            dueDate: p.due_date || '',
        };
    }
    function mapTask(t) {
        return {
            id: t.id,
            projectId: t.project_id,
            title: t.title,
            description: t.description || '',
            status: t.status || 'todo',
            priority: t.priority || 'media',
            assigneeId: t.assignee_id || null,
            dueDate: t.due_date || '',
            position: t.position || 0,
            completedAt: t.completed_at || null,
            tags: (t.tags || []).map(tg => ({ id: tg.id, name: tg.name, color: tg.color })),
        };
    }

    /* ---------- Loading ---------- */
    async function loadProjects() {
        try {
            const res = await apiFetch(`${API_BASE.PROJECTS}/projects`);
            if (!res.ok) throw new Error('Falha ao carregar projectos');
            state.projects = (await res.json()).map(mapProject);
        } catch (e) { state.projects = []; }
    }
    async function loadTasks() {
        try {
            const res = await apiFetch(`${API_BASE.PROJECTS}/tasks`);
            if (!res.ok) throw new Error('Falha ao carregar tarefas');
            state.tasks = (await res.json()).map(mapTask);
        } catch (e) { state.tasks = []; }
    }
    async function loadEmployees() {
        try {
            const res = await apiFetch(`${API_BASE.RH}/employees`);
            if (!res.ok) throw new Error('Falha ao carregar funcionários');
            const employees = await res.json();
            state.employees = employees.map(e => ({ id: e.id, name: e.full_name, photoUrl: e.photo_url || '' }));
        } catch (e) { state.employees = []; }
    }
    async function loadAll() {
        await Promise.all([loadProjects(), loadTasks(), loadEmployees()]);
        renderAll();
    }
    function renderAll() {
        renderKpis();
        populateProjectSelects();
        renderProjectsTable();
        renderTaskList();
        renderKanban();
        renderCalendar();
    }

    /* ---------- KPIs ---------- */
    function renderKpis() {
        const totalEl = document.getElementById('projKpiTotal');
        const progEl = document.getElementById('projKpiInProgress');
        const overdueEl = document.getElementById('projKpiOverdue');
        const doneWeekEl = document.getElementById('projKpiDoneWeek');
        if (!totalEl) return;
        totalEl.textContent = state.projects.filter(p => p.status === 'ativo').length;
        progEl.textContent = state.tasks.filter(t => t.status === 'in_progress').length;
        overdueEl.textContent = state.tasks.filter(isOverdue).length;
        const weekAgo = addDaysIso(todayIso(), -7);
        doneWeekEl.textContent = state.tasks.filter(t => t.completedAt && t.completedAt.slice(0, 10) >= weekAgo).length;
    }

    /* ---------- Selects & filters ---------- */
    function populateProjectSelects() {
        const opts = state.projects.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');

        const filterSel = document.getElementById('projFilterProject');
        if (filterSel) {
            const current = filterSel.value;
            filterSel.innerHTML = `<option value="">Todos os projectos</option>${opts}`;
            filterSel.value = current;
        }

        const taskSel = document.getElementById('taskProject');
        if (taskSel) {
            const current = taskSel.value;
            taskSel.innerHTML = opts;
            if (current) taskSel.value = current;
        }
    }

    function populateAssigneeSelect() {
        const sel = document.getElementById('taskAssignee');
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = `<option value="">— Sem responsável —</option>` +
            state.employees.map(e => `<option value="${e.id}">${escapeHtml(e.name)}</option>`).join('');
        if (current) sel.value = current;
    }

    function filteredTasks() {
        return state.tasks.filter(t => {
            if (state.filterProject && t.projectId !== Number(state.filterProject)) return false;
            if (state.filterPriority && t.priority !== state.filterPriority) return false;
            return true;
        });
    }

    /* ---------- Vista: Projectos ---------- */
    function renderProjectsTable() {
        const body = document.getElementById('projTableBody');
        const empty = document.getElementById('projEmpty');
        if (!body) return;
        if (!state.projects.length) {
            body.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;

        body.innerHTML = state.projects.map(p => {
            const tasks = tasksForProject(p.id);
            const done = tasks.filter(t => t.status === 'done').length;
            const progress = tasks.length ? Math.round((done / tasks.length) * 100) : 0;
            const assigneeIds = [...new Set(tasks.map(t => t.assigneeId).filter(Boolean))].slice(0, 4);
            const team = assigneeIds.length
                ? `<div class="avatar-stack">${assigneeIds.map(id => {
                    const e = employee(id);
                    return `<span class="avatar ${avatarTone(id)}" title="${escapeHtml(e?.name || '')}">${avatarInner(id, e?.name, e?.photoUrl)}</span>`;
                }).join('')}</div>`
                : `<span class="field-hint">${tasks.length ? 'Sem responsável atribuído' : 'Sem tarefas ainda'}</span>`;

            return `
                <tr class="proj-row" data-proj-row="${p.id}">
                    <td class="proj-name">
                        ${escapeHtml(p.name)}
                        <span class="status-pill status-pill--${p.status === 'ativo' ? 'online' : 'off'}" style="margin-left:8px">${PROJECT_STATUS_LABEL[p.status] || p.status}</span>
                    </td>
                    <td class="proj-progress">
                        <div class="bar"><span class="bar-fill" data-progress="${progress}"></span></div>
                        <span class="bar-label">${progress}%</span>
                    </td>
                    <td class="proj-date">
                        <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.4" fill="none"/><path d="M8 5 v3 l2 1.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>
                        <span>${fmtDate(p.dueDate)}</span>
                    </td>
                    <td class="proj-team">${team}</td>
                    <td class="action-col">
                        <button class="btn-icon-mini" data-view-tasks="${p.id}" aria-label="Ver tarefas" title="Ver tarefas">
                            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3 L11 8 L6 13" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');

        runProgressBarsInScope(document.querySelector('[data-projpane="projectos"]') || document);
    }

    /* ---------- Vista: Lista ---------- */
    const PRIORITY_FLAG_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2 V14 M4 2.5 H12 L9.5 5.5 L12 8.5 H4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/></svg>';
    function priorityFlagHtml(priority) {
        return `<span class="priority-flag priority-flag--${priority}">${PRIORITY_FLAG_ICON}${PRIORITY_LABEL[priority]}</span>`;
    }

    function taskRowHtml(t) {
        return `
            <tr data-task-row="${t.id}">
                <td>${escapeHtml(t.title)}</td>
                <td>${escapeHtml(projectName(t.projectId))}</td>
                <td>${priorityFlagHtml(t.priority)}</td>
                <td>
                    <span class="col-owner">
                        ${assigneeAvatarHtml(t.assigneeId)}
                        ${escapeHtml(employeeName(t.assigneeId))}
                    </span>
                </td>
                <td class="${isOverdue(t) ? 'task-card-due is-overdue' : ''}">${fmtDate(t.dueDate)}</td>
                <td>${tagPillsHtml(t.tags) || '—'}</td>
            </tr>
        `;
    }

    function taskGroupHtml(status, tasks) {
        const collapsed = state.collapsedGroups.has(status) ? 'is-collapsed' : '';
        const rows = tasks.length
            ? `<table class="task-group-table">
                <thead>
                    <tr><th>Tarefa</th><th>Projecto</th><th>Prioridade</th><th>Responsável</th><th>Prazo</th><th>Etiquetas</th></tr>
                </thead>
                <tbody>${tasks.map(taskRowHtml).join('')}</tbody>
               </table>`
            : '<p class="task-group-empty">Sem tarefas neste estado.</p>';

        return `
            <div class="task-group ${collapsed}" data-status-group="${status}">
                <button type="button" class="task-group-header" data-toggle-group="${status}">
                    <svg class="task-group-caret" viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3 L11 8 L5 13" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    <span class="status-pill status-pill--${status}">${STATUS_LABEL[status]}</span>
                    <span class="task-group-count">${tasks.length}</span>
                </button>
                <div class="task-group-body">
                    ${rows}
                    <button type="button" class="task-group-add" data-add-task-status="${status}">
                        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3 V13 M3 8 H13" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
                        Adicionar tarefa
                    </button>
                </div>
            </div>
        `;
    }

    function renderTaskList() {
        const container = document.getElementById('taskGroups');
        const empty = document.getElementById('taskListEmpty');
        if (!container) return;

        if (!state.projects.length) {
            container.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;

        const list = filteredTasks();
        container.innerHTML = Object.keys(STATUS_LABEL)
            .map(status => taskGroupHtml(status, list.filter(t => t.status === status)))
            .join('');
    }

    /* ---------- Vista: Quadro ---------- */
    function taskCardHtml(t) {
        const due = t.dueDate ? `<span class="task-card-due ${isOverdue(t) ? 'is-overdue' : ''}">${fmtDate(t.dueDate)}</span>` : '<span></span>';
        return `
            <article class="task-card" draggable="true" data-id="${t.id}">
                <div class="task-card-top">
                    <h4 class="task-card-title">${escapeHtml(t.title)}</h4>
                    <span class="priority-pill priority-pill--${t.priority}">${PRIORITY_LABEL[t.priority]}</span>
                </div>
                <p class="field-hint">${escapeHtml(projectName(t.projectId))}</p>
                ${t.tags.length ? `<div class="task-card-tags">${tagPillsHtml(t.tags)}</div>` : ''}
                <div class="task-card-bottom">
                    ${due}
                    ${assigneeAvatarHtml(t.assigneeId)}
                </div>
            </article>
        `;
    }
    function bumpTaskCount(list) {
        const col = list.closest('.kanban-col');
        if (!col) return;
        const badge = col.querySelector('.col-count');
        if (!badge) return;
        badge.textContent = list.querySelectorAll('.task-card').length;
        badge.classList.remove('is-bumped');
        void badge.offsetWidth;
        badge.classList.add('is-bumped');
    }
    function renderKanban() {
        document.querySelectorAll('#taskKanban .kanban-list').forEach(list => { list.innerHTML = ''; });
        filteredTasks().forEach(t => {
            const list = document.querySelector(`#taskKanban .kanban-col[data-task-stage="${t.status}"] .kanban-list`);
            if (!list) return;
            list.insertAdjacentHTML('beforeend', taskCardHtml(t));
        });
        document.querySelectorAll('#taskKanban .kanban-list').forEach(bumpTaskCount);
    }

    let draggedTaskCard = null;
    function initKanbanDragDrop() {
        document.addEventListener('dragstart', (e) => {
            const card = e.target.closest?.('.task-card');
            if (!card) return;
            draggedTaskCard = card;
            card.classList.add('is-dragging');
            e.dataTransfer.effectAllowed = 'move';
            try { e.dataTransfer.setData('text/plain', ''); } catch (err) {}
        });
        document.addEventListener('dragend', () => {
            if (draggedTaskCard) draggedTaskCard.classList.remove('is-dragging');
            draggedTaskCard = null;
            document.querySelectorAll('[data-task-drop].is-drop-target').forEach(l => l.classList.remove('is-drop-target'));
        });
        document.querySelectorAll('[data-task-drop]').forEach(list => {
            list.addEventListener('dragover', (e) => {
                if (!draggedTaskCard) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                list.classList.add('is-drop-target');
            });
            list.addEventListener('dragleave', (e) => {
                if (!list.contains(e.relatedTarget)) list.classList.remove('is-drop-target');
            });
            list.addEventListener('drop', async (e) => {
                e.preventDefault();
                if (!draggedTaskCard) return;
                const origin = draggedTaskCard.closest('[data-task-drop]');
                list.appendChild(draggedTaskCard);
                list.classList.remove('is-drop-target');
                bumpTaskCount(list);
                if (origin && origin !== list) bumpTaskCount(origin);

                const newStatus = list.closest('.kanban-col')?.dataset.taskStage;
                const taskId = Number(draggedTaskCard.dataset.id);
                const task = state.tasks.find(t => t.id === taskId);
                if (!task || !newStatus || task.status === newStatus) return;
                task.status = newStatus;
                task.completedAt = newStatus === 'done' ? new Date().toISOString() : null;
                renderKpis();
                renderTaskList();
                try {
                    await apiFetch(`${API_BASE.PROJECTS}/tasks/${taskId}/status`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: newStatus }),
                    });
                } catch (err) {}
            });
        });
    }

    /* ---------- Vista: Calendário ---------- */
    function renderCalendar() {
        const grid = document.getElementById('calGrid');
        const label = document.getElementById('calMonthLabel');
        if (!grid) return;
        label.textContent = `${MONTH_NAMES[state.calMonth]} ${state.calYear}`;

        const firstOfMonth = new Date(state.calYear, state.calMonth, 1);
        const startWeekday = firstOfMonth.getDay();
        const daysInMonth = new Date(state.calYear, state.calMonth + 1, 0).getDate();
        const daysInPrevMonth = new Date(state.calYear, state.calMonth, 0).getDate();
        const today = todayIso();
        const tasks = filteredTasks();

        const cells = [];
        for (let i = startWeekday - 1; i >= 0; i--) {
            cells.push({ day: daysInPrevMonth - i, outside: true, iso: null });
        }
        for (let d = 1; d <= daysInMonth; d++) {
            const iso = `${state.calYear}-${pad(state.calMonth + 1)}-${pad(d)}`;
            cells.push({ day: d, outside: false, iso });
        }
        let nextMonthDay = 1;
        while (cells.length % 7 !== 0) {
            cells.push({ day: nextMonthDay++, outside: true, iso: null });
        }

        grid.innerHTML = cells.map(c => {
            if (c.outside) return `<div class="cal-cell is-outside"><span class="cal-cell-num">${c.day}</span></div>`;
            const dayTasks = tasks.filter(t => t.dueDate === c.iso);
            const chips = dayTasks.slice(0, 3).map(t => {
                const color = t.priority === 'urgente' ? '#EF4444' : t.priority === 'alta' ? '#F59E0B' : t.priority === 'baixa' ? '#6B7280' : '#3B82F6';
                return `<span class="cal-chip" style="--chip-color:${color}" data-task-chip="${t.id}" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>`;
            }).join('');
            const more = dayTasks.length > 3 ? `<span class="field-hint">+${dayTasks.length - 3}</span>` : '';
            return `<div class="cal-cell ${c.iso === today ? 'is-today' : ''}"><span class="cal-cell-num">${c.day}</span>${chips}${more}</div>`;
        }).join('');
    }

    function initCalendarNav() {
        document.getElementById('calPrevBtn')?.addEventListener('click', () => {
            state.calMonth -= 1;
            if (state.calMonth < 0) { state.calMonth = 11; state.calYear -= 1; }
            renderCalendar();
        });
        document.getElementById('calNextBtn')?.addEventListener('click', () => {
            state.calMonth += 1;
            if (state.calMonth > 11) { state.calMonth = 0; state.calYear += 1; }
            renderCalendar();
        });
        document.getElementById('calGrid')?.addEventListener('click', (e) => {
            const chip = e.target.closest('[data-task-chip]');
            if (!chip) return;
            openTaskModal(Number(chip.dataset.taskChip));
        });
    }

    /* ---------- Sub-nav de vistas ---------- */
    function initProjTabs() {
        document.querySelectorAll('[data-projtab]').forEach(btn => {
            btn.addEventListener('click', () => {
                const target = btn.dataset.projtab;
                document.querySelectorAll('[data-projtab]').forEach(b => {
                    const active = b === btn;
                    b.classList.toggle('is-active', active);
                    b.setAttribute('aria-selected', String(active));
                });
                document.querySelectorAll('[data-projpane]').forEach(p => {
                    p.classList.toggle('is-active', p.dataset.projpane === target);
                });
                const filters = document.getElementById('projTaskFilters');
                if (filters) filters.hidden = target === 'projectos';
                if (target === 'documentos') ScopedDocs.mount('projDocsWidget', 'Projetos');
            });
        });

        document.getElementById('projFilterProject')?.addEventListener('change', (e) => {
            state.filterProject = e.target.value;
            renderTaskList(); renderKanban(); renderCalendar();
        });
        document.getElementById('projFilterPriority')?.addEventListener('change', (e) => {
            state.filterPriority = e.target.value;
            renderTaskList(); renderKanban(); renderCalendar();
        });
    }

    function enterProjectTasks(projectId) {
        state.filterProject = String(projectId);
        const sel = document.getElementById('projFilterProject');
        if (sel) sel.value = state.filterProject;
        document.querySelector('[data-projtab="lista"]')?.click();
        renderTaskList(); renderKanban(); renderCalendar();
    }

    /* ---------- Modal: Projecto ---------- */
    const projectModal = () => document.getElementById('projectModal');
    const projectForm = () => document.getElementById('projectForm');

    function renderSwatches(containerId, hiddenInputId, selectedColor) {
        const container = document.getElementById(containerId);
        const hidden = document.getElementById(hiddenInputId);
        if (!container) return;
        container.innerHTML = SWATCH_COLORS.map(c => `<button type="button" class="color-swatch ${c === selectedColor ? 'is-selected' : ''}" data-color="${c}" style="--swatch-color:${c}"></button>`).join('');
        container.querySelectorAll('.color-swatch').forEach(btn => {
            btn.addEventListener('click', () => {
                container.querySelectorAll('.color-swatch').forEach(b => b.classList.remove('is-selected'));
                btn.classList.add('is-selected');
                if (hidden) hidden.value = btn.dataset.color;
            });
        });
    }

    async function loadProjectTags(projectId) {
        try {
            const res = await apiFetch(`${API_BASE.PROJECTS}/projects/${projectId}/tags`);
            if (!res.ok) throw new Error();
            return await res.json();
        } catch (e) { return []; }
    }

    function renderProjectTagsList(tags, projectId) {
        const list = document.getElementById('projTagsList');
        if (!list) return;
        list.innerHTML = tags.map(t => `
            <li class="tag-manage-item" style="--tag-color:${t.color}">
                ${escapeHtml(t.name)}
                <span class="tag-manage-remove" data-remove-tag="${t.id}" role="button" aria-label="Remover etiqueta">×</span>
            </li>
        `).join('');
        list.querySelectorAll('[data-remove-tag]').forEach(el => {
            el.addEventListener('click', async () => {
                const tagId = el.dataset.removeTag;
                try {
                    await apiFetch(`${API_BASE.PROJECTS}/tags/${tagId}`, { method: 'DELETE' });
                } catch (e) {}
                const tags = await loadProjectTags(projectId);
                renderProjectTagsList(tags, projectId);
            });
        });
    }

    function openProjectModal(id = null) {
        const form = projectForm();
        form.reset();
        document.getElementById('projId').value = '';
        document.getElementById('projDeleteBtn').hidden = true;
        document.getElementById('projectModalTitle').textContent = id ? 'Editar projecto' : 'Novo projecto';
        renderSwatches('projColorSwatches', 'projColor', '#3B82F6');
        renderSwatches('projNewTagColorSwatches', 'projNewTagColor', '#6B7280');
        document.getElementById('projTagsList').innerHTML = '';
        const tagsHint = document.getElementById('projTagsHint');
        const addTagBtn = document.getElementById('projAddTagBtn');

        if (id) {
            const p = project(id);
            if (p) {
                document.getElementById('projId').value = p.id;
                document.getElementById('projName').value = p.name;
                document.getElementById('projDescription').value = p.description;
                document.getElementById('projStatus').value = p.status;
                document.getElementById('projDueDate').value = p.dueDate;
                renderSwatches('projColorSwatches', 'projColor', p.color);
                document.getElementById('projDeleteBtn').hidden = false;
                tagsHint.hidden = true;
                if (addTagBtn) addTagBtn.disabled = false;
                loadProjectTags(p.id).then(tags => renderProjectTagsList(tags, p.id));
            }
        } else {
            tagsHint.hidden = false;
            tagsHint.textContent = 'Guarde o projecto primeiro para poder criar etiquetas.';
            if (addTagBtn) addTagBtn.disabled = true;
        }

        projectModal().classList.add('is-open');
        projectModal().setAttribute('aria-hidden', 'false');
        setTimeout(() => document.getElementById('projName').focus(), 100);
    }
    function closeProjectModal() {
        if (document.activeElement && projectModal()?.contains(document.activeElement)) document.activeElement.blur();
        projectModal().classList.remove('is-open');
        projectModal().setAttribute('aria-hidden', 'true');
    }

    function initProjectModal() {
        document.getElementById('projAddBtn')?.addEventListener('click', () => openProjectModal());
        projectModal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeProjectModal));

        document.getElementById('projTableBody')?.addEventListener('click', (e) => {
            const viewBtn = e.target.closest('[data-view-tasks]');
            if (viewBtn) { enterProjectTasks(Number(viewBtn.dataset.viewTasks)); return; }
            const row = e.target.closest('[data-proj-row]');
            if (!row) return;
            openProjectModal(Number(row.dataset.projRow));
        });

        document.getElementById('projAddTagBtn')?.addEventListener('click', async () => {
            const projectId = document.getElementById('projId').value;
            const nameInput = document.getElementById('projNewTagName');
            const colorInput = document.getElementById('projNewTagColor');
            const name = nameInput.value.trim();
            if (!projectId || !name) return;
            try {
                await apiFetch(`${API_BASE.PROJECTS}/projects/${projectId}/tags`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, color: colorInput.value }),
                });
                nameInput.value = '';
                const tags = await loadProjectTags(projectId);
                renderProjectTagsList(tags, projectId);
            } catch (e) { await UIModal.alert('Não foi possível criar a etiqueta.'); }
        });

        projectForm()?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(projectForm());
            const idVal = fd.get('id');
            const body = {
                name: (fd.get('name') || '').trim(),
                description: (fd.get('description') || '').trim() || null,
                color: fd.get('color') || '#3B82F6',
                status: fd.get('status') || 'ativo',
                due_date: fd.get('due_date') || null,
            };
            try {
                const res = await apiFetch(
                    idVal ? `${API_BASE.PROJECTS}/projects/${idVal}` : `${API_BASE.PROJECTS}/projects`,
                    {
                        method: idVal ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    }
                );
                if (!res.ok) throw new Error('Falha ao guardar projecto');
                await loadProjects();
            } catch (err) {
                await UIModal.alert('Não foi possível guardar o projecto.');
                return;
            }
            renderAll();
            closeProjectModal();
            UIToast.success(idVal ? 'Projecto actualizado com sucesso!' : 'Projecto criado com sucesso!');
        });

        document.getElementById('projDeleteBtn')?.addEventListener('click', async () => {
            const id = Number(document.getElementById('projId').value);
            if (!id) return;
            if (!await UIModal.confirm('Apagar este projecto e todas as suas tarefas? Esta acção não pode ser desfeita.', { danger: true, okLabel: 'Apagar' })) return;
            try {
                const res = await apiFetch(`${API_BASE.PROJECTS}/projects/${id}`, { method: 'DELETE' });
                if (!res.ok) throw new Error('Falha ao apagar projecto');
                await loadAll();
            } catch (err) {
                await UIModal.alert('Não foi possível apagar o projecto.');
                return;
            }
            closeProjectModal();
            UIToast.success('Projecto apagado com sucesso!');
        });
    }

    /* ---------- Modal: Tarefa ---------- */
    const taskModal = () => document.getElementById('taskModal');
    const taskForm = () => document.getElementById('taskForm');
    let checkedTagIds = new Set();

    function initTaskTabs() {
        const form = taskForm();
        if (!form) return;
        form.querySelectorAll('[data-tasktab]').forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.dataset.tasktab;
                form.querySelectorAll('[data-tasktab]').forEach(t => {
                    const active = t === tab;
                    t.classList.toggle('is-active', active);
                    t.setAttribute('aria-selected', String(active));
                });
                form.querySelectorAll('[data-tasktabpane]').forEach(p => {
                    p.classList.toggle('is-active', p.dataset.tasktabpane === target);
                });
            });
        });
    }
    function resetTaskTabs() {
        const form = taskForm();
        if (!form) return;
        form.querySelectorAll('[data-tasktab]').forEach((t, i) => {
            t.classList.toggle('is-active', i === 0);
            t.setAttribute('aria-selected', String(i === 0));
        });
        form.querySelectorAll('[data-tasktabpane]').forEach((p, i) => p.classList.toggle('is-active', i === 0));
    }

    async function renderTaskTagCheckboxes(projectId) {
        const container = document.getElementById('taskTagsList');
        if (!container) return;
        if (!projectId) {
            container.innerHTML = '<span class="tag-check-empty">Escolha um projecto para ver as etiquetas disponíveis.</span>';
            return;
        }
        const tags = await loadProjectTags(projectId);
        if (!tags.length) {
            container.innerHTML = '<span class="tag-check-empty">Este projecto ainda não tem etiquetas.</span>';
            return;
        }
        container.innerHTML = tags.map(t => `
            <label class="tag-check-item ${checkedTagIds.has(t.id) ? 'is-checked' : ''}" style="--tag-color:${t.color}">
                <input type="checkbox" value="${t.id}" ${checkedTagIds.has(t.id) ? 'checked' : ''}>
                ${escapeHtml(t.name)}
            </label>
        `).join('');
        container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', () => {
                const id = Number(cb.value);
                if (cb.checked) checkedTagIds.add(id); else checkedTagIds.delete(id);
                cb.closest('.tag-check-item').classList.toggle('is-checked', cb.checked);
            });
        });
    }

    async function loadTaskComments(taskId) {
        const list = document.getElementById('taskCommentsList');
        if (!list) return;
        list.innerHTML = '<li class="comment-empty">A carregar...</li>';
        try {
            const res = await apiFetch(`${API_BASE.PROJECTS}/tasks/${taskId}/comments`);
            if (!res.ok) throw new Error();
            const comments = await res.json();
            if (!comments.length) { list.innerHTML = '<li class="comment-empty">Sem comentários ainda.</li>'; return; }
            list.innerHTML = comments.map(c => `
                <li class="comment-item">
                    <div class="comment-item-head">
                        <span class="comment-author">${escapeHtml(c.author_name || 'Utilizador')}</span>
                        <span class="comment-time">${new Date(c.created_at).toLocaleString('pt-PT')}</span>
                    </div>
                    <p class="comment-body">${escapeHtml(c.body)}</p>
                </li>
            `).join('');
        } catch (e) {
            list.innerHTML = '<li class="comment-empty">Não foi possível carregar os comentários.</li>';
        }
    }

    async function loadTaskAttachments(taskId) {
        const list = document.getElementById('taskAttachList');
        if (!list) return;
        list.innerHTML = '<li class="attach-empty">A carregar...</li>';
        try {
            const res = await apiFetch(`${API_BASE.PROJECTS}/tasks/${taskId}/attachments`);
            if (!res.ok) throw new Error();
            const items = await res.json();
            if (!items.length) { list.innerHTML = '<li class="attach-empty">Sem anexos ainda.</li>'; return; }
            list.innerHTML = items.map(a => `
                <li class="attach-item">
                    <span class="attach-name">${escapeHtml(a.filename)}</span>
                    <span class="attach-item-actions">
                        <a class="attach-download" href="${a.url}" target="_blank" rel="noopener">Transferir</a>
                        <button type="button" class="attach-remove" data-attach-remove="${a.id}" aria-label="Apagar anexo">×</button>
                    </span>
                </li>
            `).join('');
        } catch (e) {
            list.innerHTML = '<li class="attach-empty">Não foi possível carregar os anexos.</li>';
        }
    }

    function setActivityGuards(hasId) {
        document.getElementById('taskCommentsHint').hidden = !!hasId;
        document.getElementById('taskAddCommentBtn').disabled = !hasId;
        document.getElementById('taskNewComment').disabled = !hasId;
        document.getElementById('taskAttachHint').hidden = !!hasId;
        document.getElementById('taskAttachSubmitBtn').disabled = !hasId;
        document.getElementById('taskAttachFile').disabled = !hasId;
        if (!hasId) {
            document.getElementById('taskCommentsList').innerHTML = '';
            document.getElementById('taskAttachList').innerHTML = '';
        }
    }

    function openTaskModal(id = null, presetProjectId = null, presetStatus = null) {
        const form = taskForm();
        form.reset();
        resetTaskTabs();
        checkedTagIds = new Set();
        document.getElementById('taskId').value = '';
        document.getElementById('taskDeleteBtn').hidden = true;
        document.getElementById('taskModalTitle').textContent = id ? 'Editar tarefa' : 'Nova tarefa';
        populateAssigneeSelect();
        setActivityGuards(false);

        if (id) {
            const t = state.tasks.find(x => x.id === id);
            if (t) {
                document.getElementById('taskId').value = t.id;
                document.getElementById('taskTitle').value = t.title;
                document.getElementById('taskDescription').value = t.description;
                document.getElementById('taskProject').value = t.projectId;
                document.getElementById('taskStatus').value = t.status;
                document.getElementById('taskPriority').value = t.priority;
                document.getElementById('taskAssignee').value = t.assigneeId || '';
                document.getElementById('taskDueDate').value = t.dueDate;
                document.getElementById('taskDeleteBtn').hidden = false;
                checkedTagIds = new Set(t.tags.map(tg => tg.id));
                renderTaskTagCheckboxes(t.projectId);
                setActivityGuards(true);
                loadTaskComments(t.id);
                loadTaskAttachments(t.id);
            }
        } else {
            const pid = presetProjectId || state.filterProject || (state.projects[0] && state.projects[0].id);
            if (pid) document.getElementById('taskProject').value = pid;
            if (presetStatus) document.getElementById('taskStatus').value = presetStatus;
            renderTaskTagCheckboxes(document.getElementById('taskProject').value);
        }

        taskModal().classList.add('is-open');
        taskModal().setAttribute('aria-hidden', 'false');
        setTimeout(() => document.getElementById('taskTitle').focus(), 100);
    }
    function closeTaskModal() {
        if (document.activeElement && taskModal()?.contains(document.activeElement)) document.activeElement.blur();
        taskModal().classList.remove('is-open');
        taskModal().setAttribute('aria-hidden', 'true');
    }

    async function saveTaskTags(taskId) {
        try {
            await apiFetch(`${API_BASE.PROJECTS}/tasks/${taskId}/tags`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag_ids: [...checkedTagIds] }),
            });
        } catch (e) {}
    }

    function initTaskModal() {
        initTaskTabs();
        document.getElementById('projTaskAddBtn')?.addEventListener('click', () => openTaskModal());
        taskModal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeTaskModal));

        document.getElementById('taskGroups')?.addEventListener('click', (e) => {
            const toggle = e.target.closest('[data-toggle-group]');
            if (toggle) {
                const status = toggle.dataset.toggleGroup;
                if (state.collapsedGroups.has(status)) state.collapsedGroups.delete(status);
                else state.collapsedGroups.add(status);
                toggle.closest('.task-group')?.classList.toggle('is-collapsed');
                return;
            }
            const addBtn = e.target.closest('[data-add-task-status]');
            if (addBtn) { openTaskModal(null, null, addBtn.dataset.addTaskStatus); return; }
            const row = e.target.closest('[data-task-row]');
            if (!row) return;
            openTaskModal(Number(row.dataset.taskRow));
        });
        document.getElementById('taskKanban')?.addEventListener('click', (e) => {
            const card = e.target.closest('.task-card');
            if (!card) return;
            openTaskModal(Number(card.dataset.id));
        });

        document.getElementById('taskProject')?.addEventListener('change', (e) => {
            renderTaskTagCheckboxes(e.target.value);
        });

        taskForm()?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = taskForm();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const fd = new FormData(taskForm());
            const idVal = fd.get('id');
            const body = {
                project_id: Number(fd.get('project_id')),
                title: (fd.get('title') || '').trim(),
                description: (fd.get('description') || '').trim() || null,
                status: fd.get('status') || 'todo',
                priority: fd.get('priority') || 'media',
                assignee_id: fd.get('assignee_id') ? Number(fd.get('assignee_id')) : null,
                due_date: fd.get('due_date') || null,
            };
            try {
                const res = await apiFetch(
                    idVal ? `${API_BASE.PROJECTS}/tasks/${idVal}` : `${API_BASE.PROJECTS}/tasks`,
                    {
                        method: idVal ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    }
                );
                if (!res.ok) throw new Error('Falha ao guardar tarefa');
                const saved = await res.json();
                await saveTaskTags(saved.id);
                await loadTasks();
            } catch (err) {
                await UIModal.alert('Não foi possível guardar a tarefa.');
                return;
            }
            renderAll();
            closeTaskModal();
            UIToast.success(idVal ? 'Tarefa actualizada com sucesso!' : 'Tarefa criada com sucesso!');
        });

        document.getElementById('taskDeleteBtn')?.addEventListener('click', async () => {
            const id = Number(document.getElementById('taskId').value);
            if (!id) return;
            if (!await UIModal.confirm('Apagar esta tarefa? Esta acção não pode ser desfeita.', { danger: true, okLabel: 'Apagar' })) return;
            try {
                const res = await apiFetch(`${API_BASE.PROJECTS}/tasks/${id}`, { method: 'DELETE' });
                if (!res.ok) throw new Error('Falha ao apagar tarefa');
                await loadTasks();
            } catch (err) {
                await UIModal.alert('Não foi possível apagar a tarefa.');
                return;
            }
            renderAll();
            closeTaskModal();
            UIToast.success('Tarefa apagada com sucesso!');
        });

        document.getElementById('taskAddCommentBtn')?.addEventListener('click', async () => {
            const taskId = document.getElementById('taskId').value;
            const input = document.getElementById('taskNewComment');
            const body = input.value.trim();
            if (!taskId || !body) return;
            try {
                await apiFetch(`${API_BASE.PROJECTS}/tasks/${taskId}/comments`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ body }),
                });
                input.value = '';
                await loadTaskComments(taskId);
            } catch (e) { await UIModal.alert('Não foi possível adicionar o comentário.'); }
        });

        document.getElementById('taskAttachSubmitBtn')?.addEventListener('click', async () => {
            const taskId = document.getElementById('taskId').value;
            const fileInput = document.getElementById('taskAttachFile');
            const file = fileInput.files[0];
            if (!taskId || !file) return;
            const fd = new FormData();
            fd.append('file', file);
            try {
                const res = await apiFetch(`${API_BASE.PROJECTS}/tasks/${taskId}/attachments`, { method: 'POST', body: fd });
                if (!res.ok) throw new Error();
                fileInput.value = '';
                await loadTaskAttachments(taskId);
            } catch (err) { await UIModal.alert('Não foi possível carregar o anexo.'); }
        });

        document.getElementById('taskAttachList')?.addEventListener('click', async (e) => {
            const rm = e.target.closest('[data-attach-remove]');
            if (!rm) return;
            if (!await UIModal.confirm('Apagar este anexo?')) return;
            try {
                await apiFetch(`${API_BASE.PROJECTS}/attachments/${rm.dataset.attachRemove}`, { method: 'DELETE' });
                await loadTaskAttachments(document.getElementById('taskId').value);
            } catch (err) { await UIModal.alert('Não foi possível apagar o anexo.'); }
        });
    }

    /* ---------- Init ---------- */
    async function init() {
        if (!document.querySelector('[data-view="projects"]')) return;
        initProjTabs();
        initKanbanDragDrop();
        initCalendarNav();
        initProjectModal();
        initTaskModal();
        await loadAll();
    }

    return { init, enterProjectTasks };
})();

document.addEventListener('DOMContentLoaded', Projects.init);

/* ============================================================
   DOCUMENTS MODULE — árvore de pastas, versões, workflow,
   permissões, modelos e notificações
   ============================================================ */

const Documents = (() => {
    const state = {
        folders: [],
        documents: [],
        tags: [],
        templates: [],
        departments: [],
        contacts: [],
        projects: [],
        notifications: [],
        selectedFolderId: null,
        pseudoView: 'all',
        selectedDoc: null,
        viewMode: 'list',
        collapsed: new Set(),
        search: '',
        filters: { category: '', tag: '', status: '' },
        templateUseId: null,
    };

    const PSEUDO_LABELS = {
        all: 'Todos os documentos',
        favorites: 'Favoritos',
        archived: 'Arquivados',
        trash: 'Lixeira',
    };

    const WORKFLOW_LABEL = {
        enviado: 'Enviado', em_revisao: 'Em revisão', aprovado: 'Aprovado',
        publicado: 'Publicado', rejeitado: 'Rejeitado',
    };
    const SIGNATURE_LABEL = { pendente: 'Pendente', em_assinatura: 'Em assinatura', assinado: 'Assinado', rejeitado: 'Rejeitado' };

    /* ---------- Helpers ---------- */
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    function fmtDate(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        if (isNaN(d)) return '—';
        return d.toLocaleDateString('pt-PT');
    }
    function fmtDateTime(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        if (isNaN(d)) return '—';
        return d.toLocaleDateString('pt-PT') + ' ' + d.toLocaleTimeString('pt-PT', { hour: '2-digit', minute: '2-digit' });
    }
    function fmtBytes(n) {
        if (!n) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let i = 0, v = n;
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
        return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
    }
    function folderById(id) { return state.folders.find(f => f.id === Number(id)); }
    function docById(id) { return state.documents.find(d => d.id === Number(id)); }
    function docIconSvg() {
        return '<svg viewBox="0 0 16 16" fill="none"><path d="M3 2.5 a1 1 0 0 1 1 -1 h4 l3 3 v8 a1 1 0 0 1 -1 1 H4 a1 1 0 0 1 -1 -1 z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    }
    function folderIconSvg() {
        return '<svg class="doc-tree-icon" viewBox="0 0 16 16" fill="none"><path d="M2 4 a1 1 0 0 1 1 -1 h3 l1.5 2 H13 a1 1 0 0 1 1 1 v6.5 a1 1 0 0 1 -1 1 H3 a1 1 0 0 1 -1 -1 z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    }
    function workflowBadgeHtml(ws) {
        return `<span class="doc-workflow-badge doc-workflow-badge--${ws}">${WORKFLOW_LABEL[ws] || ws}</span>`;
    }

    /* ---------- Loading ---------- */
    async function loadFolders() {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders`);
            if (!res.ok) throw new Error();
            state.folders = await res.json();
        } catch (e) { state.folders = []; }
    }
    async function loadTags() {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/tags`);
            if (!res.ok) throw new Error();
            state.tags = await res.json();
        } catch (e) { state.tags = []; }
    }
    async function loadDepartments() {
        try {
            const res = await apiFetch(`${API_BASE.RH}/departments`);
            if (!res.ok) throw new Error();
            state.departments = await res.json();
        } catch (e) { state.departments = []; }
    }
    async function ensureAllDepartmentFolders() {
        for (const dept of state.departments) {
            try {
                await apiFetch(`${API_BASE.DOCS}/departments/${dept.id}/ensure-folder`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ department_name: dept.name }),
                });
            } catch (e) {}
        }
    }
    async function loadContacts() {
        try {
            const res = await apiFetch(`${API_BASE.CRM}/contacts`);
            if (!res.ok) throw new Error();
            state.contacts = await res.json();
        } catch (e) { state.contacts = []; }
    }
    async function loadProjects() {
        try {
            const res = await apiFetch(`${API_BASE.PROJECTS}/projects`);
            if (!res.ok) throw new Error();
            state.projects = await res.json();
        } catch (e) { state.projects = []; }
    }
    async function loadStats() {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/stats`);
            if (!res.ok) throw new Error();
            const s = await res.json();
            const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
            set('docKpiTotal', s.total);
            set('docKpiExpired', s.expired);
            set('docKpiExpiringSoon', s.expiring_soon);
            set('docKpiPendingSignature', s.pending_signature);
            set('docKpiArchived', s.archived);
            set('docKpiStorage', fmtBytes(s.storage_used_bytes));
        } catch (e) {}
    }
    async function loadDocuments() {
        const params = new URLSearchParams();
        if (state.pseudoView === 'favorites') params.set('favorite', 'true');
        else if (state.pseudoView === 'archived') params.set('status_filter', 'arquivado');
        else if (state.pseudoView === 'trash') params.set('trashed', 'true');
        else if (state.selectedFolderId) params.set('folder_id', state.selectedFolderId);

        if (state.search) params.set('search', state.search);
        if (state.filters.category) params.set('category', state.filters.category);
        if (state.filters.tag) params.set('tag_id', state.filters.tag);
        if (state.filters.status && state.pseudoView !== 'archived') params.set('status_filter', state.filters.status);

        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents?${params.toString()}`);
            if (!res.ok) throw new Error();
            state.documents = await res.json();
        } catch (e) { state.documents = []; }
        renderTable();
        renderGrid();
        renderCategoryFilterOptions();
    }
    async function loadTemplates() {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/templates`);
            if (!res.ok) throw new Error();
            state.templates = await res.json();
        } catch (e) { state.templates = []; }
    }
    async function loadNotifications() {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/notifications`);
            if (!res.ok) throw new Error();
            state.notifications = await res.json();
        } catch (e) { state.notifications = []; }
        renderNotifList();
    }

    /* ---------- Tree ---------- */
    function renderTree() {
        const tree = document.getElementById('docTree');
        if (!tree) return;

        const byParent = new Map();
        state.folders.forEach(f => {
            const key = f.parent_id || 'root';
            if (!byParent.has(key)) byParent.set(key, []);
            byParent.get(key).push(f);
        });

        function nodeHtml(folder) {
            const children = byParent.get(folder.id) || [];
            const collapsed = state.collapsed.has(folder.id);
            const isActive = !state.pseudoView && state.selectedFolderId === folder.id;
            return `
                <li class="doc-tree-item ${collapsed ? 'is-collapsed' : ''}" data-folder-id="${folder.id}">
                    <div class="doc-tree-row ${isActive ? 'is-active' : ''}" data-folder-select="${folder.id}">
                        <svg class="doc-tree-caret ${children.length ? '' : 'is-empty'}" data-folder-toggle="${folder.id}" viewBox="0 0 16 16" fill="none"><path d="M6 4 L10 8 L6 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        ${folderIconSvg()}
                        <span class="doc-tree-name">${escapeHtml(folder.name)}</span>
                        ${folder.is_system ? '' : `
                            <button type="button" class="doc-tree-menu-btn" data-folder-menu="${folder.id}" aria-label="Opções da pasta" title="Opções da pasta">
                                <svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="3.4" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="8" cy="12.6" r="1.3"/></svg>
                            </button>
                        `}
                    </div>
                    ${children.length ? `<ul>${children.map(nodeHtml).join('')}</ul>` : ''}
                </li>`;
        }

        const roots = byParent.get('root') || [];
        const pseudoHtml = Object.entries(PSEUDO_LABELS).map(([key, label]) => `
            <li class="doc-tree-item">
                <div class="doc-tree-row ${state.pseudoView === key ? 'is-active' : ''}" data-pseudo-select="${key}">
                    <svg class="doc-tree-caret is-empty" viewBox="0 0 16 16"></svg>
                    ${folderIconSvg()}
                    <span>${PSEUDO_LABELS[key]}</span>
                </div>
            </li>`).join('');

        tree.innerHTML = pseudoHtml + roots.map(nodeHtml).join('');
    }

    function renderBreadcrumb() {
        const el = document.getElementById('docBreadcrumb');
        if (!el) return;
        if (state.pseudoView) {
            el.innerHTML = `<span class="doc-crumb">${PSEUDO_LABELS[state.pseudoView]}</span>`;
            return;
        }
        const chain = [];
        let current = folderById(state.selectedFolderId);
        while (current) {
            chain.unshift(current);
            current = current.parent_id ? folderById(current.parent_id) : null;
        }
        el.innerHTML = chain.map(f => `<span class="doc-crumb" data-folder-select="${f.id}">${escapeHtml(f.name)}</span>`)
            .join(' <span aria-hidden="true">/</span> ') || '<span class="doc-crumb">Documentos</span>';
    }

    /* ---------- Filters ---------- */
    function renderCategoryFilterOptions() {
        const sel = document.getElementById('docFilterCategory');
        if (!sel) return;
        const current = sel.value;
        const cats = [...new Set(state.documents.map(d => d.category).filter(Boolean))].sort();
        sel.innerHTML = '<option value="">Todas as categorias</option>' +
            cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
        sel.value = current;
    }
    function renderTagFilterOptions() {
        const sel = document.getElementById('docFilterTag');
        if (!sel) return;
        sel.innerHTML = '<option value="">Todas as etiquetas</option>' +
            state.tags.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join('');
    }

    /* ---------- Table / grid ---------- */
    function renderTable() {
        const body = document.getElementById('docTableBody');
        const empty = document.getElementById('docEmpty');
        if (!body) return;
        body.innerHTML = state.documents.map(d => `
            <tr data-doc-id="${d.id}" class="${state.selectedDoc?.id === d.id ? 'is-selected' : ''}">
                <td><span class="doc-fav-star ${d.is_favorite ? 'is-fav' : ''}" data-fav-toggle="${d.id}">★</span></td>
                <td><span class="doc-name-cell">${docIconSvg()}${escapeHtml(d.name)}</span></td>
                <td>${escapeHtml(d.category || '—')}</td>
                <td>${escapeHtml(d.owner_name || '—')}</td>
                <td>${fmtDate(d.created_at)}</td>
                <td>${workflowBadgeHtml(d.workflow_state || 'publicado')}</td>
                <td>${fmtBytes(d.size_bytes)}</td>
                <td><button type="button" class="icon-btn icon-btn--sm" data-doc-menu="${d.id}">⋯</button></td>
            </tr>`).join('');
        if (empty) empty.hidden = state.documents.length > 0;
        document.getElementById('docTableList').hidden = state.viewMode !== 'list';
    }
    function renderGrid() {
        const grid = document.getElementById('docBrowserGrid');
        if (!grid) return;
        grid.hidden = state.viewMode !== 'grid';
        grid.innerHTML = state.documents.map(d => `
            <div class="doc-grid-card ${state.selectedDoc?.id === d.id ? 'is-selected' : ''}" data-doc-id="${d.id}">
                <span class="doc-grid-icon">${docIconSvg()}</span>
                <span class="doc-grid-name">${escapeHtml(d.name)}</span>
                <span class="doc-grid-meta">${fmtBytes(d.size_bytes)}</span>
            </div>`).join('');
    }

    /* ---------- Selection ---------- */
    function selectFolder(id) {
        state.pseudoView = null;
        state.selectedFolderId = Number(id);
        state.selectedDoc = null;
        closeDetailPanel();
        renderTree();
        renderBreadcrumb();
        loadDocuments();
    }
    function selectPseudo(key) {
        state.pseudoView = key;
        state.selectedFolderId = null;
        state.selectedDoc = null;
        closeDetailPanel();
        renderTree();
        renderBreadcrumb();
        loadDocuments();
    }
    async function selectDocument(id) {
        const doc = docById(id);
        if (!doc) return;
        state.selectedDoc = doc;
        renderTable();
        renderGrid();
        await renderDetailPanel();
    }
    function closeDetailPanel() {
        const panel = document.getElementById('docDetailPanel');
        if (panel) { panel.hidden = true; panel.innerHTML = ''; }
        state.selectedDoc = null;
    }

    /* ---------- Detail panel ---------- */
    async function renderDetailPanel() {
        const panel = document.getElementById('docDetailPanel');
        const d = state.selectedDoc;
        if (!panel || !d) return;
        panel.hidden = false;

        const tagPills = state.tags.map(t => {
            const active = (d.tags || []).some(dt => dt.id === t.id);
            return `<span class="tag-pill ${active ? '' : 'is-outline'}" style="--tag-color:${t.color};cursor:pointer;opacity:${active ? 1 : 0.4}" data-tag-toggle="${t.id}">${escapeHtml(t.name)}</span>`;
        }).join(' ');

        const wf = d.workflow_state || 'publicado';
        let wfActions = '';
        if (wf === 'enviado' || wf === 'rejeitado') wfActions += `<button type="button" class="btn-soft" data-wf-action="submit">Submeter para revisão</button>`;
        if (wf === 'em_revisao') wfActions += `<button type="button" class="btn-kora" data-wf-action="approve">Aprovar</button><button type="button" class="btn-soft" data-wf-action="reject">Rejeitar</button>`;
        if (wf === 'aprovado') wfActions += `<button type="button" class="btn-kora" data-wf-action="publish">Publicar</button>`;

        panel.innerHTML = `
            <button type="button" class="icon-btn icon-btn--sm" id="docDetailCloseBtn" style="float:right">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
            </button>
            <div class="doc-detail-title">${escapeHtml(d.name)}</div>
            <div class="doc-detail-sub">${fmtBytes(d.size_bytes)} · v${d.current_version || 1}</div>
            ${workflowBadgeHtml(wf)}

            <div class="doc-detail-section">
                <h4>Informação</h4>
                <div class="doc-detail-row"><span>Proprietário</span><strong>${escapeHtml(d.owner_name || '—')}</strong></div>
                <div class="doc-detail-row"><span>Categoria</span><strong>${escapeHtml(d.category || '—')}</strong></div>
                <div class="doc-detail-row"><span>Criado</span><strong>${fmtDateTime(d.created_at)}</strong></div>
                <div class="doc-detail-row"><span>Actualizado</span><strong>${fmtDateTime(d.updated_at)}</strong></div>
                <div class="doc-detail-row"><span>Validade</span><strong>${fmtDate(d.expiry_date)}</strong></div>
                <div class="doc-detail-row"><span>Assinatura</span><strong>${SIGNATURE_LABEL[d.signature_status] || '—'}</strong></div>
            </div>

            <div class="doc-detail-section">
                <h4>Ligações</h4>
                <div class="doc-detail-row">
                    <span>Cliente</span>
                    <select class="form-input" id="docDetailContactSel" style="max-width:170px">
                        <option value="">— Nenhum —</option>
                        ${state.contacts.map(c => `<option value="${c.id}" ${d.contact_id === c.id ? 'selected' : ''}>${escapeHtml(c.name)}</option>`).join('')}
                    </select>
                </div>
                <div class="doc-detail-row">
                    <span>Projecto</span>
                    <select class="form-input" id="docDetailProjectSel" style="max-width:170px">
                        <option value="">— Nenhum —</option>
                        ${state.projects.map(p => `<option value="${p.id}" ${d.project_id === p.id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('')}
                    </select>
                </div>
            </div>

            <div class="doc-detail-section">
                <h4>Etiquetas</h4>
                <div>${tagPills || '<span class="doc-detail-sub">Sem etiquetas disponíveis.</span>'}</div>
            </div>

            <div class="doc-detail-section">
                <h4>Workflow</h4>
                <div class="doc-detail-actions">${wfActions || '<span class="doc-detail-sub">Sem transições disponíveis.</span>'}</div>
                <button type="button" class="btn-link-sm" id="docWfHistoryToggle" style="margin-top:8px">Ver histórico</button>
                <div id="docWfHistoryList" hidden></div>
            </div>

            <div class="doc-detail-section">
                <h4>Versões</h4>
                <div id="docVersionsList">A carregar…</div>
                <button type="button" class="btn-soft" id="docNewVersionBtn" style="margin-top:10px">Enviar nova versão</button>
            </div>

            <div class="doc-detail-actions">
                <button type="button" class="btn-soft" data-action="download">Descarregar</button>
                <button type="button" class="btn-soft" data-action="rename">Renomear</button>
                <button type="button" class="btn-soft" data-action="move">Mover</button>
                <button type="button" class="btn-soft" data-action="favorite">${d.is_favorite ? 'Desfavoritar' : 'Favoritar'}</button>
                <button type="button" class="btn-soft" data-action="archive">${d.status === 'arquivado' ? 'Reativar' : 'Arquivar'}</button>
                <button type="button" class="btn-soft" data-action="perms">Permissões da pasta</button>
                <button type="button" class="btn-soft" data-action="trash">Apagar</button>
            </div>
        `;

        loadVersionsIntoPanel(d.id);
    }

    async function loadVersionsIntoPanel(docId) {
        const el = document.getElementById('docVersionsList');
        if (!el) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${docId}/versions`);
            if (!res.ok) throw new Error();
            const versions = await res.json();
            el.innerHTML = versions.map(v => `
                <div class="doc-version-item ${v.is_current ? 'is-current' : ''}" data-version="${v.version_number}">
                    <span class="doc-version-label">v${v.version_number}${v.is_current ? ' (atual)' : ''} · ${escapeHtml(v.uploaded_by_name || '—')} · ${fmtDate(v.created_at)}</span>
                    <span class="doc-version-actions">
                        <button type="button" data-version-download="${v.version_number}">Descarregar</button>
                        ${v.is_current ? '' : `<button type="button" data-version-restore="${v.version_number}">Restaurar</button>`}
                    </span>
                </div>`).join('') || '<p class="doc-detail-sub">Sem histórico.</p>';
        } catch (e) {
            el.innerHTML = '<p class="doc-detail-sub">Não foi possível carregar as versões.</p>';
        }
    }

    async function loadWorkflowHistoryIntoPanel(docId) {
        const el = document.getElementById('docWfHistoryList');
        if (!el) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${docId}/workflow/history`);
            if (!res.ok) throw new Error();
            const log = await res.json();
            el.innerHTML = log.map(h => `
                <div class="doc-workflow-log-item">
                    <span>${WORKFLOW_LABEL[h.from_state] || h.from_state || '—'} → ${WORKFLOW_LABEL[h.to_state] || h.to_state}<br>
                    <small>${escapeHtml(h.user_name || '—')} · ${fmtDateTime(h.created_at)}${h.notes ? ' · ' + escapeHtml(h.notes) : ''}</small></span>
                </div>`).join('') || '<p class="doc-detail-sub">Sem histórico.</p>';
        } catch (e) {
            el.innerHTML = '<p class="doc-detail-sub">Não foi possível carregar o histórico.</p>';
        }
    }

    /* ---------- Document actions ---------- */
    async function toggleFavorite(id) {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}/favorite`, { method: 'POST' });
            if (!res.ok) throw new Error();
            const updated = await res.json();
            const idx = state.documents.findIndex(x => x.id === updated.id);
            if (idx >= 0) state.documents[idx] = updated;
            if (state.selectedDoc?.id === updated.id) state.selectedDoc = updated;
            renderTable(); renderGrid();
        } catch (e) { await UIModal.alert('Não foi possível atualizar o favorito.'); }
    }
    async function toggleArchive(id) {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}/archive`, { method: 'POST' });
            if (!res.ok) throw new Error();
            await loadDocuments();
            await loadStats();
            closeDetailPanel();
        } catch (e) { await UIModal.alert('Não foi possível arquivar o documento.'); }
    }
    async function trashDocument(id) {
        if (!await UIModal.confirm('Enviar este documento para a lixeira?', { danger: true, okLabel: 'Apagar' })) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error();
            await loadDocuments();
            await loadStats();
            closeDetailPanel();
            UIToast.success('Documento enviado para a lixeira!');
        } catch (e) { await UIModal.alert('Não foi possível apagar o documento.'); }
    }
    async function restoreDocument(id) {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}/restore`, { method: 'POST' });
            if (!res.ok) throw new Error();
            await loadDocuments();
            UIToast.success('Documento restaurado com sucesso!');
        } catch (e) { await UIModal.alert('Não foi possível restaurar o documento.'); }
    }
    async function permanentDelete(id) {
        if (!await UIModal.confirm('Apagar definitivamente? Esta ação não pode ser revertida.', { danger: true, okLabel: 'Apagar' })) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}/permanent`, { method: 'DELETE' });
            if (!res.ok) throw new Error();
            await loadDocuments();
            UIToast.success('Documento apagado definitivamente.');
        } catch (e) { await UIModal.alert('Não foi possível apagar definitivamente.'); }
    }
    async function renameDocument(id) {
        const d = docById(id);
        const name = await UIModal.prompt('Novo nome:', d?.name || '');
        if (!name) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            if (!res.ok) throw new Error();
            await loadDocuments();
            UIToast.success('Documento renomeado com sucesso!');
        } catch (e) { await UIModal.alert('Não foi possível renomear.'); }
    }
    async function moveDocument(id) {
        const options = state.folders.map(f => `${f.id}: ${f.name}`).join('\n');
        const answer = await UIModal.prompt(`Mover para qual pasta? Indique o ID:\n${options}`);
        const folderId = Number(answer);
        if (!answer || !folderId) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder_id: folderId }),
            });
            if (!res.ok) throw new Error();
            await loadDocuments();
            closeDetailPanel();
            UIToast.success('Documento movido com sucesso!');
        } catch (e) { await UIModal.alert('Não foi possível mover o documento.'); }
    }
    async function toggleTagOnDoc(docId, tagId) {
        const d = docById(docId);
        if (!d) return;
        const current = (d.tags || []).map(t => t.id);
        const next = current.includes(tagId) ? current.filter(x => x !== tagId) : [...current, tagId];
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${docId}/tags`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag_ids: next }),
            });
            if (!res.ok) throw new Error();
            d.tags = await res.json();
            state.selectedDoc = d;
            renderDetailPanel();
        } catch (e) { await UIModal.alert('Não foi possível atualizar as etiquetas.'); }
    }
    async function downloadDocument(id) {
        const d = docById(id);
        if (d?.url) window.open(d.url, '_blank');
    }
    async function linkDocumentEntity(id, patch) {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            if (!res.ok) throw new Error();
            const updated = await res.json();
            const idx = state.documents.findIndex(x => x.id === updated.id);
            if (idx >= 0) state.documents[idx] = updated;
            state.selectedDoc = updated;
        } catch (e) { await UIModal.alert('Não foi possível actualizar a ligação.'); }
    }

    /* ---------- Upload ---------- */
    async function uploadFiles(fileList) {
        const folderId = state.selectedFolderId || state.folders.find(f => !f.parent_id)?.id;
        if (!folderId) { await UIModal.alert('Selecione uma pasta antes de carregar ficheiros.'); return; }
        let uploaded = 0;
        for (const file of fileList) {
            const fd = new FormData();
            fd.append('file', file);
            try {
                const res = await apiFetch(`${API_BASE.DOCS}/documents?folder_id=${folderId}`, { method: 'POST', body: fd });
                if (!res.ok) throw new Error();
                uploaded++;
            } catch (e) { await UIModal.alert(`Falha ao enviar ${file.name}.`); }
        }
        await loadDocuments();
        await loadStats();
        if (uploaded) UIToast.success(uploaded === 1 ? 'Documento enviado com sucesso!' : `${uploaded} documentos enviados com sucesso!`);
    }

    async function createFolder() {
        const name = await UIModal.prompt('Nome da nova pasta:');
        if (!name) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, parent_id: state.selectedFolderId || null }),
            });
            if (!res.ok) throw new Error();
            await loadFolders();
            renderTree();
            UIToast.success('Pasta criada com sucesso!');
        } catch (e) { await UIModal.alert('Não foi possível criar a pasta.'); }
    }

    async function createSubfolder(parentId) {
        const name = await UIModal.prompt('Nome da nova subpasta:');
        if (!name) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, parent_id: parentId }),
            });
            if (!res.ok) throw new Error();
            state.collapsed.delete(parentId);
            await loadFolders();
            renderTree();
            UIToast.success('Subpasta criada com sucesso!');
        } catch (e) { await UIModal.alert('Não foi possível criar a subpasta.'); }
    }

    async function renameFolder(folderId) {
        const folder = folderById(folderId);
        if (!folder) return;
        const name = await UIModal.prompt('Novo nome da pasta:', folder.name);
        if (!name || !name.trim() || name.trim() === folder.name) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders/${folderId}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim() }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Falha ao renomear');
            }
            await loadFolders();
            renderTree();
            renderBreadcrumb();
            UIToast.success('Pasta renomeada com sucesso!');
        } catch (e) { await UIModal.alert(e.message || 'Não foi possível renomear a pasta.'); }
    }

    async function deleteFolder(folderId) {
        const folder = folderById(folderId);
        if (!folder) return;
        if (!await UIModal.confirm(
            `Apagar a pasta "${folder.name}"? A pasta tem de estar vazia — sem subpastas nem documentos.`,
            { danger: true, okLabel: 'Apagar' }
        )) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders/${folderId}`, { method: 'DELETE' });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Falha ao apagar');
            }
            if (state.selectedFolderId === folderId) {
                state.selectedFolderId = folder.parent_id || null;
            }
            await loadFolders();
            renderTree();
            renderBreadcrumb();
            await loadDocuments();
            UIToast.success('Pasta apagada com sucesso!');
        } catch (e) { await UIModal.alert(e.message || 'Não foi possível apagar a pasta.'); }
    }

    async function handleFolderContextAction(action, folderId) {
        closeContextMenu();
        switch (action) {
            case 'new-subfolder': await createSubfolder(folderId); break;
            case 'rename-folder': await renameFolder(folderId); break;
            case 'delete-folder': await deleteFolder(folderId); break;
        }
    }

    /* ---------- Workflow / versions actions ---------- */
    async function runWorkflowAction(action) {
        const d = state.selectedDoc;
        if (!d) return;
        const notes = (action === 'reject') ? await UIModal.prompt('Motivo da rejeição (opcional):') || '' : '';
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${d.id}/workflow/${action}`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ notes }),
            });
            if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail); }
            const updated = await res.json();
            state.selectedDoc = updated;
            const idx = state.documents.findIndex(x => x.id === updated.id);
            if (idx >= 0) state.documents[idx] = updated;
            renderTable();
            await renderDetailPanel();
        } catch (e) { await UIModal.alert(e.message || 'Não foi possível concluir a transição.'); }
    }

    async function uploadNewVersion(file) {
        const d = state.selectedDoc;
        if (!d || !file) return;
        const fd = new FormData();
        fd.append('file', file);
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${d.id}/versions`, { method: 'POST', body: fd });
            if (!res.ok) throw new Error();
            const updated = await res.json();
            state.selectedDoc = updated;
            const idx = state.documents.findIndex(x => x.id === updated.id);
            if (idx >= 0) state.documents[idx] = updated;
            await renderDetailPanel();
        } catch (e) { await UIModal.alert('Não foi possível enviar a nova versão.'); }
    }
    async function downloadVersionAction(versionNumber) {
        const d = state.selectedDoc;
        if (!d) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${d.id}/versions/${versionNumber}/download`);
            if (!res.ok) throw new Error();
            const { url } = await res.json();
            window.open(url, '_blank');
        } catch (e) { await UIModal.alert('Não foi possível descarregar a versão.'); }
    }
    async function restoreVersionAction(versionNumber) {
        const d = state.selectedDoc;
        if (!d || !await UIModal.confirm(`Restaurar a versão ${versionNumber}?`)) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents/${d.id}/versions/${versionNumber}/restore`, { method: 'POST' });
            if (!res.ok) throw new Error();
            const updated = await res.json();
            state.selectedDoc = updated;
            const idx = state.documents.findIndex(x => x.id === updated.id);
            if (idx >= 0) state.documents[idx] = updated;
            await renderDetailPanel();
        } catch (e) { await UIModal.alert('Não foi possível restaurar a versão.'); }
    }

    /* ---------- Context menu ---------- */
    function closeContextMenu() {
        const menu = document.getElementById('docContextMenu');
        if (menu) menu.hidden = true;
    }
    function openContextMenu(x, y, items) {
        const menu = document.getElementById('docContextMenu');
        if (!menu) return;
        menu.innerHTML = items.map(it => `<li><button type="button" class="${it.danger ? 'is-danger' : ''}" data-menu-action="${it.action}">${escapeHtml(it.label)}</button></li>`).join('');
        menu.style.left = `${Math.min(x, window.innerWidth - 220)}px`;
        menu.style.top = `${Math.min(y, window.innerHeight - 240)}px`;
        menu.hidden = false;
        menu.dataset.targetId = items.targetId || '';
        menu.dataset.targetType = items.targetType || 'document';
    }
    function folderContextItems(folder) {
        const items = [
            { action: 'new-subfolder', label: 'Nova subpasta' },
            { action: 'rename-folder', label: 'Renomear' },
            { action: 'delete-folder', label: 'Apagar', danger: true },
        ];
        items.targetId = folder.id;
        items.targetType = 'folder';
        return items;
    }
    function docContextItems(doc) {
        const items = [
            { action: 'open', label: 'Ver detalhes' },
            { action: 'download', label: 'Descarregar' },
            { action: 'rename', label: 'Renomear' },
            { action: 'move', label: 'Mover' },
            { action: 'favorite', label: doc.is_favorite ? 'Desfavoritar' : 'Favoritar' },
            { action: 'archive', label: doc.status === 'arquivado' ? 'Reativar' : 'Arquivar' },
        ];
        if (state.pseudoView === 'trash') {
            items.push({ action: 'restore', label: 'Restaurar' });
            items.push({ action: 'permanent', label: 'Apagar definitivamente', danger: true });
        } else {
            items.push({ action: 'trash', label: 'Apagar', danger: true });
        }
        items.targetId = doc.id;
        return items;
    }

    async function handleContextAction(action, docId) {
        closeContextMenu();
        switch (action) {
            case 'open': await selectDocument(docId); break;
            case 'download': await downloadDocument(docId); break;
            case 'rename': await renameDocument(docId); break;
            case 'move': await moveDocument(docId); break;
            case 'favorite': await toggleFavorite(docId); break;
            case 'archive': await toggleArchive(docId); break;
            case 'trash': await trashDocument(docId); break;
            case 'restore': await restoreDocument(docId); break;
            case 'permanent': await permanentDelete(docId); break;
        }
    }

    /* ---------- Permissions modal ---------- */
    function permsModal() { return document.getElementById('docPermsModal'); }
    async function openPermsModal(folderId) {
        const modal = permsModal();
        if (!modal) return;
        modal.dataset.folderId = folderId;
        const sel = document.getElementById('docPermScopeValueSelect');
        sel.innerHTML = state.departments.map(dep => `<option value="${dep.id}">${escapeHtml(dep.name)}</option>`).join('');
        openModal(modal);
        await loadPerms(folderId);
    }
    async function loadPerms(folderId) {
        const list = document.getElementById('docPermsList');
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/permissions?folder_id=${folderId}`);
            if (!res.ok) throw new Error();
            const rules = await res.json();
            const scopeLabel = { company: 'Empresa', department: 'Departamento', role: 'Cargo', user: 'Utilizador' };
            list.innerHTML = rules.map(r => `
                <li>
                    <span>${scopeLabel[r.scope_type]}${r.scope_value ? ' · ' + escapeHtml(r.scope_value) : ''} —
                        ${r.can_view ? 'Ver ' : ''}${r.can_edit ? 'Editar ' : ''}${r.can_delete ? 'Apagar' : ''}</span>
                    <button type="button" class="doc-perm-remove" data-perm-remove="${r.id}">Remover</button>
                </li>`).join('') || '<li>Sem regras — acesso livre.</li>';
        } catch (e) { list.innerHTML = '<li>Não foi possível carregar as permissões.</li>'; }
    }
    function openModal(el) { if (el) { el.classList.add('is-open'); el.setAttribute('aria-hidden', 'false'); } }
    function closeModal(el) { if (el) { el.classList.remove('is-open'); el.setAttribute('aria-hidden', 'true'); } }

    /* ---------- Templates ---------- */
    function templatesModal() { return document.getElementById('docTemplatesModal'); }
    async function openTemplatesModal() {
        await loadTemplates();
        renderTemplatesList();
        openModal(templatesModal());
    }
    function renderTemplatesList() {
        const list = document.getElementById('docTemplatesList');
        const empty = document.getElementById('docTemplatesEmpty');
        if (!list) return;
        list.innerHTML = state.templates.map(t => `
            <li>
                <span>${escapeHtml(t.name)}<br><span class="doc-template-meta">${escapeHtml(t.category || 'Sem categoria')}</span></span>
                <span>
                    <button type="button" class="btn-soft" data-template-use="${t.id}">Usar</button>
                    <button type="button" class="btn-link-sm" data-template-delete="${t.id}" style="color:#DC2626">Apagar</button>
                </span>
            </li>`).join('');
        if (empty) empty.hidden = state.templates.length > 0;
    }
    function openTemplateUseModal(templateId) {
        state.templateUseId = templateId;
        document.getElementById('docTemplateUseId').value = templateId;
        document.getElementById('docTemplateUseName').value = '';
        document.getElementById('docTemplateUseFields').innerHTML = '';
        closeModal(templatesModal());
        openModal(document.getElementById('docTemplateUseModal'));
    }
    function addTemplateFieldRow() {
        const wrap = document.getElementById('docTemplateUseFields');
        const row = document.createElement('div');
        row.className = 'doc-template-use-field';
        row.innerHTML = `<input class="form-input" placeholder="campo" data-field-key>
                          <input class="form-input" placeholder="valor" data-field-value>`;
        wrap.appendChild(row);
    }

    /* ---------- Notifications ---------- */
    function renderNotifList() {
        const list = document.getElementById('notifList');
        const empty = document.getElementById('notifEmpty');
        const dot = document.getElementById('notifDot');
        if (!list) return;
        const unread = state.notifications.filter(n => !n.is_read).length;
        if (dot) dot.hidden = unread === 0;
        list.innerHTML = state.notifications.map(n => `
            <li class="${n.is_read ? '' : 'is-unread'}" data-notif-id="${n.id}">
                ${escapeHtml(n.message)}
                <span class="notif-time">${fmtDateTime(n.created_at)}</span>
            </li>`).join('');
        if (empty) empty.hidden = state.notifications.length > 0;
    }
    async function markNotifRead(id) {
        try {
            await apiFetch(`${API_BASE.DOCS}/notifications/${id}/read`, { method: 'PATCH' });
            const n = state.notifications.find(x => x.id === id);
            if (n) n.is_read = true;
            renderNotifList();
        } catch (e) {}
    }
    async function markAllNotifRead() {
        try {
            await apiFetch(`${API_BASE.DOCS}/notifications/read-all`, { method: 'POST' });
            state.notifications.forEach(n => n.is_read = true);
            renderNotifList();
        } catch (e) {}
    }

    /* ---------- Init handlers ---------- */
    function initTreeClicks() {
        document.getElementById('docTree')?.addEventListener('click', (e) => {
            const menuBtn = e.target.closest('[data-folder-menu]');
            if (menuBtn) {
                const folder = folderById(menuBtn.dataset.folderMenu);
                if (folder) {
                    const rect = menuBtn.getBoundingClientRect();
                    openContextMenu(rect.left, rect.bottom, folderContextItems(folder));
                }
                return;
            }
            const toggle = e.target.closest('[data-folder-toggle]');
            if (toggle) {
                const id = Number(toggle.dataset.folderToggle);
                state.collapsed.has(id) ? state.collapsed.delete(id) : state.collapsed.add(id);
                renderTree();
                return;
            }
            const pseudo = e.target.closest('[data-pseudo-select]');
            if (pseudo) { selectPseudo(pseudo.dataset.pseudoSelect); return; }
            const row = e.target.closest('[data-folder-select]');
            if (row) selectFolder(row.dataset.folderSelect);
        });
        document.getElementById('docBreadcrumb')?.addEventListener('click', (e) => {
            const crumb = e.target.closest('[data-folder-select]');
            if (crumb) selectFolder(crumb.dataset.folderSelect);
        });
    }

    function initTableClicks() {
        document.getElementById('docTableBody')?.addEventListener('click', (e) => {
            const fav = e.target.closest('[data-fav-toggle]');
            if (fav) { toggleFavorite(Number(fav.dataset.favToggle)); return; }
            const menuBtn = e.target.closest('[data-doc-menu]');
            if (menuBtn) {
                const rect = menuBtn.getBoundingClientRect();
                const doc = docById(menuBtn.dataset.docMenu);
                openContextMenu(rect.left, rect.bottom, docContextItems(doc));
                return;
            }
            const row = e.target.closest('tr[data-doc-id]');
            if (row) selectDocument(row.dataset.docId);
        });
        document.getElementById('docBrowserGrid')?.addEventListener('click', (e) => {
            const card = e.target.closest('[data-doc-id]');
            if (card) selectDocument(card.dataset.docId);
        });
        document.getElementById('docBrowserGrid')?.addEventListener('contextmenu', (e) => {
            const card = e.target.closest('[data-doc-id]');
            if (!card) return;
            e.preventDefault();
            openContextMenu(e.clientX, e.clientY, docContextItems(docById(card.dataset.docId)));
        });
        document.getElementById('docTableBody')?.addEventListener('contextmenu', (e) => {
            const row = e.target.closest('tr[data-doc-id]');
            if (!row) return;
            e.preventDefault();
            openContextMenu(e.clientX, e.clientY, docContextItems(docById(row.dataset.docId)));
        });
        document.getElementById('docContextMenu')?.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-menu-action]');
            if (!btn) return;
            const menu = document.getElementById('docContextMenu');
            if (menu.dataset.targetType === 'folder') {
                handleFolderContextAction(btn.dataset.menuAction, Number(menu.dataset.targetId));
            } else {
                handleContextAction(btn.dataset.menuAction, menu.dataset.targetId);
            }
        });
        document.addEventListener('click', (e) => {
            if (!e.target.closest('#docContextMenu') && !e.target.closest('[data-doc-menu]') && !e.target.closest('[data-folder-menu]')) closeContextMenu();
        });
    }

    function initDetailPanel() {
        document.getElementById('docDetailPanel')?.addEventListener('change', (e) => {
            if (!state.selectedDoc) return;
            if (e.target.id === 'docDetailContactSel') {
                linkDocumentEntity(state.selectedDoc.id, { contact_id: e.target.value ? Number(e.target.value) : null });
            } else if (e.target.id === 'docDetailProjectSel') {
                linkDocumentEntity(state.selectedDoc.id, { project_id: e.target.value ? Number(e.target.value) : null });
            }
        });
        document.getElementById('docDetailPanel')?.addEventListener('click', async (e) => {
            if (e.target.closest('#docDetailCloseBtn')) { closeDetailPanel(); return; }
            const tag = e.target.closest('[data-tag-toggle]');
            if (tag && state.selectedDoc) { toggleTagOnDoc(state.selectedDoc.id, Number(tag.dataset.tagToggle)); return; }
            const wfBtn = e.target.closest('[data-wf-action]');
            if (wfBtn) { runWorkflowAction(wfBtn.dataset.wfAction); return; }
            if (e.target.closest('#docWfHistoryToggle')) {
                const list = document.getElementById('docWfHistoryList');
                list.hidden = !list.hidden;
                if (!list.hidden) await loadWorkflowHistoryIntoPanel(state.selectedDoc.id);
                return;
            }
            if (e.target.closest('#docNewVersionBtn')) { document.getElementById('docVersionInput').click(); return; }
            const verDownload = e.target.closest('[data-version-download]');
            if (verDownload) { downloadVersionAction(Number(verDownload.dataset.versionDownload)); return; }
            const verRestore = e.target.closest('[data-version-restore]');
            if (verRestore) { restoreVersionAction(Number(verRestore.dataset.versionRestore)); return; }

            const actionBtn = e.target.closest('[data-action]');
            if (!actionBtn || !state.selectedDoc) return;
            const id = state.selectedDoc.id;
            switch (actionBtn.dataset.action) {
                case 'download': downloadDocument(id); break;
                case 'rename': renameDocument(id); break;
                case 'move': moveDocument(id); break;
                case 'favorite': toggleFavorite(id); break;
                case 'archive': toggleArchive(id); break;
                case 'trash': trashDocument(id); break;
                case 'perms': openPermsModal(state.selectedDoc.folder_id); break;
            }
        });
    }

    function initUpload() {
        document.getElementById('docUploadBtn')?.addEventListener('click', () => document.getElementById('docUploadInput').click());
        document.getElementById('docUploadInput')?.addEventListener('change', (e) => {
            if (e.target.files.length) uploadFiles(e.target.files);
            e.target.value = '';
        });
        document.getElementById('docVersionInput')?.addEventListener('change', (e) => {
            if (e.target.files.length) uploadNewVersion(e.target.files[0]);
            e.target.value = '';
        });
        document.getElementById('docFolderAddBtn')?.addEventListener('click', createFolder);
    }

    function initDropzone() {
        const overlay = document.getElementById('docDropzoneOverlay');
        const view = document.querySelector('[data-view="documents"]');
        if (!overlay || !view) return;
        let dragCounter = 0;
        view.addEventListener('dragenter', (e) => { e.preventDefault(); dragCounter++; overlay.hidden = false; });
        view.addEventListener('dragover', (e) => e.preventDefault());
        view.addEventListener('dragleave', () => { dragCounter--; if (dragCounter <= 0) { dragCounter = 0; overlay.hidden = true; } });
        view.addEventListener('drop', (e) => {
            e.preventDefault();
            dragCounter = 0;
            overlay.hidden = true;
            if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
        });
    }

    function initFilters() {
        let searchTimer;
        document.getElementById('docSearchInput')?.addEventListener('input', (e) => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => { state.search = e.target.value.trim(); loadDocuments(); }, 300);
        });
        document.getElementById('docFilterCategory')?.addEventListener('change', (e) => { state.filters.category = e.target.value; loadDocuments(); });
        document.getElementById('docFilterTag')?.addEventListener('change', (e) => { state.filters.tag = e.target.value; loadDocuments(); });
        document.getElementById('docFilterStatus')?.addEventListener('change', (e) => { state.filters.status = e.target.value; loadDocuments(); });
        document.querySelectorAll('[data-docview]').forEach(btn => {
            btn.addEventListener('click', () => {
                state.viewMode = btn.dataset.docview;
                document.querySelectorAll('[data-docview]').forEach(b => b.classList.toggle('is-active', b === btn));
                renderTable(); renderGrid();
            });
        });
    }

    function initPermsModal() {
        permsModal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(permsModal())));
        document.getElementById('docPermScopeType')?.addEventListener('change', (e) => {
            const isDept = e.target.value === 'department';
            document.getElementById('docPermScopeValueSelect').hidden = !isDept;
            document.getElementById('docPermScopeValueText').hidden = isDept || e.target.value === 'company';
        });
        document.getElementById('docPermsList')?.addEventListener('click', async (e) => {
            const rm = e.target.closest('[data-perm-remove]');
            if (!rm) return;
            try {
                await apiFetch(`${API_BASE.DOCS}/permissions/${rm.dataset.permRemove}`, { method: 'DELETE' });
                await loadPerms(permsModal().dataset.folderId);
                UIToast.success('Regra removida com sucesso!');
            } catch (e2) { await UIModal.alert('Não foi possível remover a regra.'); }
        });
        document.getElementById('docPermsForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const scopeType = document.getElementById('docPermScopeType').value;
            const scopeValue = scopeType === 'department'
                ? document.getElementById('docPermScopeValueSelect').value
                : (scopeType === 'company' ? null : document.getElementById('docPermScopeValueText').value.trim());
            const body = {
                scope_type: scopeType,
                scope_value: scopeValue || null,
                folder_id: Number(permsModal().dataset.folderId),
                can_view: document.getElementById('docPermCanView').checked,
                can_edit: document.getElementById('docPermCanEdit').checked,
                can_delete: document.getElementById('docPermCanDelete').checked,
            };
            try {
                const res = await apiFetch(`${API_BASE.DOCS}/permissions`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error();
                e.target.reset();
                await loadPerms(permsModal().dataset.folderId);
                UIToast.success('Regra de permissão adicionada com sucesso!');
            } catch (e2) { await UIModal.alert('Não foi possível adicionar a regra.'); }
        });
    }

    function initTemplatesModals() {
        document.getElementById('docTemplatesBtn')?.addEventListener('click', openTemplatesModal);
        templatesModal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(templatesModal())));
        document.getElementById('docTemplatesList')?.addEventListener('click', async (e) => {
            const useBtn = e.target.closest('[data-template-use]');
            if (useBtn) { openTemplateUseModal(Number(useBtn.dataset.templateUse)); return; }
            const delBtn = e.target.closest('[data-template-delete]');
            if (delBtn) {
                if (!await UIModal.confirm('Apagar este modelo?', { danger: true, okLabel: 'Apagar' })) return;
                try {
                    await apiFetch(`${API_BASE.DOCS}/templates/${delBtn.dataset.templateDelete}`, { method: 'DELETE' });
                    await loadTemplates();
                    renderTemplatesList();
                    UIToast.success('Modelo apagado com sucesso!');
                } catch (e2) { await UIModal.alert('Não foi possível apagar o modelo.'); }
            }
        });
        document.getElementById('docTemplateUploadForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const name = document.getElementById('docTemplateName').value.trim();
            const category = document.getElementById('docTemplateCategory').value.trim();
            const file = document.getElementById('docTemplateFile').files[0];
            if (!name || !file) return;
            const fd = new FormData();
            fd.append('file', file);
            const params = new URLSearchParams({ name });
            if (category) params.set('category', category);
            try {
                const res = await apiFetch(`${API_BASE.DOCS}/templates?${params.toString()}`, { method: 'POST', body: fd });
                if (!res.ok) throw new Error();
                e.target.reset();
                await loadTemplates();
                renderTemplatesList();
                UIToast.success('Modelo criado com sucesso!');
            } catch (e2) { await UIModal.alert('Não foi possível adicionar o modelo.'); }
        });

        const useModal = document.getElementById('docTemplateUseModal');
        useModal?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(useModal)));
        document.getElementById('docTemplateAddFieldBtn')?.addEventListener('click', addTemplateFieldRow);
        document.getElementById('docTemplateUseForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const fields = {};
            document.querySelectorAll('#docTemplateUseFields .doc-template-use-field').forEach(row => {
                const key = row.querySelector('[data-field-key]').value.trim();
                const value = row.querySelector('[data-field-value]').value.trim();
                if (key) fields[key] = value;
            });
            const folderId = state.selectedFolderId || state.folders.find(f => !f.parent_id)?.id;
            if (!folderId) { await UIModal.alert('Selecione uma pasta de destino na árvore antes de usar um modelo.'); return; }
            const body = {
                folder_id: folderId,
                fields,
                document_name: document.getElementById('docTemplateUseName').value.trim() || null,
            };
            try {
                const res = await apiFetch(`${API_BASE.DOCS}/templates/${state.templateUseId}/use`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
                });
                if (!res.ok) throw new Error();
                closeModal(useModal);
                await loadDocuments();
                UIToast.success('Documento criado a partir do modelo com sucesso!');
            } catch (e2) { await UIModal.alert('Não foi possível criar o documento a partir do modelo.'); }
        });
    }

    function initNotifications() {
        const bell = document.getElementById('notifBellBtn');
        const panel = document.getElementById('notifPanel');
        bell?.addEventListener('click', async (e) => {
            e.stopPropagation();
            const isHidden = panel.hidden;
            panel.hidden = !isHidden ? true : false;
            if (isHidden) { await loadNotifications(); }
            panel.hidden = !isHidden;
            bell.setAttribute('aria-expanded', String(panel.hidden === false));
        });
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.notif-wrap')) panel.hidden = true;
        });
        document.getElementById('notifList')?.addEventListener('click', (e) => {
            const li = e.target.closest('[data-notif-id]');
            if (li) markNotifRead(Number(li.dataset.notifId));
        });
        document.getElementById('notifReadAllBtn')?.addEventListener('click', markAllNotifRead);
    }

    /* ---------- Init ---------- */
    async function init() {
        if (!document.querySelector('[data-view="documents"]')) return;
        initTreeClicks();
        initTableClicks();
        initDetailPanel();
        initUpload();
        initDropzone();
        initFilters();
        initPermsModal();
        initTemplatesModals();
        initNotifications();

        await Promise.all([loadTags(), loadDepartments(), loadStats(), loadContacts(), loadProjects()]);
        await ensureAllDepartmentFolders();
        await loadFolders();
        renderTree();
        renderTagFilterOptions();
        state.pseudoView = 'all';
        renderBreadcrumb();
        await loadDocuments();
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', Documents.init);

/* ============================================================
   SCOPED DOCS — mini gestor de pastas/documentos embutido
   em módulos (Projects, People, Books), sem a árvore global
   ============================================================ */

const ScopedDocs = (() => {
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    function fmtDate(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        return isNaN(d) ? '—' : d.toLocaleDateString('pt-PT');
    }
    function fmtBytes(n) {
        if (!n) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let i = 0, v = n;
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
        return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
    }

    async function loadFolders(inst) {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders`);
            inst.folders = res.ok ? await res.json() : [];
        } catch (e) { inst.folders = []; }
    }
    async function loadDocuments(inst) {
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/documents?folder_id=${inst.currentId}`);
            inst.documents = res.ok ? await res.json() : [];
        } catch (e) { inst.documents = []; }
    }

    function render(inst) {
        const { container } = inst;
        container.querySelector('[data-crumb]').innerHTML = inst.path.map(f =>
            `<span class="doc-crumb" data-nav="${f.id}">${escapeHtml(f.name)}</span>`
        ).join(' <span aria-hidden="true">/</span> ');

        const subfolders = inst.folders.filter(f => f.parent_id === inst.currentId);
        container.querySelector('[data-folders]').innerHTML = subfolders.map(f => `
            <span class="scoped-folder-chip">
                <span class="scoped-folder-chip-label" data-nav="${f.id}">
                    <svg viewBox="0 0 16 16" fill="none"><path d="M2 4 a1 1 0 0 1 1 -1 h3 l1.5 2 H13 a1 1 0 0 1 1 1 v6.5 a1 1 0 0 1 -1 1 H3 a1 1 0 0 1 -1 -1 z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
                    ${escapeHtml(f.name)}
                </span>
                ${f.is_system ? '' : `
                    <button type="button" class="scoped-folder-chip-btn" data-rename-folder="${f.id}" aria-label="Renomear pasta" title="Renomear pasta">✎</button>
                    <button type="button" class="scoped-folder-chip-btn" data-delete-folder="${f.id}" aria-label="Apagar pasta" title="Apagar pasta">×</button>
                `}
            </span>`).join('');

        const body = container.querySelector('[data-doc-body]');
        body.innerHTML = inst.documents.map(d => `
            <tr data-open="${d.id}">
                <td>${escapeHtml(d.name)}</td>
                <td>${fmtDate(d.created_at)}</td>
                <td>${fmtBytes(d.size_bytes)}</td>
                <td class="scoped-docs-row-actions">
                    <button type="button" class="attach-remove" data-delete-doc="${d.id}" aria-label="Apagar documento" title="Apagar">×</button>
                </td>
            </tr>`).join('');
        container.querySelector('[data-empty]').hidden = inst.documents.length > 0 || subfolders.length > 0;
    }

    async function refresh(inst) {
        await loadDocuments(inst);
        render(inst);
    }

    async function renameFolder(inst, folderId) {
        const folder = inst.folders.find(f => f.id === folderId);
        if (!folder) return;
        const name = await UIModal.prompt('Novo nome da pasta:', folder.name);
        if (!name || !name.trim() || name.trim() === folder.name) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders/${folderId}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim() }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Falha ao renomear');
            }
            await loadFolders(inst);
            render(inst);
        } catch (e) { await UIModal.alert(e.message || 'Não foi possível renomear a pasta.'); }
    }

    async function deleteFolder(inst, folderId) {
        const folder = inst.folders.find(f => f.id === folderId);
        if (!folder) return;
        if (!await UIModal.confirm(
            `Apagar a pasta "${folder.name}"? A pasta tem de estar vazia — sem subpastas nem documentos.`,
            { danger: true }
        )) return;
        try {
            const res = await apiFetch(`${API_BASE.DOCS}/folders/${folderId}`, { method: 'DELETE' });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || 'Falha ao apagar');
            }
            await loadFolders(inst);
            render(inst);
        } catch (e) { await UIModal.alert(e.message || 'Não foi possível apagar a pasta.'); }
    }

    function navigateTo(inst, folderId) {
        const idx = inst.path.findIndex(f => f.id === folderId);
        if (idx >= 0) {
            inst.path = inst.path.slice(0, idx + 1);
        } else {
            const folder = inst.folders.find(f => f.id === folderId);
            if (!folder) return;
            inst.path = [...inst.path, folder];
        }
        inst.currentId = folderId;
        refresh(inst);
    }

    function wireEvents(inst) {
        const { container } = inst;
        container.addEventListener('click', async (e) => {
            const renameBtn = e.target.closest('[data-rename-folder]');
            if (renameBtn) { await renameFolder(inst, Number(renameBtn.dataset.renameFolder)); return; }

            const delFolderBtn = e.target.closest('[data-delete-folder]');
            if (delFolderBtn) { await deleteFolder(inst, Number(delFolderBtn.dataset.deleteFolder)); return; }

            const nav = e.target.closest('[data-nav]');
            if (nav) { navigateTo(inst, Number(nav.dataset.nav)); return; }

            const delBtn = e.target.closest('[data-delete-doc]');
            if (delBtn) {
                const id = Number(delBtn.dataset.deleteDoc);
                const doc = inst.documents.find(d => d.id === id);
                if (!await UIModal.confirm(`Apagar "${doc?.name || 'este documento'}"? Esta acção não pode ser desfeita.`)) return;
                try {
                    const res = await apiFetch(`${API_BASE.DOCS}/documents/${id}`, { method: 'DELETE' });
                    if (!res.ok) throw new Error();
                    await refresh(inst);
                } catch (e2) { await UIModal.alert('Não foi possível apagar o documento.'); }
                return;
            }

            const openRow = e.target.closest('[data-open]');
            if (openRow) {
                const doc = inst.documents.find(d => d.id === Number(openRow.dataset.open));
                if (doc?.url) window.open(doc.url, '_blank');
                return;
            }
            if (e.target.closest('[data-new-folder]')) {
                const name = await UIModal.prompt('Nome da nova pasta:');
                if (!name) return;
                try {
                    const res = await apiFetch(`${API_BASE.DOCS}/folders`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name, parent_id: inst.currentId }),
                    });
                    if (!res.ok) throw new Error();
                    await loadFolders(inst);
                    render(inst);
                } catch (e2) { await UIModal.alert('Não foi possível criar a pasta.'); }
                return;
            }
            if (e.target.closest('[data-upload]')) { container.querySelector('[data-file-input]').click(); }
        });
        container.querySelector('[data-file-input]').addEventListener('change', async (e) => {
            for (const file of e.target.files) {
                const fd = new FormData();
                fd.append('file', file);
                try {
                    const res = await apiFetch(`${API_BASE.DOCS}/documents?folder_id=${inst.currentId}`, { method: 'POST', body: fd });
                    if (!res.ok) throw new Error();
                } catch (e2) { await UIModal.alert(`Falha ao enviar ${file.name}.`); }
            }
            e.target.value = '';
            await refresh(inst);
        });
    }

    async function mount(containerId, rootFolderName) {
        const container = document.getElementById(containerId);
        if (!container || container.dataset.mounted) return;
        container.dataset.mounted = 'true';

        container.innerHTML = `
            <div class="scoped-docs-toolbar">
                <span class="scoped-docs-breadcrumb" data-crumb></span>
                <div class="scoped-docs-actions">
                    <button type="button" class="btn-soft" data-new-folder>+ Nova pasta</button>
                    <button type="button" class="btn-kora" data-upload>Carregar</button>
                    <input type="file" data-file-input multiple hidden>
                </div>
            </div>
            <div class="scoped-folders" data-folders></div>
            <div class="scoped-docs-table-wrap">
                <table class="proj-table scoped-docs-table">
                    <thead><tr><th scope="col">Nome</th><th scope="col">Criado</th><th scope="col">Tamanho</th><th scope="col"></th></tr></thead>
                    <tbody data-doc-body></tbody>
                </table>
                <p class="hr-empty-state" data-empty hidden>Sem documentos aqui.</p>
            </div>`;

        const inst = { container, folders: [], documents: [], rootId: null, currentId: null, path: [] };

        await loadFolders(inst);
        const root = inst.folders.find(f => f.name === rootFolderName && f.is_system && !f.parent_id);
        if (!root) {
            container.innerHTML = `<p class="hr-empty-state">Pasta "${escapeHtml(rootFolderName)}" não encontrada.</p>`;
            return;
        }
        inst.rootId = root.id;
        inst.currentId = root.id;
        inst.path = [root];

        wireEvents(inst);
        await refresh(inst);
    }

    return { mount };
})();

/* ============================================================
   ACESSO — perfil da sessão, filtragem de nav por módulo,
   gestão de colaboradores e permissões (só visível para admins)
   ============================================================ */

const Access = (() => {
    let me = null;
    let collaborators = [];

    const MODULE_LABELS = {
        crm: 'CRM', books: 'Books', projects: 'Projects', desk: 'Desk',
        people: 'People', accounting: 'Contabilidade', stock: 'Stock', creator: 'Creator', kora: 'Kora AI',
    };

    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    async function fetchMe() {
        try {
            const res = await apiFetch(`${API_BASE.AUTH}/me`);
            if (!res.ok) return null;
            return await res.json();
        } catch (e) {
            return null;
        }
    }

    function applyNavFiltering() {
        if (!me) return;
        const allowed = new Set(me.modules || []);

        document.querySelectorAll('.nav-item[data-target]').forEach(item => {
            const target = item.dataset.target;
            if (target === 'home') return;
            if (target === 'admin') {
                item.hidden = !me.is_admin;
                return;
            }
            item.hidden = !(me.is_admin || allowed.has(target));
        });

        const activeItem = document.querySelector('.nav-item.is-active[data-target]');
        if (activeItem && activeItem.hidden) {
            activateView('home');
        }
    }

    async function loadCollaborators() {
        const body = document.getElementById('adminUsersBody');
        const empty = document.getElementById('adminEmpty');
        try {
            const res = await apiFetch(`${API_BASE.AUTH}/colaboradores`);
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || `Erro ${res.status} ao carregar colaboradores`);
            }
            collaborators = await res.json();
        } catch (err) {
            collaborators = [];
            if (body) body.innerHTML = '';
            if (empty) {
                empty.hidden = false;
                empty.textContent = `Não foi possível carregar os colaboradores: ${err.message}`;
            }
            return;
        }
        renderCollaborators();
    }

    function renderCollaborators() {
        const body = document.getElementById('adminUsersBody');
        const empty = document.getElementById('adminEmpty');
        if (!body) return;

        if (!collaborators.length) {
            body.innerHTML = '';
            if (empty) {
                empty.hidden = false;
                empty.textContent = 'Sem colaboradores ainda.';
            }
            return;
        }
        if (empty) empty.hidden = true;

        body.innerHTML = collaborators.map(c => `
            <tr>
                <td>${escapeHtml(c.nome)}${c.id === me?.id ? ' <span class="badge badge--outro">(eu)</span>' : ''}</td>
                <td>${escapeHtml(c.email)}</td>
                <td>${c.is_admin ? '<span class="badge badge--cycle">Admin</span>' : '<span class="badge badge--outro">Colaborador</span>'}</td>
                <td>${c.is_admin
                    ? '<span class="badge badge--empresa">Todos</span>'
                    : ((c.modules || []).map(m => `<span class="badge badge--empresa">${escapeHtml(MODULE_LABELS[m] || m)}</span>`).join(' ') || '<span class="badge badge--outro">Nenhum</span>')}</td>
                <td style="white-space:nowrap;">${c.is_admin ? '' : `
                    <button type="button" class="btn-link-sm" data-edit-user="${c.id}">Editar</button>
                    <button type="button" class="btn-link-sm" data-del-user="${c.id}" style="color:#DC2626;">Apagar</button>
                `}</td>
            </tr>
        `).join('');
    }

    const modal = () => document.getElementById('adminUserModal');

    function openModal(user) {
        const form = document.getElementById('adminUserForm');
        if (!form) return;
        form.reset();

        document.getElementById('adminUserId').value = user?.id || '';
        document.getElementById('adminUserName').value = user?.nome || '';
        document.getElementById('adminUserEmail').value = user?.email || '';
        document.getElementById('adminUserModalTitle').textContent = user ? 'Editar colaborador' : 'Adicionar colaborador';

        const isEdit = !!user;
        document.getElementById('adminUserName').disabled = false;
        document.getElementById('adminUserEmail').disabled = false;
        document.getElementById('adminUserPwdField').hidden = false;
        document.getElementById('adminUserPwd').required = !isEdit;
        document.getElementById('adminUserPwdLabel').innerHTML = isEdit
            ? 'Nova password (opcional)'
            : 'Password inicial <span class="req">*</span>';
        document.getElementById('adminUserPwdHint').hidden = !isEdit;

        const checked = new Set(user?.modules || []);
        document.querySelectorAll('#adminUserModules input[type="checkbox"]').forEach(cb => {
            cb.checked = checked.has(cb.value);
        });

        modal()?.classList.add('is-open');
        modal()?.setAttribute('aria-hidden', 'false');
    }

    function closeModal() {
        modal()?.classList.remove('is-open');
        modal()?.setAttribute('aria-hidden', 'true');
    }

    function wireEvents() {
        document.getElementById('adminAddBtn')?.addEventListener('click', () => openModal(null));

        modal()?.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModal));

        document.getElementById('adminUsersBody')?.addEventListener('click', async (e) => {
            const editBtn = e.target.closest('[data-edit-user]');
            if (editBtn) {
                const user = collaborators.find(c => c.id === editBtn.dataset.editUser);
                if (user) openModal(user);
                return;
            }
            const delBtn = e.target.closest('[data-del-user]');
            if (delBtn) {
                const user = collaborators.find(c => c.id === delBtn.dataset.delUser);
                if (!user) return;
                if (!await UIModal.confirm(`Apagar a conta de ${user.nome} (${user.email})? Esta acção não pode ser desfeita.`, { danger: true, okLabel: 'Apagar' })) return;
                try {
                    const res = await apiFetch(`${API_BASE.AUTH}/colaboradores/${user.id}`, { method: 'DELETE' });
                    if (!res.ok) {
                        const data = await res.json().catch(() => ({}));
                        throw new Error(data.detail || 'Não foi possível apagar o colaborador.');
                    }
                } catch (err) {
                    await UIModal.alert(err.message || 'Não foi possível apagar o colaborador.');
                    return;
                }
                await loadCollaborators();
                UIToast.success('Colaborador apagado com sucesso!');
            }
        });

        document.getElementById('adminUserForm')?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = document.getElementById('adminUserForm');
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const id = document.getElementById('adminUserId').value;
            const modules = Array.from(document.querySelectorAll('#adminUserModules input:checked')).map(cb => cb.value);

            try {
                if (id) {
                    const body = {
                        nome: document.getElementById('adminUserName').value.trim(),
                        email: document.getElementById('adminUserEmail').value.trim(),
                        modules,
                    };
                    const senha = document.getElementById('adminUserPwd').value;
                    if (senha) body.senha = senha;
                    const res = await apiFetch(`${API_BASE.AUTH}/colaboradores/${id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!res.ok) {
                        const data = await res.json().catch(() => ({}));
                        await UIModal.alert(data.detail || 'Não foi possível guardar as alterações.');
                        return;
                    }
                } else {
                    const body = {
                        nome: document.getElementById('adminUserName').value.trim(),
                        email: document.getElementById('adminUserEmail').value.trim(),
                        senha: document.getElementById('adminUserPwd').value,
                        modules,
                    };
                    const res = await apiFetch(`${API_BASE.AUTH}/colaboradores`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!res.ok) {
                        const data = await res.json().catch(() => ({}));
                        await UIModal.alert(data.detail || 'Não foi possível criar o colaborador.');
                        return;
                    }
                }
                closeModal();
                await loadCollaborators();
                UIToast.success(id ? 'Colaborador actualizado com sucesso!' : 'Colaborador criado com sucesso!');
            } catch (err) {
                await UIModal.alert('Ocorreu um erro. Tente novamente.');
            }
        });
    }

    function applyUserAvatar() {
        const btn = document.getElementById('userAvatarBtn');
        if (!btn) return;
        const nome = (me?.nome || '').trim();
        btn.textContent = nome ? nome[0].toUpperCase() : '?';
        btn.title = nome;
    }

    async function init() {
        me = await fetchMe();
        applyUserAvatar();
        applyNavFiltering();
        wireEvents();
        if (me?.is_admin) {
            await loadCollaborators();
        }
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', Access.init);
/* ============================================================
   PAYROLL MODULE — folha salarial, perfil salarial e
   regras de dedução (rh-service)
   ============================================================ */

const PAYROLL = (() => {
    const state = {
        employees: [],
        currentEmp: null,       // funcionário aberto na vista de detalhe
        profileExists: false,   // se o funcionário actual já tem perfil salarial
        payrolls: [],           // folhas do funcionário actual
        currentPayroll: null,   // { payroll, items } da folha aberta
        rules: [],              // regras de dedução
    };

    const MONTHS = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
        'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];

    const PAYROLL_STATUS_LABEL = { draft: 'Rascunho', approved: 'Aprovada' };
    const PAYROLL_STATUS_CLASS = { draft: 'status-pill--meeting', approved: 'status-pill--online' };
    const CALC_TYPE_LABEL = { fixed: 'Valor fixo', percentage: 'Percentagem', bracket: 'Escalões' };
    const CALC_BASE_LABEL = { gross_salary: 'Salário Bruto', base_salary: 'Salário Base' };

    /* ---------- Helpers ---------- */
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
    function fmtKz(val) {
        return Number(val || 0).toLocaleString('pt-AO', { maximumFractionDigits: 2 }) + ' Kz';
    }
    function openModal(el) {
        if (!el) return;
        el.classList.add('is-open');
        el.setAttribute('aria-hidden', 'false');
    }
    function closeModal(el) {
        if (!el) return;
        el.classList.remove('is-open');
        el.setAttribute('aria-hidden', 'true');
    }
    function initials(name) {
        return (name || '').trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() || '').join('') || '?';
    }
    async function apiJson(url, options = {}) {
        const res = await apiFetch(url, options);
        const data = await res.json().catch(() => null);
        if (!res.ok) {
            const detail = data?.detail || `Erro ${res.status}`;
            const err = new Error(detail);
            err.status = res.status;
            throw err;
        }
        return data;
    }

    /* ---------- Navegação entre vistas ---------- */
    function showView(view) {
        const list = document.getElementById('prListView');
        const detail = document.getElementById('prDetailView');
        const payroll = document.getElementById('prPayrollDetailView');
        if (!list || !detail || !payroll) return;
        list.hidden = view !== 'list';
        detail.hidden = view !== 'detail';
        payroll.hidden = view !== 'payroll';
    }

    function activatePrTab(target) {
        document.querySelectorAll('[data-prtab]').forEach(t => {
            const active = t.dataset.prtab === target;
            t.classList.toggle('is-active', active);
            t.setAttribute('aria-selected', String(active));
        });
        document.querySelectorAll('[data-prpane]').forEach(p => {
            p.classList.toggle('is-active', p.dataset.prpane === target);
        });
        if (target === 'perfil' && state.currentEmp) loadSalaryProfile(state.currentEmp.id);
        if (target === 'folhas' && state.currentEmp) loadPayrolls(state.currentEmp.id);
    }

    /* ---------- Lista de funcionários ---------- */
    let departmentsCache = [];

    async function loadEmployees() {
        try {
            const [employees, departments] = await Promise.all([
                apiJson(`${API_BASE.RH}/employees`),
                apiJson(`${API_BASE.RH}/departments`).catch(() => []),
            ]);
            state.employees = employees;
            departmentsCache = departments;
        } catch (e) {
            state.employees = [];
        }
    }

    function deptName(id) {
        return departmentsCache.find(d => d.id === Number(id))?.name || '—';
    }

    function renderEmployeeList() {
        const tbody = document.getElementById('prEmpTableBody');
        const empty = document.getElementById('prEmpEmpty');
        if (!tbody) return;
        const q = (document.getElementById('prSearch')?.value || '').trim().toLowerCase();
        const list = state.employees.filter(e =>
            !q || (e.full_name || '').toLowerCase().includes(q) || (e.role || '').toLowerCase().includes(q)
        );
        if (!list.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = list.map(e => `
            <tr class="inv-row" data-pr-open="${e.id}" style="cursor:pointer;">
                <td class="inv-client">${escapeHtml(e.full_name)}</td>
                <td>${escapeHtml(e.role || '—')}</td>
                <td>${escapeHtml(deptName(e.department_id))}</td>
                <td class="action-col">
                    <button class="row-action" data-pr-open="${e.id}" aria-label="Abrir perfil" title="Abrir perfil">
                        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4 L10 8 L6 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
                    </button>
                </td>
            </tr>
        `).join('');
    }

    /* ---------- Detalhe do funcionário ---------- */
    function openEmployee(empId) {
        const emp = state.employees.find(e => e.id === Number(empId));
        if (!emp) return;
        state.currentEmp = emp;

        document.getElementById('prEmpName').textContent = emp.full_name || '—';
        document.getElementById('prEmpRole').textContent = emp.role || '—';
        const avatar = document.getElementById('prEmpAvatar');
        if (avatar) {
            avatar.innerHTML = emp.photo_url
                ? `<img src="${emp.photo_url}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:inherit;">`
                : escapeHtml(initials(emp.full_name));
        }

        renderEmployeeInfo(emp);
        showView('detail');
        activatePrTab('informacoes');
    }

    function infoItem(label, value) {
        return `
            <div class="pr-info-item">
                <span class="pr-info-label">${label}</span>
                <span class="pr-info-value">${escapeHtml(value || '—')}</span>
            </div>`;
    }

    function renderEmployeeInfo(emp) {
        const host = document.getElementById('prInfoGrid');
        if (!host) return;
        host.innerHTML = [
            infoItem('Nome completo', emp.full_name),
            infoItem('Nº do colaborador', emp.employee_number),
            infoItem('Cargo', emp.role),
            infoItem('Email', emp.email),
            infoItem('Telefone', emp.phone || emp.mobile),
            infoItem('NIF', emp.nif),
            infoItem('NISS (Seg. Social)', emp.niss),
            infoItem('Banco', emp.bank_name),
            infoItem('Nº da conta', emp.bank_account),
            infoItem('IBAN', emp.iban),
        ].join('');
    }

    /* ---------- Perfil Salarial ---------- */
    async function loadSalaryProfile(empId) {
        const form = document.getElementById('salaryProfileForm');
        const hint = document.getElementById('prProfileHint');
        const submitBtn = document.getElementById('spSubmitBtn');
        if (!form) return;
        form.reset();
        state.profileExists = false;

        try {
            const profile = await apiJson(`${API_BASE.RH}/employees/${empId}/salary-profile`);
            state.profileExists = true;
            document.getElementById('spBaseSalary').value = Number(profile.base_salary);
            document.getElementById('spFood').value = Number(profile.food_allowance || 0);
            document.getElementById('spTransport').value = Number(profile.transport_allowance || 0);
            document.getElementById('spOther').value = Number(profile.other_allowance || 0);
            if (hint) hint.textContent = 'Perfil salarial configurado — pode editar os valores e guardar.';
            if (submitBtn) submitBtn.textContent = 'Atualizar Perfil Salarial';
        } catch (e) {
            if (hint) {
                hint.textContent = e.status === 404
                    ? 'Este funcionário ainda não tem perfil salarial. Preencha os valores e guarde.'
                    : 'Não foi possível carregar o perfil salarial.';
            }
            if (submitBtn) submitBtn.textContent = 'Salvar Perfil Salarial';
        }
        updateProfilePreview();
    }

    function updateProfilePreview() {
        const host = document.getElementById('spPreview');
        if (!host) return;
        const base = Number(document.getElementById('spBaseSalary')?.value || 0);
        const food = Number(document.getElementById('spFood')?.value || 0);
        const transport = Number(document.getElementById('spTransport')?.value || 0);
        const other = Number(document.getElementById('spOther')?.value || 0);
        const gross = base + food + transport + other;
        host.innerHTML = `
            <div class="sal-preview-inner">
                <div class="sal-row"><span>Salário Base</span><span>${fmtKz(base)}</span></div>
                ${food ? `<div class="sal-row"><span>Subsídio de Alimentação</span><span>+ ${fmtKz(food)}</span></div>` : ''}
                ${transport ? `<div class="sal-row"><span>Subsídio de Transporte</span><span>+ ${fmtKz(transport)}</span></div>` : ''}
                ${other ? `<div class="sal-row"><span>Outros Subsídios</span><span>+ ${fmtKz(other)}</span></div>` : ''}
                <div class="sal-row sal-net"><span>Salário Bruto mensal</span><span>${fmtKz(gross)}</span></div>
            </div>`;
    }

    function initSalaryProfileForm() {
        const form = document.getElementById('salaryProfileForm');
        if (!form) return;
        ['spBaseSalary', 'spFood', 'spTransport', 'spOther'].forEach(id => {
            document.getElementById(id)?.addEventListener('input', updateProfilePreview);
        });
        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            if (!state.currentEmp) return;
            const body = {
                base_salary: Number(document.getElementById('spBaseSalary').value) || 0,
                food_allowance: Number(document.getElementById('spFood').value) || 0,
                transport_allowance: Number(document.getElementById('spTransport').value) || 0,
                other_allowance: Number(document.getElementById('spOther').value) || 0,
            };
            try {
                await apiJson(`${API_BASE.RH}/employees/${state.currentEmp.id}/salary-profile`, {
                    method: state.profileExists ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível guardar o perfil salarial.');
                return;
            }
            await UIModal.alert(state.profileExists ? 'Perfil salarial atualizado.' : 'Perfil salarial criado.');
            loadSalaryProfile(state.currentEmp.id);
        });
    }

    /* ---------- Folhas Salariais do funcionário ---------- */
    async function loadPayrolls(empId) {
        const tbody = document.getElementById('prPayrollTableBody');
        const empty = document.getElementById('prPayrollEmpty');
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="5" class="empty-row">A carregar folhas...</td></tr>';
        try {
            state.payrolls = await apiJson(`${API_BASE.RH}/employees/${empId}/payrolls`);
        } catch (e) {
            state.payrolls = [];
        }
        if (!state.payrolls.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.payrolls.map(p => `
            <tr class="inv-row">
                <td>${MONTHS[Number(p.month)] || p.month}</td>
                <td>${p.year}</td>
                <td class="num">${fmtKz(p.net_salary)}</td>
                <td><span class="status-pill ${PAYROLL_STATUS_CLASS[p.status] || ''}">${PAYROLL_STATUS_LABEL[p.status] || p.status}</span></td>
                <td class="action-col">
                    <button class="btn-soft" data-pr-view="${p.id}" style="padding:4px 12px;">Ver</button>
                </td>
            </tr>
        `).join('');
    }

    /* ---------- Gerar folha ---------- */
    function initPayrollGenModal() {
        const modal = document.getElementById('payrollGenModal');
        const form = document.getElementById('payrollGenForm');
        if (!modal || !form) return;

        document.getElementById('prGenPayrollBtn')?.addEventListener('click', () => {
            if (!state.currentEmp) return;
            const now = new Date();
            document.getElementById('pgMonth').value = String(now.getMonth() + 1);
            document.getElementById('pgYear').value = now.getFullYear();
            document.getElementById('payrollGenHint').textContent =
                `Gerar folha salarial de ${state.currentEmp.full_name}. As regras de dedução activas serão aplicadas automaticamente.`;
            openModal(modal);
        });
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            if (!state.currentEmp) return;
            const body = {
                month: Number(document.getElementById('pgMonth').value),
                year: Number(document.getElementById('pgYear').value),
            };
            let result;
            try {
                result = await apiJson(`${API_BASE.RH}/employees/${state.currentEmp.id}/payrolls/generate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível gerar a folha salarial.');
                return;
            }
            closeModal(modal);
            activatePrTab('folhas');
            if (result?.payroll?.id) openPayrollDetail(result.payroll.id);
        });
    }

    /* ---------- Detalhe da folha ---------- */
    async function openPayrollDetail(payrollId) {
        let data;
        try {
            data = await apiJson(`${API_BASE.RH}/payrolls/${payrollId}`);
        } catch (e) {
            await UIModal.alert(e.message || 'Não foi possível carregar a folha.');
            return;
        }
        state.currentPayroll = data;
        renderPayrollDetail();
        showView('payroll');
    }

    function renderPayrollDetail() {
        const data = state.currentPayroll;
        if (!data) return;
        const p = data.payroll;
        const items = data.items || [];
        const empName = state.currentEmp?.full_name
            || state.employees.find(e => e.id === p.employee_id)?.full_name
            || `Funcionário #${p.employee_id}`;

        document.getElementById('prPayrollTitle').textContent =
            `Folha Salarial · ${MONTHS[Number(p.month)] || p.month} ${p.year}`;

        document.getElementById('prPayrollInfo').innerHTML = [
            infoItem('Funcionário', empName),
            infoItem('Mês', MONTHS[Number(p.month)] || String(p.month)),
            infoItem('Ano', String(p.year)),
            `<div class="pr-info-item">
                <span class="pr-info-label">Estado</span>
                <span class="pr-info-value"><span class="status-pill ${PAYROLL_STATUS_CLASS[p.status] || ''}">${PAYROLL_STATUS_LABEL[p.status] || p.status}</span></span>
            </div>`,
        ].join('');

        const earnings = items.filter(i => i.item_type === 'earning');
        const deductions = items.filter(i => i.item_type === 'deduction');

        const renderItems = (list, sign) => list.length
            ? list.map(i => `
                <li class="pr-item-row">
                    <span>${escapeHtml(i.description)}</span>
                    <span class="${sign === '-' ? 'pr-amount-neg' : 'pr-amount-pos'}">${sign} ${fmtKz(i.amount)}</span>
                </li>
            `).join('')
            : '<li class="attach-empty">Nenhum item.</li>';

        document.getElementById('prEarningsList').innerHTML = renderItems(earnings, '+');
        document.getElementById('prDeductionsList').innerHTML = renderItems(deductions, '-');

        document.getElementById('prPayrollSummary').innerHTML = `
            <div class="sal-preview-inner">
                <div class="sal-row"><span>Salário Bruto</span><span>${fmtKz(p.gross_salary)}</span></div>
                <div class="sal-row"><span>Total de Ganhos</span><span>${fmtKz(p.total_earnings)}</span></div>
                <div class="sal-row sal-deduction"><span>Total de Deduções</span><span>- ${fmtKz(p.total_deductions)}</span></div>
                <div class="sal-row sal-net"><span>Salário Líquido</span><span>${fmtKz(p.net_salary)}</span></div>
            </div>`;

        const approveBtn = document.getElementById('prApproveBtn');
        if (approveBtn) approveBtn.hidden = p.status !== 'draft';
    }

    function initApproveButton() {
        document.getElementById('prApproveBtn')?.addEventListener('click', async () => {
            const p = state.currentPayroll?.payroll;
            if (!p) return;
            if (!await UIModal.confirm('Aprovar esta folha salarial? Após a aprovação não poderá ser alterada.')) return;
            try {
                await apiJson(`${API_BASE.RH}/payrolls/${p.id}/approve`, { method: 'POST' });
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível aprovar a folha.');
                return;
            }
            openPayrollDetail(p.id);
            UIToast.success('Folha salarial aprovada com sucesso!');
        });
    }

    /* ---------- Regras de Dedução ---------- */
    async function loadDeductionRules() {
        const tbody = document.getElementById('dedTableBody');
        const empty = document.getElementById('dedEmpty');
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="7" class="empty-row">A carregar regras...</td></tr>';
        try {
            state.rules = await apiJson(`${API_BASE.RH}/deduction-rules`);
        } catch (e) {
            state.rules = [];
        }
        if (!state.rules.length) {
            tbody.innerHTML = '';
            if (empty) empty.hidden = false;
            return;
        }
        if (empty) empty.hidden = true;
        tbody.innerHTML = state.rules.map(r => `
            <tr class="inv-row">
                <td class="inv-client">${escapeHtml(r.name)}${r.description ? `<div class="field-hint" style="margin:2px 0 0;">${escapeHtml(r.description)}</div>` : ''}</td>
                <td>${CALC_TYPE_LABEL[r.calculation_type] || escapeHtml(r.calculation_type)}</td>
                <td>${CALC_BASE_LABEL[r.calculation_base] || escapeHtml(r.calculation_base || '—')}</td>
                <td class="num">${r.calculation_type === 'percentage' ? Number(r.value) + ' %' : fmtKz(r.value)}</td>
                <td>${escapeHtml(r.country_code || '—')}</td>
                <td><span class="status-pill ${r.active ? 'status-pill--online' : 'status-pill--off'}">${r.active ? 'Activa' : 'Inactiva'}</span></td>
                <td class="action-col">
                    <button class="row-action" data-ded-edit="${r.id}" aria-label="Editar" title="Editar">
                        <svg viewBox="0 0 16 16" fill="none"><path d="M11 2 L14 5 L5 14 L2 14 L2 11 L11 2 z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>
                    </button>
                </td>
            </tr>
        `).join('');
    }

    async function openDedRuleModal(ruleId = null) {
        const modal = document.getElementById('dedRuleModal');
        const form = document.getElementById('dedRuleForm');
        if (!modal || !form) return;
        form.reset();
        document.getElementById('dedRuleId').value = '';
        document.getElementById('dedCountry').value = 'AO';
        document.getElementById('dedActiveField').hidden = true;
        document.getElementById('dedRuleModalTitle').textContent = 'Nova regra de dedução';

        if (ruleId) {
            let rule;
            try {
                rule = await apiJson(`${API_BASE.RH}/deduction-rules/${ruleId}`);
            } catch (e) {
                await UIModal.alert(e.message || 'Não foi possível carregar a regra.');
                return;
            }
            document.getElementById('dedRuleId').value = rule.id;
            document.getElementById('dedName').value = rule.name || '';
            document.getElementById('dedDescription').value = rule.description || '';
            document.getElementById('dedCalcType').value = rule.calculation_type || 'percentage';
            document.getElementById('dedCalcBase').value = rule.calculation_base || 'gross_salary';
            document.getElementById('dedValue').value = Number(rule.value || 0);
            document.getElementById('dedCountry').value = rule.country_code || 'AO';
            document.getElementById('dedCountry').disabled = true;
            document.getElementById('dedActive').checked = rule.active !== false;
            document.getElementById('dedActiveField').hidden = false;
            document.getElementById('dedRuleModalTitle').textContent = 'Editar regra de dedução';
        } else {
            document.getElementById('dedCountry').disabled = false;
        }
        openModal(modal);
    }

    function initDedRules() {
        const modal = document.getElementById('dedRuleModal');
        const form = document.getElementById('dedRuleForm');
        if (!modal || !form) return;

        document.getElementById('dedAddBtn')?.addEventListener('click', () => openDedRuleModal());
        modal.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(modal)));

        document.getElementById('dedTableBody')?.addEventListener('click', e => {
            const btn = e.target.closest('[data-ded-edit]');
            if (btn) openDedRuleModal(Number(btn.dataset.dedEdit));
        });

        document.getElementById('dedCalcType')?.addEventListener('change', () => {
            const type = document.getElementById('dedCalcType').value;
            document.getElementById('dedValueHint').textContent =
                type === 'percentage' ? 'Percentagem sobre a base de cálculo (ex: 3 = 3%).'
                : type === 'fixed' ? 'Valor fixo em Kz descontado todos os meses.'
                : 'Valor de referência — os escalões são configurados no backend.';
        });

        form.addEventListener('submit', async e => {
            e.preventDefault();
            if (!form.checkValidity()) { form.reportValidity(); return; }
            const ruleId = document.getElementById('dedRuleId').value;
            const base = {
                name: document.getElementById('dedName').value.trim(),
                description: document.getElementById('dedDescription').value.trim() || null,
                calculation_type: document.getElementById('dedCalcType').value,
                calculation_base: document.getElementById('dedCalcBase').value,
                value: Number(document.getElementById('dedValue').value) || 0,
            };
            try {
                if (ruleId) {
                    await apiJson(`${API_BASE.RH}/deduction-rules/${ruleId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ...base, active: document.getElementById('dedActive').checked }),
                    });
                } else {
                    await apiJson(`${API_BASE.RH}/deduction-rules`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ...base, country_code: (document.getElementById('dedCountry').value || 'AO').trim().toUpperCase() }),
                    });
                }
            } catch (err) {
                await UIModal.alert(err.message || 'Não foi possível guardar a regra.');
                return;
            }
            closeModal(modal);
            loadDeductionRules();
            UIToast.success(ruleId ? 'Regra de dedução actualizada com sucesso!' : 'Regra de dedução criada com sucesso!');
        });
    }

    /* ---------- Eventos globais da secção ---------- */
    function initEvents() {
        // entrada nas secções via sidenav do RH
        document.querySelectorAll('[data-hrtab="folha"]').forEach(btn => {
            btn.addEventListener('click', async () => {
                showView('list');
                await loadEmployees();
                renderEmployeeList();
            });
        });
        document.querySelectorAll('[data-hrtab="deducoes"]').forEach(btn => {
            btn.addEventListener('click', () => loadDeductionRules());
        });

        document.getElementById('prSearch')?.addEventListener('input', renderEmployeeList);

        document.getElementById('prEmpTableBody')?.addEventListener('click', e => {
            const opener = e.target.closest('[data-pr-open]');
            if (opener) openEmployee(Number(opener.dataset.prOpen));
        });

        document.getElementById('prPayrollTableBody')?.addEventListener('click', e => {
            const btn = e.target.closest('[data-pr-view]');
            if (btn) openPayrollDetail(Number(btn.dataset.prView));
        });

        document.querySelectorAll('[data-prtab]').forEach(tab => {
            tab.addEventListener('click', () => activatePrTab(tab.dataset.prtab));
        });

        document.getElementById('prBackToListBtn')?.addEventListener('click', () => showView('list'));
        document.getElementById('prBackToEmpBtn')?.addEventListener('click', () => {
            showView('detail');
            activatePrTab('folhas');
        });
    }

    /* ---------- Init ---------- */
    function init() {
        if (!document.querySelector('[data-hrpane="folha"]')) return;
        initEvents();
        initSalaryProfileForm();
        initPayrollGenModal();
        initApproveButton();
        initDedRules();
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', PAYROLL.init);

/* ============================================================
   HOME DASHBOARD — KPIs e gráfico de receita com dados reais
   (agrega finance-service, crm-service e projects-service).
   Cada pedido falha de forma isolada: se o utilizador não tiver
   acesso a um módulo, essa tile mostra "—" em vez de rebentar
   o resto da página.
   ============================================================ */

const HomeDashboard = (() => {
    const MONTH_ABBR = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
    const MONTH_FULL = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
    const WEEKDAY_FULL = ['Domingo', 'Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado'];

    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }

    function fmtCompactKz(n) {
        const val = Number(n) || 0;
        if (Math.abs(val) >= 1000000) return 'Kz ' + (val / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (Math.abs(val) >= 1000) return 'Kz ' + (val / 1000).toFixed(0) + 'K';
        return 'Kz ' + val.toLocaleString('pt-AO');
    }

    function setKpi(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    async function safeFetchJson(url) {
        try {
            const res = await apiFetch(url);
            if (!res.ok) return null;
            return await res.json();
        } catch (e) {
            return null;
        }
    }

    function trendIconSvg(up) {
        return up
            ? '<svg viewBox="0 0 16 16"><path d="M2 12 L6 8 L9 10 L14 4 M14 4 L14 8 M14 4 L10 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>'
            : '<svg viewBox="0 0 16 16"><path d="M2 4 L6 8 L9 6 L14 12 M14 12 L14 8 M14 12 L10 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
    }

    /** Mostra a variação face ao período anterior. Esconde a badge se não houver
     * baseline (ambos os valores a zero) — nunca inventa uma percentagem. */
    function renderTrend(el, current, previous) {
        if (!el) return;
        if (!previous && !current) { el.hidden = true; return; }
        el.hidden = false;
        el.classList.remove('up', 'down', 'neutral');
        if (!previous) {
            el.classList.add('up');
            el.innerHTML = `${trendIconSvg(true)}Novo`;
            return;
        }
        const pct = ((current - previous) / previous) * 100;
        const up = pct >= 0;
        el.classList.add(up ? 'up' : 'down');
        el.innerHTML = `${trendIconSvg(up)}${up ? '+' : '−'}${Math.abs(pct).toFixed(1)}%`;
    }

    function sameMonth(dateStr, year, month) {
        if (!dateStr) return false;
        const d = new Date(dateStr);
        return !isNaN(d) && d.getFullYear() === year && d.getMonth() === month;
    }

    function last6Months() {
        const now = new Date();
        const months = [];
        for (let i = 5; i >= 0; i--) {
            const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
            months.push({ year: d.getFullYear(), month: d.getMonth() });
        }
        return months;
    }

    function isoDate(year, month, day) {
        return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    }

    /* ---------- Saudação e data reais ---------- */
    async function renderGreeting() {
        const now = new Date();
        const hour = now.getHours();
        const period = hour < 12 ? 'Bom dia' : hour < 19 ? 'Boa tarde' : 'Boa noite';
        const dateLabel = `${WEEKDAY_FULL[now.getDay()]}, ${now.getDate()} de ${MONTH_FULL[now.getMonth()]}`;

        const subEl = document.getElementById('homeDateSub');
        if (subEl) subEl.textContent = `${dateLabel} · Eis a sua operação hoje.`;

        const me = await safeFetchJson(`${API_BASE.AUTH}/me`);
        const firstName = (me?.nome || '').trim().split(/\s+/)[0] || '';
        const greetEl = document.getElementById('homeGreeting');
        if (greetEl) {
            greetEl.innerHTML = `${period}${firstName ? ', ' + escapeHtml(firstName) : ''} <span class="wave" aria-hidden="true">👋</span>`;
        }
    }

    /* ---------- Gráfico de receita (recibos reais agregados por mês) ---------- */
    function renderChart(totals, months) {
        const xs = [40, 168, 296, 424, 552, 680];
        const yTop = 30, yBottom = 210, baseline = 230;
        const max = Math.max(...totals, 0);
        const denom = max || 1;
        const ys = totals.map(v => yBottom - (v / denom) * (yBottom - yTop));

        const linePath = 'M ' + xs.map((x, i) => `${x} ${ys[i].toFixed(1)}`).join(' L ');
        const areaPath = `${linePath} L ${xs[5]} ${baseline} L ${xs[0]} ${baseline} Z`;

        document.getElementById('homeChartLine')?.setAttribute('d', linePath);
        document.getElementById('homeChartArea')?.setAttribute('d', areaPath);

        const dotsHost = document.getElementById('homeChartDots');
        if (dotsHost) {
            dotsHost.innerHTML = xs.map((x, i) => `
                <circle class="chart-dot" cx="${x}" cy="${ys[i].toFixed(1)}" r="${i === xs.length - 1 ? 5.5 : 4.5}">
                    <title>${escapeHtml(MONTH_ABBR[months[i].month])} ${months[i].year} · ${fmtCompactKz(totals[i])}</title>
                </circle>
            `).join('');
        }

        const labelsHost = document.getElementById('homeChartXLabels');
        if (labelsHost) {
            labelsHost.innerHTML = months.map(mo => `<span>${MONTH_ABBR[mo.month]}</span>`).join('');
        }
    }

    /* ---------- Init ---------- */
    async function init() {
        if (!document.querySelector('[data-view="home"]')) return;

        renderGreeting();

        const months = last6Months();
        const dateFrom = isoDate(months[0].year, months[0].month, 1);
        const now = new Date();
        const dateTo = isoDate(now.getFullYear(), now.getMonth(), now.getDate());

        const [summary, contacts, tasks, receipts] = await Promise.all([
            safeFetchJson(`${API_BASE.FIN}/summary`),
            safeFetchJson(`${API_BASE.CRM}/contacts`),
            safeFetchJson(`${API_BASE.PROJECTS}/tasks`),
            safeFetchJson(`${API_BASE.FIN}/receipts?date_from=${dateFrom}&date_to=${dateTo}`),
        ]);

        /* Vendas (mês) + tendência vs mês anterior, a partir dos recibos reais */
        if (Array.isArray(receipts)) {
            const totals = months.map(() => 0);
            receipts.forEach(r => {
                const d = new Date(r.payment_date);
                if (isNaN(d)) return;
                const idx = months.findIndex(mo => mo.year === d.getFullYear() && mo.month === d.getMonth());
                if (idx >= 0) totals[idx] += Number(r.amount) || 0;
            });
            renderChart(totals, months);
            renderTrend(document.getElementById('homeTrendVendas'), totals[5], totals[4]);
        } else {
            document.getElementById('homeTrendVendas')?.setAttribute('hidden', '');
        }

        if (summary) {
            setKpi('homeKpiVendas', fmtCompactKz(summary.recebido_mes));
            setKpi('homeKpiDespesas', String(summary.despesas_pendentes ?? 0));
        } else {
            setKpi('homeKpiVendas', 'Kz —');
            setKpi('homeKpiDespesas', '—');
        }

        /* Leads novos (estado "novo") + tendência de criação face ao mês anterior */
        if (Array.isArray(contacts)) {
            const novos = contacts.filter(c => c.stage === 'novo').length;
            setKpi('homeKpiLeads', String(novos));

            const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
            const thisMonthCount = contacts.filter(c => sameMonth(c.created_at, now.getFullYear(), now.getMonth())).length;
            const prevMonthCount = contacts.filter(c => sameMonth(c.created_at, prev.getFullYear(), prev.getMonth())).length;
            renderTrend(document.getElementById('homeTrendLeads'), thisMonthCount, prevMonthCount);
        } else {
            setKpi('homeKpiLeads', '—');
            document.getElementById('homeTrendLeads')?.setAttribute('hidden', '');
        }

        /* Tarefas ainda não concluídas em todos os projectos */
        if (Array.isArray(tasks)) {
            const pendentes = tasks.filter(t => t.status !== 'done').length;
            setKpi('homeKpiTarefas', String(pendentes));
        } else {
            setKpi('homeKpiTarefas', '—');
        }
    }

    return { init };
})();

document.addEventListener('DOMContentLoaded', HomeDashboard.init);


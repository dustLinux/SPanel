/* ═══════════════════════════════════════════════
   Spanel — JS
   ═══════════════════════════════════════════════ */

Chart.defaults.color = '#64748b';
Chart.defaults.font.family = 'Inter';

let cpuChart, memChart, historyChart;
let currentServerUrl = '';

// ── Sidebar (mobile) ──
function toggleSidebar() { document.getElementById('sidebar').classList.toggle('open'); }

// ── Views ──
function switchView(id, el) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.nav-link').forEach(n => n.classList.remove('active'));
    document.getElementById('view-' + id).classList.add('active');
    if (el) el.classList.add('active');
    document.getElementById('sidebar').classList.remove('open');
    if (id === 'terminal' && window.TM) window.TM.refitActive();
}

// ── Auth ──
async function loadUser() {
    try {
        const res = await fetch('/api/auth/me');
        if (!res.ok) { window.location.href = '/login'; return; }
        const u = await res.json();
        document.getElementById('user-name').textContent = u.username;
    } catch(e) { console.error(e); }
}

async function doLogout() {
    await fetch('/api/auth/logout', { method:'POST' });
    window.location.href = '/login';
}

// ── Host rename ──
function toggleRename() {
    const box = document.getElementById('rename-box');
    box.classList.toggle('hide');
    if (!box.classList.contains('hide')) {
        const inp = document.getElementById('rename-input');
        inp.value = document.getElementById('display-hostname').textContent;
        inp.focus();
    }
}

async function saveRename() {
    const name = document.getElementById('rename-input').value.trim();
    if (!name) return;
    await fetch('/api/host/rename', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ new_name: name })
    });
    document.getElementById('display-hostname').textContent = name;
    document.getElementById('rename-box').classList.add('hide');
}

// ── Settings ──
async function loadSettings() {
    try {
        const d = await (await fetch('/api/settings')).json();

        // Panel name
        const pn = d.panel_name || 'Spanel';
        document.getElementById('panel-name').textContent = pn;
        document.getElementById('set-panel-name').value = pn;
        document.title = pn;

        // Theme
        applyTheme(d.theme || 'dark');
        
        // Terminal Theme
        applyTermTheme(d.terminal_theme || 'tokyo_night');

        // Floating
        const fl = !!d.floating_enabled;
        document.getElementById('set-floating').checked = fl;
        document.getElementById('float-label').textContent = fl ? 'Enabled' : 'Disabled';
        applyFloating(fl);
    } catch(e) { console.error(e); }
}

async function savePanelName() {
    const name = document.getElementById('set-panel-name').value.trim();
    if (!name) return;
    await fetch('/api/settings', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ panel_name: name })
    });
    document.getElementById('panel-name').textContent = name;
    document.title = name;
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.querySelectorAll('.theme-options:not(#term-theme-options) .theme-swatch').forEach(s => {
        s.classList.toggle('active', s.dataset.theme === theme);
    });
}

async function setTheme(theme) {
    applyTheme(theme);
    await fetch('/api/settings', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ theme })
    });
    reinitCharts();
}

function applyTermTheme(theme) {
    document.documentElement.setAttribute('data-term-theme', theme);
    document.querySelectorAll('#term-theme-options .theme-swatch').forEach(s => {
        s.classList.toggle('active', s.dataset.term === theme);
    });
    // Dynamically update terminal if exists
    if (window.TM) window.TM.updateThemeFromCSS();
}

async function setTermTheme(theme) {
    applyTermTheme(theme);
    await fetch('/api/settings', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ terminal_theme: theme })
    });
}

async function toggleFloating(on) {
    document.getElementById('float-label').textContent = on ? 'Enabled' : 'Disabled';
    applyFloating(on);
    await fetch('/api/settings', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ floating_enabled: on })
    });
}

async function changePW() {
    const old = document.getElementById('set-old-pw').value;
    const nw = document.getElementById('set-new-pw').value;
    const msg = document.getElementById('pw-msg');
    if (!old || !nw) { msg.textContent = 'Fill both fields'; msg.className = 'pw-msg error'; return; }
    const res = await fetch('/api/auth/password', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ current_password: old, new_password: nw })
    });
    if (res.ok) {
        msg.textContent = 'Password changed!'; msg.className = 'pw-msg';
        document.getElementById('set-old-pw').value = '';
        document.getElementById('set-new-pw').value = '';
    } else {
        const d = await res.json();
        msg.textContent = d.detail || 'Error'; msg.className = 'pw-msg error';
    }
}

// ── Floating Windows ──
function applyFloating(on) {
    const container = document.getElementById('cards-container');
    if (!container) return;
    if (on) {
        container.classList.add('floating');
        initDraggable();
    } else {
        container.classList.remove('floating');
        // Reset inline styles
        container.querySelectorAll('.card').forEach(c => {
            c.style.cssText = '';
        });
    }
}

function initDraggable() {
    const container = document.getElementById('cards-container');
    const cards = container.querySelectorAll('.card');

    // Restore saved positions or auto-position
    const saved = JSON.parse(localStorage.getItem('spanel_float_pos') || '{}');
    let offsetY = 10;

    cards.forEach(card => {
        const cid = card.dataset.cardId;

        // Add drag handle if not present
        if (!card.querySelector('.drag-handle')) {
            const h = document.createElement('i');
            h.className = 'fa-solid fa-grip drag-handle';
            card.appendChild(h);
        }

        if (saved[cid]) {
            card.style.left = saved[cid].left;
            card.style.top = saved[cid].top;
            if (saved[cid].width) card.style.width = saved[cid].width;
            if (saved[cid].height) card.style.height = saved[cid].height;
        } else {
            card.style.left = '10px';
            card.style.top = offsetY + 'px';
            card.style.width = cid === 'history' ? '95%' : '45%';
            offsetY += 320;
        }

        // Drag logic
        makeDraggable(card);
    });

    // Save on resize
    new MutationObserver(() => saveFloatPos()).observe(container, { attributes:true, subtree:true, attributeFilter:['style'] });
}

function makeDraggable(el) {
    let startX, startY, origX, origY;
    const onDown = (e) => {
        // Only start drag from card-head or drag-handle
        const t = e.target.closest('.card-head, .drag-handle');
        if (!t) return;
        e.preventDefault();
        const ev = e.touches ? e.touches[0] : e;
        startX = ev.clientX; startY = ev.clientY;
        origX = el.offsetLeft; origY = el.offsetTop;
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', onUp);
    };
    const onMove = (e) => {
        const ev = e.touches ? e.touches[0] : e;
        el.style.left = (origX + ev.clientX - startX) + 'px';
        el.style.top = (origY + ev.clientY - startY) + 'px';
    };
    const onUp = () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        saveFloatPos();
    };
    el.addEventListener('pointerdown', onDown);
}

function saveFloatPos() {
    const container = document.getElementById('cards-container');
    if (!container || !container.classList.contains('floating')) return;
    const pos = {};
    container.querySelectorAll('.card').forEach(c => {
        pos[c.dataset.cardId] = {
            left: c.style.left, top: c.style.top,
            width: c.style.width, height: c.style.height
        };
    });
    localStorage.setItem('spanel_float_pos', JSON.stringify(pos));
}

// ── Multi-Server ──
function toggleAddServer() { document.getElementById('add-server-box').classList.toggle('hide'); }

async function loadServers() {
    const res = await fetch('/api/servers');
    const servers = await res.json();
    const ul = document.getElementById('server-list');
    const tSel = document.getElementById('term-host-select');
    ul.innerHTML = '';
    tSel.innerHTML = '';

    servers.forEach(s => {
        // Populate term host select
        const opt = document.createElement('option');
        opt.value = s.is_self ? '' : s.url;
        opt.textContent = s.name + (s.is_self ? ' (Local)' : '');
        tSel.appendChild(opt);

        const li = document.createElement('li');
        li.className = (s.is_self && !currentServerUrl) || (s.url === currentServerUrl) ? 'active' : '';
        li.innerHTML = `<span>${s.name} ${s.is_self ? '<i class="fa-solid fa-house" style="font-size:.55rem;opacity:.4"></i>' : ''}</span>`;
        if (!s.is_self) {
            const rm = document.createElement('span');
            rm.className = 'srv-remove';
            rm.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
            rm.onclick = async (e) => {
                e.stopPropagation();
                await fetch('/api/servers/remove', {
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({ server_id: s.id })
                });
                if (currentServerUrl === s.url) {
                    currentServerUrl = '';
                    fetchRealtime(); fetchHistorical();
                }
                loadServers();
            };
            li.appendChild(rm);
        }
        li.addEventListener('click', () => {
            const nurl = s.is_self ? '' : s.url;
            if (currentServerUrl === nurl) return;
            currentServerUrl = nurl;
            loadServers();
            fetchRealtime(); fetchHistorical();
            if (window.TM) {
                // Do not auto-recreate terminal context on simply viewing the panel!
                // Terminal sessions are now bound to the host they were created on.
            }
        });
        ul.appendChild(li);
    });
}

async function addServer() {
    const name = document.getElementById('srv-name').value.trim();
    const url = document.getElementById('srv-url').value.trim();
    if (!name || !url) return;
    await fetch('/api/servers/add', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ name, url })
    });
    document.getElementById('srv-name').value = '';
    document.getElementById('srv-url').value = '';
    document.getElementById('add-server-box').classList.add('hide');
    loadServers();
}

function apiFetch(path) { return fetch((currentServerUrl || '') + path); }

// ── Charts ──
function getThemeColors() {
    const s = getComputedStyle(document.documentElement);
    return {
        accent: s.getPropertyValue('--accent').trim() || '#6366f1',
        accent2: s.getPropertyValue('--accent2').trim() || '#818cf8',
        red: s.getPropertyValue('--red').trim() || '#f43f5e',
        text3: s.getPropertyValue('--text3').trim() || '#475569',
    };
}

function gradient(ctx, c1, c2) {
    const g = ctx.createLinearGradient(0,0,0,300);
    g.addColorStop(0, c1); g.addColorStop(1, c2);
    return g;
}

function initCharts() {
    const tc = getThemeColors();
    const dOpts = {
        responsive:true, maintainAspectRatio:false,
        plugins:{ legend:{display:false}, tooltip:{enabled:false} },
        cutout:'78%',
    };
    cpuChart = new Chart(document.getElementById('cpuChart'), {
        type:'doughnut',
        data:{ labels:['Used','Free'], datasets:[{
            data:[0,100],
            backgroundColor:[ gradient(document.getElementById('cpuChart').getContext('2d'), tc.red, '#fb923c'), 'rgba(128,128,128,.08)' ],
            borderWidth:0, borderRadius:6
        }]},
        options:dOpts
    });
    memChart = new Chart(document.getElementById('memChart'), {
        type:'doughnut',
        data:{ labels:['Used','Free'], datasets:[{
            data:[0,100],
            backgroundColor:[ gradient(document.getElementById('memChart').getContext('2d'), tc.accent, tc.accent2), 'rgba(128,128,128,.08)' ],
            borderWidth:0, borderRadius:6
        }]},
        options:dOpts
    });
    historyChart = new Chart(document.getElementById('historyChart'), {
        type:'line',
        data:{ labels:[], datasets:[
            { label:'CPU %', data:[], borderColor:tc.red, backgroundColor:tc.red + '14', borderWidth:2, tension:.4, fill:true, pointRadius:0 },
            { label:'RAM %', data:[], borderColor:tc.accent, backgroundColor:tc.accent + '14', borderWidth:2, tension:.4, fill:true, pointRadius:0 },
        ]},
        options:{
            responsive:true, maintainAspectRatio:false,
            interaction:{ mode:'index', intersect:false },
            plugins:{ legend:{ position:'top', labels:{ usePointStyle:true, boxWidth:6 } } },
            scales:{
                x:{ grid:{color:'rgba(128,128,128,.06)'}, ticks:{ maxTicksLimit:8, callback(v){ const d=new Date(this.getLabelForValue(v)); return d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}); }}},
                y:{ min:0, max:100, grid:{color:'rgba(128,128,128,.08)'} }
            }
        }
    });
}

function reinitCharts() {
    if (cpuChart) cpuChart.destroy();
    if (memChart) memChart.destroy();
    if (historyChart) historyChart.destroy();
    initCharts();
    fetchRealtime(); fetchHistorical();
}

// ── Data Fetching ──
async function fetchRealtime() {
    try {
        const d = await (await apiFetch('/api/realtime')).json();
        document.getElementById('display-hostname').textContent = d.display_hostname;
        document.getElementById('host-os').textContent = 'OS: ' + d.platform;
        document.getElementById('host-physical').textContent = 'Physical: ' + d.physical_hostname;

        const tag = document.getElementById('host-tag');
        if (tag) {
            if (currentServerUrl) {
                tag.innerHTML = '<i class="fa-solid fa-globe"></i> Remote';
                tag.style.color = 'var(--accent2)';
            } else {
                tag.innerHTML = '<i class="fa-solid fa-circle dot-pulse"></i> This Machine';
                tag.style.color = 'var(--green)';
            }
        }

        cpuChart.data.datasets[0].data = [d.cpu.percent, 100 - d.cpu.percent];
        cpuChart.update();
        document.getElementById('cpu-pct').textContent = d.cpu.percent.toFixed(1) + ' %';
        document.getElementById('cpu-cores').innerHTML = `<i class="fa-solid fa-layer-group"></i> ${d.cpu.cores} cores`;
        document.getElementById('cpu-freq').innerHTML = `<i class="fa-solid fa-bolt"></i> ${d.cpu.freq}`;

        memChart.data.datasets[0].data = [d.memory.percent, 100 - d.memory.percent];
        memChart.update();
        document.getElementById('mem-pct').textContent = d.memory.percent.toFixed(1) + ' %';
        const gu = (d.memory.used / 1073741824).toFixed(2);
        const gt = (d.memory.total / 1073741824).toFixed(2);
        document.getElementById('mem-used').innerHTML = `<i class="fa-solid fa-arrow-up"></i> ${gu} GB`;
        document.getElementById('mem-total').innerHTML = `<i class="fa-solid fa-hard-drive"></i> ${gt} GB`;
    } catch(e) { console.error(e); }
}

async function fetchHistorical() {
    try {
        const data = await (await apiFetch('/api/historical')).json();
        if (!data.length) return;
        historyChart.data.labels = data.map(d => d.timestamp);
        historyChart.data.datasets[0].data = data.map(d => d.cpu.percent);
        historyChart.data.datasets[1].data = data.map(d => d.memory.percent);
        historyChart.update('none');
    } catch(e) { console.error(e); }
}

// ══════════════════════════════════════════════
// Terminal Manager
// ══════════════════════════════════════════════
class TerminalManager {
    constructor() {
        this.tabsEl = document.getElementById('term-tabs');
        this.bodyEl = document.getElementById('term-body');
        this.sessions = {};
        this.activeId = null;
        this.n = 0;
        this.ctrlOn = false;
        this.altOn = false;
        this._addPlusBtn();
        this._initOSK();
        this.create();
    }

    updateThemeFromCSS() {
        const s = getComputedStyle(document.documentElement);
        const termBg = s.getPropertyValue('--term-bg').trim() || '#0d1117';
        const termFg = s.getPropertyValue('--term-fg').trim() || '#e6edf3';
        const cursor = s.getPropertyValue('--term-cursor').trim() || s.getPropertyValue('--accent').trim() || '#58a6ff';
        const selBg = s.getPropertyValue('--term-sel').trim() || 'rgba(88,166,255,0.3)';

        Object.values(this.sessions).forEach(ses => {
            ses.term.options.theme = {
                background: termBg, foreground: termFg,
                cursor: cursor, selectionBackground: selBg
            };
        });
    }

    _addPlusBtn() {
        const btn = document.createElement('div');
        btn.className = 't-tab t-tab-add';
        btn.innerHTML = '<i class="fa-solid fa-plus"></i>';
        btn.onclick = () => this.create();
        this.tabsEl.appendChild(btn);
    }

    create() {
        const id = 'ses_' + Math.random().toString(36).slice(2,10);
        this.n++;
        const tab = document.createElement('div');
        tab.className = 't-tab';
        tab.innerHTML = `<i class="fa-solid fa-terminal" style="font-size:.6rem"></i> Term ${this.n} <span class="t-tab-close"><i class="fa-solid fa-xmark"></i></span>`;
        tab.addEventListener('click', () => this.activate(id));
        tab.querySelector('.t-tab-close').addEventListener('click', e => { e.stopPropagation(); this.close(id); });
        this.tabsEl.insertBefore(tab, this.tabsEl.lastChild);

        const pane = document.createElement('div');
        pane.className = 'term-pane';
        pane.id = 'pane_' + id;
        this.bodyEl.appendChild(pane);

        const s = getComputedStyle(document.documentElement);
        const termBg = s.getPropertyValue('--term-bg').trim() || '#0d1117';
        const termFg = s.getPropertyValue('--term-fg').trim() || '#e6edf3';
        const cursor = s.getPropertyValue('--term-cursor').trim() || s.getPropertyValue('--accent').trim() || '#58a6ff';
        const selBg = s.getPropertyValue('--term-sel').trim() || 'rgba(88,166,255,0.3)';

        const isMobile = window.innerWidth <= 768;
        const term = new Terminal({
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: isMobile ? 10 : 13, cursorBlink: true, convertEol: true,
            theme: { background: termBg, foreground: termFg, cursor: cursor, selectionBackground: selBg },
        });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);

        // Resolve Host Selection
        const hSel = document.getElementById('term-host-select');
        let targetHost = hSel ? hSel.value : currentServerUrl;

        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        let wsUrl = `${proto}//${location.host}/ws/terminal/${id}`;
        if (targetHost) wsUrl = targetHost.replace(/^http/, 'ws') + `/ws/terminal/${id}`;

        const connectWS = () => {
            const ws = new WebSocket(wsUrl);
            ws.onmessage = ev => term.write(ev.data);
            ws.onopen = () => {
                term.write(`\x1b[38;5;75m● Shell connected\x1b[0m\r\n`);
                if (this.sessions[id]) {
                    const dims = this.sessions[id].fitAddon.proposeDimensions();
                    if (dims) ws.send(`\x1b[8;${dims.rows};${dims.cols}t`);
                }
            };
            ws.onclose = () => {
                term.write(`\x1b[38;5;203m\r\n● Disconnected. Reconnecting...\x1b[0m\r\n`);
                if (this.sessions[id]) {
                    setTimeout(() => {
                        if (this.sessions[id]) {
                            this.sessions[id].ws = connectWS();
                        }
                    }, 3000);
                }
            };
            term.onData(data => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
            return ws;
        };

        const ws = connectWS();

        this.sessions[id] = { tab, pane, term, ws, fitAddon, opened: false };
        this.activate(id);
    }

    activate(id) {
        Object.values(this.sessions).forEach(s => {
            s.tab.classList.remove('active');
            s.pane.classList.remove('active');
        });
        const s = this.sessions[id];
        if (!s) return;
        s.tab.classList.add('active');
        s.pane.classList.add('active');
        this.activeId = id;
        requestAnimationFrame(() => {
            if (!s.opened) { s.term.open(s.pane); s.opened = true; }
            try { s.fitAddon.fit(); } catch(e){}
            s.term.focus();
            const dims = s.fitAddon.proposeDimensions();
            if (dims && s.ws.readyState === WebSocket.OPEN) {
                s.ws.send(`\x1b[8;${dims.rows};${dims.cols}t`);
            }
        });
    }

    refitActive() {
        if (!this.activeId) return;
        const s = this.sessions[this.activeId];
        if (!s || !s.opened) return;
        requestAnimationFrame(() => { try { s.fitAddon.fit(); } catch(e){} s.term.focus(); });
    }

    close(id) {
        const s = this.sessions[id];
        if (!s) return;
        s.ws.close(); s.term.dispose(); s.tab.remove(); s.pane.remove();
        delete this.sessions[id];
        if (this.activeId === id) {
            const keys = Object.keys(this.sessions);
            this.activeId = null;
            if (keys.length) this.activate(keys[keys.length - 1]);
        }
    }

    sendToActive(data) {
        if (!this.activeId) return;
        const s = this.sessions[this.activeId];
        if (s && s.ws.readyState === WebSocket.OPEN) { s.ws.send(data); s.term.focus(); }
    }

    _initOSK() {
        const oskEl = document.getElementById('osk');
        if (!oskEl) return;

        const ANSI = {
            'ArrowUp':'\x1b[A','ArrowDown':'\x1b[B','ArrowRight':'\x1b[C','ArrowLeft':'\x1b[D',
            'Home':'\x1b[H','End':'\x1b[F','PageUp':'\x1b[5~','PageDown':'\x1b[6~',
            'Tab':'\t','Escape':'\x1b',
        };
        // F-key ANSI sequences
        const FKEYS = {
            '1':'\x1bOP','2':'\x1bOQ','3':'\x1bOR','4':'\x1bOS',
            '5':'\x1b[15~','6':'\x1b[17~','7':'\x1b[18~','8':'\x1b[19~',
            '9':'\x1b[20~','10':'\x1b[21~','11':'\x1b[23~','12':'\x1b[24~',
        };

        oskEl.querySelectorAll('.osk-btn').forEach(btn => {
            btn.addEventListener('touchstart', e => e.preventDefault(), {passive:false});
            btn.addEventListener('click', () => {
                if (btn.id === 'osk-ctrl') { this.ctrlOn = !this.ctrlOn; btn.classList.toggle('osk-active', this.ctrlOn); return; }
                if (btn.id === 'osk-alt') { this.altOn = !this.altOn; btn.classList.toggle('osk-active', this.altOn); return; }

                // F-keys
                const fk = btn.getAttribute('data-fkey');
                if (fk && FKEYS[fk]) {
                    this.sendToActive(FKEYS[fk]);
                    this._resetMods();
                    return;
                }

                const ds = btn.getAttribute('data-send');
                if (ds) { this.sendToActive(ds); this._resetMods(); return; }

                const key = btn.getAttribute('data-key');
                if (!key) return;
                let seq = ANSI[key] || key;
                if (this.ctrlOn && key.length === 1) seq = String.fromCharCode(key.toUpperCase().charCodeAt(0) - 64);
                if (this.altOn) seq = '\x1b' + seq;
                this.sendToActive(seq);
                this._resetMods();
            });
        });

        // Make OSK draggable
        const handle = oskEl.querySelector('.osk-drag-handle');
        if (handle) {
            let sx, sy, ox, oy;
            handle.addEventListener('pointerdown', e => {
                e.preventDefault();
                sx = e.clientX; sy = e.clientY;
                ox = oskEl.offsetLeft; oy = oskEl.offsetTop;
                const onMove = ev => {
                    oskEl.style.left = (ox + ev.clientX - sx) + 'px';
                    oskEl.style.top = (oy + ev.clientY - sy) + 'px';
                    oskEl.style.bottom = 'auto'; // release bottom anchoring
                    oskEl.style.transform = 'none'; // release horiz center anchoring
                };
                const onUp = () => {
                    document.removeEventListener('pointermove', onMove);
                    document.removeEventListener('pointerup', onUp);
                };
                document.addEventListener('pointermove', onMove);
                document.addEventListener('pointerup', onUp);
            });
        }
    }

    _resetMods() {
        this.ctrlOn = false; this.altOn = false;
        const c = document.getElementById('osk-ctrl');
        const a = document.getElementById('osk-alt');
        if (c) c.classList.remove('osk-active');
        if (a) a.classList.remove('osk-active');
    }
}

// ── Boot ──
document.addEventListener('DOMContentLoaded', () => {
    loadUser();
    loadSettings();
    initCharts();
    fetchRealtime();
    fetchHistorical();
    loadServers();
    setInterval(() => { fetchRealtime(); fetchHistorical(); }, 2000);

    window.TM = new TerminalManager();

    let rt;
    window.addEventListener('resize', () => {
        clearTimeout(rt);
        rt = setTimeout(() => { if (window.TM) window.TM.refitActive(); }, 120);
    });

    document.getElementById('rename-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') saveRename();
    });
    document.getElementById('set-panel-name').addEventListener('keydown', e => {
        if (e.key === 'Enter') savePanelName();
    });

    // Subviews initialization
    loadSysProcs();
    loadManagedProcs();
    loadFiles();
    loadContainers();
});

// ══════════════════════════════════════════════
// Processes & Files & Containers API Bindings
// ══════════════════════════════════════════════

async function loadSysProcs() {
    try {
        const d = await (await apiFetch('/api/processes')).json();
        const tb = document.querySelector('#sys-table tbody');
        tb.innerHTML = '';
        d.forEach(p => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${p.pid}</td><td>${p.user}</td><td>${p.name}</td>
                <td><span style="color:var(--text);font-weight:600">${p.cpu} %</span></td>
                <td>${p.mem} %</td><td style="font-size:.75rem;color:var(--text3)">${p.cmd}</td>
                <td><button class="settings-btn" style="padding:.2rem .5rem;background:var(--red)" onclick="killSysProc(${p.pid})"><i class="fa-solid fa-skull"></i> Kill</button></td>
            `;
            tb.appendChild(tr);
        });
    } catch(e) {}
}

async function killSysProc(pid) {
    if(!confirm("Kill process " + pid + "?")) return;
    await fetch((currentServerUrl||'') + '/api/processes/kill/' + pid, {method:'POST'});
    loadSysProcs();
}

async function loadManagedProcs() {
    try {
        const d = await (await apiFetch('/api/processes/managed')).json();
        const tb = document.querySelector('#mp-table tbody');
        tb.innerHTML = '';
        d.forEach(p => {
            const tr = document.createElement('tr');
            const st = p.alive ? '<span style="color:var(--green)">Running</span>' : '<span style="color:var(--red)">Stopped</span>';
            tr.innerHTML = `
                <td>${p.name}</td><td style="font-family:monospace;font-size:.7rem">${p.command}</td>
                <td>${p.pid || '—'}</td><td>${st}</td>
                <td style="display:flex;gap:.3rem">
                    <button class="settings-btn" style="padding:.2rem .5rem" onclick="signalProc('${p.id}','TERM')">Stop</button>
                    <button class="settings-btn" style="padding:.2rem .5rem;background:var(--red)" onclick="signalProc('${p.id}','KILL')">Force</button>
                </td>
            `;
            tb.appendChild(tr);
        });
    } catch(e) {}
}

async function startManagedProc() {
    const name = document.getElementById('mp-name').value.trim();
    const cmd = document.getElementById('mp-cmd').value.trim();
    if (!name || !cmd) return;
    await fetch((currentServerUrl||'') + '/api/processes/start', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ name, command:cmd })
    });
    document.getElementById('mp-name').value = '';
    document.getElementById('mp-cmd').value = '';
    loadManagedProcs();
}

async function signalProc(id, sig) {
    await fetch((currentServerUrl||'') + '/api/processes/signal', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ proc_id: id, sig })
    });
    setTimeout(loadManagedProcs, 500);
}

const _fmtBytes = (bytes) => {
    if (bytes === 0) return '0 B';
    const k = 1024; const dm = 2; const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
};

async function loadFiles() {
    try {
        const d = await (await apiFetch('/api/files')).json();
        const tb = document.querySelector('#files-table tbody');
        tb.innerHTML = '';
        d.forEach(f => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><i class="fa-regular fa-file" style="margin-right:.5rem;color:var(--text3)"></i> ${f.name}</td>
                <td style="color:var(--text3)">${_fmtBytes(f.size)}</td>
                <td style="color:var(--text3)">${new Date(f.mtime).toLocaleString()}</td>
                <td style="display:flex;gap:.3rem">
                    <a class="settings-btn" style="padding:.2rem .5rem;text-decoration:none" href="${(currentServerUrl||'')}/api/files/download/${f.name}" target="_blank"><i class="fa-solid fa-download"></i></a>
                    <button class="settings-btn" style="padding:.2rem .5rem;background:var(--red)" onclick="deleteFile('${f.name}')"><i class="fa-solid fa-trash"></i></button>
                </td>
            `;
            tb.appendChild(tr);
        });
    } catch(e) {}
}

async function uploadFile(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    await fetch((currentServerUrl||'') + '/api/files/upload', { method: 'POST', body: fd });
    loadFiles();
}

// Drag & Drop
const __dropzone = document.getElementById('file-dropzone');
if (__dropzone) {
    __dropzone.addEventListener('dragover', (e) => { e.preventDefault(); __dropzone.style.borderColor = 'var(--accent)'; });
    __dropzone.addEventListener('dragleave', () => { __dropzone.style.borderColor = 'var(--border)'; });
    __dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        __dropzone.style.borderColor = 'var(--border)';
        if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
    });
}

async function deleteFile(name) {
    if(!confirm("Delete file: " + name + "?")) return;
    await fetch((currentServerUrl||'') + '/api/files/delete', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ filename: name })
    });
    loadFiles();
}

async function loadContainers() {
    try {
        // Populate tarballs dropdown first
        try {
            const fRes = await fetch((currentServerUrl||'') + '/api/rootfs');
            const f = await fRes.json();
            const sel = document.getElementById('ct-new-tarball');
            if (sel) {
                sel.innerHTML = '<option value="">Select Rootfs Tarball from /rootfs</option>';
                f.forEach(file => {
                    if (file.name.endsWith('.tar.gz') || file.name.endsWith('.tar.xz')) {
                        const opt = document.createElement('option');
                        opt.value = file.name;
                        opt.textContent = file.name;
                        sel.appendChild(opt);
                    }
                });
            }
        } catch(e) {}

        const cRes = await fetch((currentServerUrl||'') + '/api/containers');
        const d = await cRes.json();
        const tb = document.querySelector('#ct-table tbody');
        if (!tb) return;
        tb.innerHTML = '';
        d.forEach(c => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${c.id}</td>
                <td style="font-weight:600">${c.name}</td>
                <td><span class="badge ${c.distro.includes('ubuntu') ? 'green' : ''}">${c.distro}</span></td>
                <td><i class="fa-solid fa-network-wired"></i> ${c.port || '--'}</td>
                <td><span class="status ${c.status === 'running' ? 'ok' : 'err'}"><i class="fa-solid fa-circle"></i> ${c.status}</span></td>
                <td><button class="settings-btn" style="padding:0.2rem 0.6rem;font-size:0.75rem" onclick="openContainerShell('${c.id}')"><i class="fa-solid fa-terminal"></i> Shell</button></td>
            `;
            tb.appendChild(tr);
        });
    } catch(e) { console.error(e); }
}

async function createContainer() {
    const name = document.getElementById('ct-new-name').value.trim();
    const tarball = document.getElementById('ct-new-tarball').value;
    const entrypoint = document.getElementById('ct-new-entrypoint').value.trim() || '/bin/sh';
    const cmd = document.getElementById('ct-new-cmd').value.trim();
    if (!name || !tarball) { alert('Name and Tarball required'); return; }
    
    try {
        const res = await fetch((currentServerUrl||'') + '/api/containers/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, tarball, entrypoint, cmd })
        });
        const data = await res.json();
        if (res.ok) {
            document.getElementById('ct-new-name').value = '';
            document.getElementById('ct-new-tarball').value = '';
            setTimeout(loadContainers, 1000);
        } else {
            alert(data.detail || 'Failed to create container');
        }
    } catch(e) {
        alert(e);
    }
}

// ══════════════════════════════════════════════
// Container Shell
// ══════════════════════════════════════════════
window.ctShellTerm = null;
window.ctShellFit = null;
window.ctShellWS = null;

function openContainerShell(cid) {
    const card = document.getElementById('ct-shell-card');
    const body = document.getElementById('ct-shell-body');
    card.hidden = false;
    body.innerHTML = '';

    // Close previous
    if (window.ctShellWS) { try { window.ctShellWS.close(); } catch(e){} }
    if (window.ctShellTerm) { try { window.ctShellTerm.dispose(); } catch(e){} }

    const s = getComputedStyle(document.documentElement);
    const termBg = s.getPropertyValue('--term-bg').trim() || '#0d1117';
    const termFg = s.getPropertyValue('--term-fg').trim() || '#e6edf3';
    const cursor = s.getPropertyValue('--term-cursor').trim() || s.getPropertyValue('--accent').trim() || '#58a6ff';
    const selBg = s.getPropertyValue('--term-sel').trim() || 'rgba(88,166,255,0.3)';
    
    const isMobile = window.innerWidth <= 768;
    const term = new Terminal({
        fontFamily: '"JetBrains Mono", monospace',
        fontSize: isMobile ? 10 : 13, cursorBlink: true, convertEol: true,
        theme: { background: termBg, foreground: termFg, cursor: cursor, selectionBackground: selBg },
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(body);
    fitAddon.fit();

    window.ctShellTerm = term;
    window.ctShellFit = fitAddon;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let wsUrl = `${proto}//${location.host}/ws/container/${cid}`;
    if (currentServerUrl) wsUrl = currentServerUrl.replace(/^http/, 'ws') + `/ws/container/${cid}`;

    const ws = new WebSocket(wsUrl);
    window.ctShellWS = ws;
    ws.onmessage = ev => term.write(ev.data);
    ws.onopen = () => {
        term.write(`\x1b[38;5;75m● Container shell connected (${cid}).\x1b[0m\r\n`);
        const dims = fitAddon.proposeDimensions();
        if (dims) ws.send(`\x1b[8;${dims.rows};${dims.cols}t`);
    };
    ws.onclose = () => term.write(`\x1b[38;5;203m\r\n● Connection closed.\x1b[0m\r\n`);
    term.onData(data => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
}

function closeContainerShell() {
    if (window.ctShellWS) { try { window.ctShellWS.close(); } catch(e){} window.ctShellWS = null; }
    if (window.ctShellTerm) { try { window.ctShellTerm.dispose(); } catch(e){} window.ctShellTerm = null; }
    const card = document.getElementById('ct-shell-card');
    if (card) card.hidden = true;
}

// ── Mobile Terminal Resize Fix ──
window.addEventListener('resize', () => {
    if (window.TM && window.TM.sessions) {
        Object.values(window.TM.sessions).forEach(ses => {
            if (ses.fitAddon) {
                try { ses.fitAddon.fit(); } catch(e){}
            }
        });
    }
    if (window.sandboxTerm && window.sandboxFit) {
        try { window.sandboxFit.fit(); } catch(e){}
    }
});

// ══════════════════════════════════════════════
// Sandbox Environment
// ══════════════════════════════════════════════
window.sandboxTerm = null;
window.sandboxFit = null;

async function launchSandbox() {
    const btn = document.getElementById('btn-launch-sandbox');
    if (btn) btn.disabled = true;

    try {
        // Spin up the sandbox container on the backend
        const res = await fetch((currentServerUrl||'') + '/api/sandbox/launch', { method: 'POST' });
        const data = await res.json();
        
        if (!res.ok) {
            alert(data.detail || 'Failed to launch Sandbox');
            if (btn) btn.disabled = false;
            return;
        }

        // Initialize Sandbox Terminal
        const body = document.getElementById('sandbox-term-body');
        body.innerHTML = '';
        
        const s = getComputedStyle(document.documentElement);
        const termBg = s.getPropertyValue('--term-bg').trim() || '#0d1117';
        const termFg = s.getPropertyValue('--term-fg').trim() || '#e6edf3';
        const cursor = s.getPropertyValue('--term-cursor').trim() || s.getPropertyValue('--accent').trim() || '#58a6ff';
        const selBg = s.getPropertyValue('--term-sel').trim() || 'rgba(88,166,255,0.3)';

        const isMobile = window.innerWidth <= 768;
        const term = new Terminal({
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: isMobile ? 10 : 13, cursorBlink: true, convertEol: true,
            theme: { background: termBg, foreground: termFg, cursor: cursor, selectionBackground: selBg },
        });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        
        term.open(body);
        fitAddon.fit();

        window.sandboxTerm = term;
        window.sandboxFit = fitAddon;

        // Connect WebSocket directly to /bin/sh in the sandbox
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        let wsUrl = `${proto}//${location.host}/ws/sandbox/${data.session_id}`;
        if (currentServerUrl) wsUrl = currentServerUrl.replace(/^http/, 'ws') + `/ws/sandbox/${data.session_id}`;

        const connectWS = () => {
            const ws = new WebSocket(wsUrl);
            ws.onmessage = ev => term.write(ev.data);
            ws.onopen = () => {
                term.write(`\x1b[38;5;75m● Sandbox Shell connected. Alpine Linux.\x1b[0m\r\n`);
                const dims = fitAddon.proposeDimensions();
                if (dims) ws.send(`\x1b[8;${dims.rows};${dims.cols}t`);
            };
            ws.onclose = () => {
                term.write(`\x1b[38;5;203m\r\n● Connection closed.\x1b[0m\r\n`);
                if (btn) btn.disabled = false;
            };
            term.onData(data => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });
            return ws;
        };
        connectWS();

    } catch(e) {
        alert("Sandbox Error: " + e);
        if (btn) btn.disabled = false;
    }
}

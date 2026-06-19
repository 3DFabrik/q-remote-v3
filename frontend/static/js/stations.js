/**
 * Q-Remote V3 — Stations Editor
 * Vanilla JS for channel memory management with full inline editing.
 */

// ─── State ────────────────────────────────────────────────────

let channels = [];
let filter = 'used';  // 'used' | 'all'
let selectedChannelNum = null;
let editingCell = null;

const table = document.getElementById('channels-table');
const tbody = document.getElementById('channels-body');

// ─── Editable field definitions ───────────────────────────────

const EDITABLE = {
    name:        { type: 'text',     maxLen: 16 },
    rxFreq:      { type: 'number',   min: 0, max: 999.99999, step: 0.00001 },
    txOffset:    { type: 'number',   min: 0, max: 999.99999, step: 0.00001 },
    offsetDir:   { type: 'select',   options: ['Off', '+', '-'] },
    rxCode:      { type: 'code',     codeTypeField: 'rxCodeType' },
    txCode:      { type: 'code',     codeTypeField: 'txCodeType' },
    modulation:  { type: 'select',   options: ['FM', 'AM', 'USB'] },
    bandwidth:   { type: 'select',   options: ['Wide', 'Narrow'] },
    power:       { type: 'select',   options: ['High', 'Mid', 'Low'] },
    step:        { type: 'select',   options: [
        '2.5kHz','5kHz','6.25kHz','10kHz','12.5kHz','25kHz','8.33kHz',
        '0.01kHz','0.05kHz','0.1kHz','0.25kHz','0.5kHz','1kHz',
        '1.25kHz','15kHz','30kHz','50kHz','100kHz','125kHz','250kHz','500kHz'
    ]},
    busyLock:    { type: 'toggle' },
    reverse:     { type: 'toggle' },
    pttId:       { type: 'select',   options: ['Off', 'BOT', 'EOT', 'Both'] },
    dtmf:        { type: 'toggle' },
    scramble:    { type: 'select',   options: [
        'Off','2600Hz','2700Hz','2800Hz','2900Hz','3000Hz','3100Hz',
        '3200Hz','3300Hz','3400Hz','3500Hz'
    ]},
    compander:   { type: 'select',   options: ['Off', 'TX', 'RX', 'Both'] },
    scanlist:    { type: 'select',   options: ['None', 'List 1', 'List 2', 'Both'] },
    // band and number are NOT editable (band is auto-calculated)
};

// ─── Column class → field mapping (in table order) ────────────

const COL_FIELD_MAP = [
    { cls: 'col-name',       field: 'name' },
    { cls: 'col-freq',       field: 'rxFreq' },
    { cls: 'col-offset',     field: 'txOffset' },
    { cls: 'col-dir',        field: 'offsetDir' },
    { cls: 'col-rxcode',     field: 'rxCode' },
    { cls: 'col-txcode',     field: 'txCode' },
    { cls: 'col-mod',        field: 'modulation' },
    { cls: 'col-bw',         field: 'bandwidth' },
    { cls: 'col-power',      field: 'power' },
    { cls: 'col-step',       field: 'step' },
    { cls: 'col-bool',       field: null },  // resolved per-cell via data-field
    { cls: 'col-pttid',      field: 'pttId' },
    { cls: 'col-scramble',   field: 'scramble' },
    { cls: 'col-compander',  field: 'compander' },
    { cls: 'col-scanlist',   field: 'scanlist' },
];

// ─── Init ─────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    checkRadioStatus();
    loadChannels();
    loadBackups();
    bindEvents();
});

async function checkRadioStatus() {
    const light = document.getElementById('status-light');
    const label = document.getElementById('radio-status');
    try {
        const res = await authFetch('/api/status');
        const data = await res.json();
        if (data.state === 'connected') {
            light.style.background = 'var(--green)';
            light.style.boxShadow = '0 0 8px var(--green)';
            label.textContent = 'CONNECTED';
        } else {
            light.style.background = 'var(--red)';
            light.style.boxShadow = '0 0 8px var(--red)';
            label.textContent = 'DISCONNECTED';
        }
    } catch {
        light.style.background = 'var(--red)';
        label.textContent = 'ERROR';
    }
}

// ─── Load Channels ────────────────────────────────────────────

async function loadChannels() {
    try {
        const res = await authFetch('/stations/api/stations');
        const data = await res.json();
        channels = data.channels || [];
        renderTable();
    } catch (e) {
        console.error('Failed to load channels:', e);
    }
}

// ─── Render Table ─────────────────────────────────────────────

function renderTable() {
    const filtered = filter === 'used'
        ? channels.filter(c => c.inUse)
        : channels;

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="19" class="empty-msg">No channels loaded. Click "Read from Radio" to load.</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(ch => `
        <tr data-ch="${ch.number}" class="${ch.inUse ? '' : 'unused'}">
            <td class="col-num">${ch.number}</td>
            <td class="col-name">${escHtml(ch.name || '\u2014')}</td>
            <td class="col-freq">${fmtFreq(ch.rxFreq)}</td>
            <td class="col-offset">${fmtFreq(ch.txOffset)}</td>
            <td class="col-dir">${escHtml(ch.offsetDir)}</td>
            <td class="col-rxcode">${fmtCode(ch.rxCode, ch.rxCodeType)}</td>
            <td class="col-txcode">${fmtCode(ch.txCode, ch.txCodeType)}</td>
            <td class="col-mod">${escHtml(ch.modulation)}</td>
            <td class="col-bw">${escHtml(ch.bandwidth)}</td>
            <td class="col-power">${escHtml(ch.power)}</td>
            <td class="col-step">${escHtml(ch.step || '\u2014')}</td>
            <td class="col-bool" data-field="busyLock">${fmtBool(ch.busyLock)}</td>
            <td class="col-bool" data-field="reverse">${fmtBool(ch.reverse)}</td>
            <td class="col-pttid">${escHtml(ch.pttId)}</td>
            <td class="col-bool" data-field="dtmf">${fmtBool(ch.dtmf)}</td>
            <td class="col-scramble">${escHtml(ch.scramble)}</td>
            <td class="col-compander">${escHtml(ch.compander)}</td>
            <td class="col-scanlist">${escHtml(ch.scanlist)}</td>
            <td class="col-band">${ch.band !== undefined ? ch.band : '\u2014'}</td>
        </tr>
    `).join('');
}

// ─── Formatters ───────────────────────────────────────────────

function fmtFreq(mhz) {
    if (!mhz || mhz === 0) return '\u2014';
    return mhz.toFixed(5);
}

function fmtCode(code, codeType) {
    if (codeType === 'None' || codeType === undefined) return '\u2014';
    if (codeType === 'CTCSS') {
        const tones = [
            67.0,69.3,71.9,74.4,77.0,79.7,82.5,85.4,88.5,91.5,
            94.8,97.4,100.0,103.5,107.2,110.9,114.8,118.8,123.0,127.3,
            131.8,136.5,141.3,146.2,151.4,156.7,159.8,162.2,165.5,167.9,
            171.3,173.8,177.3,179.9,183.5,186.2,189.9,192.8,196.6,199.5,
            203.5,206.5,210.7,218.1,225.7,229.1,233.6,241.8,250.3,254.1,
        ];
        return code < tones.length ? `${tones[code].toFixed(1)}` : `${code}`;
    }
    if (codeType === 'DCS' || codeType === 'ReverseDCS') {
        return `D${code.toString().padStart(3, '0')}`;
    }
    return `${code}`;
}

function fmtBool(val) {
    return val ? '\u2713' : '\u2014';
}

function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

// ─── Inline Editing ───────────────────────────────────────────

function startEdit(td, ch, field) {
    if (editingCell) return;
    const def = EDITABLE[field];
    if (!def) return;

    editingCell = { td, ch, field };
    td.classList.add('editing');

    let input;

    switch (def.type) {
        case 'select':
            input = createSelect(def.options, ch[field]);
            break;

        case 'toggle':
            // Toggle immediately — no input needed
            ch[field] = !ch[field];
            saveChannel(ch);
            editingCell = null;
            td.classList.remove('editing');
            renderTable();
            return;

        case 'code':
            // Show code type dropdown + code number input
            startCodeEdit(td, ch, field, def);
            return;

        case 'number':
            input = document.createElement('input');
            input.type = 'number';
            input.value = ch[field] || 0;
            if (def.min !== undefined) input.min = def.min;
            if (def.max !== undefined) input.max = def.max;
            if (def.step !== undefined) input.step = def.step;
            break;

        case 'text':
        default:
            input = document.createElement('input');
            input.type = 'text';
            input.value = ch[field] || '';
            if (def.maxLen) input.maxLength = def.maxLen;
            break;
    }

    td.textContent = '';
    td.appendChild(input);
    input.focus();
    if (input.select) input.select();

    const commit = () => finishEdit(td, ch, field, input, def);
    const cancel = () => {
        td.classList.remove('editing');
        renderTable();
        editingCell = null;
    };

    input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    input.addEventListener('blur', commit);
}

function startCodeEdit(td, ch, field, def) {
    const codeTypeField = def.codeTypeField;
    const currentType = ch[codeTypeField] || 'None';
    const currentCode = ch[field] || 0;

    const wrapper = document.createElement('span');
    wrapper.className = 'code-editor';

    const typeSelect = createSelect(['None', 'CTCSS', 'DCS', 'ReverseDCS'], currentType);
    const codeInput = document.createElement('input');
    codeInput.type = 'number';
    codeInput.value = currentCode;
    codeInput.min = 0;
    codeInput.style.width = '40px';

    // When type is None, disable code input
    codeInput.disabled = (currentType === 'None');

    typeSelect.addEventListener('change', () => {
        codeInput.disabled = (typeSelect.value === 'None');
        if (typeSelect.value === 'None') codeInput.value = 0;
    });

    wrapper.appendChild(typeSelect);
    wrapper.appendChild(codeInput);
    td.textContent = '';
    td.appendChild(wrapper);
    typeSelect.focus();

    const commit = () => {
        if (!editingCell) return;
        editingCell = null;
        td.classList.remove('editing');

        const newType = typeSelect.value;
        let newCode = parseInt(codeInput.value) || 0;

        // Validate code range
        if (newType === 'CTCSS') {
            newCode = Math.max(0, Math.min(49, newCode));
        } else if (newType === 'DCS' || newType === 'ReverseDCS') {
            newCode = Math.max(0, Math.min(511, newCode));
        } else {
            newCode = 0;
        }

        ch[codeTypeField] = newType;
        ch[field] = newCode;
        saveChannel(ch);
        renderTable();
    };

    const cancel = () => {
        td.classList.remove('editing');
        renderTable();
        editingCell = null;
    };

    typeSelect.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    codeInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });

    // Blur on the LAST input commits
    codeInput.addEventListener('blur', e => {
        // Only commit if focus left the wrapper entirely
        setTimeout(() => {
            if (editingCell && !wrapper.contains(document.activeElement)) {
                commit();
            }
        }, 100);
    });
    typeSelect.addEventListener('blur', e => {
        setTimeout(() => {
            if (editingCell && !wrapper.contains(document.activeElement)) {
                commit();
            }
        }, 100);
    });
}

function createSelect(options, current) {
    const sel = document.createElement('select');
    options.forEach(opt => {
        const o = document.createElement('option');
        o.value = opt;
        o.textContent = opt;
        if (opt === current) o.selected = true;
        sel.appendChild(o);
    });
    return sel;
}

async function finishEdit(td, ch, field, input, def) {
    if (!editingCell) return;
    editingCell = null;
    td.classList.remove('editing');

    let val = input.value;

    switch (def.type) {
        case 'number':
            val = parseFloat(val);
            if (isNaN(val)) {
                renderTable();
                return;
            }
            if (def.min !== undefined) val = Math.max(def.min, val);
            if (def.max !== undefined) val = Math.min(def.max, val);
            break;

        case 'text':
            val = val.trim();
            if (def.maxLen) val = val.substring(0, def.maxLen);
            // Only ASCII for radio names
            val = val.replace(/[^\x20-\x7E]/g, '');
            break;

        case 'select':
            // Value is already valid (from dropdown)
            break;
    }

    ch[field] = val;

    // Re-calculate inUse based on rxFreq
    if (field === 'rxFreq') {
        ch.inUse = val > 0;
    }

    saveChannel(ch);
    renderTable();
}

async function saveChannel(ch) {
    try {
        await authFetch('/stations/api/stations/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(ch),
        });
    } catch (e) {
        console.error('Failed to save channel:', e);
    }
}

// ─── Channel Row Operations ───────────────────────────────

async function batchSaveAll() {
    try {
        await authFetch('/stations/api/stations/update-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channels }),
        });
    } catch (e) {
        console.error('Batch save failed:', e);
    }
}

function reselectRow() {
    if (!selectedChannelNum) return;
    const row = tbody.querySelector(`tr[data-ch="${selectedChannelNum}"]`);
    if (row) {
        row.classList.add('selected');
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function moveChannel(direction) {
    if (!selectedChannelNum) { alert('Please select a channel first.'); return; }
    const targetNum = selectedChannelNum + direction;
    if (targetNum < 1 || targetNum > channels.length) return;

    const ch = channels[selectedChannelNum - 1];
    const target = channels[targetNum - 1];

    // Swap all fields except 'number'
    const tmp = { ...ch };
    Object.keys(ch).forEach(k => { if (k !== 'number') ch[k] = target[k]; });
    Object.keys(target).forEach(k => { if (k !== 'number') target[k] = tmp[k]; });

    selectedChannelNum = targetNum;
    batchSaveAll();
    renderTable();
    reselectRow();
}

function deleteChannelRow() {
    if (!selectedChannelNum) { alert('Please select a channel first.'); return; }
    const idx = selectedChannelNum - 1;

    // Shift channels down by 1 from idx
    for (let i = idx; i < channels.length - 1; i++) {
        const src = { ...channels[i + 1] };
        Object.keys(channels[i]).forEach(k => { if (k !== 'number') channels[i][k] = src[k]; });
    }
    // Clear last channel
    const last = channels[channels.length - 1];
    const empty = _emptyChannel(last.number);
    Object.keys(last).forEach(k => { if (k !== 'number') last[k] = empty[k]; });

    batchSaveAll();
    renderTable();
    reselectRow();
}

function insertChannelRow() {
    // If no channels loaded yet, initialize empty 200-channel list
    if (!channels || channels.length === 0) {
        channels = [];
        for (let i = 1; i <= 200; i++) channels.push(_emptyChannel(i));
    }
    const insertAt = selectedChannelNum ? selectedChannelNum - 1 : 0;

    // Shift channels up by 1 from insertAt
    for (let i = channels.length - 1; i > insertAt; i--) {
        const src = { ...channels[i - 1] };
        Object.keys(channels[i]).forEach(k => { if (k !== 'number') channels[i][k] = src[k]; });
    }
    // Clear the channel at insertAt (new empty row, inUse so it shows under 'Used' filter)
    const ch = channels[insertAt];
    const empty = _emptyChannel(ch.number);
    Object.keys(ch).forEach(k => { if (k !== 'number') ch[k] = empty[k]; });
    ch.inUse = true;

    selectedChannelNum = insertAt + 1;

    batchSaveAll();
    renderTable();
    reselectRow();
}

function _emptyChannel(num) {
    return { number: num, rxFreq: 0, inUse: false, name: '', txOffset: 0,
        offsetDir: 'Off', rxCode: 0, txCode: 0, rxCodeType: 'None',
        txCodeType: 'None', modulation: 'FM', bandwidth: 'Wide',
        power: 'High', step: '12.5kHz', busyLock: false, reverse: false,
        pttId: 'Off', dtmf: false, scramble: 'Off', compander: 'Off',
        scanlist: 'None', band: 15 };
}

// ─── Event Bindings ───────────────────────────────────────────

function bindEvents() {
    // Row operation buttons
    document.getElementById('btn-up').addEventListener('click', () => moveChannel(-1));
    document.getElementById('btn-down').addEventListener('click', () => moveChannel(1));
    document.getElementById('btn-del').addEventListener('click', deleteChannelRow);
    document.getElementById('btn-add').addEventListener('click', insertChannelRow);

    // Read from radio
    document.getElementById('btn-read').addEventListener('click', readFromRadio);

    // Write to radio
    document.getElementById('btn-write').addEventListener('click', writeToRadio);

    // Export CSV
    document.getElementById('btn-export').addEventListener('click', () => {
        window.location.href = '/stations/api/stations/export/csv';
    });

    // Import CSV
    document.getElementById('file-import').addEventListener('change', importCSV);

    // Filter toggle button
    const filterBtn = document.getElementById('btn-filter');
    if (filterBtn) {
        filterBtn.addEventListener('click', () => {
            if (filter === 'used') {
                filter = 'all';
                filterBtn.textContent = 'All';
                filterBtn.dataset.filter = 'all';
            } else {
                filter = 'used';
                filterBtn.textContent = 'Used';
                filterBtn.dataset.filter = 'used';
            }
            filterBtn.classList.toggle('active', filter === 'used');
            renderTable();
        });
    }

    // Table click — row select and inline edit
    tbody.addEventListener('click', e => {
        const td = e.target.closest('td');
        const tr = e.target.closest('tr');
        if (!td || !tr) return;

        const chNum = parseInt(tr.dataset.ch);
        const ch = channels.find(c => c.number === chNum);
        if (!ch) return;

        // Select row
        document.querySelectorAll('#channels-table tr.selected').forEach(r => r.classList.remove('selected'));
        tr.classList.add('selected');
        selectedChannelNum = chNum;

        // Skip editing for col-num and col-band
        if (td.classList.contains('col-num') || td.classList.contains('col-band')) return;

        // Check for data-field attribute (boolean columns)
        const dataField = td.getAttribute('data-field');
        if (dataField && EDITABLE[dataField]) {
            startEdit(td, ch, dataField);
            return;
        }

        // Check column class → field mapping
        for (const { cls, field } of COL_FIELD_MAP) {
            if (td.classList.contains(cls) && field && EDITABLE[field]) {
                startEdit(td, ch, field);
                return;
            }
        }
    });
}

// ─── Read from Radio ──────────────────────────────────────────

async function readFromRadio() {
    const btn = document.getElementById('btn-read');
    btn.disabled = true;
    btn.querySelector('.btn-label').textContent = '\u23F3 Reading...';

    showProgress('Reading EEPROM from radio...', 0, 0);

    try {
        const res = await authFetch('/stations/api/stations/read', { method: 'POST' });
        const data = await res.json();

        if (data.task_id) {
            await pollTask(data.task_id, '/stations/api/stations/read/', () => {
                return loadChannels();
            });
        }
    } catch (e) {
        console.error('Read error:', e);
        alert('Failed to read from radio: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.querySelector('.btn-label').textContent = '\u2B07 Read from Radio';
        hideProgress();
    }
}

// ─── Write to Radio ───────────────────────────────────────────

async function writeToRadio() {
    if (!channels.length) {
        alert('No channels loaded. Read from radio first.');
        return;
    }

    if (!confirm(`Write ${channels.filter(c => c.inUse).length} channels to radio? A backup will be created automatically.`)) {
        return;
    }

    const btn = document.getElementById('btn-write');
    btn.disabled = true;
    btn.querySelector('.btn-label').textContent = '\u23F3 Writing...';

    showProgress('Writing EEPROM to radio...', 0, 0);

    try {
        const res = await authFetch('/stations/api/stations/write', { method: 'POST' });
        const data = await res.json();

        if (data.task_id) {
            await pollTask(data.task_id, '/stations/api/stations/write/', () => {
                loadBackups();
            });
        }
    } catch (e) {
        console.error('Write error:', e);
        alert('Failed to write to radio: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.querySelector('.btn-label').textContent = '\u2B06 Write to Radio';
        hideProgress();
    }
}

// ─── Task Polling ─────────────────────────────────────────────

async function pollTask(taskId, baseUrl, onComplete) {
    while (true) {
        const res = await authFetch(`${baseUrl}${taskId}/status`);
        const task = await res.json();

        showProgress(
            task.status === 'running' ? 'Working...' : task.status,
            task.progress || 0,
            task.total || 0
        );

        if (task.status === 'completed') {
            if (onComplete) await onComplete();
            return task;
        }

        if (task.status === 'error') {
            throw new Error(task.error || 'Unknown error');
        }

        await new Promise(r => setTimeout(r, 500));
    }
}

// ─── Progress Bar ─────────────────────────────────────────────

function showProgress(text, progress, total) {
    const section = document.getElementById('progress-section');
    const bar = document.getElementById('progress-bar');
    const label = document.getElementById('progress-text');
    const count = document.getElementById('progress-count');

    section.style.display = 'block';
    label.textContent = text;
    count.textContent = total > 0 ? `${progress}/${total}` : '';

    const pct = total > 0 ? (progress / total * 100) : 0;
    bar.style.width = `${pct}%`;
}

function hideProgress() {
    document.getElementById('progress-section').style.display = 'none';
}

// ─── CSV Import ───────────────────────────────────────────────

async function importCSV(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await authFetch('/stations/api/stations/import/csv', {
            method: 'POST',
            body: formData,
        });
        const data = await res.json();

        if (data.errors && data.errors.length > 0) {
            alert(`Import completed with errors:\n${data.errors.slice(0, 10).join('\n')}`);
        }

        if (data.channels && data.channels.length > 0) {
            channels = data.channels;
            renderTable();
            alert(`Imported ${data.imported} channels.`);
        }
    } catch (err) {
        alert('Import failed: ' + err.message);
    }

    e.target.value = '';
}

// ─── Backups ──────────────────────────────────────────────────

async function loadBackups() {
    const container = document.getElementById('backups-list');
    try {
        const res = await authFetch('/stations/api/stations/backups');
        const data = await res.json();

        if (!data.backups || data.backups.length === 0) {
            container.innerHTML = '<span class="engraved">No backups yet</span>';
            return;
        }

        container.innerHTML = data.backups.slice(0, 20).map(b => {
            const time = b.timestamp ? b.timestamp.replace('T', ' ').slice(0, 19) : b.id;
            return `
                <div class="backup-item">
                    <span class="backup-time">${escHtml(time)}</span>
                    <button onclick="restoreBackup('${escHtml(b.id)}')">Load</button>
                    <a href="/stations/api/stations/backups/${escHtml(b.id)}" download>
                        <button>Download</button>
                    </a>
                    <button onclick="deleteBackup('${escHtml(b.id)}')">Delete</button>
                </div>
            `;
        }).join('');
    } catch {
        container.innerHTML = '<span class="engraved">Failed to load backups</span>';
    }
}

window.deleteBackup = async function(backupId) {
    if (!confirm('Delete this backup permanently?')) return;
    try {
        const res = await authFetch(`/stations/api/stations/backups/${backupId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Delete failed');
        loadBackups();
    } catch (e) {
        alert('Delete failed: ' + e.message);
    }
};

window.restoreBackup = async function(backupId) {
    if (!confirm('Load this backup into the editor?')) return;
    try {
        const res = await authFetch(`/stations/api/stations/restore/${backupId}`, { method: 'POST' });
        const data = await res.json();
        await loadChannels();
        alert(`Loaded ${data.restored} channels into editor.`);
    } catch (e) {
        alert('Restore failed: ' + e.message);
    }
};

// ─── Auth-aware fetch helper ────────────────────────────────────
async function authFetch(url, opts = {}) {
    const res = await fetch(url, opts);
    if (res.status === 401) {
        window.location.href = '/login';
        return null;
    }
    return res;
}

// ─── Session Timeout & Heartbeat ──────────────────────────────

(function setupSessionManagement() {
    const HEARTBEAT_INTERVAL = 60 * 1000;  // 1 minute

    setInterval(async () => {
        try {
            const resp = await fetch("/api/heartbeat", { method: "POST" });
            if (resp.status === 401) {
                window.location.href = "/login";
            }
        } catch (e) {
            console.warn("Heartbeat failed:", e);
        }
    }, HEARTBEAT_INTERVAL);

    // Tab close → logout (only on actual window close, not navigation)
    let _navigating = false;
    document.addEventListener("click", (e) => {
        const link = e.target.closest("a[href]");
        if (link) _navigating = true;
    });
    document.addEventListener("submit", () => { _navigating = true; });

    window.addEventListener("beforeunload", () => {
        if (!_navigating) {
            navigator.sendBeacon("/api/close");
        }
        _navigating = false;
    });
})();

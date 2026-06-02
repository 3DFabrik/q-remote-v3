/**
 * Q-Remote V3 — Stations Editor
 * Vanilla ES module for channel memory management.
 */

// ─── State ────────────────────────────────────────────────────

let channels = [];
let filter = 'used';  // 'used' | 'all'
let selectedRow = null;
let editingCell = null;

const table = document.getElementById('channels-table');
const tbody = document.getElementById('channels-body');

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
        const res = await fetch('/api/status');
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
        const res = await fetch('/stations/api/stations');
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

    document.getElementById('count-used').textContent = channels.filter(c => c.inUse).length;
    document.getElementById('count-total').textContent = channels.length;

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="11" class="empty-msg">No channels loaded. Click "Read from Radio" to load.</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(ch => `
        <tr data-ch="${ch.number}" class="${ch.inUse ? '' : 'unused'}">
            <td class="col-num">${ch.number}</td>
            <td class="col-name">${escHtml(ch.name || '\u2014')}</td>
            <td class="col-freq">${fmtFreq(ch.rxFreq)}</td>
            <td class="col-offset">${fmtFreq(ch.txOffset)}</td>
            <td class="col-dir">${escHtml(ch.offsetDir)}</td>
            <td class="col-code">${fmtCode(ch.rxCode, ch.rxCodeType)}</td>
            <td class="col-code">${fmtCode(ch.txCode, ch.txCodeType)}</td>
            <td class="col-mod">${escHtml(ch.modulation)}</td>
            <td class="col-bw">${escHtml(ch.bandwidth)}</td>
            <td class="col-power">${escHtml(ch.power)}</td>
            <td class="col-step">${escHtml(ch.step || '\u2014')}</td>
            <td class="col-busylock">${ch.busyLock ? 'On' : 'Off'}</td>
            <td class="col-reverse">${ch.reverse ? 'On' : 'Off'}</td>
            <td class="col-pttid">${escHtml(ch.pttId || 'Off')}</td>
            <td class="col-dtmf">${ch.dtmf ? 'On' : 'Off'}</td>
            <td class="col-scramble">${escHtml(ch.scramble || 'Off')}</td>
            <td class="col-compander">${escHtml(ch.compander || 'Off')}</td>
            <td class="col-scanlist">${escHtml(ch.scanlist || 'None')}</td>
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

function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

// ─── Inline Editing ───────────────────────────────────────────

function startEdit(td, ch, field) {
    if (editingCell) return;
    editingCell = { td, ch, field };
    td.classList.add('editing');

    const raw = ch[field];
    let input;

    if (field === 'offsetDir') {
        input = createSelect(['Off', '+', '-'], raw);
    } else if (field === 'modulation') {
        input = createSelect(['FM', 'AM', 'USB'], raw);
    } else if (field === 'bandwidth') {
        input = createSelect(['Wide', 'Narrow'], raw);
    } else if (field === 'power') {
        input = createSelect(['High', 'Mid', 'Low'], raw);
    } else {
        input = document.createElement('input');
        input.type = 'text';
        input.value = field === 'rxFreq' || field === 'txOffset' ? fmtFreq(raw) : (raw || '');
    }

    td.textContent = '';
    td.appendChild(input);
    input.focus();
    if (input.select) input.select();

    const commit = () => finishEdit(td, ch, field, input);
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

async function finishEdit(td, ch, field, input) {
    if (!editingCell) return;
    editingCell = null;
    td.classList.remove('editing');

    let val = input.value;
    if (field === 'rxFreq' || field === 'txOffset') {
        val = parseFloat(val);
        if (isNaN(val)) val = 0;
    }

    ch[field] = val;

    // Update on server
    try {
        await fetch('/stations/api/stations/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(ch),
        });
    } catch (e) {
        console.error('Failed to save channel:', e);
    }

    renderTable();
}

// ─── Event Bindings ───────────────────────────────────────────

function bindEvents() {
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

        // Inline edit — determine field from column class
        const fieldMap = {
            'col-name': 'name',
            'col-freq': 'rxFreq',
            'col-offset': 'txOffset',
            'col-dir': 'offsetDir',
            'col-code': null,  // would need to know rx/tx
            'col-mod': 'modulation',
            'col-bw': 'bandwidth',
            'col-power': 'power',
            'col-step': 'step',
            'col-busylock': 'busyLock',
            'col-reverse': 'reverse',
            'col-pttid': 'pttId',
            'col-dtmf': 'dtmf',
            'col-scramble': 'scramble',
            'col-compander': 'compander',
            'col-scanlist': 'scanlist',
            'col-band': null,  // auto-calculated from frequency
        };

        for (const [cls, field] of Object.entries(fieldMap)) {
            if (td.classList.contains(cls) && field) {
                startEdit(td, ch, field);
                break;
            }
        }
    });
}

// ─── Read from Radio ──────────────────────────────────────────

async function readFromRadio() {
    const btn = document.getElementById('btn-read');
    btn.disabled = true;
    btn.querySelector('.btn-label').textContent = '⏳ Reading...';

    showProgress('Reading EEPROM from radio...', 0, 0);

    try {
        const res = await fetch('/stations/api/stations/read', { method: 'POST' });
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
        btn.querySelector('.btn-label').textContent = '⬇ Read from Radio';
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
    btn.querySelector('.btn-label').textContent = '⏳ Writing...';

    showProgress('Writing EEPROM to radio...', 0, 0);

    try {
        const res = await fetch('/stations/api/stations/write', { method: 'POST' });
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
        btn.querySelector('.btn-label').textContent = '⬆ Write to Radio';
        hideProgress();
    }
}

// ─── Task Polling ─────────────────────────────────────────────

async function pollTask(taskId, baseUrl, onComplete) {
    while (true) {
        const res = await fetch(`${baseUrl}${taskId}/status`);
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
        const res = await fetch('/stations/api/stations/import/csv', {
            method: 'POST',
            body: formData,
        });
        const data = await res.json();

        if (data.errors && data.errors.length > 0) {
            alert(`Import completed with errors:\n${data.errors.slice(0, 10).join('\n')}`);
        }

        if (data.channels && data.channels.length > 0) {
            // Replace entire channel list with imported data
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
        const res = await fetch('/stations/api/stations/backups');
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
        const res = await fetch(`/stations/api/stations/backups/${backupId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Delete failed');
        loadBackups();
    } catch (e) {
        alert('Delete failed: ' + e.message);
    }
};

window.restoreBackup = async function(backupId) {
    if (!confirm('Load this backup into the editor?')) return;
    try {
        const res = await fetch(`/stations/api/stations/restore/${backupId}`, { method: 'POST' });
        const data = await res.json();
        await loadChannels();
        alert(`Loaded ${data.restored} channels into editor.`);
    } catch (e) {
        alert('Restore failed: ' + e.message);
    }
};

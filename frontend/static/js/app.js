/**
 * Q-Remote V3 – Main Application (ES Module)
 * 
 * Orchestrates all frontend modules. For Phase 1 this is a simple
 * REST-based version that will be enhanced with SocketIO + Audio WS
 * in later phases.
 */

// ─── State ────────────────────────────────────────────────────────

const state = {
    connected: false,
    pttActive: false,
    rssiPolling: null,
};

// ─── DOM Elements ─────────────────────────────────────────────────

const statusEl = document.getElementById('status');
const pttBtn = document.getElementById('ptt-btn');
const smeterValue = document.getElementById('smeter-value');
const lcdCanvas = document.getElementById('lcd');
const lcdCtx = lcdCanvas.getContext('2d');

// ─── Init ─────────────────────────────────────────────────────────

async function init() {
    console.log('Q-Remote V3 starting...');
    
    // Check connection
    await checkHealth();
    
    // Wire up buttons
    setupButtons();
    
    // Wire up PTT
    setupPTT();
    
    // Start polling
    startPolling();
    
    // Init LCD
    clearLCD();
}

// ─── API Calls ────────────────────────────────────────────────────

async function api(method, path) {
    try {
        const res = await fetch(path, { method });
        return await res.json();
    } catch (e) {
        console.error(`API ${method} ${path} failed:`, e);
        return null;
    }
}

async function checkHealth() {
    const data = await api('GET', '/api/health');
    const lightEl = document.getElementById('status-light');
    if (data && data.radio === 'connected') {
        state.connected = true;
        statusEl.textContent = 'ONLINE';
        lightEl.className = 'status-light on';
    } else {
        state.connected = false;
        statusEl.textContent = (data?.radio || 'OFFLINE').toUpperCase();
        lightEl.className = 'status-light' + (data ? ' error' : '');
    }
}

async function updateStatus() {
    const data = await api('GET', '/api/status');
    if (!data) {
        state.connected = false;
        statusEl.textContent = 'Disconnected';
        statusEl.className = 'error';
        return;
    }
    
    const lightEl = document.getElementById('status-light');
    state.connected = data.state === 'connected';
    statusEl.textContent = data.state.toUpperCase();
    lightEl.className = 'status-light' + (state.connected ? ' on' : (data.state === 'error' ? ' error' : ''));
    
    // Update S-Meter
    if (data.rssi_dbm !== undefined) {
        smeterValue.textContent = `${data.s_unit} (${data.rssi_dbm} dBm)`;
    }
}

// ─── Button Handling ──────────────────────────────────────────────

function setupButtons() {
    document.querySelectorAll('.radio-btn').forEach(btn => {
        const keycode = parseInt(btn.dataset.key);
        
        btn.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            btn.classList.add('active');
            api('POST', `/api/key/${keycode}`);
        });
        
        btn.addEventListener('pointerup', () => {
            btn.classList.remove('active');
        });
        
        btn.addEventListener('pointerleave', () => {
            btn.classList.remove('active');
        });
    });
}

// ─── PTT Handling ─────────────────────────────────────────────────

function setupPTT() {
    pttBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        state.pttActive = true;
        pttBtn.classList.add('active');
        api('POST', '/api/ptt/true');
    });
    
    const releasePTT = () => {
        if (state.pttActive) {
            state.pttActive = false;
            pttBtn.classList.remove('active');
            api('POST', '/api/ptt/false');
        }
    };
    
    pttBtn.addEventListener('pointerup', releasePTT);
    pttBtn.addEventListener('pointerleave', releasePTT);
    pttBtn.addEventListener('pointercancel', releasePTT);
}

// ─── LCD ──────────────────────────────────────────────────────────

function clearLCD() {
    // Green phosphor CRT look
    lcdCtx.fillStyle = '#0a0f0a';
    lcdCtx.fillRect(0, 0, lcdCanvas.width, lcdCanvas.height);
    
    // Placeholder text in green phosphor
    lcdCtx.fillStyle = '#00ff41';
    lcdCtx.font = '10px monospace';
    lcdCtx.textAlign = 'center';
    lcdCtx.shadowColor = '#00ff41';
    lcdCtx.shadowBlur = 4;
    lcdCtx.fillText('Q-REMOTE V3', 64, 28);
    lcdCtx.font = '7px monospace';
    lcdCtx.fillStyle = '#00aa2a';
    lcdCtx.shadowColor = '#00aa2a';
    lcdCtx.shadowBlur = 2;
    lcdCtx.fillText('AWAITING SIGNAL...', 64, 44);
    lcdCtx.textAlign = 'left';
    lcdCtx.shadowBlur = 0;
}

// ─── Polling ──────────────────────────────────────────────────────

function startPolling() {
    // Poll status every 2 seconds
    setInterval(updateStatus, 2000);
    updateStatus();
}

// ─── Go! ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);

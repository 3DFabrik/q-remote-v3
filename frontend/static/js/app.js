/**
 * Q-Remote V3 – Main Application
 * 
 * Phase 2: SocketIO for real-time control + live LCD rendering.
 */

import { control } from './control.js';
import { DisplayRenderer } from './display.js';

// ─── State ────────────────────────────────────────────────────────

const state = {
    radioConnected: false,
    pttActive: false,
};

// ─── DOM ──────────────────────────────────────────────────────────

const statusEl = document.getElementById('status');
const statusLight = document.getElementById('status-light');
const pttBtn = document.getElementById('ptt-btn');
const smeterValue = document.getElementById('smeter-value');
const smeterBar = document.getElementById('smeter-bar');
const lcdCanvas = document.getElementById('lcd');

// Display renderer
const display = new DisplayRenderer(lcdCanvas);

// ─── Init ─────────────────────────────────────────────────────────

async function init() {
    console.log('Q-Remote V3 starting...');
    
    // Setup control module
    control.onConnect = () => {
        console.log('SocketIO connected');
        statusEl.textContent = 'CONNECTED';
        statusLight.className = 'status-light on';
    };
    
    control.onDisconnect = () => {
        console.log('SocketIO disconnected');
        statusEl.textContent = 'OFFLINE';
        statusLight.className = 'status-light error';
    };
    
    control.onRadioState = (radioState) => {
        state.radioConnected = radioState === 'connected';
        if (radioState === 'connected') {
            statusEl.textContent = 'ONLINE';
            statusLight.className = 'status-light on';
        } else {
            statusEl.textContent = radioState.toUpperCase();
            statusLight.className = 'status-light' + (radioState === 'error' ? ' error' : '');
        }
    };
    
    control.onDisplayUpdate = (data) => {
        display.processCommand(data);
    };
    
    control.onRssiUpdate = (dbm, sUnit) => {
        smeterValue.textContent = `${sUnit} ${dbm} dBm`;
        // Map dBm to bar width: -130 = 0%, -73 (S9) = 70%, -40 = 100%
        const pct = Math.max(0, Math.min(100, ((dbm + 130) / 90) * 100));
        smeterBar.style.width = `${pct}%`;
    };
    
    control.onPttStatus = (active, holder, error) => {
        state.pttActive = active;
        if (active) {
            pttBtn.classList.add('active');
        } else {
            pttBtn.classList.remove('active');
            if (error) {
                console.warn('PTT error:', error);
            }
        }
    };
    
    // Connect SocketIO
    control.connect();
    
    // Wire up buttons
    setupButtons();
    setupPTT();
    
    // Start periodic RSSI requests
    setInterval(() => control.requestRssi(), 1000);
}

// ─── Button Handling ──────────────────────────────────────────────

function setupButtons() {
    document.querySelectorAll('.radio-btn').forEach(btn => {
        const keycode = parseInt(btn.dataset.key);
        
        btn.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            btn.classList.add('active');
            control.sendKey(keycode);
        });
        
        const release = () => btn.classList.remove('active');
        btn.addEventListener('pointerup', release);
        btn.addEventListener('pointerleave', release);
        btn.addEventListener('pointercancel', release);
    });
}

// ─── PTT Handling ─────────────────────────────────────────────────

function setupPTT() {
    pttBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        control.pttOn();
    });
    
    const release = () => {
        if (state.pttActive) {
            control.pttOff();
        }
    };
    
    pttBtn.addEventListener('pointerup', release);
    pttBtn.addEventListener('pointerleave', release);
    pttBtn.addEventListener('pointercancel', release);
}

// ─── Go! ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);

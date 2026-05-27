/**
 * Q-Remote V3 – Main Application
 * Uses V1's lcd_update events for display rendering.
 */

import { control } from './control.js';
import { DisplayRenderer } from './display.js';

const state = {
    radioConnected: false,
    pttActive: false,
};

const statusEl = document.getElementById('status');
const statusLight = document.getElementById('status-light');
const pttBtn = document.getElementById('ptt-btn');
const smeterValue = document.getElementById('smeter-value');
const smeterBar = document.getElementById('smeter-bar');
const lcdCanvas = document.getElementById('lcd');

const display = new DisplayRenderer(lcdCanvas);

async function init() {
    console.log('Q-Remote V3 starting...');

    control.onConnect = () => {
        statusEl.textContent = 'CONNECTED';
        statusLight.className = 'status-light on';
    };

    control.onDisconnect = () => {
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
            statusLight.className = 'status-light error';
        }
    };

    // V1's lcd_update event
    control.onDisplayUpdate = (lcdState) => {
        display.processEvent(lcdState);
    };

    control.onRssiUpdate = (dbm, sUnit) => {
        smeterValue.textContent = `${sUnit} ${dbm} dBm`;
        const pct = Math.max(0, Math.min(100, ((dbm + 130) / 90) * 100));
        smeterBar.style.width = `${pct}%`;
    };

    control.onPttStatus = (active, holder, error) => {
        state.pttActive = active;
        if (active) {
            pttBtn.classList.add('active');
        } else {
            pttBtn.classList.remove('active');
        }
    };

    control.connect();
    setupButtons();
    setupPTT();
    setInterval(() => control.requestRssi(), 1000);
}

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

function setupPTT() {
    pttBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        control.pttOn();
    });
    const release = () => {
        if (state.pttActive) control.pttOff();
    };
    pttBtn.addEventListener('pointerup', release);
    pttBtn.addEventListener('pointerleave', release);
    pttBtn.addEventListener('pointercancel', release);
}

document.addEventListener('DOMContentLoaded', init);

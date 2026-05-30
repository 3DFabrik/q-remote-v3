/**
 * Q-Remote V3 - Main Application
 */

import { control } from "./control.js";
import { DisplayRenderer } from "./display.js";
import { AnalogSMeter } from "./smeter.js";
import { RxAudio } from "./audio.js";
import { TxAudio } from "./tx_audio.js";

const state = {
    radioConnected: false,
    pttActive: false,
};

const statusEl = document.getElementById("status");
const statusLight = document.getElementById("status-light");
const pttBtn = document.getElementById("ptt-btn");
const smeterValue = document.getElementById("smeter-value");
const smeterCanvas = document.getElementById("smeter-canvas");
const lcdCanvas = document.getElementById("lcd");

const display = new DisplayRenderer(lcdCanvas);
const smeter = new AnalogSMeter(smeterCanvas);
const rxAudio = new RxAudio();
const txAudio = new TxAudio();

// Wire up mic level to S-meter during TX
txAudio.onMicLevel = (rms) => {
    if (state.pttActive) {
        smeter.updateTXLevel(rms);
    }
};

async function init() {
    console.log("Q-Remote V3 starting...");

    control.onConnect = () => {
        statusEl.textContent = "CONNECTED";
        statusLight.className = "status-light on";
    };

    control.onDisconnect = () => {
        statusEl.textContent = "OFFLINE";
        statusLight.className = "status-light error";
    };

    control.onRadioState = (radioState) => {
        state.radioConnected = radioState === "connected";
        if (radioState === "connected") {
            statusEl.textContent = "ONLINE";
            statusLight.className = "status-light on";
        } else {
            statusEl.textContent = radioState.toUpperCase();
            statusLight.className = "status-light error";
        }
    };

    let _lcdTimer = null;
    let _pendingLcd = null;
    control.onDisplayUpdate = (lcdState) => {
        _pendingLcd = lcdState;
        if (_lcdTimer) clearTimeout(_lcdTimer);
        _lcdTimer = setTimeout(() => {
            if (_pendingLcd) {
                display.processEvent(_pendingLcd);
                _pendingLcd = null;
            }
        }, 50);
    };

    control.onRssiUpdate = (dbm, sUnit, sRaw) => {
        smeterValue.textContent = sUnit;
        smeter.updateRX(sRaw);
    };

    control.onPttStatus = (active, holder, error) => {
        state.pttActive = active;
        if (active) {
            pttBtn.classList.add("active");
            // Don't set static TX level – mic meter will drive the needle
            smeter.isTX = true;
            smeter.targetAngle = 0;
            smeter.draw();
        } else {
            pttBtn.classList.remove("active");
            smeter.setRX(0);  // Reset to S0
        }
    };

    control.connect();
    setupButtons();
    setupPTT();
    setupAudioToggle();

    // Auto-start audio on load
    startAudio();
}

function setupButtons() {
    document.querySelectorAll(".radio-btn").forEach(btn => {
        const keycode = parseInt(btn.dataset.key);
        btn.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            btn.classList.add("active");
            control.sendKey(keycode);
        });
        const release = () => {
            btn.classList.remove("active");
            control.sendKey(19);
        };
        btn.addEventListener("pointerup", release);
        btn.addEventListener("pointerleave", release);
        btn.addEventListener("pointercancel", release);
    });
}

function setupPTT() {
    pttBtn.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        control.pttOn();
        txAudio.startTransmit();
    });
    const release = () => {
        if (state.pttActive) {
            control.pttOff();
        }
        txAudio.stopTransmit();
    };
    pttBtn.addEventListener("pointerup", release);
    pttBtn.addEventListener("pointerleave", release);
    pttBtn.addEventListener("pointercancel", release);
}

let audioActive = false;
const audioBtn = document.getElementById("audio-btn");
const audioIcon = audioBtn.querySelector(".audio-icon");

async function startAudio() {
    audioBtn.classList.add("active");
    audioIcon.textContent = "\u{1F50A}";
    audioActive = true;
    await rxAudio.start();
    await txAudio.start();
}

function stopAudio() {
    audioBtn.classList.remove("active");
    audioIcon.textContent = "\u{1F507}";
    audioActive = false;
    rxAudio.stop();
    txAudio.stop();
}

function setupAudioToggle() {
    audioBtn.addEventListener("click", async () => {
        if (!audioActive) {
            await startAudio();
        } else {
            stopAudio();
        }
    });
}

document.addEventListener("DOMContentLoaded", init);

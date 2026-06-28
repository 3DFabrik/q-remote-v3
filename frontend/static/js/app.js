/**
 * Q-Remote V3 - Main Application
 */

import { control } from "./control.js?v=4.7";
import { DisplayRenderer } from "./display.js?v=1.2";
import { AnalogSMeter } from "./smeter.js?v=1.1";
import { RxAudio } from "./audio.js";
import { TxAudio } from "./tx_audio.js";

const state = {
    radioConnected: false,
    pttActive: false,
    pttHolderIsMe: false,
    remotePttUser: null,
    links: { control: false, rx: false, tx: false },
};

const statusEl = document.getElementById("status");
const ledIo = document.getElementById("led-io");
const ledRx = document.getElementById("led-rx");
const ledTx = document.getElementById("led-tx");
const pttBtn = document.getElementById("ptt-btn");
const tone1750Btn = document.getElementById("tone-1750-btn");
const smeterCanvas = document.getElementById("smeter-canvas");
const lcdCanvas = document.getElementById("lcd");
const pttNetStatus = document.getElementById("ptt-net-status");

let _localPttDown = false;
let _tone1750Cooldown = false;
let _tone1750CooldownTimer = null;

function isPttMine(active, holder, user) {
    if (!active) return false;
    if (user && user === window.CURRENT_USER?.name) return true;
    if (holder && control.socket && holder === control.socket.id) return true;
    return false;
}

function clearTone1750Cooldown() {
    if (_tone1750CooldownTimer) {
        clearTimeout(_tone1750CooldownTimer);
        _tone1750CooldownTimer = null;
    }
    _tone1750Cooldown = false;
    if (tone1750Btn) {
        tone1750Btn.classList.remove("sending");
    }
}

function resetAutoTonePttUi() {
    if (_localPttDown) return;
    state.pttActive = false;
    state.pttHolderIsMe = false;
    state.remotePttUser = null;
    pttBtn.classList.remove("active");
    smeter.isTX = false;
    smeter.setRX(0);
    txAudio.stopTransmit();
    rxAudio.muted = false;
    pttNetStatus.textContent = "";
}

function updateToneButtonState() {
    if (!tone1750Btn) return;
    tone1750Btn.disabled = !state.radioConnected || !!state.remotePttUser || _tone1750Cooldown;
}

function finishTone1750() {
    clearTone1750Cooldown();
    resetAutoTonePttUi();
    updateToneButtonState();
}

function sendTone1750() {
    if (!tone1750Btn || tone1750Btn.disabled) return;
    _tone1750Cooldown = true;
    tone1750Btn.classList.add("sending");
    tone1750Btn.disabled = true;
    control.tone1750();
    if (_tone1750CooldownTimer) clearTimeout(_tone1750CooldownTimer);
    _tone1750CooldownTimer = setTimeout(finishTone1750, 2500);
}

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

// ─── Session Timeout & Heartbeat ────────────────────────────────

const HEARTBEAT_INTERVAL = 60 * 1000;  // 1 minute
let _heartbeatTimer = null;

function startHeartbeat() {
    if (_heartbeatTimer) clearInterval(_heartbeatTimer);
    _heartbeatTimer = setInterval(async () => {
        try {
            const resp = await fetch("/api/heartbeat", { method: "POST" });
            if (resp.status === 401) {
                window.location.href = "/login";
            }
        } catch (e) {
            console.warn("Heartbeat failed:", e);
        }
    }, HEARTBEAT_INTERVAL);
}

// Tab close → logout (only on actual window/tab close, NOT navigation)
function setupTabCloseLogout() {
    let _navigating = false;

    // Track clicks on links
    document.addEventListener("click", (e) => {
        const link = e.target.closest("a[href]");
        if (link) _navigating = true;
    });
    // Track form submissions
    document.addEventListener("submit", () => { _navigating = true; });

    window.addEventListener("beforeunload", () => {
        if (!_navigating) {
            // Only send close beacon on actual tab/window close
            navigator.sendBeacon("/api/close");
        }
        _navigating = false;
    });
}

// Global 401 handler
function setupGlobal401Handler() {
    const origFetch = window.fetch;
    window.fetch = async function (...args) {
        const resp = await origFetch.apply(this, args);
        if (resp.status === 401) {
            const url = typeof args[0] === "string" ? args[0] : args[0]?.url || "";
            if (url.includes("/api/")) {
                window.location.href = "/login";
            }
        }
        return resp;
    };
}

// ─── End Session Timeout ────────────────────────────────────────

let audioActive = false;
const audioBtn = document.getElementById("audio-btn");
const audioIcon = audioBtn?.querySelector(".audio-icon");

function setLed(el, mode) {
    // mode: 'on' (green), 'error' (red), 'off' (gray)
    el.className = mode === "on" ? "status-light on" : mode === "error" ? "status-light error" : "status-light";
}

function updateConnectionStatus() {
    const { control, rx, tx } = state.links;
    setLed(ledIo, control ? "on" : "error");

    if (audioActive) {
        setLed(ledRx, rx ? "on" : "error");
        setLed(ledTx, tx ? "on" : "error");
    } else {
        setLed(ledRx, "off");
        setLed(ledTx, "off");
    }

    if (!control) {
        statusEl.textContent = "OFFLINE";
    } else if (audioActive && (!rx || !tx)) {
        statusEl.textContent = "AUDIO";
    } else if (state.radioConnected) {
        statusEl.textContent = "ONLINE";
    } else {
        statusEl.textContent = "NO RADIO";
    }
}


function releasePttLocal(reason) {
    console.warn("[PTT] Local release:", reason);
    txAudio.stopTransmit();
    if (state.pttActive) {
        control.pttOff();
    }
    state.pttActive = false;
    state.pttHolderIsMe = false;
    state.remotePttUser = null;
    _localPttDown = false;
    pttBtn.classList.remove("active");
    smeter.isTX = false;
    pttNetStatus.textContent = "";
    updateToneButtonState();
}

async function init() {
    console.log("Q-Remote V3 starting...");

    try {
        const sessionResp = await fetch("/api/heartbeat", { method: "POST" });
        if (sessionResp.status === 401) {
            window.location.replace("/login");
            return;
        }
    } catch (e) {
        console.warn("Session check failed:", e);
    }

    control.onConnect = () => {
        state.links.control = true;
        updateConnectionStatus();
        updateToneButtonState();
    };

    control.onDisconnect = () => {
        state.links.control = false;
        releasePttLocal("socket disconnected");
        updateConnectionStatus();
    };

    control.onRadioState = (radioState) => {
        state.radioConnected = radioState === "connected";
        if (radioState !== "connected") {
            releasePttLocal("radio disconnected");
        }
        updateConnectionStatus();
        updateToneButtonState();
    };

    let _lcdTimer = null;
    let _pendingLcd = null;
    control.onDisplayUpdate = (lcdState) => {
        _pendingLcd = lcdState;
        if (_lcdTimer) clearTimeout(_lcdTimer);
        _lcdTimer = setTimeout(() => {
            if (_pendingLcd) {
                display.processEvent(_pendingLcd);
                if (typeof _pendingLcd.rssi_dbm === "number" && !smeter.isTX) {
                    smeter.updateRX(dbmToSraw(_pendingLcd.rssi_dbm));
                }
                _pendingLcd = null;
            }
        }, 50);
    };

    function dbmToSraw(dbm) {
        // Below -120 dBm: needle at rest (no S reading on meter)
        if (dbm <= -120) return 0;
        if (dbm >= -13) return 15;
        if (dbm <= -73) {
            return 1 + (dbm - (-121)) / 6;
        }
        return 9 + (-73 - dbm) / (-10);
    }

    control.onGpioButtons = function(buttons) {
        if (typeof updateGpioButtons === 'function') updateGpioButtons(buttons);
    };
    
    control.onPttStatus = (active, holder, error, user) => {
        if (error) {
            _localPttDown = false;
            state.pttHolderIsMe = false;
            state.remotePttUser = null;
            updateToneButtonState();
            return;
        }
        const isMe = isPttMine(active, holder, user);
        if (!active && !_localPttDown) {
            txAudio.stopTransmit();
        }
        state.pttActive = active;
        state.pttHolderIsMe = isMe;
        state.remotePttUser = (active && user && !isMe) ? user : null;
        if (active) {
            pttBtn.classList.add("active");
            smeter.isTX = true;
            smeter.txGlowTarget = 1;
            smeter.rxGlowTarget = 0;
            smeter.targetAngle = 0;
            smeter.animate('tx');
            if (isMe) {
                rxAudio.muted = true;
                if (!_localPttDown && !txAudio.transmitting) {
                    txAudio.startTransmit();
                }
            }
            if (user && !isMe) {
                pttNetStatus.textContent = "📡 " + user + " sendet...";
            }
        } else {
            pttBtn.classList.remove("active");
            smeter.setRX(0);
            rxAudio.muted = false;
            pttNetStatus.textContent = "";
        }
        updateToneButtonState();
    };

    control.onTone1750Status = (ok, error) => {
        if (!ok) {
            console.warn("[1750] tone failed:", error || "unknown");
        }
        finishTone1750();
    };

    rxAudio.onConnectionChange = (connected) => {
        state.links.rx = connected;
        updateConnectionStatus();
    };
    txAudio.onConnectionChange = (connected) => {
        state.links.tx = connected;
        updateConnectionStatus();
    };

    control.connect();
    setupButtons();
    setupPTT();
    setupTone1750();
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") {
            releasePttLocal("tab hidden");
        }
    });
    window.addEventListener("pagehide", () => releasePttLocal("page hide"));
    setupAudioToggle();
    startAudio();

    // Session management
    setupTabCloseLogout();
    setupGlobal401Handler();
    startHeartbeat();
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
        _localPttDown = true;
        state.pttHolderIsMe = true;
        updateToneButtonState();
        rxAudio.muted = true;  // mute immediately to prevent self-echo
        control.pttOn();
        txAudio.startTransmit();
    });
    const release = () => {
        _localPttDown = false;
        updateToneButtonState();
        if (state.pttActive) {
            control.pttOff();
        }
        txAudio.stopTransmit();
    };
    pttBtn.addEventListener("pointerup", release);
    pttBtn.addEventListener("pointerleave", release);
    pttBtn.addEventListener("pointercancel", release);
}

function setupTone1750() {
    if (!tone1750Btn) return;
    tone1750Btn.addEventListener("click", (e) => {
        e.preventDefault();
        sendTone1750();
    });
    document.addEventListener("keydown", (e) => {
        if (e.repeat) return;
        if (e.key !== "t" && e.key !== "T") return;
        const tag = (e.target && e.target.tagName) || "";
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
        if (tone1750Btn.disabled) return;
        e.preventDefault();
        sendTone1750();
    });
    updateToneButtonState();
}

async function startAudio() {
    audioBtn.classList.add("active");
    audioIcon.textContent = "\u{1F50A}";
    audioActive = true;
    updateConnectionStatus();
    await rxAudio.start();
    await txAudio.start();
}

function stopAudio() {
    audioBtn.classList.remove("active");
    audioIcon.textContent = "\u{1F507}";
    audioActive = false;
    state.links.rx = false;
    state.links.tx = false;
    rxAudio.stop();
    txAudio.stop();
    updateConnectionStatus();
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

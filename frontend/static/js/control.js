/**
 * Q-Remote V3 – Control Module
 * 
 * SocketIO client for real-time communication.
 * Handles: display updates, key presses, PTT, RSSI, connection state.
 */

import { io } from 'https://cdn.socket.io/4.7.5/socket.io.esm.min.js';

class ControlModule {
    constructor() {
        this.socket = null;
        this.connected = false;
        
        // Callbacks
        this.onDisplayUpdate = null;   // (drawCommands) => {}
        this.onRssiUpdate = null;      // (dbm, sUnit, sRaw) => {}
        this.onRadioState = null;      // (state) => {}
        this.onPttStatus = null;       // (active, holder, error, user) => {}
        this.onConnect = null;         // () => {}
        this.onDisconnect = null;      // () => {}
        this.onGpioButtons = null;     // (buttons) => {}
    }
    
    connect() {
        this.socket = io({
            path: '/socket.io',
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 5000,
            reconnectionAttempts: Infinity,
        });
        
        this.socket.on('connect', () => {
            console.log('[Control] Connected, sid:', this.socket.id);
            this.connected = true;
            if (this.onConnect) this.onConnect();
        });
        
        this.socket.on('disconnect', (reason) => {
            console.log('[Control] Disconnected:', reason);
            this.connected = false;
            if (this.onDisconnect) this.onDisconnect();
        });
        
        this.socket.on('gpio_buttons', (data) => {
            if (this.onGpioButtons) this.onGpioButtons(data.buttons || {});
        });
        
        this.socket.on('display', (data) => {
            if (this.onDisplayUpdate) this.onDisplayUpdate(data);
        });
        
        this.socket.on('lcd_update', (data) => {
            if (this.onDisplayUpdate) this.onDisplayUpdate(data);
        });
        
        this.socket.on('rssi', (data) => {
            if (this.onRssiUpdate) this.onRssiUpdate(data.dbm, data.s_unit, data.s_raw ?? 0);
        });
        
        this.socket.on('radio_state', (data) => {
            if (this.onRadioState) this.onRadioState(data.state);
        });
        
        this.socket.on('ptt_status', (data) => {
            if (this.onPttStatus) this.onPttStatus(data.active, data.holder, data.error, data.user);
        });
    }
    
    sendKey(keycode) {
        if (this.socket && this.connected) {
            this.socket.emit('key_press', { keycode });
        }
    }
    
    pttOn() {
        if (this.socket && this.connected) {
            this.socket.emit('ptt_on');
        }
    }
    
    pttOff() {
        if (this.socket && this.connected) {
            this.socket.emit('ptt_off');
        }
    }
    
    requestRssi() {
        if (this.socket && this.connected) {
            this.socket.emit('request_rssi');
        }
    }
    
    requestDisplay() {
        if (this.socket && this.connected) {
            this.socket.emit('request_display');
        }
    }
}

// Singleton
export const control = new ControlModule();

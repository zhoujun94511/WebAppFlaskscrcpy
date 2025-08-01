const socket = io({ transports: ['websocket'] });
let deviceId = null;

function showNoDeviceTip(show) {
    document.getElementById('device-tip').style.display = show ? '' : 'none';
    if (show) {
        // 显示占位图
        document.getElementById('scrcpy-img').src = "/static/wcss/placeholder-mobile.svg";
    }
}

function refreshDeviceList() {
    fetch('/api/devices')
        .then(res => res.json())
        .then(data => {
            let sel = document.getElementById('device-select');
            sel.innerHTML = '';
            if (data.devices.length === 0) {
                deviceId = null;
                showNoDeviceTip(true);
            } else {
                showNoDeviceTip(false);
                data.devices.forEach(d => {
                    let opt = document.createElement('option');
                    opt.value = d;
                    opt.text = d;
                    sel.appendChild(opt);
                });
                sel.value = data.devices[0];
                deviceId = data.devices[0];
            }
        });
}

document.getElementById('refresh-devices').onclick = refreshDeviceList;
document.getElementById('device-select').onchange = function() {
    deviceId = this.value;
};
document.getElementById('start-stream').onclick = function() {
    if (deviceId)
        socket.emit('start_stream', { device_id: deviceId });
};
document.getElementById('stop-stream').onclick = function() {
    if (deviceId)
        socket.emit('stop_stream', { device_id: deviceId });
};

window.onload = function() {
    refreshDeviceList();
};

socket.on('scrcpy_frame', function(data) {
    if (data.device_id === deviceId) {
        document.getElementById('scrcpy-img').src = "data:image/jpeg;base64," + data.img;
    }
});

window.addEventListener('keydown', function(e) {
    if (!deviceId) return;
    if (e.ctrlKey && e.key.toLowerCase() === 'h') {
        socket.emit('scrcpy_event', { type: 'key', keycode: 3, action: 0, device_id: deviceId });
        socket.emit('scrcpy_event', { type: 'key', keycode: 3, action: 1, device_id: deviceId });
        e.preventDefault();
    }
});

let img = document.getElementById('scrcpy-img');
img.setAttribute('draggable', 'false');

let isDragging = false;
let dragStart = { x: 0, y: 0 };
let dragEnd = { x: 0, y: 0 };
let dragTimeStart = 0;
let dragTimeEnd = 0;

img.addEventListener('mousedown', function(e) {
    if (!deviceId) return;
    let rect = img.getBoundingClientRect();
    dragStart.x = Math.round((e.clientX - rect.left) * img.naturalWidth / rect.width);
    dragStart.y = Math.round((e.clientY - rect.top) * img.naturalHeight / rect.height);
    dragEnd.x = dragStart.x;
    dragEnd.y = dragStart.y;
    isDragging = true;
    dragTimeStart = Date.now();
});

img.addEventListener('mousemove', function(e) {
    if (!isDragging) return;
    let rect = img.getBoundingClientRect();
    dragEnd.x = Math.round((e.clientX - rect.left) * img.naturalWidth / rect.width);
    dragEnd.y = Math.round((e.clientY - rect.top) * img.naturalHeight / rect.height);
});

img.addEventListener('mouseup', function(e) {
    if (!deviceId || !isDragging) return;
    isDragging = false;
    let rect = img.getBoundingClientRect();
    dragEnd.x = Math.round((e.clientX - rect.left) * img.naturalWidth / rect.width);
    dragEnd.y = Math.round((e.clientY - rect.top) * img.naturalHeight / rect.height);
    dragTimeEnd = Date.now();

    let dx = dragEnd.x - dragStart.x;
    let dy = dragEnd.y - dragStart.y;
    let dist = Math.sqrt(dx * dx + dy * dy);
    // duration 根据鼠标拖动距离自适应，100~400ms
    let duration = Math.max(100, Math.min(400, dist * 0.5));

    if (dist > 10) {
        socket.emit('scrcpy_event', {
            type: 'swipe',
            start: [dragStart.x, dragStart.y],
            end: [dragEnd.x, dragEnd.y],
            duration: duration,
            device_id: deviceId
        });
    } else {
        socket.emit('scrcpy_event', { type: 'touch', x: dragStart.x, y: dragStart.y, action: 0, device_id: deviceId });
        socket.emit('scrcpy_event', { type: 'touch', x: dragStart.x, y: dragStart.y, action: 1, device_id: deviceId });
    }
});

document.getElementById('inputbox').addEventListener('keydown', function(e) {
    if (!deviceId || e.key !== 'Enter') return;
    socket.emit('scrcpy_event', { type: 'text', text: this.value, device_id: deviceId });
    this.value = '';
});

document.getElementById('get-clipboard').onclick = function() {
    if (!deviceId) return;
    socket.emit('scrcpy_event', { type: 'clipboard_get', device_id: deviceId });
};
socket.on('scrcpy_clipboard', function(data) {
    document.getElementById('clipboard-content').innerText = data.text;
});

document.getElementById('expand-noti').onclick = function() {
    if (!deviceId) return;
    socket.emit('scrcpy_event', { type: 'expand_notification', device_id: deviceId });
};

document.getElementById('collapse-panels').onclick = function() {
    if (!deviceId) return;
    socket.emit('scrcpy_event', { type: 'collapse_panels', device_id: deviceId });
};

document.getElementById('rotate').onclick = function() {
    if (!deviceId) return;
    socket.emit('scrcpy_event', { type: 'rotate_device', device_id: deviceId });
};
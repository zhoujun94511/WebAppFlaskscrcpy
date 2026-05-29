"""Service layer.

Every module in this package implements pure business logic, free of
Flask / Socket.IO request handling. Routes in ``api/*`` and
``terminal/routes.py`` are thin adapters that:

1. validate / parse incoming request data,
2. call a service function,
3. shape the response.

Each service module owns one concern (uploads, snapshots, adaptive
bitrate, file-transfer, scrcpy input dispatch, clipboard, WebRTC peer
setup) and exposes a small functional API.
"""

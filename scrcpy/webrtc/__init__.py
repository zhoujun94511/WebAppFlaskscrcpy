"""WebRTC pipeline for scrcpy mirroring.

Submodules:
- async_runner : background asyncio event loop, thread-safe submit/run
- rtp_h264     : RFC 6184 H.264 RTP packetizer (single NAL + FU-A)
- video_track  : aiortc MediaStreamTrack feeding raw H.264 NAL units
- peer_manager : RTCPeerConnection lifecycle keyed by browser session id
- data_channels: input (unreliable) + adb (reliable) DataChannel handlers
- bitrate_controller : TWCC-driven adaptive bitrate
"""

from .async_runner import AsyncRunner, get_runner  # noqa: F401

import asyncio
import json
import logging
import os

import av
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from picamera2 import Picamera2

ROOT = os.path.dirname(__file__)


class PiCameraTrack(VideoStreamTrack):
    """從 Pi Camera Module（CSI）抓取畫面的自訂 VideoTrack"""

    def __init__(self):
        super().__init__()
        self.cam = Picamera2()
        config = self.cam.create_video_configuration(
            main={"size": (1280, 720), "format": "RGB888"}
        )
        self.cam.configure(config)
        self.cam.start()

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        loop = asyncio.get_running_loop()
        frame_array = await loop.run_in_executor(None, self.cam.capture_array, "main")
        frame = av.VideoFrame.from_ndarray(frame_array, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def stop(self):
        self.cam.stop()
        super().stop()


camera_track = None
relay = MediaRelay()
pcs = set()


def get_camera_track():
    global camera_track
    if camera_track is None:
        camera_track = PiCameraTrack()
    return relay.subscribe(camera_track)


async def index(request):
    content = open(os.path.join(ROOT, "index.html"), "r", encoding="utf-8").read()
    return web.Response(content_type="text/html", text=content)


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print(f"連線狀態：{pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    pc.addTrack(get_camera_track())

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}),
    )


async def on_shutdown(app):
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()
    global camera_track
    if camera_track:
        camera_track.stop()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)

    logging.basicConfig(level=logging.INFO)
    print(f"請在瀏覽器輸入：http://192.168.0.113:{args.port}")
    web.run_app(app, host="0.0.0.0", port=args.port)

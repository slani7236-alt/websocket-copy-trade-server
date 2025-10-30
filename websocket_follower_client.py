"""
📦 WebSocket Client สำหรับ Master Bot
- เชื่อมต่อไปยัง WebSocket Server
- ส่งสัญญาณเมื่อตรวจจับ order ใหม่
- Auto-reconnect
"""

import asyncio
import websockets
import json
import time
import threading
import aiohttp
from typing import Dict, Any

class MasterWebSocketClient:
    def __init__(self, server_url="ws://localhost:8765"):
        self.server_url = server_url
        self.websocket = None
        self.connected = False
        self.loop = None
        self.reconnect_delay = 5
        self.max_reconnect_delay = 60
        
    async def wake_server(self):
        """Wake up Render server ถ้า cold start"""
        try:
            health_url = self.server_url.replace('wss://', 'https://').replace('ws://', 'http://')
            if 'render.com' in health_url:
                health_url = health_url.split(':')[0] + '://' + health_url.split('://')[1].split(':')[0]
            health_url = health_url.rstrip('/') + '/health'
            
            print(f"[MASTER WS] 🔔 Waking server...")
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        print(f"[MASTER WS] ✅ Server is awake")
                        return True
        except Exception as e:
            print(f"[MASTER WS] ⚠️ Wake attempt failed: {e}")
        return False
    
    async def connect(self):
        """เชื่อมต่อไปยัง WebSocket server"""
        # Wake server ก่อนถ้าเป็น Render
        if 'render.com' in self.server_url:
            await self.wake_server()
            await asyncio.sleep(2)
        
        try:
            print(f"[MASTER WS] 🔗 Connecting to {self.server_url}...")
            self.websocket = await websockets.connect(
                self.server_url,
                ping_interval=10,      # เร็วขึ้น: ping ทุก 10s (ตรวจเร็วขึ้น)
                ping_timeout=60,       # เพิ่ม timeout: รอ pong 60s (ให้โอกาส Render wake up)
                close_timeout=10,
                open_timeout=60,       # เพิ่ม open timeout เป็น 60s สำหรับ cold start
                max_size=10**7,
                compression=None       # ปิด compression เพื่อความเร็ว
            )
            self.connected = True
            
            # ส่ง register message
            await self.websocket.send(json.dumps({
                'type': 'register',
                'role': 'master',
                'timestamp': time.time()
            }))
            
            # รอ welcome message
            response = await self.websocket.recv()
            data = json.loads(response)
            
            if data.get('type') == 'welcome':
                print(f"[MASTER WS] ✅ Connected as Master")
                print(f"[MASTER WS] 📊 Server stats: {data.get('stats')}")
                self.reconnect_delay = 5  # รีเซ็ต delay
                return True
            
        except Exception as e:
            print(f"[MASTER WS] ❌ Connection failed: {e}")
            self.connected = False
            return False
    
    async def send_signal(self, signal_data: Dict[str, Any]):
        """ส่งสัญญาณไปยัง followers with auto-reconnect"""
        # ถ้า disconnected ให้ reconnect ก่อน (max 2 attempts)
        for attempt in range(2):
            if not self.connected or not self.websocket:
                print(f"[MASTER WS] 🔄 Auto-reconnecting before send (attempt {attempt+1}/2)...")
                await self.connect()
                await asyncio.sleep(1)
        
        if not self.connected or not self.websocket:
            print(f"[MASTER WS] ❌ Cannot send signal - still disconnected after reconnect attempts")
            return False
        
        try:
            # เพิ่ม metadata
            signal_data['type'] = 'signal'
            signal_data['master_timestamp'] = time.time()
            
            # ส่งสัญญาณ
            start_time = time.time()
            await self.websocket.send(json.dumps(signal_data))
            send_time = (time.time() - start_time) * 1000
            
            print(f"[MASTER WS] 📤 Signal sent ({send_time:.0f}ms)")
            print(f"[MASTER WS] 📊 {signal_data.get('asset')} {signal_data.get('direction')} ${signal_data.get('amount')}")
            return True
            
        except Exception as e:
            print(f"[MASTER WS] ❌ Send error: {e}")
            self.connected = False
            # ลอง reconnect และส่งอีกครั้ง
            print(f"[MASTER WS] 🔄 Retrying send after reconnect...")
            await self.connect()
            if self.connected:
                try:
                    await self.websocket.send(json.dumps(signal_data))
                    print(f"[MASTER WS] ✅ Signal sent successfully after retry")
                    return True
                except:
                    pass
            return False
    
    async def ping_loop(self):
        """ส่ง ping เพื่อ keep connection alive - ไม่ break เพื่อให้ reconnect ทำงานต่อ"""
        while True:
            try:
                if self.connected and self.websocket:
                    try:
                        await self.websocket.send(json.dumps({
                            'type': 'ping',
                            'timestamp': time.time()
                        }))
                    except Exception as send_error:
                        print(f"[MASTER WS] ⚠️ Ping send failed: {send_error}")
                        self.connected = False
                await asyncio.sleep(15)  # ping ทุก 15 วินาที
            except Exception as e:
                print(f"[MASTER WS] ⚠️ Ping loop error: {e}")
                await asyncio.sleep(5)  # รอแล้วลองใหม่
                # ไม่ break เพื่อให้ loop ทำงานต่อ
    
    async def reconnect_loop(self):
        """Auto-reconnect เมื่อ disconnect - loop ไม่หยุด"""
        while True:
            try:
                if not self.connected:
                    print(f"[MASTER WS] 🔄 Reconnecting in {self.reconnect_delay}s...")
                    await asyncio.sleep(self.reconnect_delay)
                    
                    try:
                        success = await self.connect()
                        if success:
                            print(f"[MASTER WS] ✅ Reconnected successfully!")
                            self.reconnect_delay = 5  # รีเซ็ต delay
                        else:
                            # เพิ่ม delay แบบ exponential backoff
                            self.reconnect_delay = min(
                                self.reconnect_delay * 1.5,
                                self.max_reconnect_delay
                            )
                            print(f"[MASTER WS] ⚠️ Reconnect failed, retry in {self.reconnect_delay:.0f}s")
                    except Exception as connect_error:
                        print(f"[MASTER WS] ❌ Reconnect error: {connect_error}")
                        self.reconnect_delay = min(self.reconnect_delay * 1.5, self.max_reconnect_delay)
                else:
                    await asyncio.sleep(3)  # check ทุก 3s
            except Exception as e:
                print(f"[MASTER WS] ⚠️ Reconnect loop error: {e}")
                await asyncio.sleep(5)
    
    async def start(self):
        """เริ่มต้น client"""
        # Connect ครั้งแรก
        await self.connect()
        
        # เริ่ม background tasks
        await asyncio.gather(
            self.ping_loop(),
            self.reconnect_loop()
        )
    
    def start_background(self):
        """เริ่มต้นใน background thread"""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.start())
        
        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        print(f"[MASTER WS] 🚀 Background client started")
    
    def send_signal_sync(self, signal_data: Dict[str, Any]):
        """ส่งสัญญาณแบบ synchronous (สำหรับเรียกจาก main thread)"""
        if self.loop and self.connected:
            future = asyncio.run_coroutine_threadsafe(
                self.send_signal(signal_data),
                self.loop
            )
            try:
                return future.result(timeout=2)
            except Exception as e:
                print(f"[MASTER WS] ❌ Sync send error: {e}")
                return False
        return False
    
    def close(self):
        """ปิด connection"""
        self.connected = False
        if self.websocket:
            asyncio.run_coroutine_threadsafe(
                self.websocket.close(),
                self.loop
            )

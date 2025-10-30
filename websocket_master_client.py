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
from typing import Dict, Any

class MasterWebSocketClient:
    def __init__(self, server_url="ws://localhost:8765"):
        self.server_url = server_url
        self.websocket = None
        self.connected = False
        self.loop = None
        self.reconnect_delay = 5
        self.max_reconnect_delay = 60
        
    async def connect(self):
        """เชื่อมต่อไปยัง WebSocket server"""
        try:
            print(f"[MASTER WS] 🔗 Connecting to {self.server_url}...")
            self.websocket = await websockets.connect(
                self.server_url,
                ping_interval=30,
                ping_timeout=10
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
        """ส่งสัญญาณไปยัง followers"""
        if not self.connected or not self.websocket:
            print(f"[MASTER WS] ⚠️ Not connected, cannot send signal")
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
            return False
    
    async def ping_loop(self):
        """ส่ง ping เพื่อ keep connection alive"""
        while True:
            try:
                if self.connected and self.websocket:
                    await self.websocket.send(json.dumps({
                        'type': 'ping',
                        'timestamp': time.time()
                    }))
                await asyncio.sleep(30)  # ping ทุก 30 วินาที
            except:
                break
    
    async def reconnect_loop(self):
        """Auto-reconnect เมื่อ disconnect"""
        while True:
            if not self.connected:
                print(f"[MASTER WS] 🔄 Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                
                success = await self.connect()
                if not success:
                    # เพิ่ม delay แบบ exponential backoff
                    self.reconnect_delay = min(
                        self.reconnect_delay * 2,
                        self.max_reconnect_delay
                    )
            else:
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

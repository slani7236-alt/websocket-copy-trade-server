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
        self.reconnect_delay = 3  # เริ่มที่ 3s สำหรับ Master (สำคัญ)
        self.max_reconnect_delay = 10  # Max 10s สำหรับ Master
    
    @property
    def is_connected(self):
        """Check if connected (อย่าตรวจสอบ closed state ที่นี่)"""
        # เพียงแค่ return self.connected flag
        # ปล่อยให้ ping_loop จัดการตรวจสอบ connection health
        return self.connected and self.websocket is not None
        
    async def wake_server(self):
        """Wake up Render server ถ้า cold start (HTTP health check)"""
        try:
            health_url = self.server_url.replace('wss://', 'https://').replace('ws://', 'http://')
            if 'render.com' in health_url:
                health_url = health_url.split(':')[0] + '://' + health_url.split('://')[1].split(':')[0]
            health_url = health_url.rstrip('/') + '/health'
            
            print(f"[MASTER WS] 🔔 Waking server via {health_url}...")
            async with aiohttp.ClientSession() as session:
                # ⚡ เพิ่ม timeout เป็น 60s สำหรับ cold start
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[MASTER WS] ✅ Server is awake - {data.get('status', 'unknown')}")
                        return True
                    else:
                        print(f"[MASTER WS] ⚠️ Server responded with status {resp.status}")
                        return False
        except asyncio.TimeoutError:
            print(f"[MASTER WS] ⏰ Wake timeout - server may still be starting...")
            return False
        except Exception as e:
            print(f"[MASTER WS] ⚠️ Wake error: {e}")
        return False
    
    async def connect(self):
        """เชื่อมต่อไปยัง WebSocket server"""
        # Wake server ก่อนถ้าเป็น Render
        if 'render.com' in self.server_url:
            print(f"[MASTER WS] 💤 Server may be sleeping, waking up...")
            wake_success = await self.wake_server()
            if wake_success:
                print(f"[MASTER WS] ⏳ Waiting 10s for server to fully start...")
                await asyncio.sleep(10)  # เพิ่มเป็น 10s เพื่อให้ server boot เสร็จ
            else:
                print(f"[MASTER WS] ⏳ Server starting (cold start may take 30-60s)...")
                await asyncio.sleep(15)  # รอนานขึ้นถ้า wake ไม่สำเร็จ
        
        try:
            print(f"[MASTER WS] 🔗 Connecting to {self.server_url}...")
            self.websocket = await websockets.connect(
                self.server_url,
                ping_interval=20,      # ลดลงเป็น 20s เพื่อประหยัด bandwidth และให้เวลา server
                ping_timeout=90,       # เพิ่มเป็น 90s สำหรับ Render cold start
                close_timeout=15,      # เพิ่มเป็น 15s
                open_timeout=90,       # เพิ่มเป็น 90s สำหรับ cold start ที่ช้า
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
        """ส่ง ping เพื่อ keep connection alive"""
        consecutive_failures = 0
        last_success_time = time.time()
        
        while True:
            try:
                if self.connected and self.websocket:
                    try:
                        # ส่ง ping โดยไม่ต้องตรวจสอบ closed ก่อน (ให้ exception จัดการ)
                        ping_task = self.websocket.ping()
                        await asyncio.wait_for(ping_task, timeout=10)  # เพิ่ม timeout เป็น 10s
                        
                        # Success
                        consecutive_failures = 0
                        last_success_time = time.time()
                        
                    except asyncio.TimeoutError:
                        consecutive_failures += 1
                        print(f"[MASTER WS] ⚠️ Ping timeout ({consecutive_failures}/3)")
                        if consecutive_failures >= 3:  # เพิ่มเป็น 3 ครั้ง
                            print(f"[MASTER WS] 🔴 Connection appears dead")
                            self.connected = False
                            consecutive_failures = 0
                            
                    except websockets.exceptions.ConnectionClosed:
                        print(f"[MASTER WS] 🔌 Connection closed during ping")
                        self.connected = False
                        consecutive_failures = 0
                        
                    except Exception as send_error:
                        consecutive_failures += 1
                        print(f"[MASTER WS] ⚠️ Ping error ({consecutive_failures}/3): {send_error}")
                        if consecutive_failures >= 3:  # เพิ่มเป็น 3 ครั้ง
                            print(f"[MASTER WS] 🔴 Connection appears dead")
                            self.connected = False
                            consecutive_failures = 0
                    
                    # ตรวจสอบว่านานเกิน 90s ไม่ได้ ping สำเร็จ (เพิ่มจาก 60s)
                    if time.time() - last_success_time > 90:
                        print(f"[MASTER WS] 🔴 No successful ping for 90s")
                        self.connected = False
                        consecutive_failures = 0
                        
                await asyncio.sleep(20)  # Ping ทุก 20 วินาที (ลดความถี่)
                
            except Exception as e:
                print(f"[MASTER WS] ❌ Ping loop error: {e}")
                self.connected = False
                await asyncio.sleep(5)
    
    async def reconnect_loop(self):
        """Auto-reconnect เมื่อ disconnect"""
        while True:
            try:
                if not self.connected:
                    wait_time = min(self.reconnect_delay, 30)  # Max 30s
                    print(f"[MASTER] ⏱️ Reconnecting in {wait_time:.0f}s...")
                    await asyncio.sleep(wait_time)
                    
                    try:
                        # Close old connection ถ้ามี
                        if self.websocket:
                            try:
                                await self.websocket.close()
                            except:
                                pass
                        
                        success = await self.connect()
                        if success:
                            print(f"[MASTER] ✅ Reconnected!")
                            self.reconnect_delay = 5
                        else:
                            self.reconnect_delay = min(self.reconnect_delay * 1.5, 30)
                            print(f"[MASTER] ❌ Reconnect failed, retry in {self.reconnect_delay:.0f}s")
                            
                    except Exception as connect_error:
                        self.reconnect_delay = min(self.reconnect_delay * 1.5, 30)
                        print(f"[MASTER] ❌ Reconnect error: {connect_error}")
                else:
                    # เพิ่ม delay เมื่อ connected เพื่อลดการตรวจสอบบ่อยเกินไป
                    await asyncio.sleep(10)
                        
            except Exception as e:
                print(f"[MASTER] ❌ Reconnect loop error: {e}")
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

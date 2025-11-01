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
        """Check if truly connected"""
        if not self.connected:
            return False
        if not self.websocket:
            return False
        if self.websocket.closed:
            self.connected = False
            return False
        return True
        
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
        """ส่ง ping เพื่อ keep connection alive แบบ aggressive"""
        consecutive_failures = 0
        last_success_time = time.time()
        
        while True:
            try:
                if self.connected and self.websocket:
                    try:
                        # ตรวจสอบว่า websocket ยังเปิดอยู่หรือไม่
                        if self.websocket.closed:
                            print(f"[MASTER WS] ⚠️ WebSocket closed, marking disconnected")
                            self.connected = False
                            consecutive_failures = 0
                            await asyncio.sleep(2)
                            continue
                        
                        # ส่ง ping ด้วย timeout สั้น
                        ping_task = self.websocket.ping()  # ใช้ built-in ping
                        await asyncio.wait_for(ping_task, timeout=5)
                        
                        # Success
                        consecutive_failures = 0
                        last_success_time = time.time()
                        
                    except asyncio.TimeoutError:
                        consecutive_failures += 1
                        print(f"[MASTER] Ping timeout ({consecutive_failures}/2)")
                        if consecutive_failures >= 2:
                            self.connected = False
                            consecutive_failures = 0
                            
                    except Exception as send_error:
                        consecutive_failures += 1
                        if consecutive_failures >= 2:
                            self.connected = False
                            consecutive_failures = 0
                    
                    # ตรวจสอบว่านานเกิน 60s ไม่ได้ ping สำเร็จ
                    if time.time() - last_success_time > 60:
                        print(f"[MASTER] No successful ping for 60s, reconnecting...")
                        self.connected = False
                        consecutive_failures = 0
                        
                await asyncio.sleep(15)  # Ping ทุก 15 วินาที (aggressive)
                
            except Exception as e:
                print(f"[MASTER] Ping error: {e}")
                self.connected = False
                await asyncio.sleep(3)
    
    async def reconnect_loop(self):
        """Auto-reconnect เมื่อ disconnect - aggressive สำหรับ Master"""
        while True:
            try:
                if not self.connected:
                    # Master สำคัญ - reconnect เร็วขึ้น
                    wait_time = min(self.reconnect_delay, 10)  # Max 10s
                    print(f"[MASTER] Reconnecting in {wait_time:.0f}s...")
                    await asyncio.sleep(wait_time)
                    
                    try:
                        # Close old connection ถ้ามี
                        if self.websocket and not self.websocket.closed:
                            try:
                                await self.websocket.close()
                            except:
                                pass
                        
                        success = await self.connect()
                        if success:
                            print(f"[MASTER] ✅ Reconnected!")
                            self.reconnect_delay = 3  # รีเซ็ตเป็น 3s สำหรับ Master
                        else:
                            # เพิ่ม delay แต่ไม่เกิน 10s สำหรับ Master
                            self.reconnect_delay = min(self.reconnect_delay * 1.2, 10)
                            
                    except Exception as connect_error:
                        self.reconnect_delay = min(self.reconnect_delay * 1.2, 10)
                else:
                    # ตรวจสอบ connection health
                    await asyncio.sleep(2)
                    
                    # ตรวจสอบว่า websocket ยังใช้งานได้
                    if self.websocket and self.websocket.closed:
                        print(f"[MASTER] WebSocket closed unexpectedly")
                        self.connected = False
                        
            except Exception as e:
                print(f"[MASTER] Reconnect error: {e}")
                await asyncio.sleep(3)
    
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

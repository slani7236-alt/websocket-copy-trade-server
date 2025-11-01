"""
📥 WebSocket Client สำหรับ Follower Bot
- เชื่อมต่อไปยัง WebSocket Server
- รับสัญญาณแบบ real-time
- Execute trade ทันที
"""

import asyncio
import websockets
import json
import time
import threading
import aiohttp
from typing import Callable, Dict, Any

class FollowerWebSocketClient:
    def __init__(self, server_url="ws://localhost:8765", on_signal_callback=None):
        self.server_url = server_url
        self.websocket = None
        self.connected = False
        self.loop = None
        self.on_signal_callback = on_signal_callback
        self.reconnect_delay = 5
        self.max_reconnect_delay = 30  # ลดลงเป็น 30s
        self.stats = {
            'signals_received': 0,
            'signals_executed': 0,
            'signals_failed': 0,
            'avg_latency': 0
        }
        self.latencies = []
    
    @property
    def is_connected(self):
        """Check if connected (อย่าตรวจสอบ closed state ที่นี่)"""
        # เพียงแค่ return self.connected flag
        # ปล่อยให้ ping_loop จัดการตรวจสอบ connection health
        return self.connected and self.websocket is not None
        
    async def wake_server(self):
        """Wake up Render server ถ้า cold start (HTTP health check)"""
        try:
            # Render ใช้ WebSocket port เดียวกันสำหรับ HTTP health check
            # แค่เปลี่ยน wss:// เป็น https://
            health_url = self.server_url.replace('wss://', 'https://').replace('ws://', 'http://')
            # Remove port ถ้ามี (Render ใช้ standard port 443/80)
            if 'render.com' in health_url:
                health_url = health_url.split(':')[0] + '://' + health_url.split('://')[1].split(':')[0]
            health_url = health_url.rstrip('/') + '/health'
            
            print(f"[FOLLOWER WS] 🔔 Waking server via {health_url}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[FOLLOWER WS] ✅ Server is awake - {data.get('followers', 0)} followers online")
                        return True
        except Exception as e:
            print(f"[FOLLOWER WS] ⚠️ Wake attempt failed (server may be starting...): {e}")
        return False
    
    async def connect(self):
        """เชื่อมต่อไปยัง WebSocket server"""
        # ลอง wake server ก่อน (สำหรับ Render cold start)
        if 'render.com' in self.server_url:
            await self.wake_server()
            await asyncio.sleep(2)  # รอ server boot
        
        try:
            print(f"[FOLLOWER WS] 🔗 Connecting to {self.server_url}...")
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
                'role': 'follower',
                'timestamp': time.time()
            }))
            
            # รอ welcome message
            response = await self.websocket.recv()
            data = json.loads(response)
            
            if data.get('type') == 'welcome':
                print(f"[FOLLOWER WS] ✅ Connected as Follower")
                print(f"[FOLLOWER WS] 📊 Server stats: {data.get('stats')}")
                self.reconnect_delay = 5  # รีเซ็ต delay
                return True
            
        except Exception as e:
            print(f"[FOLLOWER WS] ❌ Connection failed: {e}")
            self.connected = False
            return False
    
    async def listen_for_signals(self):
        """รับฟังสัญญาณจาก server"""
        print(f"[FOLLOWER WS] 👂 Started listening for signals...")
        try:
            async for message in self.websocket:
                data = json.loads(message)
                msg_type = data.get('type')
                
                if msg_type == 'signal':
                    # ได้รับสัญญาณใหม่!
                    receive_time = time.time()
                    
                    # คำนวณ latency
                    master_time = data.get('master_timestamp', receive_time)
                    latency = (receive_time - master_time) * 1000  # ms
                    self.latencies.append(latency)
                    if len(self.latencies) > 100:
                        self.latencies = self.latencies[-100:]
                    self.stats['avg_latency'] = sum(self.latencies) / len(self.latencies)
                    
                    self.stats['signals_received'] += 1
                    
                    print(f"\n[FOLLOWER WS] ⚡ SIGNAL: {data.get('asset')} {data.get('direction')} (latency:{latency:.0f}ms)")
                    print(f"[FOLLOWER WS] 📈 Total received: {self.stats['signals_received']}")
                    
                    # Execute callback
                    if self.on_signal_callback:
                        print(f"[FOLLOWER WS] 🔄 Executing callback...")
                        try:
                            success = self.on_signal_callback(data)
                            if success:
                                self.stats['signals_executed'] += 1
                                print(f"[FOLLOWER WS] ✅ Callback success! (Total executed: {self.stats['signals_executed']})")
                            else:
                                self.stats['signals_failed'] += 1
                                print(f"[FOLLOWER WS] ❌ Callback failed! (Total failed: {self.stats['signals_failed']})")
                        except Exception as e:
                            self.stats['signals_failed'] += 1
                            print(f"[FOLLOWER WS] ❌ Callback exception: {e}")
                            import traceback
                            traceback.print_exc()
                    else:
                        print(f"[FOLLOWER WS] ⚠️ WARNING: No callback registered!")
                
                elif msg_type == 'pong':
                    # Pong response - silent
                    pass
                
                elif msg_type == 'stats':
                    # Server stats
                    print(f"[FOLLOWER WS] 📊 Server stats: {data.get('data')}")
                
                else:
                    print(f"[FOLLOWER WS] 📩 Unknown message type: {msg_type}")
        
        except websockets.exceptions.ConnectionClosed as e:
            print(f"[FOLLOWER WS] 🔌 Connection closed: {e}")
            self.connected = False
        except Exception as e:
            print(f"[FOLLOWER WS] ❌ Listen error: {e}")
            import traceback
            traceback.print_exc()
            self.connected = False
    
    async def ping_loop(self):
        """ส่ง ping เพื่อ keep connection alive"""
        consecutive_failures = 0
        last_success_time = time.time()
        
        while True:
            try:
                if self.connected and self.websocket:
                    try:
                        # ไม่ตรวจสอบ closed ก่อน - ปล่อยให้ exception จัดการ
                        ping_task = self.websocket.ping()
                        await asyncio.wait_for(ping_task, timeout=10)  # timeout 10s
                        
                        consecutive_failures = 0
                        last_success_time = time.time()
                        
                    except asyncio.TimeoutError:
                        consecutive_failures += 1
                        print(f"[FOLLOWER WS] ⚠️ Ping timeout ({consecutive_failures}/3)")
                        if consecutive_failures >= 3:
                            print(f"[FOLLOWER WS] 🔴 Connection appears dead")
                            self.connected = False
                            consecutive_failures = 0
                    
                    except websockets.exceptions.ConnectionClosed:
                        print(f"[FOLLOWER WS] 🔌 Connection closed during ping")
                        self.connected = False
                        consecutive_failures = 0
                            
                    except Exception as e:
                        consecutive_failures += 1
                        print(f"[FOLLOWER WS] ⚠️ Ping error ({consecutive_failures}/3): {e}")
                        if consecutive_failures >= 3:
                            print(f"[FOLLOWER WS] 🔴 Connection appears dead")
                            self.connected = False
                            consecutive_failures = 0
                    
                    # Check no ping success for 90s
                    if time.time() - last_success_time > 90:
                        print(f"[FOLLOWER WS] 🔴 No successful ping for 90s")
                        self.connected = False
                        consecutive_failures = 0
                        
                await asyncio.sleep(20)  # Ping ทุก 20 วินาที
                
            except Exception as e:
                print(f"[FOLLOWER WS] ❌ Ping loop error: {e}")
                self.connected = False
                await asyncio.sleep(5)
    
    async def reconnect_loop(self):
        """Auto-reconnect เมื่อ disconnect"""
        while True:
            try:
                if not self.connected:
                    wait_time = min(self.reconnect_delay, 30)  # Max 30s
                    print(f"[FOLLOWER] ⏱️ Reconnecting in {wait_time:.0f}s...")
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
                            print(f"[FOLLOWER] ✅ Reconnected!")
                            self.reconnect_delay = 5
                            # เริ่ม listen ใหม่
                            asyncio.create_task(self.listen_for_signals())
                        else:
                            self.reconnect_delay = min(self.reconnect_delay * 1.5, 30)
                            print(f"[FOLLOWER] ❌ Reconnect failed, next retry in {self.reconnect_delay:.0f}s")
                            
                    except Exception as e:
                        self.reconnect_delay = min(self.reconnect_delay * 1.5, 30)
                        print(f"[FOLLOWER] ❌ Reconnect error: {e}, next retry in {self.reconnect_delay:.0f}s")
                else:
                    # เพิ่ม delay เมื่อ connected เพื่อลดการตรวจสอบ
                    await asyncio.sleep(10)
                    # ไม่ตรวจสอบ closed ที่นี่ - ปล่อยให้ ping_loop จัดการ
                        
            except Exception as e:
                print(f"[FOLLOWER] ❌ Reconnect loop error: {e}")
                await asyncio.sleep(5)
    
    async def start(self):
        """เริ่มต้น client"""
        # Connect ครั้งแรก
        success = await self.connect()
        
        if success:
            # เริ่ม background tasks
            await asyncio.gather(
                self.listen_for_signals(),
                self.ping_loop(),
                self.reconnect_loop()
            )
        else:
            # เริ่ม reconnect loop
            await self.reconnect_loop()
    
    def start_background(self):
        """เริ่มต้นใน background thread"""
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.start())
        
        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        print(f"[FOLLOWER WS] 🚀 Background client started")
        
        # รอให้ connect
        time.sleep(2)
    
    def get_stats(self):
        """ดึงสถิติ"""
        return self.stats
    
    def close(self):
        """ปิด connection"""
        self.connected = False
        if self.websocket and self.loop:
            asyncio.run_coroutine_threadsafe(
                self.websocket.close(),
                self.loop
            )

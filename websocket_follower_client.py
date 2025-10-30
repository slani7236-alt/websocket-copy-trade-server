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
from typing import Callable, Dict, Any

class FollowerWebSocketClient:
    def __init__(self, server_url="ws://localhost:8765", on_signal_callback=None):
        self.server_url = server_url
        self.websocket = None
        self.connected = False
        self.loop = None
        self.on_signal_callback = on_signal_callback
        self.reconnect_delay = 5
        self.max_reconnect_delay = 60
        self.stats = {
            'signals_received': 0,
            'signals_executed': 0,
            'signals_failed': 0,
            'avg_latency': 0
        }
        self.latencies = []
        
    async def connect(self):
        """เชื่อมต่อไปยัง WebSocket server"""
        try:
            print(f"[FOLLOWER WS] 🔗 Connecting to {self.server_url}...")
            self.websocket = await websockets.connect(
                self.server_url,
                ping_interval=30,
                ping_timeout=10
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
                    
                    print(f"[FOLLOWER WS] ⚡ Signal received (latency: {latency:.0f}ms)")
                    print(f"[FOLLOWER WS] 📊 {data.get('asset')} {data.get('direction')} ${data.get('amount')}")
                    
                    # Execute callback
                    if self.on_signal_callback:
                        try:
                            success = self.on_signal_callback(data)
                            if success:
                                self.stats['signals_executed'] += 1
                                print(f"[FOLLOWER WS] ✅ Trade executed")
                            else:
                                self.stats['signals_failed'] += 1
                                print(f"[FOLLOWER WS] ❌ Trade failed")
                        except Exception as e:
                            self.stats['signals_failed'] += 1
                            print(f"[FOLLOWER WS] ❌ Callback error: {e}")
                
                elif msg_type == 'pong':
                    # Pong response
                    pass
                
                elif msg_type == 'stats':
                    # Server stats
                    print(f"[FOLLOWER WS] 📊 Server: {data.get('data')}")
        
        except websockets.exceptions.ConnectionClosed:
            print(f"[FOLLOWER WS] 🔌 Connection closed")
            self.connected = False
        except Exception as e:
            print(f"[FOLLOWER WS] ❌ Listen error: {e}")
            self.connected = False
    
    async def ping_loop(self):
        """ส่ง ping เพื่อ keep connection alive"""
        while True:
            try:
                if self.connected and self.websocket:
                    await self.websocket.send(json.dumps({
                        'type': 'ping',
                        'timestamp': time.time()
                    }))
                await asyncio.sleep(30)
            except:
                break
    
    async def reconnect_loop(self):
        """Auto-reconnect เมื่อ disconnect"""
        while True:
            if not self.connected:
                print(f"[FOLLOWER WS] 🔄 Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                
                success = await self.connect()
                if success:
                    # เริ่ม listen ใหม่
                    asyncio.create_task(self.listen_for_signals())
                else:
                    # เพิ่ม delay
                    self.reconnect_delay = min(
                        self.reconnect_delay * 2,
                        self.max_reconnect_delay
                    )
            else:
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

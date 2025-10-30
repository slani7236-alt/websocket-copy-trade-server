"""
🚀 WebSocket Server สำหรับ Copy Trade System
- รองรับ Master ส่งสัญญาณ
- Broadcast ไปยัง Followers แบบ real-time
- Delay < 100ms
"""

import asyncio
import websockets
import json
import time
from datetime import datetime
from collections import defaultdict

class CopyTradeWebSocketServer:
    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.followers = set()  # Connected followers
        self.masters = set()    # Connected masters
        self.signal_history = []  # เก็บประวัติสัญญาณ
        self.stats = {
            'total_signals': 0,
            'total_followers': 0,
            'total_masters': 0,
            'start_time': time.time()
        }
    
    async def register_client(self, websocket, client_type):
        """ลงทะเบียน client (master/follower)"""
        if client_type == "master":
            self.masters.add(websocket)
            self.stats['total_masters'] = len(self.masters)
            print(f"[SERVER] 👑 Master connected: {len(self.masters)} total")
        elif client_type == "follower":
            self.followers.add(websocket)
            self.stats['total_followers'] = len(self.followers)
            print(f"[SERVER] 👥 Follower connected: {len(self.followers)} total")
        
        # ส่ง welcome message
        await websocket.send(json.dumps({
            'type': 'welcome',
            'role': client_type,
            'server_time': time.time(),
            'stats': self.stats
        }))
    
    async def unregister_client(self, websocket):
        """ลบ client เมื่อ disconnect"""
        if websocket in self.masters:
            self.masters.remove(websocket)
            self.stats['total_masters'] = len(self.masters)
            print(f"[SERVER] ❌ Master disconnected: {len(self.masters)} remaining")
        if websocket in self.followers:
            self.followers.remove(websocket)
            self.stats['total_followers'] = len(self.followers)
            print(f"[SERVER] ❌ Follower disconnected: {len(self.followers)} remaining")
    
    async def broadcast_signal(self, signal_data, sender_ws):
        """ส่งสัญญาณไปยัง followers ทั้งหมด"""
        # เพิ่ม metadata
        signal_data['broadcast_time'] = time.time()
        signal_data['server_id'] = id(self)
        
        # เก็บประวัติ
        self.signal_history.append(signal_data)
        if len(self.signal_history) > 1000:  # เก็บแค่ 1000 รายการล่าสุด
            self.signal_history = self.signal_history[-1000:]
        
        self.stats['total_signals'] += 1
        
        message = json.dumps(signal_data)
        
        # ส่งไปยัง followers ทั้งหมด (ไม่รวม sender)
        if self.followers:
            print(f"[SERVER] 📡 Broadcasting to {len(self.followers)} followers")
            results = await asyncio.gather(
                *[ws.send(message) for ws in self.followers if ws != sender_ws],
                return_exceptions=True
            )
            
            # นับจำนวนที่ส่งสำเร็จ
            success_count = sum(1 for r in results if not isinstance(r, Exception))
            print(f"[SERVER] ✅ Delivered: {success_count}/{len(self.followers)}")
        else:
            print(f"[SERVER] ⚠️ No followers connected")
    
    async def handle_client(self, websocket, path=None):
        """จัดการ connection จาก client (รองรับทั้ง websockets 12.x และ 15.x)"""
        client_type = None
        
        try:
            # รอ handshake message
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get('type')
                
                if msg_type == 'register':
                    # ลงทะเบียน client
                    client_type = data.get('role', 'follower')
                    await self.register_client(websocket, client_type)
                
                elif msg_type == 'signal' and websocket in self.masters:
                    # Master ส่งสัญญาณมา
                    print(f"[SERVER] 🔥 Signal from Master: {data.get('asset')} {data.get('direction')}")
                    await self.broadcast_signal(data, websocket)
                
                elif msg_type == 'ping':
                    # Health check
                    await websocket.send(json.dumps({
                        'type': 'pong',
                        'server_time': time.time()
                    }))
                
                elif msg_type == 'get_history':
                    # ขอประวัติสัญญาณ
                    limit = data.get('limit', 10)
                    await websocket.send(json.dumps({
                        'type': 'history',
                        'signals': self.signal_history[-limit:]
                    }))
                
                elif msg_type == 'get_stats':
                    # ขอสถิติ
                    await websocket.send(json.dumps({
                        'type': 'stats',
                        'data': self.stats
                    }))
        
        except websockets.exceptions.ConnectionClosed:
            print(f"[SERVER] 🔌 Connection closed")
        except Exception as e:
            print(f"[SERVER] ❌ Error: {e}")
        finally:
            await self.unregister_client(websocket)
    
    async def start(self):
        """เริ่มต้น WebSocket server"""
        print(f"[SERVER] 🚀 Starting WebSocket server...")
        print(f"[SERVER] 📍 Host: {self.host}:{self.port}")
        print(f"[SERVER] ⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        async with websockets.serve(
            self.handle_client,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=10**6  # 1MB max message size
        ):
            print(f"[SERVER] ✅ Server ready!")
            print(f"[SERVER] 🔗 Clients can connect to: ws://{self.host}:{self.port}")
            await asyncio.Future()  # run forever

def main():
    """เริ่มต้น server"""
    import os
    # Railway จะกำหนด PORT ให้, ถ้าไม่มีใช้ 8765 (local)
    port = int(os.environ.get('PORT', 8765))
    host = "0.0.0.0"
    
    print(f"[SERVER] 🌐 Starting on port {port} (Railway: {bool(os.environ.get('RAILWAY_ENVIRONMENT'))})")
    server = CopyTradeWebSocketServer(host=host, port=port)
    asyncio.run(server.start())

if __name__ == "__main__":
    main()

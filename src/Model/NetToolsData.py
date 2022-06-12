"""
MIT License

Copyright (c) 2021 0xKate

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import contextlib
from multiprocessing.pool import ThreadPool
from socket import gethostbyaddr
from typing import Dict, Tuple

import pubsub.pub
from scapy.arch import get_if_addr
from scapy.config import conf
from scapy.layers.inet import TCP, UDP, IP
from scapy.sendrecv import AsyncSniffer

from Enums import PROTO
from Model.HostData import HostData


class NetToolsData:
    def __init__(self):
        self.Sniffing = False
        self.BackgroundThreads = 0
        self.ReverseResolver = False
        self.Connections = {} #type: Dict[Tuple[str, int, int], HostData]
        self.SnifferEvent = asyncio.Event()
        self.LocalIP = get_if_addr(conf.iface)
        self.loop = asyncio.get_running_loop()

    ## - M.U.D - ##

    def GetAllConnections(self):
        # all_conn = self.UDPConnections | self.TCPConnections
        # return all_conn.values()
        return self.Connections.values()

    def GetNumBGThreads(self):
        return self.BackgroundThreads

    def SniffStop(self):
        if self.Sniffing:
            self.SnifferEvent.set()

    def SniffStart(self):
        if not self.Sniffing:
            self.loop.create_task(self._BGSniffer())

    ## - Helper Functions - ##

    async def HostFromAddr(self, ip):
        """
        GetHostFromAddr(ip) -> fqdn\n
        :param ip: The hosts ip address ie. 192.168.1.1
        :return: Return the fqdn (a string of the form 'sub.example.com') for a host.
        """
        # print('ThreadOpened')
        self.BackgroundThreads += 1
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        pool = ThreadPool(processes=1)

        thread_result = pool.apply_async(gethostbyaddr, (ip,), callback=lambda x: loop.call_soon_threadsafe(event.set))

        with contextlib.suppress(asyncio.TimeoutError):
            if await asyncio.wait_for(event.wait(), 5):
                result = thread_result.get()[0]
            event.clear()
            pool.close()
            pool.terminate()
            # print('ThreadClosed')
            self.BackgroundThreads -= 1
            return result

    ## - Backend Data Manipulation - ##

    async def _BGSniffer(self):
        # filter=f'host 188.138.40.87 or host 51.178.64.97 or host {WOW_WORLD_SERVER}'
        self.SnifferTask = AsyncSniffer(iface=conf.iface, prn=self._PacketCB, store=0, filter="tcp or udp")
        self.SnifferTask.start()
        self.Sniffing = True
        await self.SnifferEvent.wait()
        if self.SnifferTask.running:
            self.SnifferTask.stop()
        self.Sniffing = False
        self.SnifferEvent.clear()

    async def _UpdateConnectionData(self, conn_signature: Tuple[str, int, int],
                                remote_host, local_host,
                                conn_type, conn_direction, pkt_size):
        if conn_signature in self.Connections:
            self.Connections[conn_signature].IncrementCount(conn_direction, pkt_size)
        else:
            self.Connections[conn_signature] = HostData(*local_host, *remote_host, remote_host[0], conn_type)
            self.Connections[conn_signature].IncrementCount(conn_direction, pkt_size)
            hostname = await self.HostFromAddr(conn_signature[0])
            if hostname:
                self.Connections[conn_signature].SetRemoteHostname(hostname)

    def _PacketCB(self, pkt: IP):
        proto = None
        direction = None
        remote_socket = None
        local_socket = None
        if IP in pkt:
            if TCP in pkt:
                proto = PROTO.TCP
                if pkt[IP].dst == self.LocalIP:
                    direction = 'Incoming'
                    remote_socket = (pkt[IP].src, int(pkt[TCP].sport))
                    local_socket = (self.LocalIP, int(pkt[TCP].dport))
                elif pkt[IP].src == self.LocalIP:
                    direction = 'Outgoing'
                    remote_socket = (pkt[IP].dst, int(pkt[TCP].dport))
                    local_socket = (self.LocalIP, int(pkt[TCP].sport))
            elif UDP in pkt:
                proto = PROTO.UDP
                if pkt[IP].dst == self.LocalIP:
                    direction = 'Incoming'
                    remote_socket = (pkt[IP].src, int(pkt[UDP].sport))
                    local_socket = (self.LocalIP, int(pkt[UDP].dport))
                elif pkt[IP].src == self.LocalIP:
                    direction = 'Outgoing'
                    remote_socket = (pkt[IP].dst, int(pkt[UDP].dport))
                    local_socket = (self.LocalIP, int(pkt[UDP].sport))
            #print(f'{proto} and {direction} and {remote_socket} and {local_socket}')
            if proto is not None\
                    and direction is not None \
                    and remote_socket is not None\
                    and local_socket is not None:
                conn_signature = (str(remote_socket[0]), int(remote_socket[1]), int(proto.value))
                self.loop.create_task(self._UpdateConnectionData(conn_signature, remote_socket, local_socket,
                                                             proto.name, direction, len(pkt)))
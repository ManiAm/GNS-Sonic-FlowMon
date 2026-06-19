#!/usr/bin/env python3

""" Utility to sniff packets on a local interface """

import sys
import os
import signal
import threading
import queue
import socket
import logging
import argparse
from scapy.all import sniff, raw, PcapWriter

log = logging.getLogger(__name__)

sniffer_obj = None


class Sniffer():

    def __init__(self,
                 interface,
                 timeout=None,
                 count=0,
                 s_filter=None,
                 print_stdout=False,
                 output_file=None,
                 stream_packets=False,
                 collector_ip=None,
                 collector_port=None,
                 max_buffer=1000):

        self.interface = interface
        self.timeout = timeout
        self.count = count
        self.s_filter = s_filter

        if not (print_stdout or output_file or stream_packets):
            log.error("At least one output method must be specified: --print_stdout, --output, or --stream_packets")
            sys.exit(2)

        # Printing captured packets to stdout
        self.print_stdout = print_stdout

        # Saving captured packets to PCAP
        self.output_file = output_file
        self.pcap_writer = None
        if output_file:
            self.pcap_writer = PcapWriter(output_file, append=True, sync=True)

        # Send captured packets to remote collector
        self.stream_packets = stream_packets
        self.udp_socket = None
        self.remote_host = None
        if stream_packets:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if not collector_ip or not collector_port:
                log.error("stream_packets is enabled, but remote ip/port is missing!")
                sys.exit(2)
            self.remote_host = (collector_ip, int(collector_port))

        self.buffer = queue.Queue(maxsize=max_buffer)
        self.processing_thread = None
        self._stop_event = threading.Event()


    def start(self):

        intf_list = self._get_interface_names()
        if self.interface not in intf_list:
            return False, f"interface name is not valid: {self.interface}. Choose from {intf_list}"

        log.info("Sniffing on interface '%s' started...", self.interface)
        log.info("Hostname: %s", socket.gethostname())
        log.info("PID: %s", os.getpid())

        if self.output_file:
            log.info("Saving captured packets to %s", self.output_file)

        if self.stream_packets:
            log.info("Streaming captured packets to remote collector")

        log.info("")

        self.processing_thread = threading.Thread(target=self._process_packets, daemon=True)
        self.processing_thread.start()
        self.processing_thread.join(1)

        try:

            sniff(iface=self.interface,
                  timeout=self.timeout,
                  count=self.count,
                  filter=self.s_filter,
                  prn=self._packet_handler,
                  stop_filter=lambda x: self._stop_event.is_set())

        except Exception as e:
            log.error("Sniffing failed: %s", e)

        finally:
            log.info("Scapy is not running.")
            self.stop()

        return True, None


    def stop(self):

        self._stop_event.set()

        if self.processing_thread:
            self.processing_thread.join()


    def _get_interface_names(self):

        intf_list = []
        for _, name in socket.if_nameindex():
            intf_list.append(name)
        return intf_list


    def _packet_handler(self, packet):

        try:
            self.buffer.put(packet, block=False)
        except queue.Full:
            log.warning("Queue is full. Dropping packet.")


    def _process_packets(self):

        while not self._stop_event.is_set():

            try:

                packet = self.buffer.get(timeout=1)

                if self.print_stdout:
                    log.info(packet.summary())

                if self.pcap_writer:
                    self.pcap_writer.write(packet)

                if self.udp_socket:
                    self.udp_socket.sendto(raw(packet), self.remote_host)

                self.buffer.task_done()

            except queue.Empty:
                pass


def signal_handler(sig, frame):

    log.info("Ctrl+C received. Stopping sniffer...")

    if sniffer_obj:
        sniffer_obj.stop()


def parse_args():

    parser = argparse.ArgumentParser(description="Sniff packets on an interface")

    parser.add_argument("-i", "--interface", help="Interface to sniff on", required=True)

    parser.add_argument("-t", "--timeout", help="Stop sniffing after a given time", type=float)
    parser.add_argument("-c", "--count", help="Number of packets to capture. 0 means infinity", type=int, default=0)
    parser.add_argument("-f", "--filter", help="BPF filter to apply")

    parser.add_argument("--print_stdout", help="Print captured packets to stdout", action="store_true")
    parser.add_argument("-o", "--output", help="Output file to save packets as pcap")

    parser.add_argument("--stream_packets", help="sent the sniffed packets to the collector", action="store_true")
    parser.add_argument("--collector_ip", help="IP address of the collector")
    parser.add_argument("--collector_port", help="Port number of the collector")

    parser.add_argument("--debug", help="turn on debug log", action="store_true")

    return parser.parse_args()


if __name__ == "__main__":

    signal.signal(signal.SIGINT, signal_handler)

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    try:
        args = parse_args()
    except Exception as E:
        log.error(E)
        sys.exit(2)

    if args.debug:
        log.setLevel(logging.DEBUG)

    sniffer_obj = Sniffer(args.interface,
        timeout=args.timeout,
        count=args.count,
        s_filter=args.filter,
        print_stdout=args.print_stdout,
        output_file=args.output,
        stream_packets=args.stream_packets,
        collector_ip=args.collector_ip,
        collector_port=args.collector_port)

    status, output = sniffer_obj.start()
    if not status:
        log.error(output)
        sys.exit(2)

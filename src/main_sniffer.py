
import sys
import os
import logging
import time
import signal
import queue
from scapy.all import Ether

from router_sonic import Router_Sonic
from udp_forwarder import UDPForwarder
from packet_collector import UDP_Collector

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SSH_CONFIG = os.path.join(SCRIPT_DIR, "ssh_config")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger(__name__)

router = None
forwarder1 = None
forwarder2 = None


def stop_all():

    if router:
        router.stop()

    if forwarder1:
        forwarder1.stop()

    if forwarder2:
        forwarder2.stop()

    time.sleep(5)


def handle_sigint(sig, frame):

    log.info("Received Ctrl+C (SIGINT). Cleaning up...")
    stop_all()
    sys.exit(0)


if __name__ == "__main__":

    signal.signal(signal.SIGINT, handle_sigint)

    #####################

    print("\nConnecting to remote router.")

    router = Router_Sonic(
        host='sonic1-vm',
        username='admin',
        password='YourPaSsWoRd',
        ssh_config_file=SSH_CONFIG)

    status, output = router.connect()
    if not status:
        log.error("Cannot connect to sonic router: %s", output)
        sys.exit(1)

    #####################

    sniffer_port = 6343
    udp_collector_port = 26343

    print("\nForwarding mgmt-host → gns3-vm \n")

    forwarder1 = UDPForwarder(
        run_socat_on="mgmt-host",
        ssh_config_file=SSH_CONFIG,
        listen_port=sniffer_port,
        target_port=16343
    )

    forwarder1.start()

    print("\nForwarding gns3-vm → local host \n")

    forwarder2 = UDPForwarder(
        run_socat_on="gns3-vm",
        ssh_config_file=SSH_CONFIG,
        listen_port=16343,
        target_port=udp_collector_port
    )

    forwarder2.start()

    #####################

    duration = 120
    print(f"\nStarting sniffer collector on port {udp_collector_port} for {duration} seconds...")

    # making sure collector is up and running before starting the sniffer.
    udp_collector = UDP_Collector(host="0.0.0.0",
                                  port=udp_collector_port,
                                  max_buffer_size=3000,
                                  duration=duration)

    status, output = udp_collector.start_collector()
    if not status:
        log.error("start_collector failed: %s", output)
        sys.exit(1)

    #####################

    status, output = router.get_default_gw()
    if not status:
        log.error(output)
        sys.exit(1)

    default_gw = output

    #####################

    print(f"\nStarting remote sniffer for {duration} seconds...")

    # Starting the sniffer daemon on the router.
    # It sniffs packets on Etherne0 and sends the packets to collector ip/port.
    # We sniff for maximum of 'count' packets for 'duration' seconds.

    filter_val = "ether proto 0x0800 and icmp"

    status, output = router.start_sniffer(
            "Ethernet0",
            default_gw,
            sniffer_port,
            duration=duration,
            count=50,
            s_filter=filter_val)

    if not status:
        log.error(output)
        sys.exit(1)

    #####################

    print("\nCollecting sniffed packets in real-time...\n")

    buffer = udp_collector.get_collector_buffer()

    while udp_collector.is_collector_running() or (buffer and not buffer.empty()):

        try:
            packet_byte = buffer.get(timeout=1)
        except queue.Empty:
            continue

        decoded_packet = Ether(packet_byte)
        packet_summary = decoded_packet.summary()
        print(packet_summary)

    stop_all()

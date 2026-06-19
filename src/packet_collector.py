
import threading
import queue
import select
import socket
import time
import logging

log = logging.getLogger(__name__)


class UDP_Collector():

    def __init__(self,
                 host="0.0.0.0",
                 port=6343,
                 duration=30,
                 max_buffer_size=3000, # bytes
                 max_buffer=1000):
        """
            duration=0 means infinit
        """

        self.host = host
        self.port = port
        self.duration = duration
        self.max_buffer_size = max_buffer_size
        self.max_buffer = max_buffer

        self.collector_running = False
        self.collector_buffer = None


    def is_collector_running(self):

        return self.collector_running


    def get_collector_buffer(self):

        return self.collector_buffer


    def start_collector(self):

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        address = (self.host, self.port)
        sock.bind(address)

        if self.duration:
            log.info("Listening for UDP packets for %s seconds...", self.duration)
            end_time = time.time() + self.duration
        else:
            log.info("Listening for UDP packets...")
            end_time = None

        self.collector_buffer = queue.Queue(maxsize=self.max_buffer)

        self.collector_running = True

        processing_thread = threading.Thread(target=self._start_collector,
                                             args=(sock, end_time))
        processing_thread.name  = "packet_collector"
        processing_thread.daemon = True
        processing_thread.start()

        return True, None


    def _start_collector(self, sock, end_time):

        remaining_time = None

        try:

            while True:

                # Calculate remaining time
                if end_time:
                    remaining_time = end_time - time.time()
                    if remaining_time <= 0:
                        break

                # Use select with a timeout to wait for the socket to be ready
                readable, _, _ = select.select([sock], [], [], remaining_time)
                if not readable:
                    break

                data, addr = sock.recvfrom(self.max_buffer_size)

                log.debug("Received data from %s:", addr)

                try:
                    self.collector_buffer.put(data, block=False)
                except queue.Full:
                    log.warning("Queue is full. Dropping packet.")

        except Exception as e:
            log.exception("Collector thread crashed with exception: %s", str(e))

        finally:

            self.collector_running = False
            sock.close()

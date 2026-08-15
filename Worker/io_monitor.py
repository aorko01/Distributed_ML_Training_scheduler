import time
import threading

import psutil


class IORateMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_disk = None
        self._last_net = None
        self._last_time = None

    def sample(self) -> dict:
        try:
            disk = psutil.disk_io_counters()
            net = psutil.net_io_counters()
        except Exception:
            return {
                "diskReadBytesPerS": 0.0,
                "diskWriteBytesPerS": 0.0,
                "netRecvBytesPerS": 0.0,
                "netSentBytesPerS": 0.0,
            }

        now = time.time()
        with self._lock:
            prev_disk, prev_net, prev_time = self._last_disk, self._last_net, self._last_time
            self._last_disk, self._last_net, self._last_time = disk, net, now

            if prev_disk is None or prev_net is None or prev_time is None or now == prev_time:
                return {
                    "diskReadBytesPerS": 0.0,
                    "diskWriteBytesPerS": 0.0,
                    "netRecvBytesPerS": 0.0,
                    "netSentBytesPerS": 0.0,
                }

            dt = now - prev_time
            return {
                "diskReadBytesPerS": round(max(0.0, (disk.read_bytes - prev_disk.read_bytes) / dt), 1),
                "diskWriteBytesPerS": round(max(0.0, (disk.write_bytes - prev_disk.write_bytes) / dt), 1),
                "netRecvBytesPerS": round(max(0.0, (net.bytes_recv - prev_net.bytes_recv) / dt), 1),
                "netSentBytesPerS": round(max(0.0, (net.bytes_sent - prev_net.bytes_sent) / dt), 1),
            }


io_monitor = IORateMonitor()

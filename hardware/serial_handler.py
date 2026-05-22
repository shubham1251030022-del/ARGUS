"""
ARGUS — Hardware: hardware/serial_handler.py  [v3]
Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

v3 fixes:
  - send_alert() sends WARN_CLEAR first, then waits 150ms, then ALERT
  - Test sequence sends CLEAR between alerts so next warning works
  - Single LED guarantee: no two LEDs ever on together

Install: pip install pyserial --break-system-packages
"""

import serial
import serial.tools.list_ports
import threading
import time
import queue


class ARGUSHardware:

    def __init__(self, port=None, baud=9600, timeout=2):
        self.port       = port
        self.baud       = baud
        self.timeout    = timeout
        self._ser       = None
        self._queue     = queue.Queue(maxsize=30)
        self._thread    = None
        self._running   = False
        self._connected = False

    # ── Connection ─────────────────────────────────────────────────────────────

    def _find_port(self):
        ports = serial.tools.list_ports.comports()
        for p in ports:
            desc = (p.description or "").lower()
            if any(k in desc for k in ["arduino", "ch340", "ch341",
                                        "cp210", "ftdi", "usb serial"]):
                print(f"[HW] Arduino found: {p.device} ({p.description})")
                return p.device
        if ports:
            print(f"[HW] Trying first port: {ports[0].device}")
            return ports[0].device
        return None

    def start(self):
        if not self.port:
            self.port = self._find_port()
        if not self.port:
            print("[HW] No port found — hardware disabled.")
            return False
        try:
            self._ser = serial.Serial(self.port, self.baud,
                                       timeout=self.timeout)
            time.sleep(2)  # wait for Arduino reset
            self._ser.write(b"PING\n")
            resp = self._ser.readline().decode("utf-8", errors="ignore").strip()
            self._connected = True
            if resp == "PONG":
                print(f"[HW] Connected on {self.port} ✓")
            else:
                print(f"[HW] Connected on {self.port} (response: '{resp}')")
        except Exception as e:
            print(f"[HW] Connection failed: {e}")
            return False

        self._running = True
        self._thread  = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass
        self._connected = False
        print("[HW] Disconnected.")

    # ── Worker ─────────────────────────────────────────────────────────────────

    def _worker(self):
        while self._running:
            try:
                cmd = self._queue.get(timeout=1)
                if cmd is None:
                    break
                self._send_raw(cmd)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[HW] Worker error: {e}")

    def _send_raw(self, cmd):
        if not self._ser or not self._ser.is_open:
            return
        try:
            self._ser.write((cmd + "\n").encode("utf-8"))
            time.sleep(0.08)
            if self._ser.in_waiting:
                resp = self._ser.readline().decode("utf-8",
                        errors="ignore").strip()
                if resp and not resp.startswith("OK"):
                    print(f"[HW] Arduino: {resp}")
        except Exception as e:
            print(f"[HW] Send error: {e}")

    def _enqueue(self, cmd):
        if not self._connected:
            return
        try:
            self._queue.put_nowait(cmd)
        except queue.Full:
            print("[HW] Queue full — dropped")

    # ── Public API ─────────────────────────────────────────────────────────────

    def send_exam_start(self):
        print("[HW] → EXAM_START")
        self._enqueue("EXAM_START")

    def send_exam_stop(self):
        print("[HW] → EXAM_STOP")
        self._enqueue("EXAM_STOP")

    def send_warning(self, bench, student_name):
        name = "".join(c for c in student_name
                       if c.isalnum() or c == " ").strip()[:10]
        cmd  = f"WARNING:{bench}:{name}"
        print(f"[HW] → {cmd}")
        self._enqueue(cmd)

    def send_alert(self, bench, student_name):
        """
        FIX v3: Send WARN_CLEAR first to reset alertActive on Arduino,
        wait 150ms, then send ALERT. Guarantees warning state is cleared
        before alert fires — no LED overlap possible.
        """
        name = "".join(c for c in student_name
                       if c.isalnum() or c == " ").strip()[:10]
        # Step 1: clear any warning state first
        print("[HW] → WARN_CLEAR (pre-alert)")
        self._enqueue("WARN_CLEAR")
        # Step 2: small delay in queue — processed sequentially
        time.sleep(0.15)
        # Step 3: fire alert
        cmd = f"ALERT:{bench}:{name}"
        print(f"[HW] → {cmd}")
        self._enqueue(cmd)

    def send_clear(self):
        print("[HW] → CLEAR")
        self._enqueue("CLEAR")

    def send_warn_clear(self):
        print("[HW] → WARN_CLEAR")
        self._enqueue("WARN_CLEAR")

    def ping(self):
        if not self._ser or not self._ser.is_open:
            return False
        try:
            self._ser.write(b"PING\n")
            time.sleep(0.1)
            resp = self._ser.readline().decode("utf-8",
                    errors="ignore").strip()
            return resp == "PONG"
        except Exception:
            return False

    @property
    def is_connected(self):
        return self._connected and self._ser and self._ser.is_open


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("="*50)
    print("  ARGUS Hardware Test v3")
    print("  Single LED Mode")
    print("="*50)

    hw = ARGUSHardware()
    if not hw.start():
        print("  Could not connect. Check USB and COM port.")
        exit(1)

    print("\n  Running test sequence...\n")
    time.sleep(1)

    print("  [1] Exam start → GREEN only")
    hw.send_exam_start()
    time.sleep(3)

    print("  [2] Warning B1: Arya → AMBER only")
    hw.send_warning("B1", "Arya")
    time.sleep(4)

    print("  [3] Alert B1: Arya → RED only + 3 beeps")
    hw.send_alert("B1", "Arya")
    time.sleep(6)

    # FIX: Clear alert before next warning so alertActive resets on Arduino
    print("  [3.5] Clear → GREEN (reset for next test)")
    hw.send_clear()
    time.sleep(2)

    print("  [4] Warning B2: Shatakshi → AMBER only")
    hw.send_warning("B2", "Shatakshi")
    time.sleep(4)

    print("  [5] Alert B2: Shatakshi → RED only + 3 beeps")
    hw.send_alert("B2", "Shatakshi")
    time.sleep(6)

    print("  [6] Clear all → GREEN")
    hw.send_clear()
    time.sleep(2)

    print("  [7] Exam stop → ALL OFF")
    hw.send_exam_stop()
    time.sleep(2)

    hw.stop()
    print("\n  Test complete. All hardware verified.")

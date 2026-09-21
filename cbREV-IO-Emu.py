# REV MAIN IO Imitator - MicroPython version for RP2040
# Original: Arduino Nano version (cbREV-IO-Emu/cbREV-IO-Emu.ino on main branch)
# 2021/03/11 NTSK-lab. Pray Tohoku.
# 2022/4/7 Update
# 2026/2/18 Update - Port to MicroPython on RP2040 (Raspberry Pi Pico)
#
# Porting policy:
# - Protocol behavior follows the Arduino working code (single-frame
#   stop-and-wait: 1 request -> 1 response, surplus bytes discarded).
# - Exception: LED shows UART RX activity (Pico has no TX/RX LEDs).
# - Parsing uses an ordered line consumer + dispatch table instead of the
#   original startsWith/substring chain. Same observable behavior, easier to read.

from machine import UART, Pin
import time

try:
    import _thread
except ImportError:  # _thread無しビルド用のフォールバック
    _thread = None

# USB-CDC は別系統のため、ゲーム基板通信は UART1 を使用
UART_ID = 1
UART_TX = 4
UART_RX = 5
UART_BAUD = 115200

# SWビット割当 (Arduino版コメントより。HP Detect=256は未対応)
# TEST=1, DOWN=2, CANCEL=4, UP=8, SERVICE=16
SW_TEST = 0x01
SW_DOWN = 0x02
SW_CANCEL = 0x04
SW_UP = 0x08
SW_SERVICE = 0x10
SW_MASK = SW_TEST | SW_DOWN | SW_CANCEL | SW_UP | SW_SERVICE

BUTTON_PINS = {
    SW_TEST: 22,
    SW_SERVICE: 21,
    SW_UP: 20,
    SW_DOWN: 19,
    SW_CANCEL: 18,
}
BUILTIN_LED = 25

# ボタンサンプリング設計 (core1で実行)
# 5ms周期で raw を読み、4回連続一致 (20ms) で安定とみなす。人手ボタン用。
SW_SAMPLE_MS = 5
SW_STABLE_COUNT = 4

ETX = 0x03
_ETX_BYTE = b"\x03"
_MAX_FRAME = 256  # Arduino版の reserve(256) に対応。ETX無し暴走時の上限。

# 静的応答は事前encode (ETXまで含めて単発writeする)
BOOT_RESP = b"/I\n:BOOT\r\n/E\n\x03"
VERSION_RESP = (
    b"/I\n"
    b"C:REVIO NTKLAB LOGICBD 2026/09/22\n"
    b"V:REVIO NTKLB IMITATOR 2026/09/22\n"
    b"F:S,?,0,0,0,00\n"
    b"/E\n\x03"
)
CORE_EXIT_RESP = b"/S\n:CORE exit?\n/E\n\x03"
POST_INIT_RESP = b"/S\n:V,0210\nST:1\nCO:F,0\nSW:0,0,0\nVO:0\n\x03"


class ButtonDebouncer:
    """連続一致回数で安定判定するだけの純粋ロジック。core1から使う。"""

    def __init__(self, require=SW_STABLE_COUNT):
        self.require = require
        self.last_raw = None
        self.count = 0
        self.stable = 0

    def update(self, raw):
        if self.last_raw is None or raw != self.last_raw:
            self.last_raw = raw
            self.count = 1
        else:
            self.count += 1
        if self.count >= self.require:
            self.stable = raw
        return self.stable


class RevIoEmulator:
    def __init__(self):
        # UART初期化（基板とのシリアル通信用）
        # rp2 port では baudrate/tx/rx をまとめて渡すのが正しい流儀。
        # Pin には Pin.OUT/Pin.IN を付けず、番号だけ渡す（UARTペリフェラルが機能を制御するため）。
        self.uart = UART(
            UART_ID,
            baudrate=UART_BAUD,
            tx=Pin(UART_TX),
            rx=Pin(UART_RX),
            rxbuf=512,
            timeout=10,
            timeout_char=2,
        )

        # 入出力ピン初期化
        self.led = Pin(BUILTIN_LED, Pin.OUT)
        self._buttons = [
            (mask, Pin(pin, Pin.IN, Pin.PULL_UP))
            for mask, pin in BUTTON_PINS.items()
        ]

        # LEDはRXアクティビティ表示。起動時は消灯。
        self.led.off()

        # 内部状態 (Arduino版の statusString/audioVolumeString/isInitialized に対応)
        self.is_initialized = False
        self._status = "CO:F,0"
        self._audio = "0,0,0,0"
        self._prev_sw = 0

        # core1と共有する安定ボタン状態。通信コアはロック下で一瞬だけ読む。
        self._sw_stable = 0
        self._sw_lock = _thread.allocate_lock() if _thread else None
        self._threaded = False
        if _thread:
            try:
                _thread.start_new_thread(self._button_task, ())
                self._threaded = True
            except Exception:
                self._threaded = False

        # ETX受信まで蓄積する受信バッファ (Arduino版の inputString に対応)
        self._rx_buffer = bytearray()

        # コマンド -> ハンドラの対応表。
        # Arduino版の if-startsWith 連鎖と等価だが、読みやすさのため表駆動にする。
        self._handlers = {
            "/i": self._on_boot,
            "/v": self._on_version,
            "/s": self._on_small_s,
            "/S": self._on_game,
        }

        # デバッグ出力用状態（USB-CDC print）
        self._debug_window_start_ms = time.ticks_ms()
        self._debug_window_frame_count = 0
        self._debug_last_command = None
        self._debug_window_ms = 5000

    # ---- 応答ハンドラ (Arduino版 loop() 内の各 if ブロックに対応) ----

    def _on_boot(self, lines):
        self.is_initialized = False
        return BOOT_RESP

    def _on_version(self, lines):
        self.is_initialized = False
        return VERSION_RESP

    def _on_small_s(self, lines):
        if not self.is_initialized:
            self.is_initialized = True
            return CORE_EXIT_RESP
        return POST_INIT_RESP

    def _on_game(self, lines):
        """Arduino版の /S ブロックに対応。行を順番に消費する。

        lines[0] == "/S" の前提。想定順序は
          [":seq" (任意), "CO:F,0"/"CO:T,0" (任意), "AV:..." (任意), "/E"...]
        で、Arduino版と同じく順序固定で1行ずつ消費する。
        該当行が無ければ状態は据え置き (Arduino版も substring を進めない)。
        """
        self.is_initialized = True
        i = 1
        if i < len(lines) and lines[i].startswith(":"):
            i += 1  # SequenceNum は使わず捨てる
        if i < len(lines) and lines[i] in ("CO:F,0", "CO:T,0"):
            self._status = lines[i]
            i += 1
        if i < len(lines) and lines[i].startswith("AV:"):
            # Arduino版は ":"〜行末をそのまま保持するので、生文字列で保持する
            self._audio = lines[i][3:]
            i += 1
        # /E 以降は無視 (Arduino版も inputString をクリアするのみ)
        switch_line = self._read_switch_line()
        return (
            b"/S\n:V,0210\nST:3\n"
            + self._status.encode("ascii")
            + b"\n"
            + switch_line.encode("ascii")
            + b"\nVO:0\nAV:"
            + self._audio.encode("ascii", "ignore")
            + b"\n/E\n\x03"
        )

    # ---- 入力ヘルパー ----

    # ---- ボタン入力 (core1: サンプリング+デバウンス / core0: 読むだけ) ----

    def _read_raw_mask(self):
        raw = 0
        for mask, pin in self._buttons:
            if pin.value() == 0:  # active-low
                raw |= mask
        return raw & SW_MASK

    def _button_task(self):
        # core1で周期実行。print/確保を避け、UARTには触らない。
        debouncer = ButtonDebouncer(SW_STABLE_COUNT)
        lock = self._sw_lock
        while True:
            stable = debouncer.update(self._read_raw_mask())
            # 変化時のみ共有状態を更新し、通信コアの待ちを最小化する
            if stable != self._sw_stable:
                lock.acquire()
                try:
                    self._sw_stable = stable
                finally:
                    lock.release()
            time.sleep_ms(SW_SAMPLE_MS)

    def _stable_snapshot(self):
        if not self._threaded:
            # フォールバック: デバウンス無しで直読み (単コア互換)
            return self._read_raw_mask()
        self._sw_lock.acquire()
        try:
            return self._sw_stable
        finally:
            self._sw_lock.release()

    def _read_switch_line(self):
        # SWフォーマット (Arduino版コメントより):
        # 1要素目: 現在押されているビットマスク
        # 2要素目: このフレームで新たに押されたビット
        # 3要素目: このフレームで新たに離されたビット
        # 対応ビットは SW_MASK (TEST/DOWN/CANCEL/UP/SERVICE)。HP Detect(256)は除く。
        # 全ボタン active-low (Pin.PULL_UP で押下時0)。
        # 安定値はcore1が更新済み。ここではエッジ計算のみでブロックしない。
        current = self._stable_snapshot()
        pressed = (current & ~self._prev_sw) & SW_MASK
        released = (self._prev_sw & ~current) & SW_MASK
        self._prev_sw = current
        return "SW:{},{},{}".format(current, pressed, released)

    def _split_lines(self, packet):
        # ETX直前までの1フレームを空行除去で行分割する
        lines = packet.split("\n")
        cleaned = []
        for line in lines:
            normalized = line.strip("\r")
            if normalized:
                cleaned.append(normalized)
        return cleaned

    def _dispatch(self, packet):
        lines = self._split_lines(packet)
        if not lines:
            self._debug_log_parse_failure(packet)
            return None
        command = lines[0]
        handler = self._handlers.get(command)
        if handler is None:
            self._debug_log_invalid_command(command, packet)
            return None
        self._debug_on_frame_received(command)
        return handler(lines)

    # ---- デバッグ ----

    def _debug_on_frame_received(self, command):
        self._debug_window_frame_count += 1
        if command != self._debug_last_command:
            print("[DBG] RX-CMD changed: {}".format(command))
            self._debug_last_command = command

    def _debug_report_fps_if_due(self):
        now_ms = time.ticks_ms()
        elapsed_ms = time.ticks_diff(now_ms, self._debug_window_start_ms)
        if elapsed_ms < self._debug_window_ms:
            return
        if elapsed_ms <= 0:
            self._debug_window_start_ms = now_ms
            self._debug_window_frame_count = 0
            return
        frame_per_sec = (self._debug_window_frame_count * 1000.0) / elapsed_ms
        print("[DBG] RX-FPS(5s average): {:.2f} frame/s".format(frame_per_sec))
        self._debug_window_start_ms = now_ms
        self._debug_window_frame_count = 0

    def _debug_log_parse_failure(self, packet):
        preview = packet.replace("\n", "\\n").replace("\r", "\\r")
        if len(preview) > 80:
            preview = preview[:80] + "..."
        print("[DBG][WARN] Parse failed: len={}, raw='{}'".format(len(packet), preview))

    def _debug_log_invalid_command(self, command, packet):
        preview = packet.replace("\n", "\\n").replace("\r", "\\r")
        if len(preview) > 80:
            preview = preview[:80] + "..."
        print("[DBG][WARN] Invalid command: '{}', raw='{}'".format(command, preview))

    # ---- 受信ポンプ (Arduino版の serialEvent() + loop() に対応) ----

    def poll(self):
        """受信可能分を一括で取り込み、ETXが揃えば1件だけ処理する。

        Arduino版と同じく stop-and-wait 単発前提。ETX以降の余剰バイトは
        破棄する (Arduino版は inputString="" で捨てる)。
        戻り値: フレームを処理したら True。main の待機制御用。
        """
        if not self.uart.any():
            return False
        chunk = self.uart.read()
        if not chunk:
            return False
        self._rx_buffer.extend(chunk)
        etx_index = self._rx_buffer.find(_ETX_BYTE)
        if etx_index < 0:
            # ETX未到達: 部分受信として保持し、暴走時のみ破棄
            if len(self._rx_buffer) > _MAX_FRAME:
                del self._rx_buffer[:]
            return True
        # ETX到達: 先頭1フレームだけ取り出し、残りは捨てる (Arduino版準拠)
        packet = bytes(self._rx_buffer[:etx_index]).decode("utf-8", "ignore")
        del self._rx_buffer[:]
        # LEDはRXアクティビティ表示 (Arduino版の状態表示とは別方針)
        self.led.on()
        try:
            response = self._dispatch(packet)
            if response:
                # 本文+ETX を1回の write で送る (フレーム間の隙間防止)
                self.uart.write(response)
        finally:
            self.led.off()
        return True


def main():
    emulator = RevIoEmulator()
    print("REV IO Imitator started on RP2040")

    while True:
        did_work = emulator.poll()
        emulator._debug_report_fps_if_due()
        if not did_work:
            # 受信がない時だけ待機。受信中・処理直後は即次周回で遅延を詰める。
            time.sleep_ms(1)


if __name__ == "__main__":
    main()

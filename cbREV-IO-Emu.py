# REV MAIN IO Imitator - MicroPython version for RP2040
# Original: Arduino Nano version
# 2021/03/11 NTSK-lab. Pray Tohoku.
# 2022/4/7 Update
# 2026/2/18 Update - Port to MicroPython on RP2040 (Raspberry Pi Pico)

from machine import UART, Pin
import time

# USB-CDC は別系統のため、ゲーム基板通信は UART1 を使用
UART_ID = 1
UART_TX = 4
UART_RX = 5
UART_BAUD = 115200

TEST_BUTTON = 22
BUILTIN_LED = 25

ETX = 0x03
VALID_COMMANDS = ("/i", "/v", "/s", "/S")


class ParsedFrame:
    def __init__(self, command, sequence=None, fields=None):
        self.command = command
        self.sequence = sequence
        self.fields = fields if fields is not None else {}


class ResponseBuilder:
    @staticmethod
    def join_lines(lines):
        return "\n".join(lines) + "\n"

    @staticmethod
    def boot_response():
        return ResponseBuilder.join_lines([
            "/I",
            ":BOOT\r",
            "/E",
        ])

    @staticmethod
    def version_response():
        return ResponseBuilder.join_lines([
            "/I",
            "C:REVIO NTKLAB LOGICBD 2026/03/11",
            "V:REVIO NTKLABIMITATOR 2026/02/18",
            "F:S,?,0,0,0,00",
            "/E",
        ])

    @staticmethod
    def core_exit_response():
        return ResponseBuilder.join_lines([
            "/S",
            ":CORE exit?",
            "/E",
        ])

    @staticmethod
    def post_init_response():
        return ResponseBuilder.join_lines([
            "/S",
            ":V,0210",
            "ST:1",
            "CO:F,0",
            "SW:0,0,0",
            "VO:0",
            "/E",
        ])

    @staticmethod
    def game_response(status_line, switch_line, audio_volume_line):
        return ResponseBuilder.join_lines([
            "/S",
            ":V,0210",
            "ST:3",
            status_line,
            switch_line,
            "VO:0",
            audio_volume_line,
            "/E",
        ])


class RevIoEmulator:
    def __init__(self):
        # UART初期化（基板とのシリアル通信用）
        self.uart = UART(UART_ID, UART_BAUD)
        self.uart.init(tx=Pin(UART_TX, Pin.OUT), rx=Pin(UART_RX, Pin.IN))

        # 入出力ピン初期化
        self.led = Pin(BUILTIN_LED, Pin.OUT)
        self.test_button = Pin(TEST_BUTTON, Pin.IN, Pin.PULL_UP)

        # 起動時はLEDを消灯
        self.led.off()

        # 内部状態
        self.is_initialized = False
        self.coin_blocker = "F"  # F: ON, T: OFF
        self.audio_volume = (0, 0, 0, 0)
        self.prev_test_pressed = False

        # ETX受信まで蓄積する受信バッファ
        self._rx_buffer = bytearray()

        # デバッグ出力用状態（USB-CDC print）
        self._debug_window_start_ms = time.ticks_ms()
        self._debug_window_frame_count = 0
        self._debug_last_command = None
        self._debug_window_ms = 5000

    def _send_response(self, text):
        # 本文 + ETX で1フレーム送信
        self.uart.write(text.encode())
        self.uart.write(bytes([ETX]))

    def _debug_on_frame_received(self, frame):
        # 5秒窓のフレーム数を加算
        self._debug_window_frame_count += 1

        # 受信コマンド種別が変わったら都度表示
        if frame.command != self._debug_last_command:
            print("[DBG] RX-CMD changed: {}".format(frame.command))
            self._debug_last_command = frame.command

    def _debug_report_fps_if_due(self):
        # 5秒ごとに平均フレーム毎秒を表示
        now_ms = time.ticks_ms()
        elapsed_ms = time.ticks_diff(now_ms, self._debug_window_start_ms)
        if elapsed_ms < self._debug_window_ms:
            return

        frame_per_sec = (self._debug_window_frame_count * 1000.0) / elapsed_ms
        print("[DBG] RX-FPS(5s average): {:.2f} frame/s".format(frame_per_sec))

        self._debug_window_start_ms = now_ms
        self._debug_window_frame_count = 0

    def _debug_log_parse_failure(self, packet):
        packet_len = len(packet)
        preview = packet.replace("\n", "\\n").replace("\r", "\\r")
        if len(preview) > 80:
            preview = preview[:80] + "..."
        print("[DBG][WARN] Parse failed: len={}, raw='{}'".format(packet_len, preview))

    def _debug_log_invalid_command(self, command, packet):
        preview = packet.replace("\n", "\\n").replace("\r", "\\r")
        if len(preview) > 80:
            preview = preview[:80] + "..."
        print("[DBG][WARN] Invalid command: '{}', raw='{}'".format(command, preview))

    def _debug_log_no_response(self, command):
        print("[DBG][WARN] No response for command: '{}'".format(command))

    def _format_status_line(self):
        return "CO:{},0".format(self.coin_blocker)

    def _format_audio_volume_line(self):
        return "AV:{},{},{},{}".format(
            self.audio_volume[0],
            self.audio_volume[1],
            self.audio_volume[2],
            self.audio_volume[3],
        )

    def _read_test_switch_line(self):
        # SWフォーマット:
        # 1要素目: 現在押されているか
        # 2要素目: このフレームで新たに押されたか
        # 3要素目: このフレームで新たに離されたか
        current_pressed = (self.test_button.value() == 0)
        was_pressed = self.prev_test_pressed

        if current_pressed and not was_pressed:
            sw = (1, 1, 0)  # 押し始めフレーム
        elif current_pressed and was_pressed:
            sw = (1, 0, 0)  # 押下継続フレーム
        elif (not current_pressed) and was_pressed:
            sw = (0, 0, 1)  # 離されたフレーム
        else:
            sw = (0, 0, 0)  # 未押下フレーム

        self.prev_test_pressed = current_pressed
        return "SW:{},{},{}".format(sw[0], sw[1], sw[2])

    def _parse_volume(self, value):
        parts = value.split(",")
        if len(parts) != 4:
            return

        parsed = []
        for part in parts:
            try:
                parsed.append(int(part))
            except ValueError:
                return
        self.audio_volume = (parsed[0], parsed[1], parsed[2], parsed[3])

    def _parse_frame(self, packet):
        # 生テキストを command/sequence/fields に分解
        lines = packet.split("\n")
        cleaned = []
        for line in lines:
            normalized = line.strip("\r")
            if normalized:
                cleaned.append(normalized)

        if not cleaned:
            return None

        command = cleaned[0]
        index = 1
        sequence = None

        if index < len(cleaned) and cleaned[index].startswith(":"):
            sequence = cleaned[index][1:]
            index += 1

        fields = {}
        for line in cleaned[index:]:
            if line == "/E":
                break
            key, sep, value = line.partition(":")
            if not sep:
                continue
            fields[key] = value

        return ParsedFrame(command=command, sequence=sequence, fields=fields)

    def _apply_game_fields(self, frame):
        # ゲーム中更新対象のフィールドだけ反映
        coin_value = frame.fields.get("CO")
        if coin_value == "F,0":
            self.coin_blocker = "F"
        elif coin_value == "T,0":
            self.coin_blocker = "T"

        audio_value = frame.fields.get("AV")
        if audio_value is not None:
            self._parse_volume(audio_value)

    def process_frame(self, frame):
        # 場合分けのみを担当（応答文字列はBuilderへ委譲）
        if frame.command == "/i":
            self.is_initialized = False
            return ResponseBuilder.boot_response()

        if frame.command == "/v":
            self.is_initialized = False
            return ResponseBuilder.version_response()

        if frame.command == "/s":
            if not self.is_initialized:
                self.is_initialized = True
                return ResponseBuilder.core_exit_response()
            return ResponseBuilder.post_init_response()

        if frame.command == "/S":
            self.is_initialized = True
            self._apply_game_fields(frame)
            switch_line = self._read_test_switch_line()
            return ResponseBuilder.game_response(
                status_line=self._format_status_line(),
                switch_line=switch_line,
                audio_volume_line=self._format_audio_volume_line(),
            )

        return ""

    def poll(self):
        # UART受信キューを空になるまで処理
        while self.uart.any():
            chunk = self.uart.read(1)
            if not chunk:
                break
            value = chunk[0]

            if value == ETX:
                # ETX到達で1パケット確定→解析→応答
                self.led.on()
                try:
                    packet = self._rx_buffer.decode("utf-8", "ignore")
                    self._rx_buffer = bytearray()
                    frame = self._parse_frame(packet)
                    if frame is None:
                        self._debug_log_parse_failure(packet)
                        continue
                    if frame.command not in VALID_COMMANDS:
                        self._debug_log_invalid_command(frame.command, packet)
                        continue
                    self._debug_on_frame_received(frame)
                    response = self.process_frame(frame)
                    if response:
                        self._send_response(response)
                    else:
                        self._debug_log_no_response(frame.command)
                finally:
                    self.led.off()
            else:
                # ETXまで受信データを蓄積
                self._rx_buffer.append(value)


def main():
    # エミュレータ初期化
    emulator = RevIoEmulator()
    print("REV IO Imitator started on RP2040")

    while True:
        # 受信があれば即処理し、短い待機で負荷を抑える
        emulator.poll()
        emulator._debug_report_fps_if_due()
        time.sleep_ms(1)


if __name__ == "__main__":
    main()

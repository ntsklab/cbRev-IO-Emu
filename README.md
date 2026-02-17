# cbRev-IO-Emu

## ファイル構成

- `cbREV-IO-Emu.py` - RP2040（Raspberry Pi Pico）版

## ピン配置（RP2040版）

```text
UART1:
  TX: GPIO4
  RX: GPIO5
  ボーレート: 115200 bps

GPIO:
  内蔵LED: GPIO25
  TESTボタン: GPIO22
```

## 現在の動作仕様

- UARTは USB-CDC とは別に UART1 をゲーム基板通信用として使用
- オンボードLED（GPIO25）は「ETX受信〜応答ETX送信完了」の処理中のみ点灯

## デバッグ出力（USB-CDC）

- 5秒ごとの受信フレームレート表示
- 受信コマンド種別が変化した時の表示
- 異常フレーム（パース失敗 / 不正コマンド / 応答なし）の警告表示

## お問い合わせ

Twitter : @nt776 まで。

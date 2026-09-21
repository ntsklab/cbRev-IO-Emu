# cbRev-IO-Emu

動作保証なし

## ファイル構成

- `cbREV-IO-Emu.py` - Raspberry Pi Pico / Pico 2（RP2040/RP2350）版

## ピン配置

```text
UART1:
  TX: GPIO4
  RX: GPIO5
  ボーレート: 115200 bps

GPIO:
  内蔵LED: GPIO25 (RXアクティビティ表示)
  TESTボタン: GPIO22 (SW bit0=1, active-low)
  SERVICEボタン: GPIO21 (SW bit4=16, active-low)
  UPボタン: GPIO20 (SW bit3=8, active-low)
  DOWNボタン: GPIO19 (SW bit1=2, active-low)
  CANCELボタン: GPIO18 (SW bit2=4, active-low)
```

## お問い合わせ

Twitter : @nt776 まで

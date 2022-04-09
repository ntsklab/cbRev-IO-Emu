// REV MAIN IO Imitator
// Arduino Nanoで動作確認しています。動作の保証はしません。酒駆動式開発ですので乱雑です。
//ピン配列等はBlogへ。PCB CN4 : 115200bps 8N1 UART (EIA232Level) 3:PCB RX 5:PCB TX 9:GND です。

//良きに232ドライバをPCBのCN4 UART2(上記)に繋いで、このスケッチを書き込んだArduinoなどのTXD/RXDをドライバにいい感じに接続すれば動きます。

// 2021/03/11 NTSK-lab. Pray Tohoku.
// 2022/4/7 Update

String inputString = "";
String outputString = "";

String statusString = "CO:F,0\n";
String audioVolumeString = "0,0,0,0";
String switchString = "SW:0,0,0";

bool stringComplete = false;

bool isInitialized = false;
int initCnt = 0;

#define TESTBUTTON 9
#define WORKLED_328P 2

void setup()
{
    // PCB CN4 : 115200bps 8N1 UART (EIA232Level) 3:PCB RX 5:PCB TX 9:GND
    Serial.begin(115200);

    inputString.reserve(256);
    outputString.reserve(256);

    pinMode(LED_BUILTIN, OUTPUT);
    pinMode(WORKLED_328P, OUTPUT);
    pinMode(TESTBUTTON, INPUT_PULLUP);
}

void loop()
{
    if (stringComplete)
    {
        // BOOT処理(CORE)
        if (inputString.startsWith("/i"))
        {
            /*
            CH9 PCB -> IO
                /i\n
                /e\n
                <<ETX>>
            */
            outputString = "/I\n:BOOT\r\n/E\n";
            isInitialized = false;
            inputString = "";
        }

        // Version情報
        if (inputString.startsWith("/v"))
        {
            /*
            CH9 PCB -> IO
                /v\n
                /e\n
                <<ETX>>
            */
            outputString = "/I\nC:REVIO NTKLAB LOGICBD 2021/03/28\nV:REVIO NTKLB IMITATOR 2021/03/11\nF:S,?,0,0,0,00\n/E\n";
            isInitialized = false;
            inputString = "";
        }

        // CORE抜ける
        if (inputString.startsWith("/s") && !isInitialized)
        {
            /*
            CH9 PCB -> IO
                /s\n
                /e\n
                <<ETX>>
            */
            outputString = "/S\n:CORE exit?\n/E\n";
            inputString = "";
            isInitialized = true;
            digitalWrite(LED_BUILTIN, HIGH);

            /*
            CH9 PCB -> IO
                /s\n
                /e\n
                <<ETX>>
            */
        }

        // CORE抜けたあとの/s
        if (inputString.startsWith("/s") && isInitialized)
        {
            // IO基板から通信を始める。なんか一発目は内容がちょっと違った。
            outputString = "/S\n:V,0210\nST:1\nCO:F,0\nSW:0,0,0\nVO:0\n/E\n";
            inputString = "";
        }

        // ゲーム起動中
        if (inputString.startsWith("/S"))
        {
            /*
            CH9 PCB -> IO
                /S
                :314776
                CO:F,0
                AV:90,100,0,0
                /E
                <<ETX>>
            */
            isInitialized = true;
            inputString = inputString.substring(inputString.indexOf("\n") + 1); // /S消す

            if (inputString.startsWith(":"))
                inputString = inputString.substring(inputString.indexOf("\n") + 1); // SequenceNum消す

            if (inputString.startsWith("CO:F,0"))
            {
                //コインブロッカーON
                //使い道ないのでLEDでも点滅させておく。高速で見えないけど。
                digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
                digitalWrite(WORKLED_328P, !digitalRead(WORKLED_328P));
                statusString = "CO:F,0\n";
                inputString = inputString.substring(inputString.indexOf("\n") + 1); // ステータス消す
            }
            else if (inputString.startsWith("CO:T,0"))
            {
                //コインブロッカーOFF
                //同様に使いみち無いのでLEDを点灯状態にする
                digitalWrite(LED_BUILTIN, HIGH);
                digitalWrite(WORKLED_328P, HIGH);
                statusString = "CO:T,0\n";
                inputString = inputString.substring(inputString.indexOf("\n") + 1); // ステータス消す
            }

            if (inputString.startsWith("AV:"))
            {
                //設定されているボリューム情報
                //使いみち（ｒｙ
                audioVolumeString = inputString.substring(inputString.indexOf(":") + 1, inputString.indexOf("\n"));
                inputString = inputString.substring(inputString.indexOf("\n") + 1); // 音量情報消す
            }

            /*
                SWパラメータのビット(9ビット)
                1   1   1   1   1   1   1   1   1
                                                TEST(1)
                                            DOWN(2)
                                        CANCEL(4)
                                    UP(8)
                                SERVICE(16)
                            N/A?
                        N/A?
                    N/A?
                HeadPhone Detect(256)
            */
            if (digitalRead(TESTBUTTON) == LOW)
            {
                // 1要素目 現在押されているボタン
                // 2要素目 今回の通信で新たに押されたボタン
                // 3要素目 今回の通信で新たに離されたボタン
                if (switchString.startsWith("SW:1,1,0"))
                    switchString = "SW:1,0,0\n";
                else
                    switchString = "SW:1,1,0\n";
            }
            else if (switchString.startsWith("SW:1,"))
            {
                switchString = "SW:0,0,1\n";
            }
            else
            {
                switchString = "SW:0,0,0\n";
            }

            inputString = "";

            // outputStringの組み立て
            outputString = "/S\n:V,0210\nST:3\n";
            outputString.concat(statusString);
            outputString.concat(switchString);
            outputString.concat("VO:0\nAV:"); // VO : ボリュームR/Eが動いている間1になる？
            outputString.concat(audioVolumeString);
            outputString.concat("\n/E\n");
        }

        // output
        if (outputString.length() != 0)
        {
            Serial.print(outputString);
            Serial.write(0x3); // ETX
        }
        inputString = "";
        outputString = "";
        stringComplete = false;
    }
}

void serialEvent()
{
    while (Serial.available())
    {
        uint8_t readC = Serial.read();
        inputString += (char)readC;

        // ETX
        if (readC == 3)
            stringComplete = true;
    }
}

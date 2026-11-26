# Serial protocol

115200 baud, 8N1. One command per line (`\n` or `\r\n`). Commands are a
letter plus an optional number; case, spaces, `=` and `:` don't matter
(`N 42`, `n42` and `N=42` are the same). Numbers may have up to three
decimals.

| command | meaning | saved in EEPROM |
|---|---|---|
| `N <int>` | show this number (output = N ÷ gate) | – |
| `F <Hz>` | output a raw frequency, e.g. `F 1234.567` | – |
| `G <ms>` | the counter's gate time (default 1000) | yes |
| `C <ppm>` | crystal correction; + means the Arduino runs fast compared with the counter | yes |
| `R <Hz>` | reference clock on D2 (≤ 20 kHz), `R 0` = off | yes |
| `T 0/1/2` | test: 0 = off, 1 = count up, 2 = light every digit once | – |
| `?` | status | – |

Every command is answered with exactly one line:

```
OK NIXIE 1.0 mode=number N=12345 F=12345.000 G=1000 C=-12.500 R=0 max=100000
ERR too fast
```

`F` is the frequency actually requested (before the crystal correction). `C`
is in ppm. With a reference, `lock=` shows the measured crystal error in ppm,
or `lock=waiting` until the first 10-second measurement is in.

Lines starting with `#` are unsolicited notices (e.g. `# exercise done`) and
can be ignored. At power-up the board prints `NIXIE 1.0 READY`.

Limits on an ATmega328 at 16 MHz: 0.24 Hz to 100 kHz. 0 Hz stops the output
(the counter reads 00000).

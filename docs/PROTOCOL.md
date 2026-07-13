# Protocol notes

This records behavior verified against the connected panel and the open-source
`ttlcd` implementation. It is not an official vendor specification.

## Identity and interfaces

- Vendor ID `0x264A`; product ID `0x233D`
- Product `3.9 inch Bar Type TFT LCD Display Module-B`
- `MI_00`: 440-byte command output and response input payloads
- `MI_01`: 1,024-byte frame output and 16-byte acknowledgment input

Windows HID adds a leading report-ID byte, making application buffers 441,
1,025, and 17 bytes. Report ID is zero.

## Initialization

The command interface exchanges padded 440-byte messages with opcodes
`85`, `87`, `85`, `87`, `84`, `81`. Each begins `[opcode, 01, 00, 80]` and
waits for a response. Before the first frame, the host sends
`[12, 01, 00, 80, 64]` plus padding.

## JPEG transfer

1. Encode an exact 480×128 baseline/non-progressive JPEG.
2. Divide it into 1,020-byte chunks.
3. First header: `[08, packet_count, 00, 80]`.
4. Continuation header: `[08, packet_index, 00, 00]`.
5. Pad each header+chunk payload to 1,024 bytes.
6. Wait for acknowledgment before the next frame.

Animation uses a periodic `[82, 01, 00, 80]` command heartbeat.

## Safety choices

- Only exact VID/PID and interface matches are opened.
- Every response has a timeout.
- No firmware, flash, boot-animation, or unknown commands are sent.
- Microsoft's HID driver remains installed; WinUSB replacement is unnecessary.

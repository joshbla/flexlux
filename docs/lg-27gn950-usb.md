# LG 27GN950 USB connection

Reference notes on what the LG 27GN950's USB upstream port (the square USB 3.0 Type-B port beside DP-IN) offers a computer, gathered while looking for a way to control screen brightness through a KVM switch and USB-C hub.

## Findings

The monitor's USB connection carries no screen brightness control. Screen brightness travels only over DDC/CI on the video cable; see the README's KVM troubleshooting section for the tested video paths.

- The monitor appears as a USB device named "LG Monitor Controls" (vendor `0x043e`, product `0x9a8a`) behind the monitor's own TI USB hub.
- It exposes two vendor-defined HID interfaces, usage pages `0xFF00` and `0xFF01`, each with 64-byte input, output, and feature reports. Neither uses the standard USB monitor-control (HID brightness) interface that LG UltraFine and Apple displays implement, so tools such as `usb-hid-brightness` do not apply.
- The `0xFF00` feature report returns a device-information block containing the USB product ID and the monitor's serial number, consistent with LG's firmware-update channel.
- Changing brightness with the monitor's joystick produced no input reports on either interface.
- LG UltraGear Control Center (Windows), the LG app that requires this USB cable, controls only the rear LED lighting: modes, on/off, and LED brightness.
- LG OnScreen Control (Windows) read and changed screen brightness with the USB cable unplugged, so it uses DDC/CI. The macOS version showed only Screen Split through the KVM and hub.

## Lighting control: OpenRGB

OpenRGB has a driver for this monitor's lighting, written by Morgan Guimard and licensed GPL-2.0-or-later. The 27GN950 is listed in OpenRGB issue #1008 with the same USB IDs.

- Driver source: `Controllers/LGMonitorController/` in the OpenRGB repository (https://gitlab.com/CalcProgrammer1/OpenRGB, mirrored at https://github.com/calcprogrammer1/openrgb).
- Protocol summary from that driver: HID output reports of 65 bytes (report ID 0 plus 64 bytes). Each command starts with `S` `C` (`0x53 0x43`), then a command byte, a length/flag pair, the payload, an XOR checksum of the bytes after the report ID, and ends with `E` `D` (`0x45 0x44`). Commands include lighting power and LED brightness (`0xCF`), mode selection (`0xCA`), static slot colour (`0xCD`), and direct per-LED colour for 48 LEDs sent in three 64-byte packets (`0xC1`).
- Because the protocol is already published, using it would not require reverse engineering LG's software. OpenRGB's code is GPL, so copying it into FlexLux would make FlexLux's licensing a consideration; working from the protocol description avoids that question.

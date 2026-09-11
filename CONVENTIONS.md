# Host conventions — microReticulum_Firmware

Inventory of the patterns new code must follow. Written 2026-09-11 while
adding the TCP interface; spot-check against the code before relying on it.

## Layout & build
- Flat repo: every source file sits at the root, `build_src_filter = -<*> +<*.ino> +<*.cpp>` (platformio.ini). Headers hold most logic; `RNode_Firmware.ino` is the single translation unit that includes them in a fixed order (microReticulum first, then `Provisioning.h`, then interface headers, then `Pages.h`).
- Features are compile-time flags in `build_flags` per env: `-DLORA_TRANSPORT`, `-DUDP_TRANSPORT`, `-DTCP_TRANSPORT`, `-DENABLE_WEBSOCKETS`, `-DURTN_STATS_PAGES`. Board capabilities come from `Boards.h` (`HAS_WIFI`, `HAS_BLUETOOTH`, ...).
- Libraries are `lib_deps` git URLs, unpinned, per env (`https://github.com/attermann/microReticulum.git`). `*-local` envs use `symlink://../microReticulum`.
- GPL header block (Mark Qvist / Chad Attermann) at the top of every file; `#pragma once`.

## RNS interfaces
- One header per interface: `LoRaInterface.h`, `UDPInterface.h`, `TCPInterface.h`. Class `XInterface : public RNS::InterfaceImpl`, name ctor + default ctor delegating to it, destructor sets `_name = "deleted"`.
- `handle_incoming` / `send_outgoing` wrap `InterfaceImpl::handle_incoming` / `handle_outgoing` in `try` with `bad_alloc` and `std::exception` catches, logging via `ERROR`/`ERRORF` (UDPInterface.h:35-80).
- Runtime settings the interface needs are plain globals defined in its header (`udp_port` in UDPInterface.h:29; `tcp_mode`/`tcp_host`/`tcp_port` in TCPInterface.h) and `extern`'d wherever else they are read.
- Wrapper globals live in the .ino under the feature flag: `RNS::Interface udp_interface(RNS::Type::NONE);` (RNode_Firmware.ino:216). Created in setup only when `wifi_mode != WR_WIFI_OFF`, default `mode(MODE_GATEWAY)`, then `RNS::Transport::register_interface()` before `reticulum = RNS::Reticulum()` (RNode_Firmware.ino:1023-1075).
- `RNS::Interface` hides the impl; when host code needs impl-specific accessors keep a raw `XInterface*` alongside the wrapper (`tcp_impl`).

## WiFi lifecycle (Remote.h)
- `wifi_mode` (EEPROM `ADDR_CONF_WIFI`, values `WR_WIFI_OFF/STA/AP` from Config.h:46) drives `wifi_remote_init()` → `wifi_remote_start()`. Sockets that ride on WiFi begin/end inside `wifi_remote_start()`'s `wifi_initialized` branches (Remote.h:143-163).
- Per-loop servicing of WiFi transports goes in `update_wifi()` (Remote.h), called from `loop()` when `wifi_initialized`. There is no per-interface task; everything is cooperative in the superloop with a 60 s task WDT.
- `wifi_remote_init()` runs in setup *before* the RNS interfaces exist, so anything that must start once both WiFi and the interface are up is started again after `reticulum.start()` in the .ino (idempotent `start()`).

## Settings (Provisioning)
- Persistent settings go through `RNS::Provisioning` (Provisioning.cpp), never a parallel store. Namespaces and field IDs are `#define`s in Provisioning.h; **IDs are append-only, never renumber**.
- Interface `mode` is a `field_enum` under the General namespace with `FF_LIVE_APPLY` (Provisioning.cpp `PROV_GENERAL_UDP_MODE`). Network-level knobs go under `PROV_NS_NETWORK` with `FF_REBOOT_REQUIRED` and a setter that writes the global.
- Each interface gets a metrics namespace under Metrics > Interfaces registered with `interface.name()` and a `PROV_NS_IFACE_*` ID, guarded by both the feature flag and `if (interface)`.
- Radio settings remain EEPROM via rnodeconf; Provisioning mirrors them.

## Stats pages
- `Pages.h` builds the nomadnet JSON page; each interface adds its own `{ ... add_interface_details(...) }` block guarded by its flag and `wifi_mode != WR_WIFI_OFF`.

## Logging
- microReticulum macros only: `TRACE/TRACEF`, `INFO/INFOF`, `WARNING/WARNINGF`, `ERROR/ERRORF`, `HEAD(msg, level)`. Bare `printf` appears in setup diagnostics but not in interface code.

## Style
- Tabs for class-level indentation, two spaces inside method bodies (mixed in upstream; match the file you are in). `CBA` comments mark Chad Attermann's notes.

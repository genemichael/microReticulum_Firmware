import time
import hashlib
import shutil
import platform as platformlib

from firmware_image import esp_image_sha256, firmware_hash_kiss_frame

#
# Helpier functions
#

def get_target():

    # Detect the operating system
    platform_system = platformlib.system().lower()
    #print("System:", platform_system)
    if "linux" in platform_system:
        os_name = "linux"
    elif "darwin" in platform_system:
        os_name = "darwin"
    else:
        os_name = "unknown"

    # Get OS release details
    try:
        platform_os_info = platformlib.freedesktop_os_release()
        #print("OS Release:", platform_os_info)
        if platform_os_info.get('VERSION_CODENAME'):
            distro_name = platform_os_info.get('ID') + "-" + platform_os_info.get('VERSION_CODENAME')
        else:
            distro_name = platform_os_info.get('ID')
        os_name += "-" + distro_name
    except Exception:
        pass

    # Detect the architecture
    platform_machine = platformlib.machine().lower()
    #print("Machine:", platform_machine)
    if platform_machine == "x86_64":
        arch_name = "amd64"
    elif "aarch" in platform_machine or "arm64" in platform_machine:
        arch_name = "arm64"
    elif "arm" in platform_machine:
        arch_name = "armhf"
    else:
        arch_name = "unknown"

    return os_name + "-" + arch_name

#
# Custom targets
#

def target_package(target, source, env):
    print("*** Executing target_package steps...")
    print("Platform:", env.GetProjectOption("platform"))
    print("Board:", env.GetProjectOption("board"))
    print("Variant:", env.GetProjectOption("custom_variant"))
    #if env.GetProjectOption("custom_variant").endswith('_local'):
    #    print("*** Skipping target_package for local build")
    #    return
    # do some actions
    platform = env.GetProjectOption("platform")
    board = env.GetProjectOption("board")
    firmware_package(env)

#
# Upload actions
#

def pre_upload(source, target, env):
    print("*** Executing pre_upload steps...")
    # do some actions

def post_upload(source, target, env):
    print("*** Executing post_upload steps...")
    print("Platform:", env.GetProjectOption("platform"))
    print("Board:", env.GetProjectOption("board"))
    print("Variant:", env.GetProjectOption("custom_variant"))
    print("Serial port:", env.subst("$UPLOAD_PORT"))
    # do some actions
    platform = env.GetProjectOption("platform")
    board = env.GetProjectOption("board")
    if ("espressif32" in platform):
        time.sleep(10)
        verify_upload(source, env)
        # device provisioning is incomplete and only currently appropriate for 915MHz T-Beam
        #device_wipe(env)
        device_provision(env)
        firmware_hash(source, env)
        # firmware pacakaging is incomplete due to missing console image
        #firmware_package(env)
    elif ("nordicnrf52" in platform):
        time.sleep(10)
        verify_upload(source, env)
        # device provisioning is incomplete and only currently appropriate for 915MHz RAK4631
        #device_wipe(env)
        device_provision(env)
        time.sleep(5)
        firmware_hash(source, env)
        # firmware pacakaging is incomplete due to missing console image
        #firmware_package(env)

def pre_clean(env):
    print("*** Executing pre_clean steps...")
    print("Platform:", env.GetProjectOption("platform"))
    print("Board:", env.GetProjectOption("board"))
    print("Variant:", env.GetProjectOption("custom_variant"))
    project_dir = env.subst("$PROJECT_DIR")
    print("project_dir:", project_dir)
    env.Execute("rm -f " + project_dir + "/Release/" + env.subst("$PROGNAME") + ".zip")
    env.Execute("rm -f " + project_dir + "/Debug/" + env.subst("$PROGNAME") + ".elf")
    env.Execute("rm -f " + project_dir + "/Debug/" + env.subst("$PROGNAME") + ".map")
    env.Execute("rm -f " + project_dir + "/Release/" + env.subst("$PROGNAME") + "_debug.zip")

def full_clean(env):
    print("*** Executing full_clean steps...")
    project_dir = env.subst("$PROJECT_DIR")
    print("project_dir:", project_dir)
    env.Execute("rm -f " + project_dir + "/Release/release.json")

def device_wipe(env):
    # Device wipe
    print("--- Wiping Device ---")
    env.Execute("rnodeconf --eeprom-wipe " + env.subst("$UPLOAD_PORT"))

def device_set_firmware_hash(firmware_hash, env, boot_wait=4.0):
    import serial

    port_path = env.subst("$UPLOAD_PORT")
    frame = firmware_hash_kiss_frame(firmware_hash)
    print("Writing firmware hash directly over KISS (boot wait %.0fs)..." % boot_wait)
    with serial.Serial(port_path, 115200, timeout=0.1) as port:
        # Opening the port resets boards with native USB (Tracker V2, S3
        # boards) and USB-UART bridges alike. Drain startup output and wait
        # until setup() has reached the serial command loop; with trace
        # logging over a 115200-baud UART that can take well over 5 s.
        ready_at = time.monotonic() + boot_wait
        while time.monotonic() < ready_at:
            port.read(4096)
        for _ in range(2):
            port.write(frame)
            port.flush()
            time.sleep(1)


def expected_image_hash(source, env):
    """Return the SHA-256 (bytes) the device will report for the image that was
    just uploaded, computed the same way the firmware does, or None."""
    source_file = source[0].get_abspath()
    platform = env.GetProjectOption("platform")
    if platform == "nordicnrf52":
        build_dir = env.subst("$BUILD_DIR")
        env.Execute("cd " + build_dir + "; unzip -o " + source_file + " " + env.subst("$PROGNAME") + ".bin")
        data = open(build_dir + "/" + env.subst("$PROGNAME") + ".bin", "rb").read()
        return hashlib.sha256(data).digest()
    data = open(source_file, "rb").read()
    if env.GetProjectOption("custom_variant") in ("heltec_tracker_v2", "heltec_tracker_v2_local"):
        try:
            return esp_image_sha256(data)
        except ValueError:
            return None
    # ESP32 app images end with their own 32-byte SHA-256 over everything before it,
    # which is also what esp_partition_get_sha256() reports for the running app.
    return hashlib.sha256(data[0:-32]).digest()

def device_read_running_hash(env, boot_wait=12.0):
    """Ask the board for the SHA-256 of the firmware it is actually running
    (KISS CMD_HASHES 0x02). Returns 32 bytes, or None if it did not answer."""
    import serial
    FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
    port_path = env.subst("$UPLOAD_PORT")
    try:
        # DTR asserted on open: nRF52 native-USB boards only emit with DTR set;
        # UART-bridge boards reset on open, which boot_wait absorbs.
        with serial.Serial(port_path, 115200, timeout=0.2) as port:
            ready_at = time.monotonic() + boot_wait
            while time.monotonic() < ready_at:
                port.read(4096)
            for _ in range(5):
                port.reset_input_buffer()
                port.write(bytes([FEND, 0x60, 0x02, FEND]))
                port.flush()
                deadline = time.monotonic() + 2.0
                buf = b""
                while time.monotonic() < deadline:
                    buf += port.read(4096)
                for frame in buf.split(bytes([FEND])):
                    if len(frame) >= 3 and frame[0] == 0x60 and frame[1] == 0x02:
                        body = frame[2:].replace(bytes([FESC, TFEND]), bytes([FEND])).replace(bytes([FESC, TFESC]), bytes([FESC]))
                        if len(body) == 32:
                            return body
    except Exception as error:
        print("Could not read running firmware hash:", error)
    return None

def verify_upload(source, env):
    """Confirm the board is really running the image we just uploaded. A DFU or
    esptool session can fail (for example when another program holds the
    serial port) while the rest of the post-upload steps still succeed, which
    used to end in a misleading SUCCESS."""
    print("--- Verifying Upload ---")
    expected = expected_image_hash(source, env)
    if expected is None:
        print("Cannot compute expected image hash for this target; skipping verification.")
        return
    running = device_read_running_hash(env)
    if running is None:
        print("WARNING: board did not report its running firmware hash; upload NOT verified.")
        return
    print("expected:", expected.hex())
    print("running: ", running.hex())
    if running != expected:
        print("")
        print("*** UPLOAD DID NOT LAND: the board is still running a different image. ***")
        print("*** Check that no other program (RNode Console, a monitor) holds the  ***")
        print("*** serial port, then flash again. Skipping provisioning and hash.     ***")
        print("")
        env.Exit(1)
    print("Upload verified: board is running the new image.")

def device_provision(env):
    # Device provision
    print("--- Provisioning Device ---")
    platform = env.GetProjectOption("platform")
    print("Platform:", platform)
    board = env.GetProjectOption("board")
    print("Board:", board)
    variant = env.GetProjectOption("custom_variant")
    print("Variant:", variant)
    match variant:
        case "tbeam" | "tbeam_local":
            env.Execute("rnodeconf --product e0 --model e9 --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "lora32v21" | "lora32v21_local":
            env.Execute("rnodeconf --product b1 --model b9 --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "heltec32v4pa" | "heltec32v4pa_local":
            env.Execute("rnodeconf --product c3 --model c8 --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "heltec32v3" | "heltec32v3_local":
            # 915 MHz SX1262 variant (model 0xCA); 433 MHz boards are 0xC5.
            env.Execute("rnodeconf --product c1 --model ca --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "heltec_tracker_v2" | "heltec_tracker_v2_local":
            env.Execute("rnodeconf --product c4 --model cb --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "rak4631" | "rak4631_local":
            env.Execute("rnodeconf --product 10 --model 12 --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "rak3401" | "rak3401_local":
            env.Execute("rnodeconf --product 10 --model 14 --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "techo" | "techo_local":
            env.Execute("rnodeconf --product 15 --model 17 --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case "heltec_t114" | "heltec_t114_local":
            env.Execute("rnodeconf --product c2 --model c7 --hwrev 1 --rom " + env.subst("$UPLOAD_PORT"))
        case _:
            print(f"Unknown board variant {variant}, can not provision device!")

def firmware_hash(source, env):
    # Firmware hash
    print("--- Updating Firmware Hash ---")
    source_file = source[0].get_abspath()
    platform = env.GetProjectOption("platform")
    print("Platform:", platform)
    if (platform == "nordicnrf52"):
        build_dir = env.subst("$BUILD_DIR")
        env.Execute("cd " + build_dir + "; unzip -o " + source_file + " " + env.subst("$PROGNAME") + ".bin")
        #source_file.replace(".zip", ".bin")
        source_file = build_dir + "/" + env.subst("$PROGNAME") + ".bin";
        print("source_file:", source_file)
        firmware_data = open(source_file, "rb").read()
        calc_hash = hashlib.sha256(firmware_data).digest()
        hex_hash = calc_hash.hex()
        print("firmware_hash:", hex_hash)
        env.Execute("rnodeconf --firmware-hash " + hex_hash + " " + env.subst("$UPLOAD_PORT"))
    else:
        print("source_file:", source_file)
        firmware_data = open(source_file, "rb").read()
        if env.GetProjectOption("custom_variant") in ("heltec_tracker_v2", "heltec_tracker_v2_local"):
            try:
                calc_hash = esp_image_sha256(firmware_data)
            except ValueError as error:
                print(f"Unable to calculate firmware hash: {error}")
                return
            print("firmware_hash:", calc_hash.hex())
            device_set_firmware_hash(calc_hash, env)
        else:
            calc_hash = hashlib.sha256(firmware_data[0:-32]).digest()
            part_hash = firmware_data[-32:]
            hex_hash = calc_hash.hex()
            print("firmware_hash:", hex_hash)
            if calc_hash == part_hash:
                # rnodeconf sends one DETECT ~3 s after opening the port, which
                # resets UART-bridge boards; microReticulum is often still
                # booting then. Fall back to a raw KISS write with a long wait.
                rc = env.Execute("rnodeconf --firmware-hash " + hex_hash + " " + env.subst("$UPLOAD_PORT"))
                if rc != 0:
                    print("rnodeconf could not set the firmware hash, retrying over raw KISS...")
                    device_set_firmware_hash(calc_hash, env, boot_wait=12.0)
            else:
                print("Calculated hash does not match!")

def firmware_package(env):
    # Firmware package
    print("--- Packaging Firmware ---")
    platform = env.GetProjectOption("platform")
    print("Platform:", platform)
    board = env.GetProjectOption("board")
    print("Board:", board)
    variant = env.GetProjectOption("custom_variant")
    print("Variant:", variant)
    core_dir = env.subst("$CORE_DIR")
    print("core_dir:", core_dir)
    packages_dir = env.subst("$PACKAGES_DIR")
    print("packages_dir:", packages_dir)
    workspace_dir = env.subst("$WORKSPACE_DIR")
    print("workspace_dir:", workspace_dir)
    project_dir = env.subst("$PROJECT_DIR")
    print("project_dir:", project_dir)
    #build_dir = env.subst("$BUILD_DIR").get_abspath()
    build_dir = env.subst("$BUILD_DIR")
    print("build_dir:", build_dir)
    env.Execute("mkdir -p " + project_dir + "/Release")
    env.Execute("mkdir -p " + project_dir + "/Debug")
    if (platform == "espressif32"):
        #env.Execute("cp " + packages_dir + "/framework-arduinoespressif32/tools/partitions/boot_app0.bin " + build_dir + "/rnode_firmware_" + variant + ".boot_app0")
        env.Execute("cp ~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin " + build_dir + "/rnode_firmware_" + variant + ".boot_app0")
        env.Execute("cp " + build_dir + "/bootloader.bin " + build_dir + "/" + env.subst("$PROGNAME") + ".bootloader")
        env.Execute("cp " + build_dir + "/partitions.bin " + build_dir + "/" + env.subst("$PROGNAME") + ".partitions")
        env.Execute("rm -f " + project_dir + "/Release/" + env.subst("$PROGNAME") + ".zip")
        zip_cmd = "zip --junk-paths "
        zip_cmd += project_dir + "/Release/rnode_firmware_" + variant + ".zip "
        zip_cmd += project_dir + "/Release/esptool/esptool.py "
        zip_cmd += project_dir + "/Release/console_image.bin "
        zip_cmd += build_dir + "/" + env.subst("$PROGNAME") + ".bin "
        zip_cmd += build_dir + "/" + env.subst("$PROGNAME") + ".boot_app0 "
        zip_cmd += build_dir + "/" + env.subst("$PROGNAME") + ".bootloader "
        zip_cmd += build_dir + "/" + env.subst("$PROGNAME") + ".partitions "
        env.Execute(zip_cmd)
        env.Execute("cp " + build_dir + "/" + env.subst("$PROGNAME") + ".elf " + project_dir + "/Debug/.")
        env.Execute("cp " + build_dir + "/" + env.subst("$PROGNAME") + ".map " + project_dir + "/Debug/.")
        zip_cmd = "zip --junk-paths "
        zip_cmd += project_dir + "/Release/rnode_firmware_" + variant + "_debug.zip "
        zip_cmd += build_dir + "/" + env.subst("$PROGNAME") + ".elf "
        zip_cmd += build_dir + "/" + env.subst("$PROGNAME") + ".map "
        env.Execute(zip_cmd)
    elif (platform == "nordicnrf52"):
        env.Execute("cp " + build_dir + "/" + env.subst("$PROGNAME") + ".zip " + project_dir + "/Release/.")
    else:
        env.Execute("cp " + build_dir + "/" + env.subst("$PROGNAME") + " " + build_dir + "/rnoded")
        env.Execute("rm -f " + project_dir + "/Release/rnoded-" + get_target() + ".zip")
        zip_cmd = "zip --junk-paths "
        zip_cmd += project_dir + "/Release/rnoded-" + get_target() + ".zip "
        zip_cmd += build_dir + "/rnoded "
        zip_cmd += project_dir + "/rnoded.example.conf "
        zip_cmd += project_dir + "/rnoded.example.service "
        env.Execute(zip_cmd)
        get_target()
    env.Execute("python3 " + project_dir + "/release_hashes.py > " + project_dir + "/Release/release.json")

#
# Main script
#

Import("env")

env.Replace(PROGNAME="rnode_firmware_%s" % env.GetProjectOption("custom_variant"))
print("PROGNAME:", env.subst("$PROGNAME"))

print("*** Running custom script...")
platform = env.GetProjectOption("platform")
print("Platform:", platform)
targets = env.GetProjectOption("targets", [])
print("Targets:", targets)

# Clean
if env.IsCleanTarget():
    pre_clean(env)
    if "cleanall" in targets or "fullclean" in targets:
        full_clean(env)

# Add custom targets
if (platform == "espressif32"):
    env.AddCustomTarget(
        name="package",
        dependencies="$BUILD_DIR/${PROGNAME}.bin",
        actions=[
            target_package
        ],
        title="Package",
        description="Package esp32 firmware for delivery"
    )
elif (platform == "nordicnrf52"):
    # remove --specs=nano.specs to allow exceptions to work
    if '--specs=nano.specs' in env['LINKFLAGS']:
        env['LINKFLAGS'].remove('--specs=nano.specs')
    env.AddCustomTarget(
        name="package",
        dependencies="$BUILD_DIR/${PROGNAME}.zip",
        actions=[
            target_package
        ],
        title="Package",
        description="Package nrf52 firmware for delivery"
    )
else:
    env.AddCustomTarget(
        name="package",
        dependencies="$BUILD_DIR/${PROGNAME}",
        actions=[
            target_package
        ],
        title="Package",
        description="Package native daemon for delivery"
    )

# Register actions
env.AddPreAction("upload", pre_upload)
env.AddPostAction("upload", post_upload)

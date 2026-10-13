# KytyPS5 payload

sharpdroid now runs [KytyPS5](https://github.com/KytyPS5/KytyPS5) (C++, Vulkan 1.3 renderer, 2D and 3D
titles) as a payload next to SharpEmu. the host layer is unchanged: Kyty is a linux-x64 ELF executed
under FEXCore exactly as SharpEmu is. a build opts in with `"family": "kyty"` in `meta.json`.

## what was changed in Kyty (`external/kytyps5`, all behind `KYTY_HOST=android`)

| area | change |
| --- | --- |
| window | `SDL_VIDEO_DRIVER=offscreen`. SDL3's offscreen driver creates Vulkan surfaces with `VK_EXT_headless_surface`, which the vulkan thunk turns into a real `ANativeWindow` surface. window size comes from `KYTY_HOST_WINDOW_SIZE`; the game's video-out is scaled onto it |
| audio | `AndroidHost::AudioOut`: ring buffer plus one pump thread writing to AAudio (decoy `libaaudio.so`, write only, timeout 0). >2 channels are downmixed if the device refuses them |
| input | the pad bridge (`syscall(0x50440000+cmd)`, contract-4 64-byte format, port 1) is polled from the window loop and fed to `Controller::SetButton/SetAxis/SetTouchPad/SetSensor`. rumble goes back through command 2 |
| address space | android userspace is usually 39-bit (512 GiB). Kyty's 1 TiB layout is clamped to a 64..320 GiB guest window and the libc arena moves to 320..352 GiB, so the host keeps free address space. override with `KYTY_ADDR_USER_MIN/MAX`, `KYTY_ADDR_ARENA_FLOOR/LIMIT` (hex) |
| syscalls | `process_vm_readv` is not provided by the host layer; the readable-memory probe uses a pipe `write()` instead |
| data | `KYTY_DATA_DIR` (app `files/user/kyty`) becomes the working directory, so saves and caches leave the build directory |

launcher side: `MainActivity` sets `KYTY_HOST*`, `KYTY_HOST_WINDOW_SIZE`, `KYTY_DATA_DIR` and runs
`kyty_emulator --game <dir> --no-splash --present-mode Fifo` for the `kyty` family.

## building (GitHub Actions only)

everything is built by `.github/workflows/build.yml`: *Actions -> build -> Run workflow*. it
1. fetches Kyty's third-party sources at the commit this tree was cut from (`scripts/fetch-sources.py`; the
   uploaded archives were GitHub zips, so they carry no submodule pins),
2. builds the payload in a `debian:12` container and packages `kyty-*.zip`,
3. fetches FEX and libadrenotools at sharpdroid's pins, installs the pinned Android SDK pieces, builds the host layer
   and the APK with the Kyty build bundled, and uploads `sharpdroid-kyty.apk` plus the zip as artifacts.

step-by-step setup (Russian): `README-KYTY-RU.md`.

## status: not run on a device

this tree was assembled without an NDK, network or device. nothing here has been compiled or booted. expect first-run
fixes. things to check first, in the launch log:

1. `[android] 39-bit address space detected` and the guest layout line; if Kyty exits with
   "failed to reserve guest address space", adjust the `KYTY_ADDR_*` variables.
2. Vulkan device selection. Kyty requires Vulkan 1.3 and VK_EXT_depth_clip_control, image_view_min_lod,
   push_descriptor, depth_clip_enable, color_write_enable and fragment_shader_barycentric. a driver without them is
   rejected with the reason printed ("Could not find suitable device"). Turnip is the likelier fit than the stock Adreno driver.
3. 3D needs the GPU to import guest memory; if a driver lacks the needed host-memory features, the
   renderer reports it at startup.
4. `[pad] the guest is polling pad state` and `AAudio stream:` lines confirm input and audio.

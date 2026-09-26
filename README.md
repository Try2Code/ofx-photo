# ofx-photo

A minimal [OpenFX](https://openeffects.org/) host for applying OFX plugins to **still
photographs** on Linux, plus a command line tool and a small GUI built on top of it.

Written to run the [spektrafilm](https://spektrafilm.114c.de/) film-emulation plugin on
Linux, where the vendor ships the plugin but the standalone photo application is macOS
only, and the documented hosts (DaVinci Resolve Studio, Nuke) are expensive video tools
that are awkward for single images.

> **Status: working.** Verified against spektrafilm v0.2 on Intel Iris Xe / Mesa Vulkan:
> 830 parameters, all 88 bundled presets, and preset save/load round-trips.

## Why this exists

OFX is a *plugin* API, so a plugin needs a host to run inside. There is no simple,
free, photo-oriented OFX host on Linux. This is one.

It turns out to be a small job for this class of plugin:

- the plugin is a plain **Filter**-context effect — one input, one output
- it declares **CPU render support**, so the host only has to hand it float buffers
- any GPU work it does is its own business, internal and invisible to the host
- it references only 7 OFX suites: Property, Parameter, ImageEffect, Memory,
  MultiThread, Message and Progress

## Design

```
 photo.jpg ──┐
 photo.NEF ──┤  spektra (Python)
 photo.tif ──┘   · decode (Pillow / dcraw_emu)
                 · sRGB → linear, orientation, alpha
                 · resolve preset + parameter overrides
                       │
                       │  .sfraw  (float32 RGBA, y-up)
                       ▼
                 spektra-render (C++)
                  · dlopen the .ofx bundle
                  · describe → describeInContext(Filter) → createInstance
                  · set parameters, bind clips, render one frame
                       │
                       │  .sfraw
                       ▼
                 spektra (Python)
                  · linear → output transfer, quantise
                  · write JPEG / PNG / TIFF
```

The host runs as a **one-shot child process**. The plugin initialises Vulkan and
allocates sizeable GPU resources, so a fresh process per render guarantees clean teardown
and keeps a plugin crash from taking the GUI down with it.

The GUI and CLI build their parameter lists **dynamically**, from what the plugin reports
at describe time. No parameter names are hard-coded, so the tools survive plugin updates.

## Requirements

- Linux x86_64, glibc 2.28+
- A C++17 compiler and CMake
- Vulkan 1.2 loader and a working GPU driver (for the spektrafilm plugin specifically)
- Python 3 with `numpy`, `Pillow`, and `PyQt5` for the GUI
- `libraw-bin` (`dcraw_emu`) for camera RAW input — optional

## The plugin is not included

**No plugin binaries are in this repository, by design.** Obtain the plugin from its
vendor and install it normally, then point the tools at it:

```sh
spektra photo.jpg out.jpg --preset Marty_Warm \
    --bundle /usr/OFX/Plugins/spektrafilm.ofx.bundle
```

The bundle is located automatically if it sits in `/usr/OFX/Plugins`, `~/OFX/Plugins`, or
anywhere on `$OFX_PLUGIN_PATH`.

## Presets

For spektrafilm specifically, preset save and load are implemented **inside the plugin**
and exposed as ordinary OFX parameters. This project therefore contains no preset
serialisation code at all — it sets a dropdown and presses a button. Presets saved here
are standard `.spkpreset` files and remain usable in any other host.

## Usage

```sh
cmake -S . -B build && cmake --build build      # build the host

./spektra --list-presets                        # 88 bundled, plus your own
./spektra --list-params --grep grain            # search 830 parameters
./spektra photo.jpg out.jpg --preset "Marty - Warm"
./spektra photo.NEF out.tif --preset "CineStill 800T" --bit-depth 16
./spektra photo.jpg out.jpg --set technicolorGrainAmount=1.7
./spektra shots/*.NEF outdir/ --preset "Vintage Faded" --jobs 2
./spektra-gui photo.jpg                         # the window
```

A 2560×1709 JPEG takes roughly two seconds on integrated graphics.

Preset names are matched loosely and suggest alternatives when wrong, which helps: the
real names are easy to mistype (`Chromium-Noir`, `CineStill 800T`, `Marty - Warm`).

## Notes

**Grain is stochastic.** Two identical renders differ by ~0.0006 mean absolute error.
That is the film grain, not a bug.

**The preview is downscaled** to 1100 px on the long edge so the controls stay
responsive. Grain and halation are resolution dependent, so they will not look exactly
like the full render — use **Render full resolution** before saving to check.

**Only parameters you actually change are sent** to the plugin. Sending them all would
re-apply defaults over whatever preset was just loaded, which silently destroys the look.

## Roadmap

- [x] OFX property sets, parameter, clip and image model (`src/host.h`)
- [x] All 7 OFX suites (`src/suites.cpp`)
- [x] Action sequence: load → describe → createInstance → render (`src/host.cpp`)
- [x] `spektra-render` CLI and `.sfraw` I/O (`src/main.cpp`)
- [x] `spektra` — photo formats, colour handling, presets, batch
- [x] `spektra-gui` — preview, presets, dynamic parameter panel
- [ ] The `_diffuse` and `_lens` companion plugins
- [ ] 16-bit and OpenEXR output beyond TIFF/PNG

## Licence

GPL-3.0 — see [LICENSE](LICENSE).

`ofx-include/` is the OpenFX API, copyright OpenFX and contributors, redistributed
unmodified under BSD-3-Clause; see [ofx-include/LICENSE.md](ofx-include/LICENSE.md).

## Not affiliated

This is an independent, unofficial project. It is not affiliated with, endorsed by, or
sponsored by the authors of any plugin it can run, including spektrafilm and Aedan Diez.
No trademark rights are claimed or granted. Plugin names appear only to describe
interoperability.

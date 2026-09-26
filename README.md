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
./install-desktop.sh                            # menu entry and "Open With"
```

### Desktop launcher

`./install-desktop.sh` adds a **Spektra Photo** entry to the applications menu and to the
*Open With* list for JPEG, PNG, TIFF and common raw formats. Everything is written under
`$HOME`, so it needs no root and installs nothing system-wide; `--uninstall` removes it.

To make it the default for a format:

```sh
xdg-mime default ofx-photo.desktop image/x-nikon-nef
```

### The companion plugins

The family ships four bundles. They are **one engine with different defaults**, not four
different effects, which is worth knowing before reaching for them:

| Short name | Differs from `spektrafilm` by |
|---|---|
| `film` (default) | — |
| `diffuse` | camera diffusion on, spatial scale 35 |
| `lens` | only a Resolve-oriented default colourspace |
| `flow` | motion based, of little use for stills |

So `--bundle lens` on its own does nothing that the main plugin cannot; a **bare lens pass
is a verified no-op**. What the companions add is the ability to run *more than one pass*,
the way a node graph would:

```sh
./spektra photo.jpg out.jpg --preset "Marty - Warm" --chain diffuse
./spektra photo.jpg out.jpg --bundle diffuse --preset "OIL!"
```

A pass only bites once its effects are switched on, which is easiest to express in a
session:

```json
"chain": [
  {"bundle": "lens",
   "params": {"quickVignetteEnabled": "true",
              "quickVignettePreset": "Vintage Mechanical"}}
]
```

Each pass reads in whatever encoding the pass before it wrote. In the GUI there are
**Diffuse pass** and **Lens pass** tick boxes, with vignette and distortion dropdowns,
since a bare lens pass would otherwise look broken.

A 2560×1709 JPEG takes roughly two seconds on integrated graphics.

### Sessions: the GUI as JSON

Everything the window holds is a session, so a look can be exported from the GUI and
replayed from the terminal, byte for byte:

```sh
./spektra photo.jpg out.jpg --preset "OIL!" --seed 7 --dump-session look.json
./spektra --session look.json
./spektra --session '{"input":"a.jpg","output":"b.jpg","seed":7}'   # or inline
./spektra --session - < look.json                                   # or stdin
./spektra-gui look.json                                             # back into the GUI
```

In the GUI: **Export session…** / **Import session…**, and `SPEKTRA_SESSION_OUT=/tmp/s.json`
rewrites the session on every preview, so you can watch what the window is doing.

`--seed N` pins every random seed. The plugin randomises grain, gate weave, flicker and
dust seeds per instance, so without it two runs of the same settings differ slightly;
with it they are identical, which is what makes automated comparison possible.

`--info` reports EXIF, ICC and the decoded linear statistics — the first thing to check
when a render looks wrong.

## Tests

```sh
tests/test_spektra.py                                   # synthetic chart
tests/test_spektra.py --photo p.jpg --raw p.NEF         # your own files
tests/test_spektra.py --quick -k session                # narrow it down
```

77 checks across the host, presets, determinism, parameter overrides, sessions,
colourspaces, output encoding, chained passes, image I/O, raw scaling, ICC handling and
the GUI model. The GUI runs offscreen, so the whole suite needs no display and no
clicking.

Preset names are matched loosely and suggest alternatives when wrong, which helps: the
real names are easy to mistype (`Chromium-Noir`, `CineStill 800T`, `Marty - Warm`).

## Notes

**The plugin returns display-encoded pixels**, not linear ones. It applies its own output
transform, so what comes back is already sRGB. Encoding it again lifts shadows by up to
70 levels out of 255.

**Renders vary between runs unless you pin the seed.** The plugin randomises its grain,
gate weave, flicker and dust seeds for each new instance. `--seed N` fixes all of them
and makes output byte-for-byte repeatable.

**The preview is downscaled** to 1100 px on the long edge so the controls stay
responsive. Grain and halation are resolution dependent, so they will not look exactly
like the full render — use **Render full resolution** before saving to check.

**Choosing a preset for an effect switches that effect on.** The plugin keeps the two
separate, so picking a vignette preset while vignette is off does nothing at all; the GUI
now arms it for you and says so in the status bar.

**Only parameters you actually change are sent** to the plugin. Sending them all would
re-apply defaults over whatever preset was just loaded, which silently destroys the look.

## Roadmap

- [x] OFX property sets, parameter, clip and image model (`src/host.h`)
- [x] All 7 OFX suites (`src/suites.cpp`)
- [x] Action sequence: load → describe → createInstance → render (`src/host.cpp`)
- [x] `spektra-render` CLI and `.sfraw` I/O (`src/main.cpp`)
- [x] `spektra` — photo formats, colour handling, presets, batch
- [x] `spektra-gui` — preview, presets, dynamic parameter panel
- [x] Session JSON shared by the CLI and the GUI, with reproducible seeds
- [x] Automated tests that need no display
- [x] The `_diffuse` and `_lens` companion plugins, as chained passes
- [x] Desktop launcher and "Open With" integration
- [ ] OpenEXR output

## Licence

GPL-3.0 — see [LICENSE](LICENSE).

`ofx-include/` is the OpenFX API, copyright OpenFX and contributors, redistributed
unmodified under BSD-3-Clause; see [ofx-include/LICENSE.md](ofx-include/LICENSE.md).

## Not affiliated

This is an independent, unofficial project. It is not affiliated with, endorsed by, or
sponsored by the authors of any plugin it can run, including spektrafilm and Aedan Diez.
No trademark rights are claimed or granted. Plugin names appear only to describe
interoperability.

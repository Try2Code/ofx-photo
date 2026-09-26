# ofx-photo

A minimal [OpenFX](https://openeffects.org/) host for applying OFX plugins to **still
photographs** on Linux, with a command line tool and a small GUI on top.

Written to run the [spektrafilm](https://spektrafilm.114c.de/) film-emulation plugin,
where the vendor ships the plugin for Linux but the standalone photo application is macOS
only, and the documented hosts — DaVinci Resolve Studio and Nuke — are expensive video
tools that are awkward for single images.

```sh
cmake -S . -B build && cmake --build build     # build the host
./install-desktop.sh                           # menu entry and "Open With"
./spektra-gui photo.NEF                        # or just open it from the menu
```

---

## Contents

- [Why this exists](#why-this-exists) · [Install](#install) · [The plugin is not included](#the-plugin-is-not-included)
- [The GUI](#the-gui) · [The command line](#the-command-line) · [Sessions](#sessions-the-gui-as-json)
- [Presets](#presets) · [Extra passes](#extra-passes-the-companion-plugins) · [Colour](#colour-what-goes-in-and-what-comes-out)
- [Raw files](#raw-files) · [Tests](#tests) · [How it works](#how-it-works) · [Gotchas](#gotchas-worth-knowing)

---

## Why this exists

OFX is a *plugin* API, so a plugin needs a host to run inside, and there is no simple,
free, photo-oriented OFX host on Linux. This is one.

It turns out to be a small job for this class of plugin:

- it is a plain **Filter**-context effect — one input, one output
- it declares **CPU render support**, so the host only has to hand it float buffers
- any GPU work is its own business, internal and invisible to the host
- it references only 7 OFX suites: Property, Parameter, ImageEffect, Memory, MultiThread,
  Message and Progress

## Install

Requirements:

| | |
|---|---|
| Linux x86_64, glibc 2.28+ | |
| C++17 compiler and CMake | builds `spektra-render` |
| Vulkan 1.2 and a working driver | required by the spektrafilm plugin itself |
| Python 3 with `numpy`, `Pillow` | the CLI |
| `PyQt5` | the GUI |
| `libraw-bin` (`dcraw_emu`) | camera raw, optional |
| ImageMagick (`convert`) | 16-bit output and ICC conversion, optional |

```sh
cmake -S . -B build && cmake --build build
./tests/test_spektra.py                        # check it works
./install-desktop.sh                            # --uninstall reverses it
```

`install-desktop.sh` adds a **Spektra Photo** entry to the applications menu and to the
*Open With* list for JPEG, PNG, TIFF and the common raw formats. It writes only under
`$HOME` and needs no root. To make it the default for a format:

```sh
xdg-mime default ofx-photo.desktop image/x-nikon-nef
```

## The plugin is not included

**No plugin binaries are in this repository, by design.** They belong to their vendor, and
their resources exceed GitHub's file size limit. Obtain the plugin separately and install it. The
tools search, in order:

1. every entry of **`$OFX_PLUGIN_PATH`** — set this and it always wins
2. `/usr/OFX/Plugins`, `/usr/local/OFX/Plugins`, `/opt/OFX/Plugins`
3. `~/OFX/Plugins`, `~/.OFX/Plugins`, `~/.local/share/OFX/Plugins`, `~/local/OFX`, `~/.local/OFX`
4. beside this checkout, which is where an unpacked download sits

Each is tried directly and with a `Plugins/` subdirectory. Check which one won with
`./spektra --info any.jpg` or simply override it:

```sh
./spektra photo.jpg out.jpg --bundle /usr/OFX/Plugins/spektrafilm.ofx.bundle
```

---

## The GUI

```sh
./spektra-gui                 # empty, then Open…
./spektra-gui photo.NEF       # straight to a photo
./spektra-gui look.json       # straight to a saved session
```

| Control | What it does |
|---|---|
| ☀ / ☾ (far left) | dark or light theme, remembered between runs |
| **Open…** | JPEG, PNG, TIFF, and raw; drag and drop works |
| **Hold to compare** | press and hold to see the untouched original |
| **Category / Preset** | the 88 bundled presets |
| **Yours** | presets you saved, from `~/Documents/spektrafilm/presets` |
| **Input** | input colourspace; `Linear Rec.709` suits photos and raw |
| **Save preset… / Reset** | store the current look; drop your edits |
| **Pin seed** | fix every random seed so renders repeat exactly |
| **Export / Import session** | the whole window state as JSON |
| **EV bias** | undo the camera's exposure compensation; **raw only**, and greyed out with the reason for anything else |
| **Diffuse pass / Lens pass** | extra passes through the companion plugins |
| **Show every parameter** | all 830, grouped and collapsed |
| **Render full resolution** | the preview is downscaled; this is the real thing |

Numeric parameters are **sliders** with a value readout and a ↺ button that returns them
to whatever the preset said. The button greys out when the value is already there, so you
can see at a glance what you have changed.

Groups fold. **Quick Access, Color Management, Film, Print** and **Grain** are open by
default — 61 controls; the remaining ~800 are one tick box away. **Effects** sits at the
bottom, being a long list of optional extras.

## The command line

```sh
./spektra in.jpg out.jpg --preset "Marty - Warm"
./spektra in.NEF out.tif --preset "CineStill 800T" --bit-depth 16
./spektra in.jpg out.jpg --set filmExposureEv=1.5 --set quickGrainEnabled=false
./spektra in.NEF out.jpg --raw-ev-bias                    # undo -1.67 EV, say
./spektra shots/*.NEF outdir/ --preset "Vintage Faded" --jobs 2
```

Finding your way around 830 parameters:

```sh
./spektra --list-presets                  # 88 bundled, plus your own
./spektra --list-params --grep grain      # search names, labels and hints
./spektra --list-stocks film              # which films each Stock Category offers
./spektra --list-stocks print             # and which papers
./spektra --info photo.NEF                # EXIF, ICC, and decoded statistics
```

`--info` is the first thing to reach for when a render looks wrong: it reports what the
file claims to be and what it actually decoded to.

| Option | |
|---|---|
| `--preset NAME` | a bundled preset, matched loosely, with suggestions when wrong |
| `--user-preset NAME` | one of your own |
| `--save-preset NAME` | store the current settings as a new one |
| `--set NAME=VALUE` | any of the 830 parameters, repeatable |
| `--bundle NAME\|PATH` | `film`, `diffuse`, `lens`, `flow`, or a path |
| `--chain NAME[:PRESET]` | run another pass afterwards, repeatable |
| `--seed N` | pin every random seed |
| `--preview [PX]` | render small and fast, as the GUI preview does |
| `--raw-ev-bias` | undo the camera's EV compensation on raw |
| `--input-colorspace` | override the input transform |
| `--bit-depth 8\|16`, `--quality N` | output |
| `--jobs N` | images in parallel |
| `--session SPEC`, `--dump-session` | see below |

Preset names are matched loosely and suggest alternatives when wrong, which helps: the
real ones are easy to mistype — `Chromium-Noir`, `CineStill 800T`, `Marty - Warm`, `OIL!`.

## Sessions: the GUI as JSON

Everything the window holds is a session, so a look can leave the GUI and be replayed from
the terminal, **byte for byte**:

```sh
./spektra photo.jpg out.jpg --preset "OIL!" --seed 7 --dump-session look.json
./spektra --session look.json
./spektra --session '{"input":"a.jpg","output":"b.jpg","seed":7}'    # inline
./spektra --session - < look.json                                    # stdin
./spektra-gui look.json                                              # back to the GUI
```

```json
{
  "input": "photo.NEF",
  "output": "out.jpg",
  "bundle": "spektrafilm",
  "preset": {"category": "Creative", "selection": "Marty - Warm"},
  "user_preset": null,
  "input_colorspace": "Linear Rec.709",
  "output_colorspace": "sRGB",
  "params": {"filmExposureEv": "1.5"},
  "seed": 7,
  "raw_exposure_bias": true,
  "chain": [{"bundle": "lens",
             "params": {"quickVignetteEnabled": "true",
                        "quickVignettePreset": "Vintage Mechanical"}}],
  "preview": {"long_edge": 1100},
  "output_options": {"bit_depth": 8, "quality": 95}
}
```

In the GUI: **Export session… / Import session…**. And to watch it live:

```sh
SPEKTRA_SESSION_OUT=/tmp/s.json ./spektra-gui     # rewritten on every preview
./spektra --session /tmp/s.json                   # replay whatever it is doing now
```

A session may carry any of the 830 parameters, including ones the panel does not show;
those survive a round trip through the GUI untouched.

## Presets

Preset save and load are implemented **inside the plugin**, so this project contains no
preset serialisation at all — it sets a dropdown and presses a button. Presets you save
are ordinary `.spkpreset` files in `~/Documents/spektrafilm/presets`, usable in any other
host.

88 come bundled: 1 Clean Slate, 47 Creative, and 40 Neutral film/print stock pairings such
as `Kodak Vision3 250D on 2383` and `Ilford HP5 Plus on Ilfobrom Galerie FB K1`.

```sh
./spektra --list-presets
./spektra in.jpg out.jpg --preset "Chromium-Noir"
./spektra in.jpg out.jpg --set … --save-preset "My Look"
./spektra in.jpg out.jpg --user-preset "My Look"
```

## Extra passes: the companion plugins

The family ships four bundles. They are **one engine with different defaults**, not four
different effects:

| Short name | Differs from `spektrafilm` by |
|---|---|
| `film` (default) | — |
| `diffuse` | camera diffusion on, spatial scale 35 |
| `lens` | only a Resolve-oriented default colourspace |
| `flow` | motion based, of little use for stills |

So `--bundle lens` alone does nothing the main plugin cannot, and a **bare lens pass is a
verified no-op**. What they add is running *more than one pass*, as a node graph would:

```sh
./spektra photo.jpg out.jpg --preset "Marty - Warm" --chain diffuse
./spektra photo.jpg out.jpg --bundle diffuse --preset "OIL!"
```

### The companion bundles are stripped

`_lens` and `_diffuse` ship **without** the spectral data and without the preset library:

| | `spektrafilm` | `_flow` | `_lens` | `_diffuse` |
|---|---|---|---|---|
| `SpektraSpectralUpsampling.f32` (103 MB) | ✅ | ✅ | ✗ | ✗ |
| `SpektraHanatos2025Spectra.f32` (12 MB) | ✅ | ✅ | ✗ | ✗ |
| `SpektraOutputGamutCompression.f32` | ✅ | ✅ | ✗ | ✗ |
| bundled presets | 88 | 88 | 0 | 0 |

Two consequences:

- **`--preset` mostly does not work against `lens` or `diffuse`.** They offer 6 entries
  rather than 88, so a name from the main library will not resolve. Drive them with
  `--set` and per-pass `params` instead, which is what `--chain` does anyway.
- **One unexplained failure.** A save once reported `Unable to locate
  SpektraHanatos2025Spectra.f32 for Vulkan Hanatos RGB-to-raw`, and the missing files make
  these bundles the obvious suspect — but that was not reproducible. All four RGB-to-Raw
  methods, Hanatos included, render from `lens` and `diffuse` here, even with the bundle
  copied somewhere on its own with no sibling beside it, so the plugin finds the data by
  some route that is not documented and not the `~/.cache/spektrafilm` pipeline cache. If
  you hit it, export the session: it can then be replayed exactly.

A pass only bites once its effects are switched on, which is easiest to express in a
session:

```json
"chain": [{"bundle": "lens",
           "params": {"quickVignetteEnabled": "true",
                      "quickVignettePreset": "Vintage Mechanical"}}]
```

Each pass reads in whatever encoding the pass before it wrote.

## Colour: what goes in and what comes out

The plugin expects **scene-linear** input and returns **display-encoded** output.

Going in, the tools hand it linear pixels and set the input colourspace to match:

- **JPEG, PNG, TIFF** — sRGB decoded to linear. An embedded ICC profile that is not sRGB
  (Adobe RGB, ProPhoto) is converted first; otherwise those files would be read as sRGB
  and every colour would shift.
- **Raw** — already linear from the decoder, with the camera's white balance applied.

Coming out, the plugin applies its own output transform, so what it returns is already
sRGB. The writers hand those pixels straight to the file. **Encoding them a second time
lifts shadows by up to 70 levels out of 255**, which is easy to do by accident and looks
merely "bright and filmic" rather than obviously broken.

## Raw files

Raw is decoded by `dcraw_emu` (`libraw-bin`) to linear 16-bit with camera white balance.

Raw and the camera's own JPEG of the same frame **will not match**, and the raw is the
more correct input. The JPEG has the camera's Picture Control baked in — a contrast curve,
highlight rolloff and baseline gain — which cannot be undone by linearising. Measured on
one frame:

| percentile | JPEG vs raw |
|---|---|
| p25 (shadows) | −0.46 stops |
| p50 (midtones) | −0.02 stops |
| p90 | +1.47 stops |
| p99 (highlights) | +2.16 stops |

Shadows down, midtones pinned, highlights lifted: a tone curve, not a brightness offset.
So a raw file looks flat and dark until you place the exposure yourself, either with the
**Exposure EV** slider in the Film group or:

```sh
./spektra photo.NEF out.jpg --set filmExposureEv=1.67
```

If the shot carries an exposure compensation, **EV bias** / `--raw-ev-bias` undoes it, so
a frame shot at −1.67 EV lands where its JPEG would. It applies to raw only: a JPEG
already carries that compensation in its rendering, and undoing it there would apply it
twice.

## Tests

```sh
./tests/test_spektra.py                                 # synthetic chart
./tests/test_spektra.py --photo p.jpg --raw p.NEF       # your own files
./tests/test_spektra.py --quick -k session              # narrow it down
```

126 checks across the host, presets, determinism, parameter overrides, sessions, output
encoding, chained passes, exposure bias, image I/O, raw scaling, ICC handling and the GUI
model. **The GUI runs offscreen**, so the whole suite needs no display and no clicking.

They earn their keep: the first run found 16-bit output silently writing 8-bit, and
sessions silently dropping 806 of 830 parameters.

## How it works

```
 photo.jpg ──┐
 photo.NEF ──┤  spektra (Python)
 photo.tif ──┘   · decode (Pillow / dcraw_emu), ICC to sRGB
                 · sRGB → linear, orientation, optional EV bias
                 · resolve preset and parameter overrides
                       │
                       │  .sfraw  (float32 RGBA, y-up)
                       ▼
                 spektra-render (C++)
                  · dlopen the .ofx bundle
                  · describe → describeInContext(Filter) → createInstance
                  · set parameters, bind clips, render one frame
                       │
                       │  .sfraw, repeated for each chained pass
                       ▼
                 spektra (Python)
                  · quantise, write JPEG / PNG / TIFF
```

The host runs as a **one-shot child process**: the plugin initialises Vulkan and allocates
sizeable GPU resources, so a fresh process per render guarantees clean teardown and keeps
a plugin crash from taking the GUI down with it.

Both the GUI and the CLI build their parameter lists from what the plugin reports at
describe time, and share one render pipeline. **No parameter names are hard-coded**, so
the tools survive plugin updates.

## Gotchas worth knowing

**Renders vary between runs unless you pin the seed.** The plugin randomises its grain,
gate weave, flicker and dust seeds for each new instance. `--seed N` fixes all of them.

**Choosing a preset for an effect switches that effect on.** The plugin keeps the preset
and the enable as separate parameters, so picking a vignette preset while vignette is off
does nothing at all. The GUI arms it for you and says so.

**The preview is downscaled** to 1100 px. Grain and halation are resolution dependent, so
use **Render full resolution** before saving to see the real thing.

**Some dropdowns rewrite others.** Choosing a Stock Category replaces the Stock list —
Motion Picture offers 14 films, B&W Still Film 6 — and a Preset Category replaces the
Preset list. A host that caches those options at describe time shows films from the wrong
category; the same stale menu appears in Natron. All three such dependencies are covered,
and `--list-stocks` prints what each category actually offers.

**Only parameters you actually change are sent** to the plugin. Sending them all would
re-apply defaults over whatever preset was just loaded.

**Output Role is withheld** from the panel: it switches the plugin to an HDR transfer that
the 8- and 16-bit writers cannot represent, so it would quietly produce a wrong file
rather than an HDR one.

**Pillow cannot handle 16-bit RGB**, reading or writing — it silently truncates to 8 bits.
Both paths go through PPM and ImageMagick instead. Worth remembering if you extend this.

## Roadmap

- [x] OFX property sets, parameter, clip and image model (`src/host.h`)
- [x] All 7 OFX suites (`src/suites.cpp`)
- [x] Action sequence: load → describe → createInstance → render (`src/host.cpp`)
- [x] `spektra-render` and `.sfraw` I/O (`src/main.cpp`)
- [x] `spektra` — photo formats, colour, presets, batch
- [x] `spektra-gui` — preview, presets, sliders, themes, dynamic panel
- [x] Session JSON shared by both, with reproducible seeds
- [x] Tests that need no display
- [x] The companion plugins, as chained passes
- [x] Desktop launcher and "Open With"
- [ ] OpenEXR and HDR output
- [ ] Render only the visible crop when zoomed in
- [ ] Batch progress and resumable runs

## Licence

GPL-3.0 — see [LICENSE](LICENSE).

`ofx-include/` is the OpenFX API, copyright OpenFX and contributors, redistributed
unmodified under BSD-3-Clause; see [ofx-include/LICENSE.md](ofx-include/LICENSE.md).

## Not affiliated

This is an independent, unofficial project, not affiliated with, endorsed by, or sponsored
by the authors of any plugin it can run, including spektrafilm and Aedan Diez. No
trademark rights are claimed or granted. Plugin names appear only to describe
interoperability.

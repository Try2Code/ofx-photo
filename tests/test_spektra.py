#!/usr/bin/env python3
"""Exercise the host, the CLI and the GUI's model without opening a window.

    tests/test_spektra.py                 # the standard sweep
    tests/test_spektra.py --quick         # fewer combinations
    tests/test_spektra.py -k preset       # only matching tests
    tests/test_spektra.py --photo P.NEF   # use your own photo

Every render pins its seeds, so results are byte-for-byte comparable.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import itertools
import json
import os
import struct
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_loader(
    "cli", importlib.machinery.SourceFileLoader("cli", str(ROOT / "spektra")))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

SEED = 1234
PASS, FAIL = "ok", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    results.append((PASS if condition else FAIL, name, detail))
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f"   {detail}" if detail else ""),
          flush=True)
    return condition


def synthetic_photo(path: Path, w: int = 320, h: int = 240) -> None:
    """A chart with a neutral ramp and primary patches, as a linear PNG source."""
    a = np.zeros((h, w, 3), np.float32)
    a[:] = np.linspace(0, 1, w)[None, :, None]
    for i, rgb in enumerate([(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (0, 1, 1), (1, 0, 1)]):
        a[10:50, 10 + i * 48: 50 + i * 48] = np.array(rgb, np.float32) * 0.8
    from PIL import Image
    Image.fromarray((cli.linear_to_srgb(a) * 255 + 0.5).astype(np.uint8)).save(path)


class Harness:
    def __init__(self, photo: Path, bundle: str | None):
        self.rend = cli.Renderer(cli.find_renderer(), cli.find_bundle(bundle))
        self.photo = photo
        self.linear, self.cs, _ = cli.load_photo(photo)
        self.small = cli.resize_linear(self.linear, 256)

    def render(self, session: dict) -> np.ndarray:
        return self.rend.render(self.small, cli.session_to_sets(session))

    def session(self, **kw) -> dict:
        s = cli.default_session()
        s["seed"] = SEED
        s["input_colorspace"] = self.cs
        for k, v in kw.items():
            s[k] = v
        return s


# ------------------------------------------------------------------- the tests


def test_host_loads(h: Harness):
    ids = h.rend.describe.__self__.params
    check("host describes parameters", len(ids) > 100, f"{len(ids)} parameters")
    types = {p["type"] for p in ids}
    check("parameter types look sane", "OfxParamTypeChoice" in types, f"{len(types)} types")


def test_presets_enumerate(h: Harness):
    cats = h.rend.by_name()[cli.P_CATEGORY]["choices"]
    check("preset categories present", len(cats) >= 2, f"{len(cats)} categories")
    total = 0
    for c in cats:
        names = h.rend.by_name([(cli.P_CATEGORY, c)])[cli.P_SELECTION]["choices"]
        total += len(names)
        if not check(f"category {c!r} lists presets", len(names) > 0):
            return
    check("bundled presets found", total >= 50, f"{total} presets")


def test_determinism(h: Harness):
    s = h.session(preset={"category": "Creative", "selection": "Chromium-Noir"})
    a, b = h.render(s), h.render(s)
    check("pinned seed gives identical renders", np.array_equal(a, b),
          "max diff %.8f" % float(np.abs(a - b).max()))

    s2 = dict(s, seed=None)
    c, d = h.render(s2), h.render(s2)
    check("unpinned seed varies between runs", not np.array_equal(c, d),
          "max diff %.6f" % float(np.abs(c - d).max()))


def test_render_sanity(h: Harness, presets: list[tuple[str, str]]):
    seen: dict[str, np.ndarray] = {}
    for cat, name in presets:
        out = h.render(h.session(preset={"category": cat, "selection": name}))
        ok = (np.all(np.isfinite(out)) and out[..., :3].max() > 0.01
              and not np.array_equal(out[..., :3], h.small[..., :3]))
        check(f"render {name!r}", ok,
              "mean %.3f max %.3f" % (float(out[..., :3].mean()), float(out[..., :3].max())))
        seen[name] = out
    names = list(seen)
    dupes = [(a, b) for a, b in itertools.combinations(names, 2)
             if np.array_equal(seen[a], seen[b])]
    check("presets differ from each other", not dupes, f"{len(dupes)} identical pairs")


def test_param_overrides(h: Harness):
    base = {"category": "Creative", "selection": "Chromium-Noir"}
    plain = h.render(h.session(preset=base))
    # Pick overrides that actually bite: this preset already has grain on, so
    # turning it *off* is the change that shows, and the Technicolor grain
    # amount only matters once its own enable is set.
    # Overrides that bite regardless of preset: this preset has grain on, so
    # turning it off is what shows, and film exposure is always in the path.
    # (Technicolor controls are gated behind a pipeline mode, so they are not
    # a fair test of whether overrides reach the plugin.)
    cases = [
        ({"quickGrainEnabled": "false"}, "grain off"),
        ({"filmExposureEv": "2.0"}, "film exposure +2 EV"),
        ({"filmExposureEv": "-2.0"}, "film exposure -2 EV"),
    ]
    for params, label in cases:
        out = h.render(h.session(preset=base, params=params))
        check(f"override {label} changes the render",
              not np.array_equal(plain, out),
              "mean diff %.5f" % float(np.abs(plain - out).mean()))

    bad = h.session(preset=base, params={"noSuchParameter": "1"})
    try:
        h.render(bad)
        check("unknown parameter is rejected", False, "it was accepted")
    except SystemExit as e:
        check("unknown parameter is rejected", "no such parameter" in str(e).lower(), str(e)[:60])


def test_preset_override_order(h: Harness):
    """A preset must not be clobbered by defaults - the bug the GUI once had."""
    base = {"category": "Creative", "selection": "Chromium-Noir"}
    out = h.render(h.session(preset=base))
    sat = float(np.abs(out[..., :3].max(-1) - out[..., :3].min(-1)).mean())
    check("monochrome preset really is monochrome", sat < 0.01, "saturation %.4f" % sat)


def test_session_roundtrip(h: Harness):
    s = h.session(preset={"category": "Creative", "selection": "OIL!"},
                  params={"technicolorGrainAmount": "1.3"})
    text = json.dumps(s)
    again = cli.load_session(text)
    check("session survives JSON round-trip", cli.session_to_sets(s) == cli.session_to_sets(again))
    check("session replays to identical pixels",
          np.array_equal(h.render(s), h.render(again)))

    partial = cli.load_session('{"seed": 5}')
    check("partial session gets defaults", partial["version"] == cli.SESSION_VERSION
          and partial["seed"] == 5 and "params" in partial)
    try:
        cli.load_session("{not json")
        check("malformed session is rejected", False)
    except SystemExit:
        check("malformed session is rejected", True)


def test_colourspaces(h: Harness, spaces: list[str]):
    outs = {}
    for cs in spaces:
        out = h.render(h.session(input_colorspace=cs,
                                 preset={"category": "Clean Slate", "selection": "Clean Slate"}))
        ok = np.all(np.isfinite(out))
        check(f"input colourspace {cs!r}", ok, "mean %.3f" % float(out[..., :3].mean()))
        outs[cs] = out
    if len(outs) > 1:
        a, b = list(outs.values())[:2]
        check("different colourspaces give different results", not np.array_equal(a, b))


def test_image_io(h: Harness):
    from PIL import Image
    out = h.render(h.session(preset={"category": "Clean Slate", "selection": "Clean Slate"}))
    with tempfile.TemporaryDirectory() as td:
        for ext, depth in [(".jpg", 8), (".png", 8), (".png", 16), (".tif", 16)]:
            path = Path(td) / f"out{depth}{ext}"
            cli.save_photo(path, out, depth, 95, {})
            ok = path.exists() and path.stat().st_size > 0
            arr = np.asarray(Image.open(path)) if ok else None
            check(f"write {ext} at {depth}-bit", ok and arr is not None and arr.size > 0,
                  f"{path.stat().st_size // 1024} KiB" if ok else "")

        # The 16-bit path must not silently collapse to 8 bits.  Pillow cannot
        # read 16-bit RGB either, so ask the PNG header directly rather than
        # letting Pillow's truncation masquerade as a bug in the writer.
        deep = Path(td) / "deep.png"
        cli.save_photo(deep, out, 16, 95, {})
        header = deep.read_bytes()[:26]
        depth, colour = header[24], header[25]
        check("16-bit PNG really has 16-bit samples", depth == 16 and colour == 2,
              f"IHDR bit depth {depth}, colour type {colour}")


def test_preview_matches_scale(h: Harness):
    s = h.session(preset={"category": "Creative", "selection": "Marty - Warm"})
    small = cli.resize_linear(h.linear, 128)
    out = h.rend.render(small, cli.session_to_sets(s))
    check("preview-sized render works", out.shape[:2] == small.shape[:2],
          f"{out.shape[1]}x{out.shape[0]}")


def test_raw_scaling(h: Harness, raw: Path | None):
    if not raw:
        results.append(("skip", "raw decode", "no raw file given"))
        print("  skip raw decode   (pass --raw to enable)")
        return
    a, cs, _ = cli.load_photo(raw)
    # A 256x error (Pillow truncating 16-bit RGB) puts the mean near 0.0003.
    check("raw decodes to plausible scene-linear", 0.005 < float(a.mean()) < 0.6,
          "mean %.5f" % float(a.mean()))
    check("raw reports a linear colourspace", "Linear" in cs, cs)


def test_output_encoding(h: Harness):
    """The plugin returns display-encoded pixels, not linear ones.

    Getting this wrong encodes gamma twice, which lifted shadows by up to 70
    levels out of 255.  Two different output encodings of the same render must
    decode back to the same linear signal.
    """
    vals = np.array([0.0, 0.02, 0.05, 0.18, 0.5, 0.9, 1.0], np.float32)
    ramp = np.ones((8, len(vals), 3), np.float32) * vals[None, :, None]

    def render_with(space):
        s = h.session(preset={"category": "Clean Slate", "selection": "Clean Slate"},
                      output_colorspace=space, params={"quickGrainEnabled": "false"})
        return h.rend.render(ramp, cli.session_to_sets(s))[4, :, 0].astype(np.float64)

    srgb = render_with("sRGB")
    g24 = render_with("Rec.709 Gamma 2.4")
    lin_a = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    lin_b = np.clip(g24, 0, None) ** 2.4
    check("two output encodings agree on the linear signal",
          np.allclose(lin_a, lin_b, atol=3e-3),
          "max diff %.5f" % float(np.abs(lin_a - lin_b).max()))

    # Clean Slate is near enough an identity that mid grey must survive it.
    mid = lin_a[3]
    check("mid grey survives a neutral preset", abs(mid - 0.18) < 0.03,
          "linear 0.18 in -> %.4f out" % mid)
    check("output is display-encoded, not linear", srgb[3] > 0.35,
          "mid grey encodes to %.4f" % srgb[3])


def test_colour_profiles(h: Harness):
    """A wide-gamut file must be converted, not assumed to be sRGB."""
    from PIL import Image, ImageCms
    import glob, subprocess

    adobe_icc = next(iter(glob.glob("/usr/share/color/icc/**/*dobeRGB*.icc",
                                    recursive=True)), None)
    convert = __import__("shutil").which("convert")
    if not (adobe_icc and convert):
        results.append(("skip", "wide-gamut conversion", "no AdobeRGB profile or ImageMagick"))
        print("  skip wide-gamut conversion")
        return

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "srgb.jpg"
        im = Image.open(h.photo)
        im.thumbnail((400, 400))
        im.convert("RGB").save(
            src, quality=95,
            icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
        wide = Path(td) / "adobe.jpg"
        subprocess.run([convert, str(src), "-profile", adobe_icc, str(wide)],
                       capture_output=True)

        a, _, ma = cli.load_photo(src)
        b, _, mb = cli.load_photo(wide)
        check("wide-gamut profile is detected", "converted to sRGB" in mb["colour_note"],
              mb["colour_note"])
        rt = float(np.abs(a - b).mean())
        naive = float(np.abs(a - cli.srgb_to_linear(
            np.asarray(Image.open(wide)).astype(np.float32) / 255)).mean())
        check("conversion recovers the original better than ignoring it",
              rt < naive, "converted %.5f vs ignored %.5f" % (rt, naive))


def test_metadata(h: Harness):
    info = cli.describe_image(h.photo)
    check("metadata reports a decode", "decoded" in info and "error" not in info["decoded"])
    check("metadata reports linear statistics",
          0.0 <= info["decoded"]["linear_mean"] <= 1.0,
          "mean %.4f" % info["decoded"]["linear_mean"])
    check("metadata is JSON-serialisable", bool(json.dumps(info)))


def test_gui_model(h: Harness):
    """Drive the window offscreen: no display needed, no clicking."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        results.append(("skip", "gui model", "PyQt5 not installed"))
        print("  skip gui model   (PyQt5 not installed)")
        return

    gspec = importlib.util.spec_from_loader(
        "gui", importlib.machinery.SourceFileLoader("gui", str(ROOT / "spektra-gui")))
    gui = importlib.util.module_from_spec(gspec)
    gspec.loader.exec_module(gui)

    app = QApplication.instance() or QApplication([])
    w = gui.Window(h.rend)
    w.resize(900, 600)

    check("panel builds quick controls", len(w.widgets) > 0, f"{len(w.widgets)} controls")

    w.chk_seed.setChecked(True)
    w.sp_seed.setValue(SEED)
    w.cb_category.setCurrentText("Creative")
    w._refresh_presets()
    w.cb_preset.setCurrentText("Marty - Warm")
    w.source_path = h.photo
    w.preview_linear = h.small

    s = w.session()
    check("session names the chosen preset",
          s["preset"]["selection"] == "Marty - Warm", str(s["preset"]))
    check("session carries the pinned seed", s["seed"] == SEED)

    gui_px = h.rend.render(h.small, w.current_sets())
    cli_px = h.rend.render(h.small, cli.session_to_sets(s))
    check("CLI reproduces the GUI exactly", np.array_equal(gui_px, cli_px),
          "max diff %.8f" % float(np.abs(gui_px - cli_px).max()))

    # A session may carry any of the 830 parameters, including ones the panel
    # does not display.  Those must survive import rather than be dropped.
    hidden = dict(s, params={"filmExposureEv": "1.5"})
    w.apply_session(hidden)
    kept = w.session()["params"].get("filmExposureEv")
    check("session keeps parameters the panel does not show", kept == "1.5", f"kept={kept!r}")
    out = h.rend.render(h.small, w.current_sets())
    check("hidden parameter reaches the plugin", not np.array_equal(gui_px, out),
          "mean diff %.5f" % float(np.abs(gui_px - out).mean()))


# ------------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photo", type=Path, help="photo to test with")
    ap.add_argument("--raw", type=Path, help="a camera raw file, to test that path")
    ap.add_argument("--bundle", help="plugin bundle")
    ap.add_argument("--quick", action="store_true", help="fewer combinations")
    ap.add_argument("-k", metavar="TEXT", help="only run tests whose name matches")
    args = ap.parse_args()

    tmp = tempfile.TemporaryDirectory()
    photo = args.photo
    if not photo:
        photo = Path(tmp.name) / "chart.png"
        synthetic_photo(photo)
    print(f"photo:  {photo}")

    h = Harness(photo, args.bundle)
    print(f"bundle: {h.rend.bundle}\n")

    presets = [("Creative", "Chromium-Noir"), ("Creative", "Marty - Warm")]
    spaces = ["Linear Rec.709", "sRGB"]
    if not args.quick:
        presets += [("Creative", "OIL!"), ("Creative", "Vintage Faded"),
                    ("Clean Slate", "Clean Slate")]
        spaces += ["ACEScg"]

    suite = [
        ("host", lambda: test_host_loads(h)),
        ("presets", lambda: test_presets_enumerate(h)),
        ("determinism", lambda: test_determinism(h)),
        ("render", lambda: test_render_sanity(h, presets)),
        ("params", lambda: test_param_overrides(h)),
        ("preset-order", lambda: test_preset_override_order(h)),
        ("session", lambda: test_session_roundtrip(h)),
        ("colourspace", lambda: test_colourspaces(h, spaces)),
        ("imageio", lambda: test_image_io(h)),
        ("preview", lambda: test_preview_matches_scale(h)),
        ("raw", lambda: test_raw_scaling(h, args.raw)),
        ("profiles", lambda: test_colour_profiles(h)),
        ("metadata", lambda: test_metadata(h)),
        ("encoding", lambda: test_output_encoding(h)),
        ("gui", lambda: test_gui_model(h)),
    ]

    for name, fn in suite:
        if args.k and args.k.lower() not in name.lower():
            continue
        print(f"{name}:")
        try:
            fn()
        except Exception:
            traceback.print_exc()
            results.append((FAIL, name, "raised an exception"))
        print()

    failed = [r for r in results if r[0] == FAIL]
    passed = [r for r in results if r[0] == PASS]
    print(f"{len(passed)} passed, {len(failed)} failed, "
          f"{len([r for r in results if r[0] == 'skip'])} skipped")
    for _, name, detail in failed:
        print(f"  FAIL {name}  {detail}")
    tmp.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

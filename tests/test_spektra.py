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

    # --set takes one NAME=VALUE.  Comma-separating two used to set the first
    # and drop the rest silently, because strtod reads 0 for what it cannot
    # parse and says nothing.
    try:
        h.render(h.session(preset=base,
                           params={"filmExposureEv": "1.0,printExposureEv=0.5"}))
        check("two parameters in one --set is refused", False, "it was accepted")
    except SystemExit as e:
        check("two parameters in one --set is refused",
              "is not a number" in str(e), str(e)[-70:])

    # Genuine multi-component values still parse.
    two = h.render(h.session(preset=base,
                             params={"quickVignetteEnabled": "true",
                                     "vignetteCenter": "10,-5"}))
    check("a two-part value is accepted", two is not None)

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


def test_chain(h: Harness):
    """The companion bundles, run as extra passes over the first result."""
    try:
        cli.find_bundle("lens")
        cli.find_bundle("diffuse")
    except SystemExit:
        results.append(("skip", "chain", "companion bundles not installed"))
        print("  skip chain   (companion bundles not installed)")
        return

    base = h.session(preset={"category": "Creative", "selection": "Marty - Warm"})
    plain = cli.render_pipeline(base, h.small, h.rend)

    # diffuse ships with camera diffusion on, so it changes the image by itself
    diff = cli.render_pipeline(dict(base, chain=[{"bundle": "diffuse", "params": {}}]),
                               h.small, h.rend)
    check("a diffuse pass changes the image", not np.array_equal(plain, diff),
          "mean diff %.5f" % float(np.abs(plain - diff).mean()))

    # lens is the same engine with every effect off, so a bare pass is a no-op
    bare = cli.render_pipeline(dict(base, chain=[{"bundle": "lens", "params": {}}]),
                               h.small, h.rend)
    check("a bare lens pass is a no-op, as its defaults imply",
          np.allclose(plain, bare, atol=2e-3),
          "mean diff %.5f" % float(np.abs(plain - bare).mean()))

    # with an effect switched on it must bite, and darken the corners
    vign = cli.render_pipeline(
        dict(base, chain=[{"bundle": "lens", "params": {
            "quickVignetteEnabled": "true",
            "quickVignettePreset": "Vintage Mechanical"}}]), h.small, h.rend)
    check("a lens pass with vignette on changes the image",
          not np.array_equal(plain, vign),
          "mean diff %.5f" % float(np.abs(plain - vign).mean()))
    corner_before = float(plain[:12, :12, :3].mean())
    corner_after = float(vign[:12, :12, :3].mean())
    check("vignette darkens the corners", corner_after < corner_before,
          "%.4f -> %.4f" % (corner_before, corner_after))

    # two passes stack
    both = cli.render_pipeline(
        dict(base, chain=[{"bundle": "diffuse", "params": {}},
                          {"bundle": "lens", "params": {
                              "quickVignetteEnabled": "true",
                              "quickVignettePreset": "Vintage Mechanical"}}]),
        h.small, h.rend)
    check("two chained passes differ from either alone",
          not np.array_equal(both, diff) and not np.array_equal(both, vign))

    # OFX_PLUGIN_PATH must win, so a deliberate choice is never ignored.
    import os as _os
    saved = _os.environ.get("OFX_PLUGIN_PATH")
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "spektrafilm.ofx.bundle"
        fake.mkdir()
        _os.environ["OFX_PLUGIN_PATH"] = td
        try:
            check("OFX_PLUGIN_PATH takes precedence",
                  cli.find_bundle(None) == fake, str(cli.find_bundle(None)))
        finally:
            if saved is None:
                _os.environ.pop("OFX_PLUGIN_PATH", None)
            else:
                _os.environ["OFX_PLUGIN_PATH"] = saved

    check("bundle aliases resolve",
          cli.find_bundle("lens").name == "spektrafilm_lens.ofx.bundle",
          cli.find_bundle("lens").name)


def test_exposure_bias(h: Harness, raw: Path | None):
    """Undoing the camera's EV compensation, raw only."""
    if not raw:
        results.append(("skip", "exposure bias", "no raw file given"))
        print("  skip exposure bias   (pass --raw to enable)")
        return

    bias = cli.read_exposure_bias(raw)
    check("exposure bias read from EXIF", bias is not None, f"{bias}")

    linear, _, meta = cli.load_photo(raw)
    out, note = cli.apply_exposure_bias(linear, meta)
    if bias:
        expected = 2.0 ** (-bias)
        got = float(out.mean() / linear.mean())
        check("raw is scaled by 2^-bias", abs(got - expected) < 1e-3,
              "expected x%.3f, got x%.3f" % (expected, got))
    else:
        check("no bias recorded leaves the image alone", np.array_equal(out, linear))

    # A JPEG already carries the camera's rendering, so it must be left alone.
    jpeg, _, jmeta = cli.load_photo(h.photo)
    same, jnote = cli.apply_exposure_bias(jpeg, jmeta)
    check("a non-raw file is left alone", same is jpeg, jnote)


def test_dependent_choices(h: Harness):
    """Each Stock Category must offer the stocks that belong to it.

    A host that caches a dropdown's options at describe time never sees the
    plugin rewrite them, and shows films from the wrong category.
    """
    by = h.rend.by_name()

    expect = {
        "Motion Picture": ("Vision3", "Kodak"),
        "B&W Motion Picture": ("Double-X", "Eastman"),
        "Still Film": ("Portra", "Kodak"),
        "B&W Still Film": ("Ilford", "Delta"),
        "Positive/Slide Film": ("Ektachrome", "Kodak"),
        "Fujifilm": ("Fujifilm",),
        "Ilford": ("Ilford",),
    }

    for cat_param, stock_param, title in cli.DEPENDENT_CHOICES:
        categories = by[cat_param]["choices"]
        seen = {}
        for category in categories:
            names = cli.stocks_for(h.rend, cat_param, stock_param, category)
            seen[category] = names
            if not check(f"{title}: {category!r} offers stocks", len(names) > 0,
                         f"{len(names)} entries"):
                continue
            wanted = expect.get(category)
            if wanted:
                hit = any(any(k in n for n in names) for k in wanted)
                check(f"{title}: {category!r} offers the right ones", hit,
                      f"{names[0]!r} …")
            # A brand category must only offer that brand.
            if category in ("Fujifilm", "Ilford"):
                check(f"{title}: {category!r} is all {category}",
                      all(category.lower() in n.lower() for n in names),
                      f"{sum(category.lower() not in n.lower() for n in names)} strays")

        lengths = {len(v) for v in seen.values()}
        check(f"{title}: categories differ from one another", len(lengths) > 1,
              ", ".join(f"{k}={len(v)}" for k, v in list(seen.items())[:4]))

    # And the full sweep: exactly these choices rewrite others.
    base = {x["name"]: list(x["choices"])
            for x in h.rend.params if x["type"] == "OfxParamTypeChoice"}
    drivers = {c[0] for c in cli.DEPENDENT_CHOICES} | {cli.P_CATEGORY}
    found = set()
    for name in ("filmCategory", "printCategory", cli.P_CATEGORY, "rgbToRawMethod",
                 "outputRole", "process"):
        if name not in base or len(base[name]) < 2:
            continue
        alt = base[name][1]
        state = h.rend.by_name([(name, alt)])
        if any(m != name and m in state and list(state[m].get("choices", [])) != b
               for m, b in base.items()):
            found.add(name)
    check("the known drivers are the ones that rewrite dropdowns",
          found == drivers, f"found {sorted(found)}, expected {sorted(drivers)}")


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


def test_tabbed_layout(h: Harness):
    """The default five-tab panel must place every group, not quietly drop one."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt5.QtWidgets import QApplication, QTabWidget, QToolButton
    except ImportError:
        results.append(("skip", "tabbed layout", "PyQt5 not installed"))
        print("  skip tabbed layout   (PyQt5 not installed)")
        return

    spec = importlib.util.spec_from_loader(
        "gui", importlib.machinery.SourceFileLoader("gui", str(ROOT / "spektra-gui")))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)

    app = QApplication.instance() or QApplication([])

    def heads(w):
        return [x for x in w.param_host.findChildren(QToolButton)
                if x.objectName() == "groupHead"]

    w = gui.Window(h.rend)
    w.chk_all.setChecked(True)
    found = w.param_host.findChildren(QTabWidget)
    check("tabs are the default layout", bool(found))
    if not found:
        return
    titles = [found[0].tabText(i) for i in range(found[0].count())]
    check("the five tabs are present",
          titles[:5] == ["MAIN", "FILM", "PRINT", "ADVANCED", "CONFIG"], str(titles))
    check("no group needed an OTHER tab", "OTHER" not in titles, str(titles))

    # A tab that refuses the click teaches nothing; CONFIG in particular holds
    # actions, so it is populated without ticking "show every parameter".
    plain = gui.Window(h.rend)
    tabs = plain.param_host.findChildren(QTabWidget)[0]
    check("every tab is clickable",
          all(tabs.isTabEnabled(i) for i in range(tabs.count())))
    cfg = [i for i in range(tabs.count()) if tabs.tabText(i) == "CONFIG"][0]
    from PyQt5.QtWidgets import QPushButton
    labels = {b.text() for b in tabs.widget(cfg).findChildren(QPushButton)}
    check("CONFIG is useful by default", "Export LUT" in labels,
          f"{len(labels)} buttons")

    # The single column is still reachable, and must show exactly the same.
    c = gui.Window(h.rend)
    c.tabbed = False
    c.rebuild_params()
    c.chk_all.setChecked(True)
    check("--single-column shows the same groups",
          len(heads(w)) == len(heads(c)), f"{len(heads(w))} vs {len(heads(c))}")
    check("and the same controls", len(w.widgets) == len(c.widgets),
          f"{len(w.widgets)} vs {len(c.widgets)}")


def test_provenance(h: Harness):
    """The settings file must rebuild the image, and move to another photo."""
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / "out.jpg"
        s = h.session(preset={"category": "Creative", "selection": "Chromium-Noir"},
                      params={"filmExposureEv": "1.2"},
                      crop={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
                      preview={"long_edge": 200})
        out, meta = cli.render_session(h.rend, s, h.photo)
        cli.save_photo(dst, out, 8, 92, meta)

        s["input"], s["output"] = str(h.photo), str(dst)
        rec = cli.provenance_record(s, h.rend, h.photo, dst, out.shape[:2])
        where = Path(td) / "out.jpg.spektra.json"
        cli.write_provenance(where, rec)

        check("it names the tool and the plugin",
              rec["tool"]["name"] == "ofx-photo" and "identifier" in rec["plugin"],
              f"{rec['plugin'].get('identifier')} v{rec['plugin'].get('version')}")
        check("it names the source bytes", bool(rec["source"]["sha256"]))
        check("it says whether the render repeats", rec["reproducible"] is True)

        # The record is itself a session.
        back = cli.load_session(str(where))
        check("the record loads as a session",
              back["preset"]["selection"] == "Chromium-Noir"
              and back["params"]["filmExposureEv"] == "1.2")
        again, _ = cli.render_session(h.rend, back, h.photo)
        check("replaying it reproduces the image exactly",
              np.array_equal(again, out), "max diff %.6f" %
              float(np.abs(again - out).max()) if again.shape == out.shape else
              f"{again.shape} vs {out.shape}")

        # A look must travel without dragging the framing along.
        look = cli.look_from(back)
        check("the look keeps the grade",
              look["preset"]["selection"] == "Chromium-Noir"
              and look["params"]["filmExposureEv"] == "1.2"
              and look["seed"] == back["seed"])
        check("and leaves the crop behind", look["crop"] is None)
        check("and the preview size", look["preview"] is None)
        check("and the paths", not look["input"] and not look["output"])

        # Unpinned seeds must be declared, not quietly implied.
        loose = cli.provenance_record(dict(s, seed=None), h.rend, h.photo, dst,
                                      out.shape[:2])
        check("an unpinned seed is called out",
              loose["reproducible"] is False and "seed" in loose["note"].lower())


def test_crop(h: Harness):
    """A crop must be a window onto the full render, not a new frame.

    The plugin normalises every optical effect to the image it is given, so
    cropping first would re-centre the vignette, re-normalise lens distortion
    and rescale grain.  The frame is rendered whole and cut afterwards.
    """
    check("a crop spec parses", cli.parse_crop("0.25,0.25,0.5,0.5") ==
          {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})
    for bad in ("1,1,1,1", "0,0,0,0.5", "0.5,0.5,0.9,0.9", "nonsense", "0,0,1"):
        try:
            cli.parse_crop(bad)
            check(f"rejects {bad!r}", False)
        except SystemExit:
            check(f"rejects {bad!r}", True)

    # A flat field makes the vignette's anchor unmistakable.
    flat = np.full((240, 360, 3), 0.18, np.float32)
    s = h.session(preset={"category": "Clean Slate", "selection": "Clean Slate"},
                  params={"quickGrainEnabled": "false",
                          "quickVignetteEnabled": "true",
                          "quickVignettePreset": "Vintage Mechanical"})
    full = cli.render_pipeline(s, flat, h.rend)

    corner = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}
    cut = cli.apply_crop(full, corner)
    rows, cols = full.shape[:2]
    check("the crop is the right size",
          cut.shape[:2] == (round(rows * 0.5), round(cols * 0.5)),
          f"{cut.shape[1]}x{cut.shape[0]} of {cols}x{rows}")
    check("and is exactly that window of the full render",
          np.array_equal(cut, full[:cut.shape[0], :cut.shape[1]]))

    # Anchored: within a top-left crop the frame corner stays the darkest
    # point and it brightens towards the frame centre.
    near = float(cut[2:6, 2:6, :3].mean())
    far = float(cut[-6:-2, -6:-2, :3].mean())
    check("the vignette stays anchored to the original frame", far > near,
          "frame corner %.4f, towards frame centre %.4f" % (near, far))

    # Had it re-centred, the crop rendered alone would differ from the window.
    alone = cli.render_pipeline(s, flat[:cut.shape[0], :cut.shape[1]], h.rend)
    check("rendering the crop alone would have differed",
          not np.allclose(alone, cut, atol=2e-3),
          "mean diff %.4f" % float(np.abs(alone - cut).mean()))

    check("no crop leaves the frame alone", cli.apply_crop(full, None) is full)

    # One rounding, so the size the panel reports is the size that is written.
    box = cli.crop_box((3008, 4512), {"x": 0.2494, "y": 0.2492,
                                      "w": 0.5, "h": 0.4498})
    cut = cli.apply_crop(np.zeros((3008, 4512, 3), np.float32),
                         {"x": 0.2494, "y": 0.2492, "w": 0.5, "h": 0.4498})
    check("the reported size is the written size",
          (box[2] - box[0], box[3] - box[1]) == (cut.shape[1], cut.shape[0]),
          f"{box[2]-box[0]}x{box[3]-box[1]} vs {cut.shape[1]}x{cut.shape[0]}")


def test_metadata_carried(h: Harness, raw: Path | None):
    """The render must keep the photograph's metadata, and correct what changed."""
    import shutil as _shutil
    import subprocess as _sp

    if not _shutil.which("exiftool"):
        results.append(("skip", "metadata", "exiftool not installed"))
        print("  skip metadata   (exiftool not installed)")
        return

    def tags(path):
        out = _sp.run(["exiftool", "-s", str(path)], capture_output=True).stdout
        return len(out.decode("utf-8", "replace").splitlines())

    def tag(path, name):
        out = _sp.run(["exiftool", "-s3", f"-{name}", str(path)], capture_output=True)
        return out.stdout.decode("utf-8", "replace").strip()

    with tempfile.TemporaryDirectory() as td:
        # A source carrying a rotation, an XMP field and a custom EXIF value.
        from PIL import Image
        src = Path(td) / "src.jpg"
        im = Image.open(h.photo)
        im.thumbnail((360, 360))
        im.convert("RGB").save(src, quality=92)
        _sp.run(["exiftool", "-q", "-overwrite_original", "-Orientation#=6",
                 "-Make=TESTCAM", "-XMP-dc:Creator=A Photographer", str(src)],
                capture_output=True)
        before = tags(src)
        w0, h0 = Image.open(src).size

        s = h.session(preset={"category": "Creative", "selection": "Chromium-Noir"})
        out, meta = cli.render_session(h.rend, s, src)
        dst = Path(td) / "out.jpg"
        cli.save_photo(dst, out, 8, 92, meta)

        after = tags(dst)
        check("most of the metadata survives", after > before * 0.7,
              f"{before} tags in, {after} out")
        check("the camera make is carried", tag(dst, "Make") == "TESTCAM",
              tag(dst, "Make"))
        check("XMP is carried too", tag(dst, "XMP-dc:Creator") == "A Photographer",
              tag(dst, "XMP-dc:Creator"))

        # The rotation is applied to the pixels, so the tag must be cleared or
        # a viewer turns the picture a second time.
        w1, h1 = Image.open(dst).size
        check("the rotation is baked into the pixels", (w1, h1) == (h0, w0),
              f"{w0}x{h0} -> {w1}x{h1}")
        check("and the orientation tag is reset",
              tag(dst, "Orientation") in ("Horizontal (normal)", "1"),
              tag(dst, "Orientation"))
        check("the colourspace describes the output", tag(dst, "ColorSpace") == "sRGB",
              tag(dst, "ColorSpace"))
        check("the dimensions describe the output",
              tag(dst, "ExifImageWidth") == str(w1), tag(dst, "ExifImageWidth"))
        check("the render is recorded", "spektra" in tag(dst, "ProcessingSoftware"),
              tag(dst, "ProcessingSoftware"))

        # And it can be turned off.  A JPEG always reports a score of
        # structural tags, so what matters is that nothing identifying the
        # photograph or the camera is left.
        bare = Path(td) / "bare.jpg"
        cli.save_photo(bare, out, 8, 92, meta, keep_metadata=False)
        leaked = [n for n in ("Make", "Model", "XMP-dc:Creator",
                              "DateTimeOriginal", "LensModel")
                  if tag(bare, n)]
        check("--strip-metadata leaves nothing identifying", not leaked,
              f"still present: {leaked}" if leaked else f"{tags(bare)} structural tags")

    if raw:
        with tempfile.TemporaryDirectory() as td:
            s = h.session(preset={"category": "Clean Slate", "selection": "Clean Slate"},
                          preview={"long_edge": 200})
            out, meta = cli.render_session(h.rend, s, raw)
            dst = Path(td) / "fromraw.jpg"
            cli.save_photo(dst, out, 8, 92, meta)
            check("a raw file's metadata reaches the JPEG",
                  tags(dst) > 100 and tag(dst, "Model") != "",
                  f"{tags(dst)} tags, Model={tag(dst, 'Model')!r}")


def test_compare_shows_the_original(h: Harness):
    """Hold-to-compare must show the file, not a re-encoded version of it.

    The render comes back display-encoded and the source is held linear, so
    the two go to screen by different routes.  Encoding the source twice
    would lift midtones by about 0.18 and still look plausible.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        results.append(("skip", "compare view", "PyQt5 not installed"))
        print("  skip compare view   (PyQt5 not installed)")
        return
    from PIL import Image

    spec = importlib.util.spec_from_loader(
        "gui", importlib.machinery.SourceFileLoader("gui", str(ROOT / "spektra-gui")))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)

    app = QApplication.instance() or QApplication([])
    w = gui.Window(h.rend)
    w.load_path(h.photo)

    # exactly what _display(..., encoded=False) sends to the screen
    shown = np.clip(cli.linear_to_srgb(w.preview_linear[..., :3]), 0, 1)

    src = np.asarray(Image.open(h.photo).convert("RGB")).astype(np.float32) / 255.0
    src = cli.resize_linear(src, gui.PREVIEW_EDGE)
    rows = min(shown.shape[0], src.shape[0])
    cols = min(shown.shape[1], src.shape[1])
    a, b = shown[:rows, :cols], src[:rows, :cols]

    check("the compare view matches the file on disk",
          abs(float(a.mean()) - float(b.mean())) < 0.02,
          "shown %.4f vs file %.4f" % (a.mean(), b.mean()))
    twice = float(np.clip(cli.linear_to_srgb(a), 0, 1).mean())
    check("and is not the double-encoded version",
          abs(float(a.mean()) - twice) > 0.05, "double encode would be %.4f" % twice)


def stub_pixmap(width: int = 600, height: int = 400):
    """A real pixmap of a known size, so the view maths has a frame to map to."""
    from PyQt5.QtGui import QPixmap
    pm = QPixmap(width, height)
    pm.fill()
    return pm


def test_zoom_and_pan(h: Harness):
    """1:1 must show real pixels, which means the full-resolution render."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QPoint, Qt as _Qt
        from PyQt5.QtGui import QMouseEvent
    except ImportError:
        results.append(("skip", "zoom", "PyQt5 not installed"))
        print("  skip zoom   (PyQt5 not installed)")
        return

    spec = importlib.util.spec_from_loader(
        "gui", importlib.machinery.SourceFileLoader("gui", str(ROOT / "spektra-gui")))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)

    app = QApplication.instance() or QApplication([])
    w = gui.Window(h.rend)
    w.resize(800, 600)
    v = w.view
    v._pm = stub_pixmap()

    check("a photograph opens fitted", v.zoom is None)

    # The part a crop discards is dimmed rather than hidden, so the framing
    # can still be judged against what surrounds it.
    from PyQt5.QtGui import QColor
    flat = stub_pixmap()
    flat.fill(QColor(200, 200, 200))
    v._pm = flat
    v._rescale()
    v.set_crop({"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4})
    shot = v.grab().toImage()
    pix = np.frombuffer(shot.bits().asstring(shot.sizeInBytes()), np.uint8)
    pix = pix.reshape(shot.height(), shot.bytesPerLine() // 4, 4)[:, :shot.width(), :3]
    fr = v._frame()
    inside = float(pix[fr.y() + fr.height() // 2, fr.x() + fr.width() // 2, 0])
    outside = float(pix[fr.y() + 6, fr.x() + 6, 0])
    check("outside the crop is dimmed to the stated brightness",
          abs(outside / max(inside, 1) - gui.CROP_OUTSIDE_BRIGHTNESS) < 0.02,
          "%.0f of %.0f = %.2f, wanted %.2f" % (outside, inside,
                                                outside / max(inside, 1),
                                                gui.CROP_OUTSIDE_BRIGHTNESS))
    check("and is still visible, not blacked out", outside > 5, "%.0f" % outside)
    v.set_crop(None)
    v._pm = stub_pixmap()

    v.set_zoom(2.0, (0.5, 0.5))
    win = v._source_window()
    check("zooming shows only part of the image",
          win.width() < 600 and win.height() < 400,
          f"{win.width()}x{win.height()} of 600x400")

    v.center = (0.5, 0.5)
    v._pan_from, v._pan_center = QPoint(400, 300), (0.5, 0.5)
    v.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, QPoint(300, 200),
                                 _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
    check("dragging pans", v.center != (0.5, 0.5), "(%.3f, %.3f)" % v.center)

    v.center = (0.5, 0.5)
    v._pan_from, v._pan_center = QPoint(0, 0), (0.0, 0.0)
    v.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, QPoint(9999, 9999),
                                 _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
    check("panning stays on the picture",
          0.0 <= v.center[0] <= 1.0 and 0.0 <= v.center[1] <= 1.0,
          "(%.3f, %.3f)" % v.center)
    v._pan_from = None

    # Coordinates must still map correctly while zoomed, or a crop drawn at
    # 1:1 would land somewhere else entirely.
    v.set_zoom(None)
    v.center = (0.5, 0.5)
    f = v._frame()
    fitted = v._to_fraction(QPoint(f.x() + f.width() // 2, f.y() + f.height() // 2))
    v.set_zoom(3.0, (0.5, 0.5))
    f = v._frame()
    zoomed = v._to_fraction(QPoint(f.x() + f.width() // 2, f.y() + f.height() // 2))
    check("the centre maps to the centre at any zoom",
          abs(fitted[0] - zoomed[0]) < 0.02 and abs(fitted[1] - zoomed[1]) < 0.02,
          f"{fitted} vs {zoomed}")

    # Fraction to widget and back must agree, for points that are on screen.
    # Without the inverse mapping the crop overlay used the visible window as
    # though it were the whole picture, so a crop drawn while zoomed jumped
    # elsewhere and changed size.
    for zoom, centre in ((None, (0.5, 0.5)), (2.0, (0.5, 0.5)), (1.0, (0.4, 0.6))):
        v.set_zoom(zoom, centre)
        win = v._source_window()
        lo_x, hi_x = win.x() / 600, (win.x() + win.width()) / 600
        lo_y, hi_y = win.y() / 400, (win.y() + win.height()) / 400
        worst = 0.0
        for fx, fy in ((0.35, 0.35), (0.5, 0.5), (0.47, 0.58)):
            if not (lo_x < fx < hi_x and lo_y < fy < hi_y):
                continue        # off screen, where clamping is the right answer
            back = v._to_fraction(v._from_fraction(fx, fy))
            worst = max(worst, abs(back[0] - fx), abs(back[1] - fy))
        check(f"coordinates round-trip at zoom {zoom}", worst < 0.01,
              "worst %.4f" % worst)

    # And a crop dragged while zoomed must be drawn back where it was drawn.
    v.set_zoom(2.0, (0.5, 0.5))
    fr = v._frame()
    a = QPoint(fr.x() + int(fr.width() * 0.25), fr.y() + int(fr.height() * 0.25))
    b = QPoint(fr.x() + int(fr.width() * 0.75), fr.y() + int(fr.height() * 0.75))
    v.set_cropping(True)
    v.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, a,
                                  _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
    v.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, b,
                                    _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
    got = v.crop
    pa = v._from_fraction(got["x"], got["y"])
    pb = v._from_fraction(got["x"] + got["w"], got["y"] + got["h"])
    check("a crop drawn while zoomed stays where it was put",
          abs(pa.x() - a.x()) <= 2 and abs(pa.y() - a.y()) <= 2
          and abs(pb.x() - b.x()) <= 2 and abs(pb.y() - b.y()) <= 2,
          f"drew {(a.x(), a.y())}-{(b.x(), b.y())}, "
          f"shows {(pa.x(), pa.y())}-{(pb.x(), pb.y())}")
    v.set_cropping(False)
    v.set_crop(None)

    # The button toggles against 1:1, not against being zoomed at all.
    v.set_zoom(0.7)
    w.full_linear = np.zeros((40, 60, 3), np.float32)
    w.full_render = np.zeros((40, 60, 3), np.float32)
    w.toggle_one_to_one()
    check("1:1 from some other zoom goes to 1:1",
          v.zoom is not None and abs(v.zoom - 1.0) < 1e-6, str(v.zoom))
    w.toggle_one_to_one()
    check("and pressing it again fits", v.zoom is None)


def test_push_buttons(h: Harness):
    """Push buttons must be shown, and must not pretend to hold a value.

    They were skipped entirely, which left the LUT Export group showing three
    settings and no way to act on them.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt5.QtWidgets import QApplication, QPushButton, QTabWidget
    except ImportError:
        results.append(("skip", "push buttons", "PyQt5 not installed"))
        print("  skip push buttons   (PyQt5 not installed)")
        return

    spec = importlib.util.spec_from_loader(
        "gui", importlib.machinery.SourceFileLoader("gui", str(ROOT / "spektra-gui")))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)

    app = QApplication.instance() or QApplication([])
    w = gui.Window(h.rend)
    w.chk_all.setChecked(True)

    labels = {b.text() for b in w.param_host.findChildren(QPushButton)}
    for wanted in ("Export LUT", "Copy Params", "Reset Factory Defaults"):
        check(f"{wanted!r} is offered", wanted in labels)

    declared = {p["name"] for p in h.rend.params
                if p["type"] == "OfxParamTypePushButton" and not p.get("secret")}
    check("no push button leaked into the value widgets",
          not (declared & set(w.widgets)), str(declared & set(w.widgets)))
    check("so none reaches a session",
          not (declared & set(w.session()["params"])))

    # Destructive ones must be guarded rather than fired on a stray click.
    for name in ("resetDefaults", "pasteParams"):
        check(f"{name} asks before acting", name in gui.Window.DESTRUCTIVE)

    # The crop is a view-level thing: dragging sets it, a plain click clears
    # it, and it reaches the session so a replay crops the same way.
    from PyQt5.QtCore import QPoint, Qt as _Qt
    from PyQt5.QtGui import QMouseEvent
    view = w.view
    view._pm = stub_pixmap()
    view.set_cropping(True)
    f = view._frame()
    if f:
        a = QPoint(f.x() + int(f.width() * 0.25), f.y() + int(f.height() * 0.25))
        b = QPoint(f.x() + int(f.width() * 0.75), f.y() + int(f.height() * 0.75))
        view.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, a,
                                         _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
        view.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, b,
                                           _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
        got = w.session()["crop"]
        check("dragging sets a crop", got is not None and 0.4 < got["w"] < 0.6,
              str(got))
        view.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, a,
                                         _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
        view.mouseReleaseEvent(QMouseEvent(QMouseEvent.MouseButtonRelease, a,
                                           _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier))
        check("a click without a drag clears it", w.session()["crop"] is None)
    view.set_cropping(False)

    ok, note = h.rend.fire([("noSuchButton", "")])
    check("firing an unknown parameter fails cleanly",
          not ok and "no such parameter" in note.lower(), note[:60])


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

    # A compositor matches a window to its .desktop file by app id.  Without
    # these the shell shows a generic icon however well the real one is
    # installed, which is exactly what happened.
    gui.install_identity(app)
    check("the app declares its desktop file", app.desktopFileName() == "ofx-photo",
          app.desktopFileName())
    check("the app declares its WM class", app.applicationName() == "ofx-photo",
          app.applicationName())
    check("a window icon is set", not app.windowIcon().isNull())

    from PyQt5.QtWidgets import QToolButton

    titles = [h.text().rsplit("  (", 1)[0]
              for h in w.param_host.findChildren(QToolButton)
              if h.objectName() == "groupHead"]
    for wanted in ("Quick Access", "Color Management", "Film", "Print", "Grain"):
        check(f"{wanted!r} shown by default", wanted in titles)
    check("Normalize W/B is reachable by default", "rcmFullRange" in w.widgets)
    # Effects is a long list of optional extras, so in the single column it
    # sorts last.  Under the default tabs it lives inside ADVANCED instead,
    # where the ordering assertion would mean nothing.
    col = gui.Window(h.rend)
    col.tabbed = False
    col.rebuild_params()
    col_titles = [x.text().rsplit("  (", 1)[0]
                  for x in col.param_host.findChildren(QToolButton)
                  if x.objectName() == "groupHead"]
    check("Effects sits at the bottom of the single column",
          col_titles[-1] == "Effects", " -> ".join(col_titles))
    check("Output Role is withheld, the writers cannot do HDR",
          "outputRole" not in w.widgets)

    # Numeric parameters are sliders with a reset, not spin boxes.
    sliders = [n for n, (k, x) in w.widgets.items() if isinstance(x, gui.SliderRow)]
    check("numeric parameters are sliders", len(sliders) > 0, f"{len(sliders)} sliders")
    if sliders:
        s = w.widgets[sliders[0]][1]
        s.setBaseline(s.value())
        start = s.value()
        s.slider.setValue(s.slider.value() + 100)
        moved = s.value()
        check("dragging a slider changes its value", moved != start,
              "%.4g -> %.4g" % (start, moved))
        check("reset is offered once moved", s.reset.isEnabled())
        s.reset.click()
        check("reset returns to the preset value", abs(s.value() - start) < 1e-9,
              "%.4g" % s.value())

    # Choosing a preset for an effect that is off used to do nothing at all.
    w.chk_all.setChecked(True)
    if "quickVignettePreset" in w.widgets and "quickVignetteEnabled" in w.widgets:
        enable = w.widgets["quickVignetteEnabled"][1]
        enable.setChecked(False)
        w.dirty.discard("quickVignetteEnabled")
        w.widgets["quickVignettePreset"][1].setCurrentText("Vintage Mechanical")
        check("choosing an effect preset switches the effect on", enable.isChecked())
    w.chk_all.setChecked(False)

    w.cb_vignette.setCurrentText("Vintage Mechanical")
    check("a lens dropdown arms the lens pass", w.chk_lens.isChecked())
    check("the EV bias box sits left of the two pass boxes",
          [b.text() for b in (w.chk_evbias, w.chk_diffuse, w.chk_lens)]
          == ["EV bias", "Diffuse pass", "Lens pass"])
    w.chk_evbias.setChecked(True)
    check("the EV bias box reaches the session", w.session()["raw_exposure_bias"])
    w.chk_evbias.setChecked(False)

    # The box only does anything for raw, so it should say so rather than
    # looking available and doing nothing.
    w.meta = {"is_raw": False, "exposure_bias": -1.0}
    w.source_path = h.photo
    w.update_evbias_state()
    check("EV bias is disabled for non-raw", not w.chk_evbias.isEnabled(),
          w.chk_evbias.text())
    w.meta = {"is_raw": True, "exposure_bias": -1.0}
    w.update_evbias_state()
    check("EV bias is available for raw with a bias", w.chk_evbias.isEnabled())
    w.meta = {"is_raw": True, "exposure_bias": 0.0}
    w.update_evbias_state()
    check("EV bias is disabled when no bias was dialled in",
          not w.chk_evbias.isEnabled())
    check("the armed pass reaches the session",
          any(c.get("bundle") == "lens" for c in w.session().get("chain", [])))
    w.cb_vignette.setCurrentText("Off")
    w.chk_lens.setChecked(False)

    for name in ("light", "dark"):
        w.set_theme(name)
        check(f"{name} theme applies", w.theme == name,
              f"button shows {w.b_theme.text()!r}")

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

    # Dependent dropdowns: picking a Stock Category rewrites the Stock list on
    # the plugin's side, and the panel has to re-read it.  Hosts get this wrong
    # - the same stale menu shows up in Natron.
    if "filmCategory" in w.widgets and "film" in w.widgets:
        cat = w.widgets["filmCategory"][1]
        stock = w.widgets["film"][1]
        seen = {}
        for name in ("B&W Still Film", "Motion Picture", "Positive/Slide Film"):
            cat.setCurrentText(name)
            seen[name] = [stock.itemText(i) for i in range(stock.count())]
        check("changing Stock Category rewrites the Stock list",
              len({len(v) for v in seen.values()}) > 1,
              ", ".join(f"{k}={len(v)}" for k, v in seen.items()))
        check("the rewritten list holds the right stocks",
              any("Ilford" in s for s in seen["B&W Still Film"])
              and any("Vision3" in s for s in seen["Motion Picture"]))
        w.dirty.discard("filmCategory")

    # The plugin declares "Print" in three separate runs and "Film" in two, so
    # a panel that starts a section whenever the parent changes shows those
    # titles several times over.  One section per group, each titled once.
    from PyQt5.QtCore import QCoreApplication, QEventLoop
    from PyQt5.QtWidgets import QToolButton
    import collections

    def drain():
        for _ in range(5):
            QCoreApplication.sendPostedEvents(None, 0)
            QCoreApplication.processEvents(QEventLoop.AllEvents, 50)

    for show_all in (False, True):
        w.chk_all.setChecked(show_all)
        drain()
        titles = [h.text().rsplit("  (", 1)[0]
                  for h in w.param_host.findChildren(QToolButton)
                  if h.objectName() == "groupHead"]
        dupes = {k: v for k, v in collections.Counter(titles).items() if v > 1}
        check(f"group titles unique ({'all' if show_all else 'quick'} mode)",
              not dupes, f"{len(titles)} sections" + (f", repeated: {dupes}" if dupes else ""))
    w.chk_all.setChecked(False)
    drain()

    # A session may carry any of the 830 parameters, including ones the panel
    # does not display.  Those must survive import rather than be dropped.
    hidden = dict(s, params={"filmExposureEv": "1.5"})
    w.apply_session(hidden)
    kept = w.session()["params"].get("filmExposureEv")
    check("session keeps parameters the panel does not show", kept == "1.5", f"kept={kept!r}")
    out = h.rend.render(h.small, w.current_sets())
    check("hidden parameter reaches the plugin", not np.array_equal(gui_px, out),
          "mean diff %.5f" % float(np.abs(gui_px - out).mean()))


# ------------------------------------------------- checks that need no plugin
#
# The plugin cannot live in the repository, so CI has no bundle to load.  These
# exercise everything around it: colour, file formats, sessions, and that the
# host binary was built and fails intelligibly when there is nothing to load.


def test_colour_maths():
    x = np.linspace(0, 1, 1024, dtype=np.float32)
    back = cli.linear_to_srgb(cli.srgb_to_linear(x))
    check("sRGB transfer round-trips", float(np.abs(back - x).max()) < 1e-5,
          "max error %.2e" % float(np.abs(back - x).max()))
    # The two anchors everyone checks a transfer against.
    check("sRGB 0.5 decodes near 0.214",
          abs(float(cli.srgb_to_linear(np.float32(0.5))) - 0.2140) < 1e-3)
    check("black and white are fixed points",
          float(cli.srgb_to_linear(np.float32(0.0))) == 0.0
          and abs(float(cli.linear_to_srgb(np.float32(1.0))) - 1.0) < 1e-6)


def test_sfraw_roundtrip():
    import struct
    a = np.random.default_rng(0).random((17, 23, 4)).astype(np.float32)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.sfraw"
        with open(p, "wb") as f:
            h, w, c = a.shape
            f.write(b"SFRW")
            f.write(struct.pack("<III", w, h, c))
            f.write(np.ascontiguousarray(a[::-1], dtype="<f4").tobytes())
        with open(p, "rb") as f:
            assert f.read(4) == b"SFRW"
            w, h, c = struct.unpack("<III", f.read(12))
            back = np.frombuffer(f.read(w * h * c * 4), dtype="<f4").reshape(h, w, c)[::-1]
    check("sfraw survives a round trip", np.array_equal(a, back), f"{a.shape}")


def test_resize():
    a = np.zeros((200, 400, 3), np.float32)
    a[..., 0] = np.linspace(0, 1, 400)[None, :]
    small = cli.resize_linear(a, 100)
    check("resize honours the long edge", small.shape[:2] == (50, 100), str(small.shape))
    check("resize keeps the value range",
          0.0 <= float(small.min()) and float(small.max()) <= 1.0)
    check("resize leaves a small image alone", cli.resize_linear(a, 4000) is a)


def test_session_model():
    s = cli.default_session()
    check("a default session has the expected keys",
          {"preset", "params", "seed", "chain", "output_colorspace"} <= set(s))
    merged = cli.load_session('{"seed": 3, "preset": {"selection": "X"}}')
    check("loading merges into the defaults",
          merged["seed"] == 3 and merged["preset"]["selection"] == "X"
          and "category" in merged["preset"])
    sets = dict(cli.session_to_sets(cli.default_session() | {"seed": 11}))
    check("a seed expands to every seed parameter",
          all(n in sets for n in cli.SEED_PARAMS), f"{len(cli.SEED_PARAMS)} parameters")
    check("the same seed always expands the same way",
          cli.seed_sets(11) == cli.seed_sets(11) and cli.seed_sets(11) != cli.seed_sets(12))
    for bad in ("{not json", "[]", '{"version": 999}'):
        try:
            cli.load_session(bad)
            check(f"rejects {bad!r}", False)
        except SystemExit:
            check(f"rejects {bad!r}", True)


def test_image_formats():
    from PIL import Image
    a = np.zeros((32, 64, 3), np.float32)
    a[..., 1] = np.linspace(0, 1, 64)[None, :]
    with tempfile.TemporaryDirectory() as td:
        for ext, depth in ((".jpg", 8), (".png", 8), (".png", 16), (".tif", 16)):
            p = Path(td) / f"o{depth}{ext}"
            cli.save_photo(p, a, depth, 92, {})
            check(f"writes {ext} at {depth}-bit", p.exists() and p.stat().st_size > 0)
        deep = Path(td) / "d.png"
        cli.save_photo(deep, a, 16, 92, {})
        head = deep.read_bytes()[:26]
        if __import__("shutil").which("convert"):
            check("16-bit PNG really has 16-bit samples",
                  head[24] == 16 and head[25] == 2, f"depth {head[24]}")
        else:
            results.append(("skip", "16-bit PNG", "no ImageMagick"))
            print("  skip 16-bit PNG   (no ImageMagick)")

        # and read one back in
        src = Path(td) / "in.png"
        Image.fromarray((cli.linear_to_srgb(a) * 255 + 0.5).astype(np.uint8)).save(src)
        lin, cs, meta = cli.load_photo(src)
        check("reads a PNG back as linear", lin.shape == a.shape and "Linear" in cs,
              f"{lin.shape} {cs}")
        check("metadata describes it", "decoded" in cli.describe_image(src))


def test_exposure_bias_maths():
    a = np.full((4, 4, 3), 0.1, np.float32)
    out, note = cli.apply_exposure_bias(a, {"is_raw": True, "exposure_bias": -1.0})
    check("a -1 EV bias doubles the raw", abs(float(out.mean()) - 0.2) < 1e-5, note)
    out, note = cli.apply_exposure_bias(a, {"is_raw": True, "exposure_bias": 1.0})
    check("a +1 EV bias halves it", abs(float(out.mean()) - 0.05) < 1e-5, note)
    out, _ = cli.apply_exposure_bias(a, {"is_raw": False, "exposure_bias": -1.0})
    check("a non-raw file is untouched", out is a)
    out, _ = cli.apply_exposure_bias(a, {"is_raw": True, "exposure_bias": None})
    check("no recorded bias means no change", out is a)


def test_host_binary():
    import subprocess as sp
    exe = cli.find_renderer()
    check("the host binary was built", exe.exists(), str(exe))
    r = sp.run([str(exe), "--help"], capture_output=True, text=True)
    check("it prints usage", r.returncode == 0 and "spektra-render" in r.stdout)
    r = sp.run([str(exe), "--bundle", "/nonexistent.ofx.bundle", "--list-plugins"],
               capture_output=True, text=True)
    check("it fails intelligibly on a missing bundle",
          r.returncode != 0 and "spektra-render:" in r.stderr,
          r.stderr.strip()[:60])
    r = sp.run([str(exe)], capture_output=True, text=True)
    check("it asks for a bundle when given none",
          r.returncode == 2 and "--bundle" in r.stderr)


def test_cli_entry():
    import subprocess as sp
    r = sp.run([sys.executable, str(ROOT / "spektra"), "--help"],
               capture_output=True, text=True)
    check("the CLI prints usage", r.returncode == 0 and "--session" in r.stdout)
    check("the examples mention the companion passes", "--chain" in r.stdout)


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

    try:
        h = Harness(photo, args.bundle)
        print(f"bundle: {h.rend.bundle}\n")
    except SystemExit as e:
        h = None
        print(f"bundle: not found - {e}\n"
              f"        running only the checks that need no plugin\n")

    presets = [("Creative", "Chromium-Noir"), ("Creative", "Marty - Warm")]
    spaces = ["Linear Rec.709", "sRGB"]
    if not args.quick:
        presets += [("Creative", "OIL!"), ("Creative", "Vintage Faded"),
                    ("Clean Slate", "Clean Slate")]
        spaces += ["ACEScg"]

    offline = [
        ("colour", test_colour_maths),
        ("sfraw", test_sfraw_roundtrip),
        ("resize", test_resize),
        ("session-model", test_session_model),
        ("formats", test_image_formats),
        ("bias-maths", test_exposure_bias_maths),
        ("binary", test_host_binary),
        ("cli", test_cli_entry),
    ]

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
        ("bias", lambda: test_exposure_bias(h, args.raw)),
        ("stocks", lambda: test_dependent_choices(h)),
        ("chain", lambda: test_chain(h)),
        ("encoding", lambda: test_output_encoding(h)),
        ("gui", lambda: test_gui_model(h)),
        ("tabs", lambda: test_tabbed_layout(h)),
        ("crop", lambda: test_crop(h)),
        ("provenance", lambda: test_provenance(h)),
        ("metadata", lambda: test_metadata_carried(h, args.raw)),
        ("compare", lambda: test_compare_shows_the_original(h)),
        ("zoom", lambda: test_zoom_and_pan(h)),
        ("buttons", lambda: test_push_buttons(h)),
    ]

    for name, fn in offline + (suite if h else []):
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

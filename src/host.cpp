// Loading a bundle and walking it through the OFX action sequence.
#include "host.h"

#include <dlfcn.h>

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <filesystem>
#include <sstream>

namespace spektra {
namespace fs = std::filesystem;

namespace {

std::string lower(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(),
                 [](unsigned char c) { return (char)std::tolower(c); });
  return s;
}

std::string trim(const std::string &s) {
  size_t b = s.find_first_not_of(" \t\r\n");
  if (b == std::string::npos) return "";
  size_t e = s.find_last_not_of(" \t\r\n");
  return s.substr(b, e - b + 1);
}

const char *statusName(OfxStatus s) {
  switch (s) {
    case kOfxStatOK: return "kOfxStatOK";
    case kOfxStatFailed: return "kOfxStatFailed";
    case kOfxStatErrFatal: return "kOfxStatErrFatal";
    case kOfxStatErrUnknown: return "kOfxStatErrUnknown";
    case kOfxStatErrMissingHostFeature: return "kOfxStatErrMissingHostFeature";
    case kOfxStatErrUnsupported: return "kOfxStatErrUnsupported";
    case kOfxStatErrExists: return "kOfxStatErrExists";
    case kOfxStatErrFormat: return "kOfxStatErrFormat";
    case kOfxStatErrMemory: return "kOfxStatErrMemory";
    case kOfxStatErrBadHandle: return "kOfxStatErrBadHandle";
    case kOfxStatErrBadIndex: return "kOfxStatErrBadIndex";
    case kOfxStatErrValue: return "kOfxStatErrValue";
    case kOfxStatReplyYes: return "kOfxStatReplyYes";
    case kOfxStatReplyNo: return "kOfxStatReplyNo";
    case kOfxStatReplyDefault: return "kOfxStatReplyDefault";
    default: return "unrecognised status";
  }
}

// An action that a plugin does not implement replies Default, which is fine.
bool actionOK(OfxStatus s) {
  return s == kOfxStatOK || s == kOfxStatReplyDefault;
}

}  // namespace

// -------------------------------------------------------------- construction

Host::Host() {
  hostProps.setString(kOfxPropType, 0, kOfxTypeImageEffectHost);
  hostProps.setString(kOfxPropName, 0, "de.114c.ofx-photo");
  hostProps.setString(kOfxPropLabel, 0, "ofx-photo");
  hostProps.setInt(kOfxPropVersion, 0, 0);
  hostProps.setInt(kOfxPropVersion, 1, 1);
  hostProps.setInt(kOfxPropVersion, 2, 0);
  hostProps.setString(kOfxPropVersionLabel, 0, "0.1.0");
  hostProps.setInt(kOfxPropAPIVersion, 0, 1);
  hostProps.setInt(kOfxPropAPIVersion, 1, 4);

  // A still renderer: no UI, no timeline, whole frame at a time.
  hostProps.setInt(kOfxImageEffectHostPropIsBackground, 0, 1);
  hostProps.setInt(kOfxImageEffectPropSupportsOverlays, 0, 0);
  hostProps.setInt(kOfxImageEffectPropSupportsMultiResolution, 0, 0);
  hostProps.setInt(kOfxImageEffectPropSupportsTiles, 0, 0);
  hostProps.setInt(kOfxImageEffectPropTemporalClipAccess, 0, 0);
  hostProps.setInt(kOfxImageEffectPropSupportsMultipleClipDepths, 0, 0);
  hostProps.setInt(kOfxImageEffectPropSupportsMultipleClipPARs, 0, 0);
  hostProps.setInt(kOfxImageEffectPropSetableFrameRate, 0, 0);
  hostProps.setInt(kOfxImageEffectPropSetableFielding, 0, 0);
  hostProps.setString(kOfxImageEffectHostPropNativeOrigin, 0,
                      kOfxHostNativeOriginBottomLeft);

  hostProps.setStrings(kOfxImageEffectPropSupportedComponents,
                       {kOfxImageComponentRGBA, kOfxImageComponentRGB,
                        kOfxImageComponentAlpha});
  hostProps.setStrings(kOfxImageEffectPropSupportedContexts,
                       {kOfxImageEffectContextFilter, kOfxImageEffectContextGeneral});
  hostProps.setStrings(kOfxImageEffectPropSupportedPixelDepths, {kOfxBitDepthFloat});

  // Parameters: values only, no animation, since a photo is a single frame.
  hostProps.setInt(kOfxParamHostPropSupportsCustomAnimation, 0, 0);
  hostProps.setInt(kOfxParamHostPropSupportsStringAnimation, 0, 0);
  hostProps.setInt(kOfxParamHostPropSupportsBooleanAnimation, 0, 0);
  hostProps.setInt(kOfxParamHostPropSupportsChoiceAnimation, 0, 0);
  hostProps.setInt(kOfxParamHostPropSupportsCustomInteract, 0, 0);
  hostProps.setInt(kOfxParamHostPropMaxParameters, 0, -1);
  hostProps.setInt(kOfxParamHostPropMaxPages, 0, 0);
  hostProps.setInt(kOfxParamHostPropPageRowColumnCount, 0, 0);
  hostProps.setInt(kOfxParamHostPropPageRowColumnCount, 1, 0);

  // We render on the CPU; the plugin's own Vulkan work is internal to it.
  hostProps.setString(kOfxImageEffectPropOpenCLRenderSupported, 0, "false");
  hostProps.setString(kOfxImageEffectPropCudaRenderSupported, 0, "false");
  hostProps.setString(kOfxImageEffectPropCudaStreamSupported, 0, "false");
  hostProps.setString(kOfxImageEffectPropMetalRenderSupported, 0, "false");
  hostProps.setInt(kOfxImageEffectPropOpenGLRenderSupported, 0, 0);

  // Hand it plain linear pixels; its own input-colourspace control does the rest.
  hostProps.setString(kOfxImageEffectPropColourManagementStyle, 0,
                      kOfxImageEffectColourManagementNone);

  hostProps.setPointer(kOfxPropHostOSHandle, 0, nullptr);

  ofxHost.host = reinterpret_cast<OfxPropertySetHandle>(&hostProps);
  ofxHost.fetchSuite = hostFetchSuite;
  current = this;
}

Host::~Host() {
  destroyInstance();
  if (plugin_ && loaded_) plugin_->mainEntry(kOfxActionUnload, nullptr, nullptr, nullptr);
  if (dso_) dlclose(dso_);
  if (current == this) current = nullptr;
}

// ------------------------------------------------------------ bundle loading

bool Host::loadBundle(const std::string &bundlePath, std::string *err) {
  fs::path root(bundlePath);
  fs::path binDir = root / "Contents" / "Linux-x86-64";

  // Accept either the bundle directory or the .ofx binary itself.
  fs::path binary;
  if (root.extension() == ".ofx" && fs::is_regular_file(root)) {
    binary = root;
  } else if (fs::is_directory(binDir)) {
    for (const auto &e : fs::directory_iterator(binDir)) {
      if (e.path().extension() == ".ofx") { binary = e.path(); break; }
    }
  }
  if (binary.empty()) {
    *err = "no .ofx binary under " + binDir.string();
    return false;
  }

  dso_ = dlopen(binary.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!dso_) {
    *err = std::string("dlopen failed: ") + dlerror();
    return false;
  }

  auto getCount = (int (*)())dlsym(dso_, "OfxGetNumberOfPlugins");
  auto getPlugin = (OfxPlugin * (*)(int)) dlsym(dso_, "OfxGetPlugin");
  if (!getCount || !getPlugin) {
    *err = "not an OFX plugin: OfxGetNumberOfPlugins/OfxGetPlugin missing";
    return false;
  }

  const int n = getCount();
  for (int i = 0; i < n; ++i) {
    if (OfxPlugin *p = getPlugin(i)) plugins_.push_back(p);
  }
  if (plugins_.empty()) {
    *err = "the bundle exports no plugins";
    return false;
  }
  return true;
}

std::vector<std::string> Host::pluginIdentifiers() const {
  std::vector<std::string> out;
  for (OfxPlugin *p : plugins_) {
    std::ostringstream os;
    os << p->pluginIdentifier << "\tv" << p->pluginVersionMajor << "."
       << p->pluginVersionMinor << "\tapi=" << p->pluginApi << " v" << p->apiVersion;
    out.push_back(os.str());
  }
  return out;
}

bool Host::callAction(const char *action, void *handle, PropSet *in, PropSet *out,
                      OfxStatus *status) {
  OfxStatus s = plugin_->mainEntry(
      action, handle, reinterpret_cast<OfxPropertySetHandle>(in),
      reinterpret_cast<OfxPropertySetHandle>(out));
  if (status) *status = s;
  return actionOK(s);
}

// ------------------------------------------------------- describe & instance

bool Host::createInstance(const std::string &identifier, std::string *err) {
  plugin_ = nullptr;
  for (OfxPlugin *p : plugins_) {
    if (identifier.empty() || identifier == p->pluginIdentifier) { plugin_ = p; break; }
  }
  if (!plugin_) {
    *err = "no plugin with identifier '" + identifier + "' in this bundle";
    return false;
  }

  plugin_->setHost(&ofxHost);

  OfxStatus s;
  if (!callAction(kOfxActionLoad, nullptr, nullptr, nullptr, &s)) {
    *err = std::string("kOfxActionLoad rejected the host: ") + statusName(s);
    if (!lastPluginMessage.empty()) *err += " (" + lastPluginMessage + ")";
    return false;
  }
  loaded_ = true;

  // ---- describe
  descriptor_ = std::make_unique<Effect>();
  descriptor_->params.owner = descriptor_.get();
  descriptor_->props.setString(kOfxPropType, 0, kOfxTypeImageEffect);
  insideDescribe = true;
  bool ok = callAction(kOfxActionDescribe, descriptor_.get(), nullptr, nullptr, &s);
  insideDescribe = false;
  if (!ok) {
    *err = std::string("kOfxActionDescribe failed: ") + statusName(s);
    if (!lastPluginMessage.empty()) *err += " (" + lastPluginMessage + ")";
    return false;
  }

  // The plugin must actually offer the Filter context.
  auto contexts = descriptor_->props.getStrings(kOfxImageEffectPropSupportedContexts);
  bool hasFilter = false;
  for (const auto &c : contexts) {
    if (c == kOfxImageEffectContextFilter) hasFilter = true;
  }
  if (!hasFilter && !contexts.empty()) {
    *err = "plugin does not support the Filter context";
    return false;
  }

  // ---- describe in context
  contextDescriptor_ = std::make_unique<Effect>();
  contextDescriptor_->params.owner = contextDescriptor_.get();
  contextDescriptor_->props = descriptor_->props;
  contextDescriptor_->props.setString(kOfxPropType, 0, kOfxTypeImageEffect);
  contextDescriptor_->props.setString(kOfxImageEffectPropContext, 0,
                                      kOfxImageEffectContextFilter);

  PropSet inArgs;
  inArgs.setString(kOfxImageEffectPropContext, 0, kOfxImageEffectContextFilter);
  insideDescribe = true;
  ok = callAction(kOfxImageEffectActionDescribeInContext, contextDescriptor_.get(),
                  &inArgs, nullptr, &s);
  insideDescribe = false;
  if (!ok) {
    *err = std::string("describeInContext failed: ") + statusName(s);
    if (!lastPluginMessage.empty()) *err += " (" + lastPluginMessage + ")";
    return false;
  }

  // ---- build the instance from the context descriptor
  instance_ = std::make_unique<Effect>();
  instance_->params.owner = instance_.get();
  instance_->props = contextDescriptor_->props;
  instance_->props.setString(kOfxPropType, 0, kOfxTypeImageEffectInstance);
  instance_->props.setString(kOfxImageEffectPropContext, 0, kOfxImageEffectContextFilter);
  instance_->props.setInt(kOfxPropIsInteractive, 0, 0);
  instance_->props.setDouble(kOfxImageEffectPropFrameRate, 0, 25.0);
  instance_->props.setDouble(kOfxImageEffectPropProjectPixelAspectRatio, 0, 1.0);
  instance_->props.setInt(kOfxImageEffectInstancePropSequentialRender, 0, 0);

  for (const auto &src : contextDescriptor_->params.order) {
    auto p = std::make_unique<Param>();
    p->name = src->name;
    p->type = src->type;
    p->props = src->props;

    // Seed the current value from the declared default.
    const PropSet &d = p->props;
    if (p->type == kOfxParamTypeString) {
      p->vs = d.getString(kOfxParamPropDefault, 0, "");
    } else if (p->isDoubleType()) {
      p->vd.assign(p->components(), 0.0);
      for (int k = 0; k < p->components(); ++k)
        p->vd[k] = d.getDouble(kOfxParamPropDefault, k, 0.0);
    } else if (p->isIntType()) {
      p->vi.assign(p->components(), 0);
      for (int k = 0; k < p->components(); ++k)
        p->vi[k] = d.getInt(kOfxParamPropDefault, k, 0);
    }

    Param *raw = p.get();
    instance_->params.order.push_back(std::move(p));
    instance_->params.byName[raw->name] = raw;
  }

  for (const auto &src : contextDescriptor_->clipOrder) {
    Clip *c = instance_->defineClip(src->name);
    c->props = src->props;
  }

  if (!callAction(kOfxActionCreateInstance, instance_.get(), nullptr, nullptr, &s)) {
    *err = std::string("kOfxActionCreateInstance failed: ") + statusName(s);
    if (!lastPluginMessage.empty()) *err += " (" + lastPluginMessage + ")";
    return false;
  }
  return true;
}

void Host::destroyInstance() {
  if (instance_ && plugin_) {
    plugin_->mainEntry(kOfxActionDestroyInstance, instance_.get(), nullptr, nullptr);
  }
  instance_.reset();
}

// ------------------------------------------------------------ setting values

bool Host::setParamFromString(const std::string &name, const std::string &value,
                              std::string *err) {
  Param *p = instance_->params.find(name);
  if (!p) {
    *err = "no such parameter: " + name;
    return false;
  }

  if (p->type == kOfxParamTypeString) {
    p->vs = value;
  } else if (p->type == kOfxParamTypeChoice) {
    // Accept the visible label or a raw index; labels are friendlier.
    auto options = p->props.getStrings(kOfxParamPropChoiceOption);
    int index = -1;
    for (size_t i = 0; i < options.size(); ++i) {
      if (lower(trim(options[i])) == lower(trim(value))) { index = (int)i; break; }
    }
    if (index < 0) {
      char *end = nullptr;
      long n = std::strtol(value.c_str(), &end, 10);
      if (end && *end == '\0' && n >= 0 && n < (long)options.size()) index = (int)n;
    }
    if (index < 0) {
      *err = "'" + value + "' is not one of the " + std::to_string(options.size()) +
             " options for " + name;
      return false;
    }
    p->vi.assign(1, index);
  } else if (p->type == kOfxParamTypeBoolean) {
    std::string v = lower(trim(value));
    p->vi.assign(1, (v == "1" || v == "true" || v == "yes" || v == "on") ? 1 : 0);
  } else if (p->isDoubleType() || p->isIntType()) {
    // Multi-component values arrive comma separated.
    std::vector<double> parts;
    std::stringstream ss(value);
    std::string item;
    while (std::getline(ss, item, ',')) parts.push_back(std::strtod(trim(item).c_str(), nullptr));
    if (parts.empty()) {
      *err = "could not read a number from '" + value + "' for " + name;
      return false;
    }
    const int n = p->components();
    if (p->isDoubleType()) {
      p->vd.assign(n, parts.back());
      for (int k = 0; k < n && k < (int)parts.size(); ++k) p->vd[k] = parts[k];
    } else {
      p->vi.assign(n, (int)parts.back());
      for (int k = 0; k < n && k < (int)parts.size(); ++k) p->vi[k] = (int)parts[k];
    }
  } else if (p->type != kOfxParamTypePushButton) {
    *err = "parameter " + name + " has type " + p->type + ", which takes no value";
    return false;
  }

  // Tell the plugin, exactly as a GUI host would.  This is where it reloads
  // presets, refreshes dropdowns and recomputes derived state.
  PropSet in;
  in.setString(kOfxPropType, 0, kOfxTypeParameter);
  in.setString(kOfxPropName, 0, name);
  in.setString(kOfxPropChangeReason, 0, kOfxChangeUserEdited);
  in.setDouble(kOfxPropTime, 0, 0.0);
  in.setDouble(kOfxImageEffectPropRenderScale, 0, 1.0);
  in.setDouble(kOfxImageEffectPropRenderScale, 1, 1.0);

  plugin_->mainEntry(kOfxActionBeginInstanceChanged, instance_.get(),
                     reinterpret_cast<OfxPropertySetHandle>(&in), nullptr);
  OfxStatus s = plugin_->mainEntry(kOfxActionInstanceChanged, instance_.get(),
                                   reinterpret_cast<OfxPropertySetHandle>(&in), nullptr);
  plugin_->mainEntry(kOfxActionEndInstanceChanged, instance_.get(),
                     reinterpret_cast<OfxPropertySetHandle>(&in), nullptr);
  if (!actionOK(s)) {
    *err = std::string("the plugin rejected a change to ") + name + ": " + statusName(s);
    if (!lastPluginMessage.empty()) *err += " (" + lastPluginMessage + ")";
    return false;
  }
  return true;
}

// ------------------------------------------------------------------ rendering

bool Host::render(Image &source, Image &output, std::string *err) {
  Clip *src = instance_->clip("Source");
  Clip *dst = instance_->clip("Output");
  if (!src || !dst) {
    *err = "the plugin did not define the Source and Output clips";
    return false;
  }

  output.allocate(source.width, source.height, source.components);

  for (Clip *c : {src, dst}) {
    c->props.setString(kOfxImageEffectPropPixelDepth, 0, kOfxBitDepthFloat);
    c->props.setString(kOfxImageEffectPropComponents, 0,
                       source.components == 4 ? kOfxImageComponentRGBA
                                              : kOfxImageComponentRGB);
    c->props.setString(kOfxImageEffectPropPreMultiplication, 0, kOfxImageUnPreMultiplied);
    c->props.setDouble(kOfxImagePropPixelAspectRatio, 0, 1.0);
    c->props.setDouble(kOfxImageEffectPropFrameRate, 0, 25.0);
    c->props.setInt(kOfxImageClipPropConnected, 0, 1);
    c->props.setInt(kOfxImageClipPropContinuousSamples, 0, 0);
    c->props.setString(kOfxImageClipPropFieldOrder, 0, kOfxImageFieldNone);
    c->props.setInt(kOfxImageEffectPropProjectSize, 0, source.width);
    c->props.setInt(kOfxImageEffectPropProjectSize, 1, source.height);
  }
  src->bound = &source;
  dst->bound = &output;

  instance_->props.setInt(kOfxImageEffectPropProjectSize, 0, source.width);
  instance_->props.setInt(kOfxImageEffectPropProjectSize, 1, source.height);
  instance_->props.setInt(kOfxImageEffectPropProjectExtent, 0, source.width);
  instance_->props.setInt(kOfxImageEffectPropProjectExtent, 1, source.height);
  instance_->props.setInt(kOfxImageEffectPropProjectOffset, 0, 0);
  instance_->props.setInt(kOfxImageEffectPropProjectOffset, 1, 0);

  OfxStatus s;

  // Let the plugin state its preferences; ignoring a Default reply is fine.
  PropSet prefsOut;
  callAction(kOfxImageEffectActionGetClipPreferences, instance_.get(), nullptr,
             &prefsOut, &s);

  OfxRangeD range{0.0, 0.0};
  PropSet beginArgs;
  beginArgs.setDouble(kOfxImageEffectPropFrameRange, 0, range.min);
  beginArgs.setDouble(kOfxImageEffectPropFrameRange, 1, range.max);
  beginArgs.setDouble(kOfxImageEffectPropFrameStep, 0, 1.0);
  beginArgs.setInt(kOfxPropIsInteractive, 0, 0);
  beginArgs.setDouble(kOfxImageEffectPropRenderScale, 0, 1.0);
  beginArgs.setDouble(kOfxImageEffectPropRenderScale, 1, 1.0);
  beginArgs.setInt(kOfxImageEffectPropSequentialRenderStatus, 0, 0);
  beginArgs.setInt(kOfxImageEffectPropInteractiveRenderStatus, 0, 0);
  beginArgs.setInt(kOfxImageEffectPropRenderQualityDraft, 0, 0);
  callAction(kOfxImageEffectActionBeginSequenceRender, instance_.get(), &beginArgs,
             nullptr, &s);

  PropSet renderArgs;
  renderArgs.setDouble(kOfxPropTime, 0, 0.0);
  renderArgs.setDouble(kOfxImageEffectPropRenderScale, 0, 1.0);
  renderArgs.setDouble(kOfxImageEffectPropRenderScale, 1, 1.0);
  renderArgs.setInt(kOfxImageEffectPropRenderWindow, 0, 0);
  renderArgs.setInt(kOfxImageEffectPropRenderWindow, 1, 0);
  renderArgs.setInt(kOfxImageEffectPropRenderWindow, 2, source.width);
  renderArgs.setInt(kOfxImageEffectPropRenderWindow, 3, source.height);
  renderArgs.setString(kOfxImageEffectPropFieldToRender, 0, kOfxImageFieldNone);
  renderArgs.setInt(kOfxImageEffectPropSequentialRenderStatus, 0, 0);
  renderArgs.setInt(kOfxImageEffectPropInteractiveRenderStatus, 0, 0);
  renderArgs.setInt(kOfxImageEffectPropRenderQualityDraft, 0, 0);
  renderArgs.setInt(kOfxImageEffectPropOpenGLEnabled, 0, 0);
  renderArgs.setInt(kOfxImageEffectPropCudaEnabled, 0, 0);
  renderArgs.setInt(kOfxImageEffectPropMetalEnabled, 0, 0);
  renderArgs.setInt(kOfxImageEffectPropOpenCLEnabled, 0, 0);

  bool ok = callAction(kOfxImageEffectActionRender, instance_.get(), &renderArgs,
                       nullptr, &s);

  callAction(kOfxImageEffectActionEndSequenceRender, instance_.get(), &beginArgs,
             nullptr, nullptr);

  src->bound = nullptr;
  dst->bound = nullptr;

  if (!ok) {
    *err = std::string("render failed: ") + statusName(s);
    if (!lastPluginMessage.empty()) *err += " (" + lastPluginMessage + ")";
    return false;
  }
  return true;
}

}  // namespace spektra

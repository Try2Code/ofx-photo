// Minimal OpenFX host: just enough to describe and render one still frame
// through a single Filter-context plugin.
#pragma once

#include <cstdarg>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "ofxCore.h"
#include "ofxImageEffect.h"
#include "ofxMemory.h"
#include "ofxMessage.h"
#include "ofxMultiThread.h"
#include "ofxParam.h"
#include "ofxProgress.h"
#include "ofxProperty.h"
#include "ofxColour.h"
#include "ofxGPURender.h"

namespace spektra {

// ---------------------------------------------------------------- properties

struct Prop {
  enum Type { Int, Double, String, Pointer } type = Int;
  std::vector<int> i;
  std::vector<double> d;
  std::vector<std::string> s;
  std::vector<void *> p;

  int dimension() const {
    switch (type) {
      case Int: return (int)i.size();
      case Double: return (int)d.size();
      case String: return (int)s.size();
      case Pointer: return (int)p.size();
    }
    return 0;
  }
};

// Every OFX handle we hand out that carries properties is one of these.
struct PropSet {
  std::map<std::string, Prop> props;

  bool has(const std::string &k) const { return props.count(k) != 0; }

  void setInt(const std::string &k, int idx, int v) {
    Prop &pr = props[k];
    pr.type = Prop::Int;
    if ((int)pr.i.size() <= idx) pr.i.resize(idx + 1, 0);
    pr.i[idx] = v;
  }
  void setDouble(const std::string &k, int idx, double v) {
    Prop &pr = props[k];
    pr.type = Prop::Double;
    if ((int)pr.d.size() <= idx) pr.d.resize(idx + 1, 0.0);
    pr.d[idx] = v;
  }
  void setString(const std::string &k, int idx, const std::string &v) {
    Prop &pr = props[k];
    pr.type = Prop::String;
    if ((int)pr.s.size() <= idx) pr.s.resize(idx + 1);
    pr.s[idx] = v;
  }
  void setPointer(const std::string &k, int idx, void *v) {
    Prop &pr = props[k];
    pr.type = Prop::Pointer;
    if ((int)pr.p.size() <= idx) pr.p.resize(idx + 1, nullptr);
    pr.p[idx] = v;
  }
  void setStrings(const std::string &k, const std::vector<std::string> &v) {
    Prop &pr = props[k];
    pr.type = Prop::String;
    pr.s = v;
  }

  int getInt(const std::string &k, int idx = 0, int fallback = 0) const {
    auto it = props.find(k);
    if (it == props.end()) return fallback;
    const Prop &pr = it->second;
    if (pr.type == Prop::Int && idx < (int)pr.i.size()) return pr.i[idx];
    if (pr.type == Prop::Double && idx < (int)pr.d.size()) return (int)pr.d[idx];
    return fallback;
  }
  double getDouble(const std::string &k, int idx = 0, double fallback = 0.0) const {
    auto it = props.find(k);
    if (it == props.end()) return fallback;
    const Prop &pr = it->second;
    if (pr.type == Prop::Double && idx < (int)pr.d.size()) return pr.d[idx];
    if (pr.type == Prop::Int && idx < (int)pr.i.size()) return (double)pr.i[idx];
    return fallback;
  }
  std::string getString(const std::string &k, int idx = 0,
                        const std::string &fallback = std::string()) const {
    auto it = props.find(k);
    if (it == props.end()) return fallback;
    const Prop &pr = it->second;
    if (pr.type == Prop::String && idx < (int)pr.s.size()) return pr.s[idx];
    return fallback;
  }
  std::vector<std::string> getStrings(const std::string &k) const {
    auto it = props.find(k);
    if (it == props.end() || it->second.type != Prop::String) return {};
    return it->second.s;
  }
  int dimension(const std::string &k) const {
    auto it = props.find(k);
    return it == props.end() ? 0 : it->second.dimension();
  }
};

// ------------------------------------------------------------------ entities

struct Param {
  std::string name;
  std::string type;
  PropSet props;          // descriptor properties (label, hint, default, range...)
  std::vector<int> vi;    // current value, integer-flavoured params
  std::vector<double> vd; // current value, double-flavoured params
  std::string vs;         // current value, string params

  bool isDoubleType() const {
    return type == kOfxParamTypeDouble || type == kOfxParamTypeDouble2D ||
           type == kOfxParamTypeDouble3D || type == kOfxParamTypeRGB ||
           type == kOfxParamTypeRGBA;
  }
  bool isIntType() const {
    return type == kOfxParamTypeInteger || type == kOfxParamTypeInteger2D ||
           type == kOfxParamTypeInteger3D || type == kOfxParamTypeBoolean ||
           type == kOfxParamTypeChoice;
  }
  int components() const {
    if (type == kOfxParamTypeDouble2D || type == kOfxParamTypeInteger2D) return 2;
    if (type == kOfxParamTypeDouble3D || type == kOfxParamTypeInteger3D ||
        type == kOfxParamTypeRGB)
      return 3;
    if (type == kOfxParamTypeRGBA) return 4;
    return 1;
  }
};

struct Effect;

struct ParamSet {
  std::vector<std::unique_ptr<Param>> order;
  std::map<std::string, Param *> byName;
  Effect *owner = nullptr; // valid during describe too, unlike Host::instance()

  Param *find(const std::string &n) const {
    auto it = byName.find(n);
    return it == byName.end() ? nullptr : it->second;
  }
};

struct Image;

struct Clip {
  std::string name;
  PropSet props;
  Image *bound = nullptr; // image handed to the plugin during render
};

struct Effect {
  PropSet props;
  ParamSet params;
  std::vector<std::unique_ptr<Clip>> clipOrder;
  std::map<std::string, Clip *> clips;

  Clip *clip(const std::string &n) const {
    auto it = clips.find(n);
    return it == clips.end() ? nullptr : it->second;
  }
  Clip *defineClip(const std::string &n) {
    if (Clip *existing = clip(n)) return existing;
    auto c = std::make_unique<Clip>();
    c->name = n;
    Clip *raw = c.get();
    clipOrder.push_back(std::move(c));
    clips[n] = raw;
    return raw;
  }
};

// A float RGBA image, row 0 = bottom scanline (OFX y-up convention).
struct Image {
  int width = 0, height = 0, components = 4;
  std::vector<float> pixels;
  PropSet props; // handed to the plugin as the image handle

  void allocate(int w, int h, int comps) {
    width = w;
    height = h;
    components = comps;
    pixels.assign((size_t)w * h * comps, 0.0f);
  }
  float *data() { return pixels.data(); }
  int rowBytes() const { return width * components * (int)sizeof(float); }
};

// Implemented in suites.cpp.
const void *hostFetchSuite(OfxPropertySetHandle host, const char *name, int version);

// ------------------------------------------------------------------- the host

class Host {
 public:
  Host();
  ~Host();

  // Load `bundlePath`/Contents/Linux-x86-64/*.ofx and select a plugin.
  bool loadBundle(const std::string &bundlePath, std::string *err);
  std::vector<std::string> pluginIdentifiers() const;

  // describe -> describeInContext(Filter) -> createInstance
  bool createInstance(const std::string &identifier, std::string *err);

  const ParamSet &params() const { return instance_->params; }
  ParamSet &params() { return instance_->params; }

  // Push a value in and notify the plugin, as a GUI host would.
  bool setParamFromString(const std::string &name, const std::string &value,
                          std::string *err);

  // Run the render actions for one frame.
  bool render(Image &source, Image &output, std::string *err);

  void destroyInstance();

  static Host *current;
  OfxHost ofxHost{};
  PropSet hostProps;

  // suite implementations need these
  Effect *instance() { return instance_.get(); }
  std::vector<std::unique_ptr<Image>> imageHandles;
  std::vector<std::unique_ptr<std::vector<char>>> memoryBlocks;
  std::string lastPluginMessage;
  bool insideDescribe = false;

 private:
  bool callAction(const char *action, void *handle, PropSet *in, PropSet *out,
                  OfxStatus *status);

  void *dso_ = nullptr;
  OfxPlugin *plugin_ = nullptr;
  std::vector<OfxPlugin *> plugins_;
  std::unique_ptr<Effect> descriptor_;
  std::unique_ptr<Effect> contextDescriptor_;
  std::unique_ptr<Effect> instance_;
  bool loaded_ = false;
};

}  // namespace spektra

// The OFX suites the host exposes to the plugin.
#include "host.h"

#include <cstdio>
#include <cstdlib>
#include <mutex>

namespace spektra {

Host *Host::current = nullptr;

namespace {

PropSet *ps(OfxPropertySetHandle h) { return reinterpret_cast<PropSet *>(h); }
OfxPropertySetHandle handleOf(PropSet *p) {
  return reinterpret_cast<OfxPropertySetHandle>(p);
}

// Images are handed out as property-set handles; keep the back-reference.
std::map<PropSet *, Image *> gImageByProps;

// ------------------------------------------------------------ property suite

OfxStatus propSetPointer(OfxPropertySetHandle h, const char *p, int i, void *v) {
  if (!h || !p) return kOfxStatErrBadHandle;
  ps(h)->setPointer(p, i, v);
  return kOfxStatOK;
}
OfxStatus propSetString(OfxPropertySetHandle h, const char *p, int i, const char *v) {
  if (!h || !p) return kOfxStatErrBadHandle;
  ps(h)->setString(p, i, v ? v : "");
  return kOfxStatOK;
}
OfxStatus propSetDouble(OfxPropertySetHandle h, const char *p, int i, double v) {
  if (!h || !p) return kOfxStatErrBadHandle;
  ps(h)->setDouble(p, i, v);
  return kOfxStatOK;
}
OfxStatus propSetInt(OfxPropertySetHandle h, const char *p, int i, int v) {
  if (!h || !p) return kOfxStatErrBadHandle;
  ps(h)->setInt(p, i, v);
  return kOfxStatOK;
}
OfxStatus propSetPointerN(OfxPropertySetHandle h, const char *p, int n, void *const *v) {
  for (int k = 0; k < n; ++k) propSetPointer(h, p, k, v[k]);
  return kOfxStatOK;
}
OfxStatus propSetStringN(OfxPropertySetHandle h, const char *p, int n, const char *const *v) {
  for (int k = 0; k < n; ++k) propSetString(h, p, k, v[k]);
  return kOfxStatOK;
}
OfxStatus propSetDoubleN(OfxPropertySetHandle h, const char *p, int n, const double *v) {
  for (int k = 0; k < n; ++k) propSetDouble(h, p, k, v[k]);
  return kOfxStatOK;
}
OfxStatus propSetIntN(OfxPropertySetHandle h, const char *p, int n, const int *v) {
  for (int k = 0; k < n; ++k) propSetInt(h, p, k, v[k]);
  return kOfxStatOK;
}

const Prop *lookup(OfxPropertySetHandle h, const char *p) {
  if (!h || !p) return nullptr;
  auto &m = ps(h)->props;
  auto it = m.find(p);
  return it == m.end() ? nullptr : &it->second;
}

OfxStatus propGetPointer(OfxPropertySetHandle h, const char *p, int i, void **v) {
  const Prop *pr = lookup(h, p);
  if (!pr) return kOfxStatErrUnknown;
  if (pr->type != Prop::Pointer || i >= (int)pr->p.size()) return kOfxStatErrBadIndex;
  *v = pr->p[i];
  return kOfxStatOK;
}
OfxStatus propGetString(OfxPropertySetHandle h, const char *p, int i, char **v) {
  const Prop *pr = lookup(h, p);
  if (!pr) return kOfxStatErrUnknown;
  if (pr->type != Prop::String || i >= (int)pr->s.size()) return kOfxStatErrBadIndex;
  // The spec lets the host own this; it stays valid until the set changes.
  *v = const_cast<char *>(pr->s[i].c_str());
  return kOfxStatOK;
}
OfxStatus propGetDouble(OfxPropertySetHandle h, const char *p, int i, double *v) {
  const Prop *pr = lookup(h, p);
  if (!pr) return kOfxStatErrUnknown;
  if (pr->type == Prop::Double && i < (int)pr->d.size()) { *v = pr->d[i]; return kOfxStatOK; }
  if (pr->type == Prop::Int && i < (int)pr->i.size()) { *v = pr->i[i]; return kOfxStatOK; }
  return kOfxStatErrBadIndex;
}
OfxStatus propGetInt(OfxPropertySetHandle h, const char *p, int i, int *v) {
  const Prop *pr = lookup(h, p);
  if (!pr) return kOfxStatErrUnknown;
  if (pr->type == Prop::Int && i < (int)pr->i.size()) { *v = pr->i[i]; return kOfxStatOK; }
  if (pr->type == Prop::Double && i < (int)pr->d.size()) { *v = (int)pr->d[i]; return kOfxStatOK; }
  return kOfxStatErrBadIndex;
}
OfxStatus propGetPointerN(OfxPropertySetHandle h, const char *p, int n, void **v) {
  for (int k = 0; k < n; ++k) {
    OfxStatus s = propGetPointer(h, p, k, &v[k]);
    if (s != kOfxStatOK) return s;
  }
  return kOfxStatOK;
}
OfxStatus propGetStringN(OfxPropertySetHandle h, const char *p, int n, char **v) {
  for (int k = 0; k < n; ++k) {
    OfxStatus s = propGetString(h, p, k, &v[k]);
    if (s != kOfxStatOK) return s;
  }
  return kOfxStatOK;
}
OfxStatus propGetDoubleN(OfxPropertySetHandle h, const char *p, int n, double *v) {
  for (int k = 0; k < n; ++k) {
    OfxStatus s = propGetDouble(h, p, k, &v[k]);
    if (s != kOfxStatOK) return s;
  }
  return kOfxStatOK;
}
OfxStatus propGetIntN(OfxPropertySetHandle h, const char *p, int n, int *v) {
  for (int k = 0; k < n; ++k) {
    OfxStatus s = propGetInt(h, p, k, &v[k]);
    if (s != kOfxStatOK) return s;
  }
  return kOfxStatOK;
}
OfxStatus propReset(OfxPropertySetHandle h, const char *p) {
  if (!h || !p) return kOfxStatErrBadHandle;
  ps(h)->props.erase(p);
  return kOfxStatOK;
}
OfxStatus propGetDimension(OfxPropertySetHandle h, const char *p, int *c) {
  const Prop *pr = lookup(h, p);
  if (!pr) return kOfxStatErrUnknown;
  *c = pr->dimension();
  return kOfxStatOK;
}

OfxPropertySuiteV1 gPropertySuite = {
    propSetPointer, propSetString,  propSetDouble,   propSetInt,
    propSetPointerN, propSetStringN, propSetDoubleN, propSetIntN,
    propGetPointer, propGetString,  propGetDouble,   propGetInt,
    propGetPointerN, propGetStringN, propGetDoubleN, propGetIntN,
    propReset,      propGetDimension};

// ----------------------------------------------------------- parameter suite

Param *param(OfxParamHandle h) { return reinterpret_cast<Param *>(h); }
ParamSet *paramSet(OfxParamSetHandle h) { return reinterpret_cast<ParamSet *>(h); }

OfxStatus paramDefine(OfxParamSetHandle set, const char *type, const char *name,
                      OfxPropertySetHandle *out) {
  if (!set || !type || !name) return kOfxStatErrBadHandle;
  ParamSet *pset = paramSet(set);
  if (pset->find(name)) return kOfxStatErrExists;

  auto p = std::make_unique<Param>();
  p->name = name;
  p->type = type;
  p->props.setString(kOfxPropType, 0, kOfxTypeParameter);
  p->props.setString(kOfxPropName, 0, name);
  p->props.setString(kOfxParamPropType, 0, type);
  Param *raw = p.get();
  pset->order.push_back(std::move(p));
  pset->byName[name] = raw;
  if (out) *out = handleOf(&raw->props);
  return kOfxStatOK;
}

OfxStatus paramGetHandle(OfxParamSetHandle set, const char *name,
                         OfxParamHandle *out, OfxPropertySetHandle *props) {
  if (!set || !name) return kOfxStatErrBadHandle;
  Param *p = paramSet(set)->find(name);
  if (!p) return kOfxStatErrUnknown;
  if (out) *out = reinterpret_cast<OfxParamHandle>(p);
  if (props) *props = handleOf(&p->props);
  return kOfxStatOK;
}

OfxStatus paramSetGetPropertySet(OfxParamSetHandle set, OfxPropertySetHandle *out) {
  if (!Host::current || !Host::current->instance()) return kOfxStatErrBadHandle;
  *out = handleOf(&Host::current->instance()->props);
  return kOfxStatOK;
}

OfxStatus paramGetPropertySet(OfxParamHandle h, OfxPropertySetHandle *out) {
  if (!h) return kOfxStatErrBadHandle;
  *out = handleOf(&param(h)->props);
  return kOfxStatOK;
}

OfxStatus readValue(Param *p, va_list args) {
  if (p->type == kOfxParamTypeString) {
    char **dst = va_arg(args, char **);
    if (dst) *dst = const_cast<char *>(p->vs.c_str());
    return kOfxStatOK;
  }
  const int n = p->components();
  if (p->isDoubleType()) {
    for (int k = 0; k < n; ++k) {
      double *dst = va_arg(args, double *);
      if (dst) *dst = k < (int)p->vd.size() ? p->vd[k] : 0.0;
    }
    return kOfxStatOK;
  }
  if (p->isIntType()) {
    for (int k = 0; k < n; ++k) {
      int *dst = va_arg(args, int *);
      if (dst) *dst = k < (int)p->vi.size() ? p->vi[k] : 0;
    }
    return kOfxStatOK;
  }
  return kOfxStatOK; // group / page / pushbutton carry no value
}

OfxStatus writeValue(Param *p, va_list args) {
  if (p->type == kOfxParamTypeString) {
    const char *v = va_arg(args, const char *);
    p->vs = v ? v : "";
    return kOfxStatOK;
  }
  const int n = p->components();
  if (p->isDoubleType()) {
    p->vd.assign(n, 0.0);
    for (int k = 0; k < n; ++k) p->vd[k] = va_arg(args, double);
    return kOfxStatOK;
  }
  if (p->isIntType()) {
    p->vi.assign(n, 0);
    for (int k = 0; k < n; ++k) p->vi[k] = va_arg(args, int);
    return kOfxStatOK;
  }
  return kOfxStatOK;
}

OfxStatus paramGetValue(OfxParamHandle h, ...) {
  if (!h) return kOfxStatErrBadHandle;
  va_list args;
  va_start(args, h);
  OfxStatus s = readValue(param(h), args);
  va_end(args);
  return s;
}

OfxStatus paramGetValueAtTime(OfxParamHandle h, OfxTime, ...) {
  if (!h) return kOfxStatErrBadHandle;
  va_list args;
  va_start(args, h);
  // Skip the OfxTime that sits before the varargs in the caller's frame?  No:
  // va_start is anchored on the last named parameter, so args already points
  // at the first vararg.
  OfxStatus s = readValue(param(h), args);
  va_end(args);
  return s;
}

OfxStatus paramSetValue(OfxParamHandle h, ...) {
  if (!h) return kOfxStatErrBadHandle;
  va_list args;
  va_start(args, h);
  OfxStatus s = writeValue(param(h), args);
  va_end(args);
  return s;
}

OfxStatus paramSetValueAtTime(OfxParamHandle h, OfxTime, ...) {
  if (!h) return kOfxStatErrBadHandle;
  va_list args;
  va_start(args, h);
  OfxStatus s = writeValue(param(h), args);
  va_end(args);
  return s;
}

OfxStatus paramGetDerivative(OfxParamHandle, OfxTime, ...) { return kOfxStatFailed; }
OfxStatus paramGetIntegral(OfxParamHandle, OfxTime, OfxTime, ...) { return kOfxStatFailed; }
OfxStatus paramGetNumKeys(OfxParamHandle, unsigned int *n) { if (n) *n = 0; return kOfxStatOK; }
OfxStatus paramGetKeyTime(OfxParamHandle, unsigned int, OfxTime *) { return kOfxStatFailed; }
OfxStatus paramGetKeyIndex(OfxParamHandle, OfxTime, int, int *) { return kOfxStatFailed; }
OfxStatus paramDeleteKey(OfxParamHandle, OfxTime) { return kOfxStatOK; }
OfxStatus paramDeleteAllKeys(OfxParamHandle) { return kOfxStatOK; }
OfxStatus paramCopy(OfxParamHandle to, OfxParamHandle from, OfxTime, const OfxRangeD *) {
  if (!to || !from) return kOfxStatErrBadHandle;
  Param *a = param(to), *b = param(from);
  a->vi = b->vi;
  a->vd = b->vd;
  a->vs = b->vs;
  return kOfxStatOK;
}
OfxStatus paramEditBegin(OfxParamSetHandle, const char *) { return kOfxStatOK; }
OfxStatus paramEditEnd(OfxParamSetHandle) { return kOfxStatOK; }

OfxParameterSuiteV1 gParameterSuite = {
    paramDefine,        paramGetHandle,      paramSetGetPropertySet,
    paramGetPropertySet, paramGetValue,      paramGetValueAtTime,
    paramGetDerivative, paramGetIntegral,    paramSetValue,
    paramSetValueAtTime, paramGetNumKeys,    paramGetKeyTime,
    paramGetKeyIndex,   paramDeleteKey,      paramDeleteAllKeys,
    paramCopy,          paramEditBegin,      paramEditEnd};

// -------------------------------------------------------- image effect suite

Effect *effect(OfxImageEffectHandle h) { return reinterpret_cast<Effect *>(h); }
Clip *clipOf(OfxImageClipHandle h) { return reinterpret_cast<Clip *>(h); }

OfxStatus effectGetPropertySet(OfxImageEffectHandle h, OfxPropertySetHandle *out) {
  if (!h) return kOfxStatErrBadHandle;
  *out = handleOf(&effect(h)->props);
  return kOfxStatOK;
}
OfxStatus effectGetParamSet(OfxImageEffectHandle h, OfxParamSetHandle *out) {
  if (!h) return kOfxStatErrBadHandle;
  *out = reinterpret_cast<OfxParamSetHandle>(&effect(h)->params);
  return kOfxStatOK;
}
OfxStatus clipDefine(OfxImageEffectHandle h, const char *name, OfxPropertySetHandle *out) {
  if (!h || !name) return kOfxStatErrBadHandle;
  Clip *c = effect(h)->defineClip(name);
  c->props.setString(kOfxPropType, 0, kOfxTypeClip);
  c->props.setString(kOfxPropName, 0, name);
  if (out) *out = handleOf(&c->props);
  return kOfxStatOK;
}
OfxStatus clipGetHandle(OfxImageEffectHandle h, const char *name,
                        OfxImageClipHandle *clip, OfxPropertySetHandle *props) {
  if (!h || !name) return kOfxStatErrBadHandle;
  Clip *c = effect(h)->clip(name);
  if (!c) return kOfxStatErrUnknown;
  if (clip) *clip = reinterpret_cast<OfxImageClipHandle>(c);
  if (props) *props = handleOf(&c->props);
  return kOfxStatOK;
}
OfxStatus clipGetPropertySet(OfxImageClipHandle h, OfxPropertySetHandle *out) {
  if (!h) return kOfxStatErrBadHandle;
  *out = handleOf(&clipOf(h)->props);
  return kOfxStatOK;
}
OfxStatus clipGetImage(OfxImageClipHandle h, OfxTime, const OfxRectD *,
                       OfxPropertySetHandle *out) {
  if (!h) return kOfxStatErrBadHandle;
  Clip *c = clipOf(h);
  if (!c->bound) return kOfxStatFailed; // plugin treats this as a black clip
  Image *img = c->bound;

  PropSet &p = img->props;
  p.setString(kOfxPropType, 0, kOfxTypeImage);
  p.setString(kOfxImageEffectPropPixelDepth, 0, kOfxBitDepthFloat);
  p.setString(kOfxImageEffectPropComponents, 0,
              img->components == 4 ? kOfxImageComponentRGBA : kOfxImageComponentRGB);
  p.setString(kOfxImageEffectPropPreMultiplication, 0, kOfxImageUnPreMultiplied);
  p.setDouble(kOfxImageEffectPropRenderScale, 0, 1.0);
  p.setDouble(kOfxImageEffectPropRenderScale, 1, 1.0);
  p.setDouble(kOfxImagePropPixelAspectRatio, 0, 1.0);
  p.setPointer(kOfxImagePropData, 0, img->data());
  p.setInt(kOfxImagePropBounds, 0, 0);
  p.setInt(kOfxImagePropBounds, 1, 0);
  p.setInt(kOfxImagePropBounds, 2, img->width);
  p.setInt(kOfxImagePropBounds, 3, img->height);
  p.setInt(kOfxImagePropRegionOfDefinition, 0, 0);
  p.setInt(kOfxImagePropRegionOfDefinition, 1, 0);
  p.setInt(kOfxImagePropRegionOfDefinition, 2, img->width);
  p.setInt(kOfxImagePropRegionOfDefinition, 3, img->height);
  p.setInt(kOfxImagePropRowBytes, 0, img->rowBytes());
  p.setString(kOfxImagePropField, 0, kOfxImageFieldNone);
  p.setString(kOfxImagePropUniqueIdentifier, 0, c->name);

  gImageByProps[&p] = img;
  if (out) *out = handleOf(&p);
  return kOfxStatOK;
}
OfxStatus clipReleaseImage(OfxPropertySetHandle h) {
  if (!h) return kOfxStatErrBadHandle;
  gImageByProps.erase(ps(h));
  return kOfxStatOK;
}
OfxStatus clipGetRegionOfDefinition(OfxImageClipHandle h, OfxTime, OfxRectD *bounds) {
  if (!h || !bounds) return kOfxStatErrBadHandle;
  Clip *c = clipOf(h);
  if (!c->bound) return kOfxStatFailed;
  bounds->x1 = 0;
  bounds->y1 = 0;
  bounds->x2 = c->bound->width;
  bounds->y2 = c->bound->height;
  return kOfxStatOK;
}
int effectAbort(OfxImageEffectHandle) { return 0; }

OfxStatus imageMemoryAlloc(OfxImageEffectHandle, size_t nBytes,
                           OfxImageMemoryHandle *handle) {
  auto block = std::make_unique<std::vector<char>>(nBytes);
  auto *raw = block.get();
  Host::current->memoryBlocks.push_back(std::move(block));
  *handle = reinterpret_cast<OfxImageMemoryHandle>(raw);
  return kOfxStatOK;
}
OfxStatus imageMemoryFree(OfxImageMemoryHandle handle) {
  auto &blocks = Host::current->memoryBlocks;
  for (auto it = blocks.begin(); it != blocks.end(); ++it) {
    if (it->get() == reinterpret_cast<std::vector<char> *>(handle)) {
      blocks.erase(it);
      return kOfxStatOK;
    }
  }
  return kOfxStatErrBadHandle;
}
OfxStatus imageMemoryLock(OfxImageMemoryHandle handle, void **ptr) {
  if (!handle || !ptr) return kOfxStatErrBadHandle;
  *ptr = reinterpret_cast<std::vector<char> *>(handle)->data();
  return kOfxStatOK;
}
OfxStatus imageMemoryUnlock(OfxImageMemoryHandle) { return kOfxStatOK; }

OfxImageEffectSuiteV1 gImageEffectSuite = {
    effectGetPropertySet, effectGetParamSet,  clipDefine,
    clipGetHandle,        clipGetPropertySet, clipGetImage,
    clipReleaseImage,     clipGetRegionOfDefinition, effectAbort,
    imageMemoryAlloc,     imageMemoryFree,    imageMemoryLock,
    imageMemoryUnlock};

// ------------------------------------------------------------ memory suite

OfxStatus memoryAlloc(void *, size_t nBytes, void **data) {
  *data = std::malloc(nBytes);
  return *data ? kOfxStatOK : kOfxStatErrMemory;
}
OfxStatus memoryFree(void *data) {
  std::free(data);
  return kOfxStatOK;
}
OfxMemorySuiteV1 gMemorySuite = {memoryAlloc, memoryFree};

// -------------------------------------------------------- multi-thread suite

OfxStatus multiThread(OfxThreadFunctionV1 func, unsigned int, void *arg) {
  func(0, 1, arg); // render on the calling thread; the plugin uses Vulkan anyway
  return kOfxStatOK;
}
OfxStatus multiThreadNumCPUs(unsigned int *n) { *n = 1; return kOfxStatOK; }
OfxStatus multiThreadIndex(unsigned int *n) { *n = 0; return kOfxStatOK; }
int multiThreadIsSpawnedThread(void) { return 0; }
OfxStatus mutexCreate(OfxMutexHandle *h, int) {
  *h = reinterpret_cast<OfxMutexHandle>(new std::recursive_mutex());
  return kOfxStatOK;
}
OfxStatus mutexDestroy(const OfxMutexHandle h) {
  delete reinterpret_cast<std::recursive_mutex *>(h);
  return kOfxStatOK;
}
OfxStatus mutexLock(const OfxMutexHandle h) {
  reinterpret_cast<std::recursive_mutex *>(h)->lock();
  return kOfxStatOK;
}
OfxStatus mutexUnLock(const OfxMutexHandle h) {
  reinterpret_cast<std::recursive_mutex *>(h)->unlock();
  return kOfxStatOK;
}
OfxStatus mutexTryLock(const OfxMutexHandle h) {
  return reinterpret_cast<std::recursive_mutex *>(h)->try_lock() ? kOfxStatOK
                                                                 : kOfxStatFailed;
}
OfxMultiThreadSuiteV1 gMultiThreadSuite = {
    multiThread, multiThreadNumCPUs, multiThreadIndex, multiThreadIsSpawnedThread,
    mutexCreate, mutexDestroy,       mutexLock,        mutexUnLock, mutexTryLock};

// ----------------------------------------------------------- message suite

OfxStatus message(void *, const char *type, const char *, const char *format, ...) {
  va_list args;
  va_start(args, format);
  char buffer[4096];
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  if (Host::current) Host::current->lastPluginMessage = buffer;
  std::fprintf(stderr, "[plugin %s] %s\n", type ? type : "message", buffer);
  return kOfxStatOK;
}
OfxStatus setPersistentMessage(void *h, const char *type, const char *id,
                               const char *format, ...) {
  va_list args;
  va_start(args, format);
  char buffer[4096];
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  if (Host::current) Host::current->lastPluginMessage = buffer;
  std::fprintf(stderr, "[plugin %s] %s\n", type ? type : "message", buffer);
  (void)h; (void)id;
  return kOfxStatOK;
}
OfxStatus clearPersistentMessage(void *) { return kOfxStatOK; }
OfxMessageSuiteV2 gMessageSuiteV2 = {message, setPersistentMessage,
                                     clearPersistentMessage};
OfxMessageSuiteV1 gMessageSuiteV1 = {message};

// ---------------------------------------------------------- progress suite

OfxStatus progressStart(void *, const char *label) {
  std::fprintf(stderr, "%s\n", label ? label : "working");
  return kOfxStatOK;
}
OfxStatus progressUpdate(void *, double) { return kOfxStatOK; }
OfxStatus progressEnd(void *) { return kOfxStatOK; }
OfxProgressSuiteV1 gProgressSuite = {progressStart, progressUpdate, progressEnd};

// ------------------------------------------------------------- fetch suite

const void *fetchSuite(OfxPropertySetHandle, const char *name, int version) {
  std::string n = name ? name : "";
  if (n == kOfxPropertySuite && version == 1) return &gPropertySuite;
  if (n == kOfxParameterSuite && version == 1) return &gParameterSuite;
  if (n == kOfxImageEffectSuite && version == 1) return &gImageEffectSuite;
  if (n == kOfxMemorySuite && version == 1) return &gMemorySuite;
  if (n == kOfxMultiThreadSuite && version == 1) return &gMultiThreadSuite;
  if (n == kOfxMessageSuite && version == 1) return &gMessageSuiteV1;
  if (n == kOfxMessageSuite && version == 2) return &gMessageSuiteV2;
  if (n == kOfxProgressSuite && version == 1) return &gProgressSuite;
  return nullptr;
}

}  // namespace

const void *hostFetchSuite(OfxPropertySetHandle h, const char *name, int version) {
  return fetchSuite(h, name, version);
}

}  // namespace spektra

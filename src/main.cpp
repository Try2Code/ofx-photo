// spektra-render: apply one OFX filter to one still frame.
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>

#include "host.h"

namespace {

using namespace spektra;

// ---------------------------------------------------------------- sfraw I/O
//
// magic "SFRW", uint32 width, height, channels, then float32 scanlines with
// row 0 at the bottom, matching OFX's y-up convention.

bool readSfraw(const std::string &path, Image &img, std::string *err) {
  std::ifstream f(path, std::ios::binary);
  if (!f) { *err = "cannot open " + path; return false; }
  char magic[4];
  f.read(magic, 4);
  if (std::memcmp(magic, "SFRW", 4) != 0) { *err = path + " is not an sfraw file"; return false; }
  uint32_t w, h, c;
  f.read((char *)&w, 4); f.read((char *)&h, 4); f.read((char *)&c, 4);
  if (!f || w == 0 || h == 0 || (c != 3 && c != 4)) {
    *err = "bad sfraw header in " + path;
    return false;
  }
  img.allocate((int)w, (int)h, (int)c);
  f.read((char *)img.data(), (std::streamsize)img.pixels.size() * sizeof(float));
  if (!f) { *err = "sfraw pixel data is truncated in " + path; return false; }
  return true;
}

bool writeSfraw(const std::string &path, Image &img, std::string *err) {
  std::ofstream f(path, std::ios::binary);
  if (!f) { *err = "cannot write " + path; return false; }
  uint32_t w = img.width, h = img.height, c = img.components;
  f.write("SFRW", 4);
  f.write((const char *)&w, 4); f.write((const char *)&h, 4); f.write((const char *)&c, 4);
  f.write((const char *)img.data(), (std::streamsize)img.pixels.size() * sizeof(float));
  if (!f) { *err = "could not finish writing " + path; return false; }
  return true;
}

// ------------------------------------------------------------- JSON emitting

std::string jsonEscape(const std::string &s) {
  std::string o;
  for (char ch : s) {
    switch (ch) {
      case '"': o += "\\\""; break;
      case '\\': o += "\\\\"; break;
      case '\n': o += "\\n"; break;
      case '\r': o += "\\r"; break;
      case '\t': o += "\\t"; break;
      default:
        if ((unsigned char)ch < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", ch); o += b; }
        else o += ch;
    }
  }
  return o;
}

void emitParamsJson(const ParamSet &params) {
  std::cout << "[\n";
  bool first = true;
  for (const auto &p : params.order) {
    if (!first) std::cout << ",\n";
    first = false;
    const PropSet &d = p->props;
    std::cout << "  {\"name\":\"" << jsonEscape(p->name) << "\""
              << ",\"type\":\"" << jsonEscape(p->type) << "\""
              << ",\"label\":\"" << jsonEscape(d.getString(kOfxPropLabel, 0, p->name)) << "\""
              << ",\"hint\":\"" << jsonEscape(d.getString(kOfxParamPropHint, 0, "")) << "\""
              << ",\"parent\":\"" << jsonEscape(d.getString(kOfxParamPropParent, 0, "")) << "\""
              << ",\"secret\":" << (d.getInt(kOfxParamPropSecret, 0, 0) ? "true" : "false");

    if (p->type == kOfxParamTypeChoice) {
      std::cout << ",\"value\":" << (p->vi.empty() ? 0 : p->vi[0]) << ",\"choices\":[";
      auto opts = d.getStrings(kOfxParamPropChoiceOption);
      for (size_t i = 0; i < opts.size(); ++i) {
        if (i) std::cout << ",";
        std::cout << "\"" << jsonEscape(opts[i]) << "\"";
      }
      std::cout << "]";
    } else if (p->type == kOfxParamTypeString) {
      std::cout << ",\"value\":\"" << jsonEscape(p->vs) << "\"";
    } else if (p->isDoubleType()) {
      std::cout << ",\"value\":[";
      for (size_t i = 0; i < p->vd.size(); ++i) { if (i) std::cout << ","; std::cout << p->vd[i]; }
      std::cout << "],\"min\":" << d.getDouble(kOfxParamPropMin, 0, -1e9)
                << ",\"max\":" << d.getDouble(kOfxParamPropMax, 0, 1e9)
                << ",\"displayMin\":" << d.getDouble(kOfxParamPropDisplayMin, 0, -1e9)
                << ",\"displayMax\":" << d.getDouble(kOfxParamPropDisplayMax, 0, 1e9);
    } else if (p->isIntType()) {
      std::cout << ",\"value\":[";
      for (size_t i = 0; i < p->vi.size(); ++i) { if (i) std::cout << ","; std::cout << p->vi[i]; }
      std::cout << "],\"min\":" << d.getInt(kOfxParamPropMin, 0, -1000000)
                << ",\"max\":" << d.getInt(kOfxParamPropMax, 0, 1000000);
    }
    std::cout << "}";
  }
  std::cout << "\n]\n";
}

// ------------------------------------------------------------- key=value file

bool readParamFile(const std::string &path, std::vector<std::pair<std::string, std::string>> *out,
                   std::string *err) {
  std::ifstream f(path);
  if (!f) { *err = "cannot open " + path; return false; }
  std::string line;
  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#') continue;
    size_t eq = line.find('=');
    if (eq == std::string::npos) continue;
    out->emplace_back(line.substr(0, eq), line.substr(eq + 1));
  }
  return true;
}

void usage() {
  std::cout <<
      "spektra-render - apply an OFX filter to a single still frame\n\n"
      "  --bundle PATH      .ofx.bundle directory (or the .ofx binary)\n"
      "  --plugin ID        plugin identifier, when a bundle holds several\n"
      "  --list-plugins     list the plugins in the bundle and exit\n"
      "  --list-params      describe the plugin's parameters as JSON and exit\n"
      "  --set NAME=VALUE   set a parameter (repeatable, applied in order)\n"
      "  --params FILE      read NAME=VALUE lines from a file\n"
      "  --in FILE          input .sfraw\n"
      "  --out FILE         output .sfraw\n"
      "  -h, --help         this text\n";
}

}  // namespace

int main(int argc, char **argv) {
  std::string bundle, plugin, inPath, outPath, paramFile;
  bool listPlugins = false, listParams = false;
  std::vector<std::pair<std::string, std::string>> sets;

  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&](const char *what) -> std::string {
      if (i + 1 >= argc) { std::cerr << "spektra-render: " << what << " needs a value\n"; std::exit(2); }
      return argv[++i];
    };
    if (a == "--bundle") bundle = next("--bundle");
    else if (a == "--plugin") plugin = next("--plugin");
    else if (a == "--in") inPath = next("--in");
    else if (a == "--out") outPath = next("--out");
    else if (a == "--params") paramFile = next("--params");
    else if (a == "--list-plugins") listPlugins = true;
    else if (a == "--list-params") listParams = true;
    else if (a == "--set") {
      std::string kv = next("--set");
      size_t eq = kv.find('=');
      if (eq == std::string::npos) { std::cerr << "spektra-render: --set wants NAME=VALUE\n"; return 2; }
      sets.emplace_back(kv.substr(0, eq), kv.substr(eq + 1));
    } else if (a == "-h" || a == "--help") { usage(); return 0; }
    else { std::cerr << "spektra-render: unknown option " << a << "\n"; return 2; }
  }

  if (bundle.empty()) { std::cerr << "spektra-render: --bundle is required\n"; return 2; }

  Host host;
  std::string err;
  if (!host.loadBundle(bundle, &err)) {
    std::cerr << "spektra-render: " << err << "\n";
    return 1;
  }

  if (listPlugins) {
    for (const auto &line : host.pluginIdentifiers()) std::cout << line << "\n";
    return 0;
  }

  if (!host.createInstance(plugin, &err)) {
    std::cerr << "spektra-render: " << err << "\n";
    return 1;
  }

  if (!paramFile.empty() && !readParamFile(paramFile, &sets, &err)) {
    std::cerr << "spektra-render: " << err << "\n";
    return 1;
  }
  for (const auto &kv : sets) {
    if (!host.setParamFromString(kv.first, kv.second, &err)) {
      std::cerr << "spektra-render: " << err << "\n";
      return 1;
    }
  }

  if (listParams) {
    emitParamsJson(host.params());
    return 0;
  }

  if (inPath.empty() || outPath.empty()) {
    std::cerr << "spektra-render: --in and --out are required to render\n";
    return 2;
  }

  Image source, output;
  if (!readSfraw(inPath, source, &err)) { std::cerr << "spektra-render: " << err << "\n"; return 1; }
  if (!host.render(source, output, &err)) { std::cerr << "spektra-render: " << err << "\n"; return 1; }
  if (!writeSfraw(outPath, output, &err)) { std::cerr << "spektra-render: " << err << "\n"; return 1; }

  std::cerr << "rendered " << output.width << "x" << output.height << "\n";
  return 0;
}

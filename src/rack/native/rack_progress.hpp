#ifndef RACK_NATIVE_RACK_PROGRESS_HPP
#define RACK_NATIVE_RACK_PROGRESS_HPP

// RACK_PROGRESS_HELPER_VERSION: 0.1.0

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <utility>

namespace rack_progress {

inline constexpr const char *kSchema = "rack.progress.v0";
inline constexpr const char *kHelperVersion = "0.1.0";

inline std::string env_string(const char *name) {
#if defined(_WIN32)
  char *value = nullptr;
  std::size_t value_size = 0U;
  if (_dupenv_s(&value, &value_size, name) != 0 || value == nullptr) {
    return std::string();
  }
  std::string result(value);
  std::free(value);
  return result;
#else
  const char *value = std::getenv(name);
  return value == nullptr ? std::string() : std::string(value);
#endif
}

inline bool env_truthy(const char *name) {
  std::string value = env_string(name);
  std::transform(
      value.begin(), value.end(), value.begin(),
      [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return !value.empty() && value != "0" && value != "false" && value != "no" &&
         value != "off";
}

inline std::string json_escape(const std::string &value) {
  std::ostringstream out;
  for (char c : value) {
    switch (c) {
    case '\\':
      out << "\\\\";
      break;
    case '"':
      out << "\\\"";
      break;
    case '\b':
      out << "\\b";
      break;
    case '\f':
      out << "\\f";
      break;
    case '\n':
      out << "\\n";
      break;
    case '\r':
      out << "\\r";
      break;
    case '\t':
      out << "\\t";
      break;
    default:
      if (static_cast<unsigned char>(c) < 0x20U) {
        out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
            << static_cast<int>(static_cast<unsigned char>(c));
      } else {
        out << c;
      }
      break;
    }
  }
  return out.str();
}

inline std::string timestamp_utc() {
  using namespace std::chrono;
  const auto now = system_clock::now();
  const auto epoch_ms = duration_cast<milliseconds>(now.time_since_epoch());
  const auto ms = epoch_ms.count() % 1000;
  const std::time_t raw_time = system_clock::to_time_t(now);
  std::tm utc_time{};

#if defined(_WIN32)
  gmtime_s(&utc_time, &raw_time);
#else
  gmtime_r(&raw_time, &utc_time);
#endif

  std::ostringstream out;
  out << std::put_time(&utc_time, "%Y-%m-%dT%H:%M:%S") << "." << std::setw(3)
      << std::setfill('0') << ms << "Z";
  return out.str();
}

inline std::string join_progress_path(const std::string &dir,
                                      const std::string &run_id) {
  if (dir.empty() || run_id.empty()) {
    return std::string();
  }
  const char last = dir[dir.size() - 1U];
  const bool has_separator = last == '/' || last == '\\';
  return dir + (has_separator ? "" : "/") + run_id + ".jsonl";
}

class Reporter {
public:
  Reporter(std::string test_id, int total = -1,
           std::string dut_kind = std::string(), double throttle_seconds = 5.0)
      : test_id_(std::move(test_id)), total_(total),
        dut_kind_(std::move(dut_kind)), throttle_seconds_(throttle_seconds),
        start_time_(std::chrono::steady_clock::now()) {
    enabled_ = env_truthy("RACK_PROGRESS");
    stderr_ = env_truthy("RACK_PROGRESS_STDERR");
    run_id_ = env_string("RACK_PROGRESS_RUN_ID");
    progress_file_ = env_string("RACK_PROGRESS_FILE");
    if (progress_file_.empty()) {
      progress_file_ =
          join_progress_path(env_string("RACK_PROGRESS_DIR"), run_id_);
    }
    if (enabled_ && progress_file_.empty()) {
      enabled_ = false;
    }
  }

  bool enabled() const { return enabled_; }

  void start(const std::string &message = std::string()) {
    emit("start", -1, std::string(), message,
         std::map<std::string, std::string>());
  }

  void progress(int done, const std::string &dut_id = std::string(),
                bool force = false,
                const std::map<std::string, std::string> &metrics = {}) {
    const auto now = std::chrono::steady_clock::now();
    if (!force && progress_emitted_) {
      const std::chrono::duration<double> delta = now - last_progress_time_;
      if (delta.count() < throttle_seconds_) {
        return;
      }
    }
    progress_emitted_ = true;
    last_progress_time_ = now;
    emit("progress", done, dut_id, std::string(), metrics);
  }

  void warning(const std::string &message,
               const std::string &dut_id = std::string()) {
    emit("warning", -1, dut_id, message, std::map<std::string, std::string>());
  }

  void failure(const std::string &message, int done = -1,
               const std::string &dut_id = std::string(),
               const std::map<std::string, std::string> &metrics = {}) {
    emit("failure", done, dut_id, message, metrics);
  }

  void finish(int done = -1, int failures = -1,
              const std::map<std::string, std::string> &metrics = {}) {
    emit("finish", done, std::string(), std::string(), metrics, failures);
  }

private:
  void emit(const std::string &event, int done, const std::string &dut_id,
            const std::string &message,
            const std::map<std::string, std::string> &metrics,
            int failures = -1) {
    if (!enabled_) {
      return;
    }

    const double elapsed_s = elapsed_seconds();
    std::ostringstream json;
    bool first = true;

    add_string(json, first, "schema", kSchema);
    add_string(json, first, "timestamp", timestamp_utc());
    add_string(json, first, "run_id", run_id_);
    add_string(json, first, "test_id", test_id_);
    add_string(json, first, "event", event);
    add_number(json, first, "elapsed_s", elapsed_s);
    if (done >= 0) {
      add_number(json, first, "done", done);
    }
    if (total_ >= 0) {
      add_number(json, first, "total", total_);
    }
    if (!dut_kind_.empty()) {
      add_string(json, first, "dut_kind", dut_kind_);
    }
    if (!dut_id.empty()) {
      add_string(json, first, "dut_id", dut_id);
    }
    if (!message.empty()) {
      add_string(json, first, "message", message);
    }
    if (failures >= 0) {
      add_number(json, first, "failures", failures);
    }
    if (!metrics.empty()) {
      add_metrics(json, first, metrics);
    }
    json << "}\n";

    append_line(json.str());
    if (stderr_) {
      write_stderr(event, done, dut_id, message, elapsed_s);
    }
  }

  double elapsed_seconds() const {
    const std::chrono::duration<double> elapsed =
        std::chrono::steady_clock::now() - start_time_;
    return elapsed.count();
  }

  static void add_prefix(std::ostringstream &json, bool &first,
                         const std::string &key) {
    json << (first ? "{" : ",");
    first = false;
    json << "\"" << json_escape(key) << "\":";
  }

  static void add_string(std::ostringstream &json, bool &first,
                         const std::string &key, const std::string &value) {
    add_prefix(json, first, key);
    json << "\"" << json_escape(value) << "\"";
  }

  template <typename T>
  static void add_number(std::ostringstream &json, bool &first,
                         const std::string &key, T value) {
    add_prefix(json, first, key);
    json << value;
  }

  static void add_metrics(std::ostringstream &json, bool &first,
                          const std::map<std::string, std::string> &metrics) {
    add_prefix(json, first, "metrics");
    json << "{";
    bool first_metric = true;
    for (const auto &item : metrics) {
      json << (first_metric ? "" : ",");
      first_metric = false;
      json << "\"" << json_escape(item.first) << "\":\""
           << json_escape(item.second) << "\"";
    }
    json << "}";
  }

  void append_line(const std::string &line) const {
    std::ofstream out(progress_file_, std::ios::out | std::ios::app);
    out << line;
  }

  void write_stderr(const std::string &event, int done,
                    const std::string &dut_id, const std::string &message,
                    double elapsed_s) const {
    const std::string count =
        (done >= 0 && total_ >= 0)
            ? std::to_string(done) + "/" + std::to_string(total_)
            : "-";
    std::cerr << "RACK_PROGRESS\t" << test_id_ << "\t" << count << "\t"
              << (dut_kind_.empty() ? "-" : dut_kind_) << "\t"
              << (dut_id.empty() ? "-" : dut_id) << "\telapsed=" << std::fixed
              << std::setprecision(1) << elapsed_s << "s\t" << event;
    if (!message.empty()) {
      std::cerr << "\t" << message;
    }
    std::cerr << std::endl;
  }

  std::string test_id_;
  int total_;
  std::string dut_kind_;
  double throttle_seconds_;
  std::chrono::steady_clock::time_point start_time_;
  bool enabled_ = false;
  bool stderr_ = false;
  bool progress_emitted_ = false;
  std::chrono::steady_clock::time_point last_progress_time_{};
  std::string run_id_;
  std::string progress_file_;
};

} // namespace rack_progress

#endif // RACK_NATIVE_RACK_PROGRESS_HPP

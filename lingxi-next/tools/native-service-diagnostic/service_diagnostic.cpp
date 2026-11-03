// Isolated Humble/FastDDS endpoint observation only; never installed on the robot.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <dlfcn.h>
#include <sstream>
#include <string>
#include <string_view>
#include <unistd.h>
#include <vector>

#include "rmw/rmw.h"
#include "rmw_fastrtps_shared_cpp/custom_client_info.hpp"
#include "rmw_fastrtps_shared_cpp/custom_service_info.hpp"

namespace {
using eprosima::fastrtps::rtps::GUID_t;
using eprosima::fastrtps::rtps::InstanceHandle_t;
using eprosima::fastrtps::rtps::iHandle2GUID;
using eprosima::fastrtps::types::ReturnCode_t;

bool value_is(const char * name, const char * expected)
{
  const char * value = std::getenv(name);
  return value && std::strcmp(value, expected) == 0;
}

bool isolated()
{
  if (!value_is("LINGXI_NAV2_SERVICE_DIAGNOSTIC", "isolated_humble_fastdds_2_6_12") ||
    !value_is("ROS_LOCALHOST_ONLY", "1") || !value_is("ROS_DOMAIN_ID", "174") ||
    !value_is("ROS_DISTRO", "humble") || !value_is("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp"))
  {
    return false;
  }
  DIR * interfaces = opendir("/sys/class/net");
  if (!interfaces) {return false;}
  bool loopback = false, other = false;
  while (dirent * entry = readdir(interfaces)) {
    if (entry->d_name[0] == '.') {continue;}
    if (std::strcmp(entry->d_name, "lo") == 0) {loopback = true;} else {other = true;}
  }
  closedir(interfaces);
  return loopback && !other;
}

bool selected(const char * implementation, const char * service)
{
  static const bool enabled = isolated();
  if (!enabled || !implementation || !service ||
    std::strcmp(implementation, "rmw_fastrtps_cpp") != 0)
  {
    return false;
  }
  std::string_view name(service), prefix("/lingxi_offline_");
  if (name.substr(0, prefix.size()) != prefix || name.size() < prefix.size() + 33) {return false;}
  const auto epoch = name.substr(prefix.size(), 32);
  if (!std::all_of(epoch.begin(), epoch.end(), [](char c) {
      return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');})) {return false;}
  const auto tail = name.substr(prefix.size() + 32);
  return tail == "/planner_server/change_state" || tail == "/controller_server/change_state" ||
         tail == "/bt_navigator/change_state" || tail == "/velocity_smoother/change_state";
}

double steady()
{
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

void * implementation(const char * symbol)
{
  // Python loads rcl locally; RTLD_NEXT can miss its real implementation.
  static void * library = dlopen("librmw_implementation.so", RTLD_NOW | RTLD_NOLOAD);
  return library ? dlsym(library, symbol) : nullptr;
}

void emit(const std::string & fields)
{
  static std::atomic<unsigned int> emitted{0};
  if (emitted.fetch_add(1) >= 64) {return;}
  std::ostringstream output;
  output.precision(17);
  output << "LINGXI_SERVICE_DIAGNOSTIC {\"schema\":1,\"pid\":" << getpid() <<
    ",\"steadySeconds\":" << steady() << ',' << fields << "}\n";
  const std::string line = output.str();
  if (line.size() <= 4096) {
    const auto written = write(STDERR_FILENO, line.data(), line.size());
    if (written < 0) {return;}
  }
}

std::string snapshot(CustomServiceInfo * info, const GUID_t & related)
{
  std::vector<InstanceHandle_t> handles;
  const auto status = info->response_writer_->get_matched_subscriptions(handles);
  const auto present = info->pub_listener_->check_for_subscription(related, std::chrono::nanoseconds(0));
  const bool matched = std::any_of(handles.begin(), handles.end(), [&](const auto & handle) {
      return iHandle2GUID(handle) == related;});
  std::ostringstream output;
  output << "{\"matchedListAvailable\":" << (status == ReturnCode_t::RETCODE_OK ? "true" : "false") <<
    ",\"matchedReaderCount\":" << handles.size() << ",\"relatedReaderMatched\":" <<
    (matched ? "true" : "false") << ",\"rmwReaderPresence\":" << static_cast<int>(present) <<
    ",\"matchedReaders\":[";
  for (size_t i = 0; i < std::min<size_t>(8, handles.size()); ++i) {
    if (i) {output << ',';}
    output << '\"' << iHandle2GUID(handles[i]) << '\"';
  }
  output << "]}";
  return output.str();
}
}  // namespace

extern "C" rmw_ret_t rmw_send_request(
  const rmw_client_t * client, const void * request, int64_t * sequence)
{
  using Function = rmw_ret_t (*)(const rmw_client_t *, const void *, int64_t *);
  static const auto original = reinterpret_cast<Function>(implementation("rmw_send_request"));
  if (!original) {return RMW_RET_ERROR;}
  const auto result = original(client, request, sequence);
  if (client && sequence && result == RMW_RET_OK &&
    selected(client->implementation_identifier, client->service_name))
  {
    const auto * info = static_cast<CustomClientInfo *>(client->data);
    std::ostringstream output;
    output << "\"operation\":\"request\",\"service\":\"" << client->service_name <<
      "\",\"sequence\":" << *sequence << ",\"declaredReader\":\"" << info->reader_guid_ <<
      "\",\"actualReader\":\"" << info->response_reader_->guid() <<
      "\",\"declaredWriter\":\"" << info->writer_guid_ << "\",\"actualWriter\":\"" <<
      info->request_writer_->guid() << "\",\"matchedRequestCount\":" <<
      info->request_publisher_matched_count_.load() << ",\"matchedResponseCount\":" <<
      info->response_subscriber_matched_count_.load();
    emit(output.str());
  }
  return result;
}

extern "C" rmw_ret_t rmw_send_response(
  const rmw_service_t * service, rmw_request_id_t * header, void * response)
{
  using Function = rmw_ret_t (*)(const rmw_service_t *, rmw_request_id_t *, void *);
  static const auto original = reinterpret_cast<Function>(implementation("rmw_send_response"));
  if (!original) {return RMW_RET_ERROR;}
  const bool inspect = service && header && selected(service->implementation_identifier, service->service_name);
  GUID_t related;
  CustomServiceInfo * info = nullptr;
  std::string before;
  if (inspect) {
    std::memcpy(related.guidPrefix.value, header->writer_guid, 12);
    std::memcpy(related.entityId.value, header->writer_guid + 12, 4);
    info = static_cast<CustomServiceInfo *>(service->data);
    before = snapshot(info, related);
  }
  const auto result = original(service, header, response);
  if (inspect) {
    std::ostringstream output;
    output << "\"operation\":\"response\",\"service\":\"" << service->service_name <<
      "\",\"sequence\":" << header->sequence_number << ",\"returnCode\":" << result <<
      ",\"relatedReader\":\"" << related << "\",\"responseWriter\":\"" <<
      info->response_writer_->guid() << "\",\"before\":" << before <<
      ",\"after\":" << snapshot(info, related);
    emit(output.str());
  }
  return result;
}

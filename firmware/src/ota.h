#pragma once

// Pull-based firmware updates. The controller asks the backend for the latest
// release shortly after boot and then every few hours; a newer image is
// downloaded over HTTPS with the same endpoint the cloud link uses and flashed
// into the inactive OTA slot. The install waits until every relay is off,
// because the reboot at the end drops all channels.
namespace ota {

void loop();

}  // namespace ota

#ifndef KYTY_COMMON_ANDROID_HOST_H_
#define KYTY_COMMON_ANDROID_HOST_H_

// Sharpdroid host integration. Active only when the launcher sets KYTY_HOST=android; on every other
// host nothing here is reachable and the emulator behaves exactly as upstream.
#include <cstddef>
#include <cstdint>

namespace Common::AndroidHost {

bool Enabled();
bool WindowSize(int* width, int* height); // KYTY_HOST_WINDOW_SIZE=WxH
bool AudioEnabled();                      // KYTY_HOST_AUDIO=android
bool InputEnabled();                      // KYTY_HOST_INPUT=android
void Initialize();                        // call first thing in main()

struct GuestLayout {
	uint64_t user_min;
	uint64_t user_max; // inclusive
	uint64_t arena_floor;
	uint64_t arena_limit;
};
const GuestLayout& Layout();

// Pad bridge, contract 4 format (64 bytes), see docs/pad.md of Sharpdroid.
struct PadTouch {
	uint8_t active, id, reserved[2];
	float   x, y;
};
struct PadState {
	uint32_t buttons;
	uint8_t  left_x, left_y, right_x, right_y, left_trigger, right_trigger;
	uint8_t  connected, type, connection, battery, motion_available, reserved;
	float    accel[3];
	float    gyro[3];
	PadTouch touch[2];
};
static_assert(sizeof(PadState) == 64, "contract 4 pad format is 64 bytes");

namespace PadButton { // Sharpdroid seam values
constexpr uint32_t Up = 1u << 0, Down = 1u << 1, Left = 1u << 2, Right = 1u << 3, Cross = 1u << 4,
                   Circle = 1u << 5, Square = 1u << 6, Triangle = 1u << 7, L1 = 1u << 8,
                   R1 = 1u << 9, L2 = 1u << 10, R2 = 1u << 11, L3 = 1u << 12, R3 = 1u << 13,
                   Options = 1u << 14, Touchpad = 1u << 15;
}

bool PadRead(int port, PadState* out); // true when a pad is connected
void PadRumble(int port, uint8_t large, uint8_t small);

// Push-model audio output on top of AAudio (write only, no callbacks: the thunk refuses those).
class AudioOut {
public:
	AudioOut()  = default;
	~AudioOut() { Close(); }
	bool Open(int freq, int channels, bool is_float);
	void Close();
	bool Put(const void* data, int bytes);
	int  Queued() const;
	void Clear();

	struct Impl;

private:
	Impl* m = nullptr;
};

// True when [addr, addr+size) is readable (write() EFAULT probe; process_vm_readv is not provided).
bool ProbeReadable(uint64_t addr, uint64_t size);

} // namespace Common::AndroidHost

#endif

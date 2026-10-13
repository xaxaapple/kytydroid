#include "common/androidHost.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <dlfcn.h>
#include <filesystem>
#include <mutex>
#include <thread>
#include <unistd.h>
#include <sys/syscall.h>
#include <vector>

namespace Common::AndroidHost {

static const char* Env(const char* n) {
	return std::getenv(n);
}
static bool EnvIs(const char* n, const char* v) {
	const char* e = Env(n);
	return e != nullptr && std::strcmp(e, v) == 0;
}
bool Enabled() {
	static const bool v = EnvIs("KYTY_HOST", "android");
	return v;
}
bool AudioEnabled() {
	return Enabled() && EnvIs("KYTY_HOST_AUDIO", "android");
}
bool InputEnabled() {
	return Enabled() && EnvIs("KYTY_HOST_INPUT", "android");
}
bool WindowSize(int* w, int* h) {
	const char* e = Env("KYTY_HOST_WINDOW_SIZE");
	int         a = 0, b = 0;
	if (!Enabled() || e == nullptr || std::sscanf(e, "%dx%d", &a, &b) != 2 || a <= 0 || b <= 0) {
		if (Enabled() && e != nullptr) {
			std::printf("[android] ignoring malformed KYTY_HOST_WINDOW_SIZE='%s'\n", e);
		}
		return false;
	}
	*w = a;
	*h = b;
	return true;
}

static uint64_t EnvHex(const char* n, uint64_t def) {
	const char* e = Env(n);
	return e != nullptr ? std::strtoull(e, nullptr, 16) : def;
}

static uint64_t HighestMapping() {
	FILE* f = std::fopen("/proc/self/maps", "r");
	if (f == nullptr) {
		return 0;
	}
	uint64_t top = 0;
	char     line[512];
	while (std::fgets(line, sizeof(line), f) != nullptr) {
		unsigned long long s = 0, e = 0;
		if (std::sscanf(line, "%llx-%llx", &s, &e) == 2 && e > top) {
			top = e;
		}
	}
	std::fclose(f);
	return top;
}

const GuestLayout& Layout() {
	static const GuestLayout layout = [] {
		// Classic layout (desktop hosts, 48-bit Android kernels).
		GuestLayout l {0x1000000000ull, 0xfbffffffffull, 0xA000000000ull, 0xFC00000000ull};
		const auto top = HighestMapping();
		if (top != 0 && top <= 0x8000000000ull) {
			// 39-bit userspace (512 GiB). Cap the reserved guest window at 320 GiB so the host
			// (FEXCore caches, GPU driver mappings, bionic) keeps free address space above it.
			l = {0x1000000000ull, 0x4fffffffffull, 0x5000000000ull, 0x5800000000ull};
			std::printf("[android] 39-bit address space detected (top=0x%llx)\n",
			            static_cast<unsigned long long>(top));
		}
		l.user_min    = EnvHex("KYTY_ADDR_USER_MIN", l.user_min);
		l.user_max    = EnvHex("KYTY_ADDR_USER_MAX", l.user_max);
		l.arena_floor = EnvHex("KYTY_ADDR_ARENA_FLOOR", l.arena_floor);
		l.arena_limit = EnvHex("KYTY_ADDR_ARENA_LIMIT", l.arena_limit);
		std::printf("[android] guest layout: user=[0x%llx,0x%llx] arena=[0x%llx,0x%llx)\n",
		            (unsigned long long)l.user_min, (unsigned long long)l.user_max,
		            (unsigned long long)l.arena_floor, (unsigned long long)l.arena_limit);
		return l;
	}();
	return layout;
}

void Initialize() {
	if (!Enabled()) {
		return;
	}
	// SDL: no X11/Wayland/KMSDRM and no sound server exist on Android.
	setenv("SDL_VIDEO_DRIVER", "offscreen", 1);
	setenv("SDL_AUDIO_DRIVER", "dummy", 1);
	setenv("SDL_JOYSTICK_DISABLE", "1", 0);
	if (const char* dir = Env("KYTY_DATA_DIR")) {
		std::error_code ec;
		std::filesystem::create_directories(dir, ec);
		std::filesystem::current_path(dir, ec);
		std::printf("[android] data dir %s (%s)\n", dir, ec ? "FAILED" : "ok");
	}
	Layout();
	std::fflush(stdout);
}

// ---- pad bridge -------------------------------------------------------------------------------
static constexpr long PAD_MAGIC = 0x50440000;

bool PadRead(int port, PadState* out) {
	std::memset(out, 0, sizeof(*out));
	const long r = ::syscall(PAD_MAGIC + 0, 4L, out, 64L, static_cast<long>(port));
	return r == 1 && out->connected != 0;
}
void PadRumble(int port, uint8_t large, uint8_t small) {
	::syscall(PAD_MAGIC + 2, static_cast<long>(port), static_cast<long>(large),
	          static_cast<long>(small));
}

bool ProbeReadable(uint64_t addr, uint64_t size) {
	static int fds[2] = {-1, -1};
	static std::once_flag once;
	std::call_once(once, [] {
		if (::pipe(fds) != 0) {
			fds[0] = fds[1] = -1;
		}
	});
	if (fds[1] < 0) {
		return false;
	}
	const uint64_t page = 4096;
	for (uint64_t cur = addr; cur < addr + size; cur = (cur & ~(page - 1)) + page) {
		if (::write(fds[1], reinterpret_cast<const void*>(cur), 1) != 1) {
			return false;
		}
		char c;
		if (::read(fds[0], &c, 1) != 1) {
			return false;
		}
	}
	return true;
}

// ---- AAudio -----------------------------------------------------------------------------------
namespace {
using Opaque = void;
struct Api {
	int (*create)(Opaque**);
	void (*setDirection)(Opaque*, int);
	void (*setPerf)(Opaque*, int);
	void (*setSharing)(Opaque*, int);
	void (*setRate)(Opaque*, int);
	void (*setChannels)(Opaque*, int);
	void (*setFormat)(Opaque*, int);
	int (*open)(Opaque*, Opaque**);
	int (*del)(Opaque*);
	int (*start)(Opaque*);
	int (*write)(Opaque*, const void*, int, int64_t);
	int (*close)(Opaque*);
	int (*getChannels)(Opaque*);
	int (*getRate)(Opaque*);
	bool ok = false;
};
Api& GetApi() {
	static Api a = [] {
		Api x {};
		void* h = dlopen("libaaudio.so", RTLD_NOW);
		if (h == nullptr) {
			std::printf("[android] libaaudio.so not found: %s\n", dlerror());
			return x;
		}
#define L(f, n) x.f = reinterpret_cast<decltype(x.f)>(dlsym(h, n))
		L(create, "AAudio_createStreamBuilder");
		L(setDirection, "AAudioStreamBuilder_setDirection");
		L(setPerf, "AAudioStreamBuilder_setPerformanceMode");
		L(setSharing, "AAudioStreamBuilder_setSharingMode");
		L(setRate, "AAudioStreamBuilder_setSampleRate");
		L(setChannels, "AAudioStreamBuilder_setChannelCount");
		L(setFormat, "AAudioStreamBuilder_setFormat");
		L(open, "AAudioStreamBuilder_openStream");
		L(del, "AAudioStreamBuilder_delete");
		L(start, "AAudioStream_requestStart");
		L(write, "AAudioStream_write");
		L(close, "AAudioStream_close");
		L(getChannels, "AAudioStream_getChannelCount");
		L(getRate, "AAudioStream_getSampleRate");
#undef L
		x.ok = x.create && x.setDirection && x.setPerf && x.setSharing && x.setRate &&
		       x.setChannels && x.setFormat && x.open && x.del && x.start && x.write && x.close;
		return x;
	}();
	return a;
}
} // namespace

struct AudioOut::Impl {
	Opaque*                 stream = nullptr;
	int                     in_ch = 2, out_ch = 2, bytes_per_sample = 4;
	bool                    is_float = true, downmix = false;
	std::mutex              mu;
	std::condition_variable cv;
	std::deque<uint8_t>     ring;
	std::atomic<bool>       stop {false};
	std::thread             pump;
	size_t                  capacity = 0;
	size_t                  frame_out() const { return static_cast<size_t>(out_ch * bytes_per_sample); }
};

static bool OpenStream(AudioOut::Impl* m, int freq, int channels) {
	auto& api = GetApi();
	Opaque* b = nullptr;
	if (api.create(&b) != 0 || b == nullptr) {
		return false;
	}
	api.setDirection(b, 0);   // OUTPUT
	api.setPerf(b, 12);       // LOW_LATENCY
	api.setSharing(b, 1);     // SHARED
	api.setRate(b, freq);
	api.setChannels(b, channels);
	api.setFormat(b, m->is_float ? 2 : 1); // PCM_FLOAT / PCM_I16
	const int r = api.open(b, &m->stream);
	api.del(b);
	return r == 0 && m->stream != nullptr;
}

bool AudioOut::Open(int freq, int channels, bool is_float) {
	auto& api = GetApi();
	if (!api.ok) {
		return false;
	}
	m                  = new Impl;
	m->in_ch           = channels;
	m->is_float        = is_float;
	m->bytes_per_sample = is_float ? 4 : 2;
	m->out_ch          = channels;
	if (!OpenStream(m, freq, channels)) {
		if (channels > 2 && (m->out_ch = 2, OpenStream(m, freq, 2))) {
			m->downmix = true;
		} else {
			delete m;
			m = nullptr;
			return false;
		}
	}
	m->capacity = static_cast<size_t>(freq) * m->frame_out(); // one second
	api.start(m->stream);
	std::printf("[android] AAudio stream: %d Hz, %d->%d ch, %s%s\n", freq, channels, m->out_ch,
	            is_float ? "f32" : "s16", m->downmix ? " (downmixed)" : "");
	std::fflush(stdout);
	Impl* impl = m;
	m->pump    = std::thread([impl, freq] {
        auto&               api   = GetApi();
        const size_t        burst = std::max<size_t>(1, static_cast<size_t>(freq) / 100); // 10 ms
        std::vector<uint8_t> buf(burst * impl->frame_out());
        while (!impl->stop.load()) {
            size_t n = 0;
            {
                std::unique_lock<std::mutex> lk(impl->mu);
                impl->cv.wait_for(lk, std::chrono::milliseconds(5),
                                  [&] { return impl->stop.load() || !impl->ring.empty(); });
                n = std::min(buf.size(), impl->ring.size());
                n -= n % impl->frame_out();
                std::copy_n(impl->ring.begin(), n, buf.begin());
            }
            if (n == 0) {
                continue;
            }
            // timeout 0 and our own bounded sleep: the thread stays interruptible (see audio thunk).
            const int frames = api.write(impl->stream, buf.data(),
                                         static_cast<int>(n / impl->frame_out()), 0);
            if (frames > 0) {
                std::lock_guard<std::mutex> lk(impl->mu);
                const size_t bytes = std::min<size_t>(impl->ring.size(), frames * impl->frame_out());
                impl->ring.erase(impl->ring.begin(), impl->ring.begin() + bytes);
            }
            if (frames <= 0) {
                std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
        }
    });
	return true;
}

void AudioOut::Close() {
	if (m == nullptr) {
		return;
	}
	m->stop = true;
	m->cv.notify_all();
	if (m->pump.joinable()) {
		m->pump.join();
	}
	GetApi().close(m->stream);
	delete m;
	m = nullptr;
}

template <typename T>
static void Downmix8(const T* in, T* out, size_t frames, int ch) {
	// SDL/PS order: FL FR FC LFE BL BR SL SR (8ch) ; extra channels ignored.
	for (size_t i = 0; i < frames; i++) {
		const T* f  = in + i * ch;
		float    l = f[0], r = f[1];
		if (ch >= 3) { l += 0.707f * f[2]; r += 0.707f * f[2]; }
		if (ch >= 6) { l += 0.707f * f[4]; r += 0.707f * f[5]; }
		if (ch >= 8) { l += 0.707f * f[6]; r += 0.707f * f[7]; }
		l *= 0.55f;
		r *= 0.55f;
		if constexpr (sizeof(T) == 2) {
			out[i * 2]     = static_cast<T>(std::clamp(l, -32768.f, 32767.f));
			out[i * 2 + 1] = static_cast<T>(std::clamp(r, -32768.f, 32767.f));
		} else {
			out[i * 2]     = std::clamp(l, -1.f, 1.f);
			out[i * 2 + 1] = std::clamp(r, -1.f, 1.f);
		}
	}
}

bool AudioOut::Put(const void* data, int bytes) {
	if (m == nullptr || data == nullptr || bytes <= 0) {
		return false;
	}
	std::vector<uint8_t> tmp;
	if (m->downmix) {
		const size_t frames = static_cast<size_t>(bytes) / (m->in_ch * m->bytes_per_sample);
		tmp.resize(frames * 2 * m->bytes_per_sample);
		if (m->is_float) {
			Downmix8(static_cast<const float*>(data), reinterpret_cast<float*>(tmp.data()), frames, m->in_ch);
		} else {
			Downmix8(static_cast<const int16_t*>(data), reinterpret_cast<int16_t*>(tmp.data()), frames, m->in_ch);
		}
		data  = tmp.data();
		bytes = static_cast<int>(tmp.size());
	}
	std::lock_guard<std::mutex> lk(m->mu);
	const auto* p = static_cast<const uint8_t*>(data);
	m->ring.insert(m->ring.end(), p, p + bytes);
	if (m->ring.size() > m->capacity) {
		m->ring.erase(m->ring.begin(), m->ring.begin() + (m->ring.size() - m->capacity));
	}
	m->cv.notify_one();
	return true;
}

int AudioOut::Queued() const {
	if (m == nullptr) {
		return 0;
	}
	std::lock_guard<std::mutex> lk(m->mu);
	// report in the *input* format's bytes so callers' pacing math matches SDL's
	return static_cast<int>(m->ring.size() * m->in_ch / m->out_ch);
}

void AudioOut::Clear() {
	if (m != nullptr) {
		std::lock_guard<std::mutex> lk(m->mu);
		m->ring.clear();
	}
}

} // namespace Common::AndroidHost

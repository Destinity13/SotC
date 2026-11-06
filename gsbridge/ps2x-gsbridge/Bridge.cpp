// ps2x-gsbridge: PCSX2-GS als Bibliothek fuer einen nativen PS2-Port (SotC Recomp).
// Abgeleitet von pcsx2-gsrunner/Main.cpp (PCSX2 Dev Team, GPL-3.0+): Host-Schnittstelle, Konfiguration und
// X11-Fenster bleiben, statt eines Dump-Files speist der Port Pakete live ein (GSDumpReplayer Live-Modus).
// SPDX-License-Identifier: GPL-3.0+
// SPDX-FileCopyrightText: 2002-2026 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <condition_variable>
#include <mutex>
#include <thread>

#ifdef _WIN32
#include "common/RedtapeWindows.h"
#endif

#include "fmt/format.h"

#include "common/Assertions.h"
#include "common/CocoaTools.h"
#include "common/Console.h"
#include "common/CrashHandler.h"
#include "common/FileSystem.h"
#include "common/MemorySettingsInterface.h"
#include "common/Path.h"
#include "common/ProgressCallback.h"
#include "common/SettingsWrapper.h"
#include "common/StringUtil.h"

#include "pcsx2/PrecompiledHeader.h"

#include "pcsx2/Achievements.h"
#include "pcsx2/CDVD/CDVD.h"
#include "pcsx2/GS.h"
#include "pcsx2/GS/Renderers/Common/GSDevice.h"
#include "pcsx2/GS/GSPerfMon.h"
#include "pcsx2/GSDumpReplayer.h"
#include "pcsx2/GameList.h"
#include "pcsx2/Host.h"
#include "pcsx2/INISettingsInterface.h"
#include "pcsx2/ImGui/FullscreenUI.h"
#include "pcsx2/ImGui/ImGuiFullscreen.h"
#include "pcsx2/ImGui/ImGuiManager.h"
#include "pcsx2/Input/InputManager.h"
#include "pcsx2/MTGS.h"
#include "pcsx2/SIO/Pad/Pad.h"
#include "pcsx2/PerformanceMetrics.h"
#include "pcsx2/VMManager.h"
#include "pcsx2/R3000A.h"
#include "pcsx2/IopHw.h"
#include "pcsx2/SPU2/spu2.h"
#include "pcsx2/SPU2/defs.h"

#include "svnrev.h"

// Down here because X11 has a lot of defines that can conflict
#if defined(__linux__)
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <sys/select.h>
#include <unistd.h>
#endif

namespace GSRunner
{
	static void InitializeConsole();
	static bool InitializeConfig();
	static void SettingsOverride();
	static bool ParseCommandLineArgs(int argc, char* argv[], VMBootParameters& params);
	static void DumpStats();

	static bool CreatePlatformWindow();
	static void DestroyPlatformWindow();
	static std::optional<WindowInfo> GetPlatformWindowInfo();
	static void PumpPlatformMessages(bool forever = false);
	static void StopPlatformMessagePump();
} // namespace GSRunner

static constexpr u32 WINDOW_WIDTH = 1280;
static constexpr u32 WINDOW_HEIGHT = 960;

static MemorySettingsInterface s_settings_interface;

static std::string s_output_prefix;
static s32 s_loop_count = 1;
static std::optional<bool> s_use_window;
static bool s_no_console = false;

// Owned by the GS thread.
static u32 s_dump_frame_number = 0;
static u32 s_loop_number = s_loop_count;
static double s_last_internal_draws = 0;
static double s_last_draws = 0;
static double s_last_render_passes = 0;
static double s_last_barriers = 0;
static double s_last_copies = 0;
static double s_last_uploads = 0;
static double s_last_readbacks = 0;
static double s_last_depth_copies_rov = 0;
static double s_last_draws_rov = 0;
static double s_last_barriers_rov = 0;
static u64 s_total_internal_draws = 0;
static u64 s_total_draws = 0;
static u64 s_total_render_passes = 0;
static u64 s_total_barriers = 0;
static u64 s_total_copies = 0;
static u64 s_total_uploads = 0;
static u64 s_total_readbacks = 0;
static u64 s_total_copies_rov = 0;
static u64 s_total_draws_rov = 0;
static u64 s_total_barriers_rov = 0;
static u32 s_total_frames = 0;
static u32 s_total_drawn_frames = 0;

static bool s_perf_enable = false;
static float s_perf_updates = 0.0f;
static float s_perf_sum_fps = 0.0f;
static float s_perf_sum_internal_fps = 0.0f;
static float s_perf_sum_cpu_thread_usage = 0.0f;
static float s_perf_sum_cpu_thread_time = 0.0f;
static float s_perf_sum_gs_thread_usage = 0.0f;
static float s_perf_sum_gs_thread_time = 0.0f;
static float s_perf_sum_gpu_time = 0.0f;
static float s_perf_sum_gpu_usage = 0.0f;

bool GSRunner::InitializeConfig()
{
	EmuFolders::SetAppRoot();
	// ps2x: Ressourcen (Shader, Schriften, GameDB) liegen neben der Bibliothek, nicht neben dem Spielprogramm
	if (const char* root = std::getenv("PS2XGS_APPROOT"); root && *root)
		EmuFolders::AppRoot = root;
	if (!EmuFolders::SetResourcesDirectory() || !EmuFolders::SetDataDirectory(nullptr))
		return false;

	CrashHandler::SetWriteDirectory(EmuFolders::DataRoot);

	const char* error;
	if (!VMManager::PerformEarlyHardwareChecks(&error))
		return false;

	{
		const std::string roboto_path =
			EmuFolders::GetOverridableResourcePath("fonts" FS_OSPATH_SEPARATOR_STR "Roboto-Regular.ttf");
		const auto roboto_data = FileSystem::MapBinaryFileForRead(roboto_path.c_str());
		if (roboto_data.empty())
		{
			Console.ErrorFmt("Failed to load font file '{}'.", roboto_path);
			return false;
		}

		std::vector<ImGuiManager::FontInfo> fonts;
		ImGuiManager::FontInfo fi{};
		fi.data = roboto_data;
		fi.exclude_ranges = {};
		fi.face_name = nullptr;
		fi.is_emoji_font = false;
		fonts.push_back(fi);

		ImGuiManager::SetFonts(std::move(fonts));
	}

	// don't provide an ini path, or bother loading. we'll store everything in memory.
	MemorySettingsInterface& si = s_settings_interface;
	Host::Internal::SetBaseSettingsLayer(&si);

	VMManager::SetDefaultSettings(si, true, true, true, true, true);

	VMManager::Internal::LoadStartupSettings();
	return true;
}

void Host::CommitBaseSettingChanges()
{
	// nothing to save, we're all in memory
}

void Host::LoadSettings(SettingsInterface& si, std::unique_lock<std::mutex>& lock)
{
}

void Host::CheckForSettingsChanges(const Pcsx2Config& old_config)
{
}

bool Host::RequestResetSettings(bool folders, bool core, bool controllers, bool hotkeys, bool ui)
{
	// not running any UI, so no settings requests will come in
	return false;
}

void Host::SetDefaultUISettings(SettingsInterface& si)
{
	// nothing
}

bool Host::LocaleCircleConfirm()
{
	// not running any UI, so no settings requests will come in
	return false;
}

std::unique_ptr<ProgressCallback> Host::CreateHostProgressCallback()
{
	return ProgressCallback::CreateNullProgressCallback();
}

void Host::ReportInfoAsync(const std::string_view title, const std::string_view message)
{
	if (!title.empty() && !message.empty())
		INFO_LOG("ReportInfoAsync: {}: {}", title, message);
	else if (!message.empty())
		INFO_LOG("ReportInfoAsync: {}", message);
}

void Host::ReportErrorAsync(const std::string_view title, const std::string_view message)
{
	if (!title.empty() && !message.empty())
		ERROR_LOG("ReportErrorAsync: {}: {}", title, message);
	else if (!message.empty())
		ERROR_LOG("ReportErrorAsync: {}", message);
}

void Host::OpenURL(const std::string_view url)
{
	// noop
}

bool Host::CopyTextToClipboard(const std::string_view text)
{
	return false;
}

std::string Host::GetTextFromClipboard()
{
	return std::string();
}

void Host::BeginTextInput()
{
	// noop
}

void Host::EndTextInput()
{
	// noop
}

std::optional<WindowInfo> Host::GetTopLevelWindowInfo()
{
	return GSRunner::GetPlatformWindowInfo();
}

void Host::OnInputDeviceConnected(const std::string_view identifier, const std::string_view device_name)
{
}

void Host::OnInputDeviceDisconnected(const InputBindingKey key, const std::string_view identifier)
{
}

void Host::SetMouseMode(bool relative_mode, bool hide_cursor)
{
}

void Host::SetMouseLock(bool state)
{
}

std::optional<WindowInfo> Host::AcquireRenderWindow(bool recreate_window)
{
	return GSRunner::GetPlatformWindowInfo();
}

void Host::ReleaseRenderWindow()
{
}

void Host::BeginPresentFrame()
{
	if (s_loop_number == 0 && !s_output_prefix.empty())
	{
		// when we wrap around, don't race other files
		GSJoinSnapshotThreads();

		// queue dumping of this frame
		std::string dump_path(fmt::format("{}_frame{:05}.png", s_output_prefix, s_dump_frame_number));
		GSQueueSnapshot(dump_path);
	}

	if (GSIsHardwareRenderer())
	{
		const u32 last_draws = s_total_internal_draws;
		const u32 last_uploads = s_total_uploads;

		static constexpr auto update_stat = [](GSPerfMon::counter_t counter, u64& dst, double& last) {
			// perfmon resets every 30 frames to zero
			const double val = g_perfmon.GetCounter(counter);
			dst += static_cast<u64>((val < last) ? val : (val - last));
			last = val;
		};

		update_stat(GSPerfMon::Draw, s_total_internal_draws, s_last_internal_draws);
		update_stat(GSPerfMon::DrawCalls, s_total_draws, s_last_draws);
		update_stat(GSPerfMon::RenderPasses, s_total_render_passes, s_last_render_passes);
		update_stat(GSPerfMon::Barriers, s_total_barriers, s_last_barriers);
		update_stat(GSPerfMon::TextureCopies, s_total_copies, s_last_copies);
		update_stat(GSPerfMon::TextureUploads, s_total_uploads, s_last_uploads);
		update_stat(GSPerfMon::Readbacks, s_total_readbacks, s_last_readbacks);
		update_stat(GSPerfMon::TextureCopiesROV, s_total_copies_rov, s_last_depth_copies_rov);
		update_stat(GSPerfMon::DrawCallsROV, s_total_draws_rov, s_last_draws_rov);
		update_stat(GSPerfMon::BarriersROV, s_total_barriers_rov, s_last_barriers_rov);

		const bool idle_frame = s_total_frames && (last_draws == s_total_internal_draws && last_uploads == s_total_uploads);

		if (!idle_frame)
			s_total_drawn_frames++;

		s_total_frames++;

		std::atomic_thread_fence(std::memory_order_release);
	}
}

void Host::RequestResizeHostDisplay(s32 width, s32 height)
{
}

void Host::OnVMStarting()
{
}

void Host::OnVMStarted()
{
}

void Host::OnVMDestroyed()
{
}

void Host::OnVMPaused()
{
}

void Host::OnVMResumed()
{
}

void Host::OnGameChanged(const std::string& title, const std::string& elf_override, const std::string& disc_path,
	const std::string& disc_serial, u32 disc_crc, u32 current_crc)
{
}

void Host::OnPerformanceMetricsUpdated()
{
	if (s_perf_enable)
	{
		s_perf_updates += 1.0f;
		s_perf_sum_fps += PerformanceMetrics::GetFPS();
		s_perf_sum_internal_fps += PerformanceMetrics::GetInternalFPS();
		s_perf_sum_cpu_thread_usage += PerformanceMetrics::GetCPUThreadUsage();
		s_perf_sum_cpu_thread_time += PerformanceMetrics::GetCPUThreadAverageTime();
		s_perf_sum_gs_thread_usage += PerformanceMetrics::GetGSThreadUsage();
		s_perf_sum_gs_thread_time += PerformanceMetrics::GetGSThreadAverageTime();
		s_perf_sum_gpu_time += PerformanceMetrics::GetGPUAverageTime();
		s_perf_sum_gpu_usage += PerformanceMetrics::GetGPUUsage();
	}
}

void Host::OnSaveStateLoading(const std::string_view filename)
{
}

void Host::OnSaveStateLoaded(const std::string_view filename, bool was_successful)
{
}

void Host::OnSaveStateSaved(const std::string_view filename)
{
}

void Host::RunOnCPUThread(std::function<void()> function, bool block /* = false */)
{
	pxFailRel("Not implemented");
}

void Host::RefreshGameListAsync(bool invalidate_cache)
{
}

void Host::CancelGameListRefresh()
{
}

bool Host::IsFullscreen()
{
	return false;
}

void Host::SetFullscreen(bool enabled)
{
}

void Host::OnCaptureStarted(const std::string& filename)
{
}

void Host::OnCaptureStopped()
{
}

void Host::RequestExitApplication(bool allow_confirm)
{
}

void Host::RequestExitBigPicture()
{
}

void Host::RequestVMShutdown(bool allow_confirm, bool allow_save_state, bool default_save_state)
{
	VMManager::SetState(VMState::Stopping);
}

void Host::OnAchievementsLoginSuccess(const char* username, u32 points, u32 sc_points, u32 unread_messages)
{
	// noop
}

void Host::OnAchievementsLoginRequested(Achievements::LoginRequestReason reason)
{
	// noop
}

void Host::OnAchievementsHardcoreModeChanged(bool enabled)
{
	// noop
}

void Host::OnAchievementsRefreshed()
{
	// noop
}

bool Host::InBatchMode()
{
	return false;
}

bool Host::InNoGUIMode()
{
	return false;
}

bool Host::ShouldPreferHostFileSelector()
{
	return false;
}

void Host::OpenHostFileSelectorAsync(std::string_view title, bool select_directory, FileSelectorCallback callback,
	FileSelectorFilters filters, std::string_view initial_directory)
{
	callback(std::string());
}

int Host::LocaleSensitiveCompare(std::string_view lhs, std::string_view rhs)
{
	const int res = std::strncmp(lhs.data(), rhs.data(), std::min(lhs.size(), rhs.size()));
	if (res != 0)
		return res;
	return lhs.size() > rhs.size() ? 1 : (lhs.size() < rhs.size() ? -1 : 0);
}

std::optional<u32> InputManager::ConvertHostKeyboardStringToCode(const std::string_view str)
{
	return std::nullopt;
}

std::optional<std::string> InputManager::ConvertHostKeyboardCodeToString(u32 code)
{
	return std::nullopt;
}

const char* InputManager::ConvertHostKeyboardCodeToIcon(u32 code)
{
	return nullptr;
}

BEGIN_HOTKEY_LIST(g_host_hotkeys)
END_HOTKEY_LIST()

static void PrintCommandLineVersion()
{
	std::fprintf(stderr, "PCSX2 GS Runner Version %s\n", GIT_REV);
	std::fprintf(stderr, "https://pcsx2.net/\n");
	std::fprintf(stderr, "\n");
}

static void PrintCommandLineHelp(const char* progname)
{
	PrintCommandLineVersion();
	std::fprintf(stderr, "Usage: %s [parameters] [--] [filename]\n", progname);
	std::fprintf(stderr, "\n");
	std::fprintf(stderr, "  -help: Displays this information and exits.\n");
	std::fprintf(stderr, "  -version: Displays version information and exits.\n");
	std::fprintf(stderr, "  -dumpdir <dir>: Frame dump directory (will be dumped as filename_frameN.png).\n");
	std::fprintf(stderr, "  -dump [rt|tex|z|f|a|i|tr|ds|fs|hw]: Enabling dumping of render target, texture, z buffer, frame, "
		"alphas, and info (context, vertices, list of transfers), transfers images, draw stats, frame stats, HW config, respectively, per draw. Generates lots of data.\n");
	std::fprintf(stderr, "  -dumprange N[,L,B]: Start dumping from draw N (base 0), stops after L draws, and only "
		"those draws that are multiples of B (intersection of -dumprange and -dumprangef used)."
		"Defaults to 0,-1,1 (all draws). Only used if -dump used.\n");
	std::fprintf(stderr, "  -dumprangef NF[,LF,BF]: Start dumping from frame NF (base 0), stops after LF frames, "
		"and only those frames that are multiples of BF (intersection of -dumprange and -dumprangef used).\n"
		"Defaults to 0,-1,1 (all frames). Only used if -dump is used.\n");
	std::fprintf(stderr, "  -loop <count>: Loops dump playback N times. Defaults to 1. 0 will loop infinitely.\n");
	std::fprintf(stderr, "  -renderer <renderer>: Sets the graphics renderer. Defaults to Auto.\n");
	std::fprintf(stderr, "  -swthreads <threads>: Sets the number of threads for the software renderer.\n");
	std::fprintf(stderr, "  -window: Forces a window to be displayed.\n");
	std::fprintf(stderr, "  -surfaceless: Disables showing a window.\n");
	std::fprintf(stderr, "  -logfile <filename>: Writes emu log to filename.\n");
	std::fprintf(stderr, "  -noshadercache: Disables the shader cache (useful for parallel runs).\n");
	std::fprintf(stderr, "  -perf: Enable frame timing performance stats.\n");
	std::fprintf(stderr, "  --: Signals that no more arguments will follow and the remaining\n"
						 "    parameters make up the filename. Use when the filename contains\n"
						 "    spaces or starts with a dash.\n");
	std::fprintf(stderr, "\n");
}

void GSRunner::InitializeConsole()
{
	const char* var = std::getenv("PCSX2_NOCONSOLE");
	s_no_console = (var && StringUtil::FromChars<bool>(var).value_or(false));
	if (!s_no_console)
		Log::SetConsoleOutputLevel(LOGLEVEL_DEBUG);
}

bool GSRunner::ParseCommandLineArgs(int argc, char* argv[], VMBootParameters& params)
{
	std::string dumpdir; // Save from argument -dumpdir for creating sub-directories
	bool no_more_args = false;
	for (int i = 1; i < argc; i++)
	{
		if (!no_more_args)
		{
#define CHECK_ARG(str) !std::strcmp(argv[i], str)
#define CHECK_ARG_PARAM(str) (!std::strcmp(argv[i], str) && ((i + 1) < argc))

			if (CHECK_ARG("-help"))
			{
				PrintCommandLineHelp(argv[0]);
				return false;
			}
			else if (CHECK_ARG("-version"))
			{
				PrintCommandLineVersion();
				return false;
			}
			else if (CHECK_ARG_PARAM("-dumpdir"))
			{
				dumpdir = s_output_prefix = StringUtil::StripWhitespace(argv[++i]);
				if (s_output_prefix.empty())
				{
					Console.Error("Invalid dump directory specified.");
					return false;
				}

				if (!FileSystem::DirectoryExists(s_output_prefix.c_str()) && !FileSystem::CreateDirectoryPath(s_output_prefix.c_str(), false))
				{
					Console.Error("Failed to create output directory");
					return false;
				}

				continue;
			}
			else if (CHECK_ARG_PARAM("-dump"))
			{
				std::string str(argv[++i]);

				s_settings_interface.SetBoolValue("EmuCore/GS", "DumpGSData", true);

				if (str.find("rt") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveRT", true);
				if (str.find("f") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveFrame", true);
				if (str.find("tex") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveTexture", true);
				if (str.find("z") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveDepth", true);
				if (str.find("a") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveAlpha", true);
				if (str.find("i") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveInfo", true);
				if (str.find("tr") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveTransferImages", true);
				if (str.find("ds") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveDrawStats", true);
				if (str.find("fs") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveFrameStats", true);
				if (str.find("hw") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "SaveHWConfig", true);
				continue;
			}
			else if (CHECK_ARG_PARAM("-dumprange"))
			{
				std::string str(argv[++i]);

				std::vector<std::string_view> split = StringUtil::SplitString(str, ',');
				int start = 0;
				int num = -1;
				int by = 1;
				if (split.size() > 0)
				{
					start = StringUtil::FromChars<int>(split[0]).value_or(0);
				}
				if (split.size() > 1)
				{
					num = StringUtil::FromChars<int>(split[1]).value_or(-1);
				}
				if (split.size() > 2)
				{
					by = std::max(1, StringUtil::FromChars<int>(split[2]).value_or(1));
				}
				s_settings_interface.SetIntValue("EmuCore/GS", "SaveDrawStart", start);
				s_settings_interface.SetIntValue("EmuCore/GS", "SaveDrawCount", num);
				s_settings_interface.SetIntValue("EmuCore/GS", "SaveDrawBy", by);
				continue;
			}
			else if (CHECK_ARG_PARAM("-dumprangef"))
			{
				std::string str(argv[++i]);

				std::vector<std::string_view> split = StringUtil::SplitString(str, ',');
				int start = 0;
				int num = -1;
				int by = 1;
				if (split.size() > 0)
				{
					start = StringUtil::FromChars<int>(split[0]).value_or(0);
				}
				if (split.size() > 1)
				{
					num = StringUtil::FromChars<int>(split[1]).value_or(-1);
				}
				if (split.size() > 2)
				{
					by = std::max(1, StringUtil::FromChars<int>(split[2]).value_or(1));
				}
				s_settings_interface.SetIntValue("EmuCore/GS", "SaveFrameStart", start);
				s_settings_interface.SetIntValue("EmuCore/GS", "SaveFrameCount", num);
				s_settings_interface.SetIntValue("EmuCore/GS", "SaveFrameBy", by);
				continue;
			}
			else if (CHECK_ARG_PARAM("-dumpdirhw"))
			{
				s_settings_interface.SetStringValue("EmuCore/GS", "HWDumpDirectory", argv[++i]);
				continue;
			}
			else if (CHECK_ARG_PARAM("-dumpdirsw"))
			{
				s_settings_interface.SetStringValue("EmuCore/GS", "SWDumpDirectory", argv[++i]);
				continue;
			}
			else if (CHECK_ARG_PARAM("-loop"))
			{
				s_loop_count = StringUtil::FromChars<s32>(argv[++i]).value_or(0);
				Console.WriteLn("Looping dump playback %d times.", s_loop_count);
				continue;
			}
			else if (CHECK_ARG_PARAM("-renderer"))
			{
				const char* rname = argv[++i];

				GSRendererType type = GSRendererType::Auto;
				if (StringUtil::Strcasecmp(rname, "Auto") == 0)
					type = GSRendererType::Auto;
#ifdef _WIN32
				else if (StringUtil::Strcasecmp(rname, "dx11") == 0)
					type = GSRendererType::DX11;
				else if (StringUtil::Strcasecmp(rname, "dx12") == 0)
					type = GSRendererType::DX12;
#endif
#ifdef ENABLE_OPENGL
				else if (StringUtil::Strcasecmp(rname, "gl") == 0)
					type = GSRendererType::OGL;
#endif
#ifdef ENABLE_VULKAN
				else if (StringUtil::Strcasecmp(rname, "vulkan") == 0)
					type = GSRendererType::VK;
#endif
#ifdef __APPLE__
				else if (StringUtil::Strcasecmp(rname, "metal") == 0)
					type = GSRendererType::Metal;
#endif
				else if (StringUtil::Strcasecmp(rname, "sw") == 0)
					type = GSRendererType::SW;
				else
				{
					Console.Error("Unknown renderer '%s'", rname);
					return false;
				}

				Console.WriteLn("Using %s renderer.", Pcsx2Config::GSOptions::GetRendererName(type));
				s_settings_interface.SetIntValue("EmuCore/GS", "Renderer", static_cast<int>(type));
				continue;
			}
			else if (CHECK_ARG_PARAM("-swthreads"))
			{
				const int swthreads = StringUtil::FromChars<int>(argv[++i]).value_or(0);
				if (swthreads < 0)
				{
					Console.WriteLn("Invalid number of software threads");
					return false;
				}
				
				Console.WriteLn(fmt::format("Setting number of software threads to {}", swthreads));
				s_settings_interface.SetIntValue("EmuCore/GS", "SWExtraThreads", swthreads);
				continue;
			}
			else if (CHECK_ARG_PARAM("-renderhacks"))
			{
				std::string str(argv[++i]);

				s_settings_interface.SetBoolValue("EmuCore/GS", "UserHacks", true);

				if (str.find("af") != std::string::npos)
					s_settings_interface.SetIntValue("EmuCore/GS", "UserHacks_AutoFlushLevel", 1);
				if (str.find("cpufb") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "UserHacks_CPU_FB_Conversion", true);
				if (str.find("dds") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "UserHacks_DisableDepthSupport", true);
				if (str.find("dpi") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "UserHacks_DisablePartialInvalidation", true);
				if (str.find("dsf") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "UserHacks_Disable_Safe_Features", true);
				if (str.find("tinrt") != std::string::npos)
					s_settings_interface.SetIntValue("EmuCore/GS", "UserHacks_TextureInsideRt", 1);
				if (str.find("plf") != std::string::npos)
					s_settings_interface.SetBoolValue("EmuCore/GS", "preload_frame_with_gs_data", true);

				continue;
			}
			else if (CHECK_ARG_PARAM("-ini"))
			{
				std::string path = std::string(StringUtil::StripWhitespace(argv[++i]));
				if (!FileSystem::FileExists(path.c_str()))
				{
					Console.ErrorFmt("INI file {} does not exit.", path);
					return false;
				}

				INISettingsInterface si_ini(path);

				if (!si_ini.Load())
				{
					Console.ErrorFmt("Unable to load INI settings from {}.", path);
					return false;
				}

				for (const auto& [key, value] : si_ini.GetKeyValueList("EmuCore/GS"))
					s_settings_interface.SetStringValue("EmuCore/GS", key.c_str(), value.c_str());

				continue;
			}
			else if (CHECK_ARG_PARAM("-upscale"))
			{
				const float upscale = StringUtil::FromChars<float>(argv[++i]).value_or(0.0f);
				if (upscale < 0.5f)
				{
					Console.WriteLn("Invalid upscale multiplier");
					return false;
				}

				Console.WriteLn(fmt::format("Setting upscale multiplier to {}", upscale));
				s_settings_interface.SetFloatValue("EmuCore/GS", "upscale_multiplier", upscale);
				continue;
			}
			else if (CHECK_ARG_PARAM("-logfile"))
			{
				const char* logfile = argv[++i];
				if (std::strlen(logfile) > 0)
				{
					// disable timestamps, since we want to be able to diff the logs
					Console.WriteLn("Logging to %s...", logfile);
					VMManager::Internal::SetFileLogPath(logfile);
					s_settings_interface.SetBoolValue("Logging", "EnableFileLogging", true);
					s_settings_interface.SetBoolValue("Logging", "EnableTimestamps", false);
				}

				continue;
			}
			else if (CHECK_ARG("-noshadercache"))
			{
				Console.WriteLn("Disabling shader cache");
				s_settings_interface.SetBoolValue("EmuCore/GS", "DisableShaderCache", true);
				continue;
			}
			else if (CHECK_ARG("-window"))
			{
				Console.WriteLn("Creating window");
				s_use_window = true;
				continue;
			}
			else if (CHECK_ARG("-surfaceless"))
			{
				Console.WriteLn("Running surfaceless");
				s_use_window = false;
				continue;
			}
			else if (CHECK_ARG("-perf"))
			{
				Console.WriteLn("Enable performance stats");
				s_perf_enable = true;
				continue;
			}
			else if (CHECK_ARG("-debugdevice"))
			{
				Console.WriteLn("Enable debug device");
				s_settings_interface.SetBoolValue("EmuCore/GS", "UseDebugDevice", true);
				continue;
			}
			else if (CHECK_ARG("--"))
			{
				no_more_args = true;
				continue;
			}
			else if (argv[i][0] == '-')
			{
				Console.Error("Unknown parameter: '%s'", argv[i]);
				return false;
			}

#undef CHECK_ARG
#undef CHECK_ARG_PARAM
		}

		if (!params.filename.empty())
			params.filename += ' ';
		params.filename += argv[i];
	}

	if (params.filename.empty())
	{
		Console.Error("No dump filename provided.");
		return false;
	}

	if (!VMManager::IsGSDumpFileName(params.filename))
	{
		Console.Error("Provided filename is not a GS dump.");
		return false;
	}

	if (s_settings_interface.GetBoolValue("EmuCore/GS", "DumpGSData") && !dumpdir.empty())
	{
		if (s_settings_interface.GetStringValue("EmuCore/GS", "HWDumpDirectory").empty())
			s_settings_interface.SetStringValue("EmuCore/GS", "HWDumpDirectory", dumpdir.c_str());
		if (s_settings_interface.GetStringValue("EmuCore/GS", "SWDumpDirectory").empty())
			s_settings_interface.SetStringValue("EmuCore/GS", "SWDumpDirectory", dumpdir.c_str());
		
		// Disable saving frames with SaveSnapshotToMemory()
		// Instead we save more "raw" snapshots when using -dump.
		s_output_prefix = "";
	}

	// set up the frame dump directory
	if (!s_output_prefix.empty())
	{
		// strip off all extensions
		std::string_view title(Path::GetFileTitle(params.filename));
		if (StringUtil::EndsWithNoCase(title, ".gs"))
			title = Path::GetFileTitle(title);

		s_output_prefix = Path::Combine(s_output_prefix, StringUtil::StripWhitespace(title));
		Console.WriteLn(fmt::format("Saving dumps as {}_frameN.png", s_output_prefix));
	}

	return true;
}

void GSRunner::SettingsOverride()
{
	// complete as quickly as possible
	s_settings_interface.SetBoolValue("EmuCore/GS", "FrameLimitEnable", false);
	s_settings_interface.SetIntValue("EmuCore/GS", "VsyncEnable", false);

	// Force screenshot quality settings to something more performant, overriding any defaults good for users.
	s_settings_interface.SetIntValue("EmuCore/GS", "ScreenshotFormat", static_cast<int>(GSScreenshotFormat::PNG));
	s_settings_interface.SetIntValue("EmuCore/GS", "ScreenshotQuality", 10);

	// ensure all input sources are disabled, we're not using them
	s_settings_interface.SetBoolValue("InputSources", "SDL", false);
	s_settings_interface.SetBoolValue("InputSources", "XInput", false);

	// we don't need any sound output
	s_settings_interface.SetStringValue("SPU2/Output", "OutputModule", "nullout");
	s_settings_interface.SetStringValue("SPU2/Output", "Backend", "Null"); // Ton nur im echten Spiel (ps2xgs_start)

	// none of the bindings are going to resolve to anything
	Pad::ClearPortBindings(s_settings_interface, 0);
	s_settings_interface.ClearSection("Hotkeys");

	// force logging
	s_settings_interface.SetBoolValue("Logging", "EnableSystemConsole", !s_no_console);
	s_settings_interface.SetBoolValue("Logging", "EnableTimestamps", true);
	s_settings_interface.SetBoolValue("Logging", "EnableVerbose", true);

	// and show some stats :)
	s_settings_interface.SetBoolValue("EmuCore/GS", "OsdShowFPS", true);
	s_settings_interface.SetBoolValue("EmuCore/GS", "OsdShowResolution", true);
	s_settings_interface.SetBoolValue("EmuCore/GS", "OsdShowGSStats", true);

	// remove memory cards, so we don't have sharing violations
	for (u32 i = 0; i < 2; i++)
	{
		s_settings_interface.SetBoolValue("MemoryCards", fmt::format("Slot{}_Enable", i + 1).c_str(), false);
		s_settings_interface.SetStringValue("MemoryCards", fmt::format("Slot{}_Filename", i + 1).c_str(), "");
	}
}

void GSRunner::DumpStats()
{
	std::atomic_thread_fence(std::memory_order_acquire);
	Console.WriteLn(fmt::format("======= HW STATISTICS FOR {} ({}) FRAMES ========", s_total_frames, s_total_drawn_frames));
	Console.WriteLn(fmt::format("@HWSTAT@ Draw Calls: {} (avg {})", s_total_draws, static_cast<u64>(std::ceil(s_total_draws / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Render Passes: {} (avg {})", s_total_render_passes, static_cast<u64>(std::ceil(s_total_render_passes / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Barriers: {} (avg {})", s_total_barriers, static_cast<u64>(std::ceil(s_total_barriers / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Copies: {} (avg {})", s_total_copies, static_cast<u64>(std::ceil(s_total_copies / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Uploads: {} (avg {})", s_total_uploads, static_cast<u64>(std::ceil(s_total_uploads / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Readbacks: {} (avg {})", s_total_readbacks, static_cast<u64>(std::ceil(s_total_readbacks / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Copies (ROV): {} (avg {})", s_total_copies_rov, static_cast<u64>(std::ceil(s_total_copies_rov / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Draws Calls (ROV): {} (avg {})", s_total_draws_rov, static_cast<u64>(std::ceil(s_total_draws_rov / static_cast<double>(s_total_drawn_frames)))));
	Console.WriteLn(fmt::format("@HWSTAT@ Barriers (ROV): {} (avg {})", s_total_barriers_rov, static_cast<u64>(std::ceil(s_total_barriers_rov / static_cast<double>(s_total_drawn_frames)))));
	if (s_perf_enable)
	{
		Console.WriteLn(fmt::format("@HWSTAT@ Minimum Frame Time: {:.3f} ms ({:.3f} FPS)", PerformanceMetrics::GetMinimumFrameTime(), 1000.0f / PerformanceMetrics::GetMinimumFrameTime()));
		Console.WriteLn(fmt::format("@HWSTAT@ Average Frame Time: {:.3f} ms ({:.3f} FPS)", PerformanceMetrics::GetAverageFrameTime(), 1000.0f / PerformanceMetrics::GetAverageFrameTime()));
		Console.WriteLn(fmt::format("@HWSTAT@ Maximum Frame Time: {:.3f} ms ({:.3f} FPS)", PerformanceMetrics::GetMaximumFrameTime(), 1000.0f / PerformanceMetrics::GetMaximumFrameTime()));
		Console.WriteLn(fmt::format("@HWSTAT@ CPU Thread Usage: {:.3f} %", s_perf_sum_cpu_thread_usage / s_perf_updates));
		Console.WriteLn(fmt::format("@HWSTAT@ GS Thread Usage: {:.3f} %", s_perf_sum_gs_thread_usage / s_perf_updates));
		Console.WriteLn(fmt::format("@HWSTAT@ GPU Usage: {:.3f} %", s_perf_sum_gpu_usage / s_perf_updates));
		Console.WriteLn(fmt::format("@HWSTAT@ Average CPU Thread Time: {:.3f} ms", s_perf_sum_cpu_thread_time / s_perf_updates));
		Console.WriteLn(fmt::format("@HWSTAT@ Average GS Thread Time: {:.3f} ms", s_perf_sum_gs_thread_time / s_perf_updates));
		Console.WriteLn(fmt::format("@HWSTAT@ Average GPU Time: {:.3f} ms", s_perf_sum_gpu_time / s_perf_updates));
	}
	Console.WriteLn("============================================");
}

#ifdef _WIN32
// We can't handle unicode in filenames if we don't use wmain on Win32.
#define main real_main
#endif

static void CPUThreadMain(VMBootParameters* params, std::atomic<int>* ret)
{
	ret->store(EXIT_FAILURE);

	if (VMManager::Internal::CPUThreadInitialize())
	{
		// apply new settings (e.g. pick up renderer change)
		VMManager::ApplySettings();
		GSDumpReplayer::SetIsDumpRunner(true);

		if (VMManager::Initialize(*params) == VMBootResult::StartupSuccess)
		{
			// run until end
			GSDumpReplayer::SetLoopCount(s_loop_count);
			VMManager::SetState(VMState::Running);
			if (s_perf_enable)
			{
				VMManager::SetLimiterMode(LimiterModeType::Unlimited);
				g_gs_device->SetGPUTimingEnabled(true);
			}
			while (VMManager::GetState() == VMState::Running)
				VMManager::Execute();
			VMManager::Shutdown(false);
			GSRunner::DumpStats();
			ret->store(EXIT_SUCCESS);
		}
	}

	VMManager::Internal::CPUThreadShutdown();
	GSRunner::StopPlatformMessagePump();
}

void Host::PumpMessagesOnCPUThread()
{
	// update GS thread copy of frame number
	MTGS::RunOnGSThread([frame_number = GSDumpReplayer::GetFrameNumber()]() { s_dump_frame_number = frame_number; });
	MTGS::RunOnGSThread([loop_number = GSDumpReplayer::GetLoopCount()]() { s_loop_number = loop_number; });
}

s32 Host::Internal::GetTranslatedStringImpl(
	const std::string_view context, const std::string_view msg, char* tbuf, size_t tbuf_space)
{
	if (msg.size() > tbuf_space)
		return -1;
	else if (msg.empty())
		return 0;

	std::memcpy(tbuf, msg.data(), msg.size());
	return static_cast<s32>(msg.size());
}

std::string Host::TranslatePluralToString(const char* context, const char* msg, const char* disambiguation, int count)
{
	TinyString count_str = TinyString::from_format("{}", count);

	std::string ret(msg);
	for (;;)
	{
		std::string::size_type pos = ret.find("%n");
		if (pos == std::string::npos)
			break;

		ret.replace(pos, pos + 2, count_str.view());
	}

	return ret;
}

//////////////////////////////////////////////////////////////////////////
// Platform specific code
//////////////////////////////////////////////////////////////////////////

#ifdef _WIN32

static constexpr LPCWSTR WINDOW_CLASS_NAME = L"PCSX2GSRunner";
static HWND s_hwnd = NULL;

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam);

bool GSRunner::CreatePlatformWindow()
{
	WNDCLASSEXW wc = {};
	wc.cbSize = sizeof(WNDCLASSEXW);
	wc.style = 0;
	wc.lpfnWndProc = WndProc;
	wc.cbClsExtra = 0;
	wc.cbWndExtra = 0;
	wc.hInstance = GetModuleHandle(nullptr);
	wc.hIcon = NULL;
	wc.hCursor = LoadCursor(NULL, IDC_ARROW);
	wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
	wc.lpszMenuName = NULL;
	wc.lpszClassName = WINDOW_CLASS_NAME;
	wc.hIconSm = NULL;

	if (!RegisterClassExW(&wc))
	{
		Console.Error("Window registration failed.");
		return false;
	}

	s_hwnd = CreateWindowExW(WS_EX_CLIENTEDGE, WINDOW_CLASS_NAME, L"PCSX2 GS Runner",
		WS_OVERLAPPEDWINDOW | WS_CAPTION | WS_MINIMIZEBOX | WS_SYSMENU | WS_SIZEBOX, CW_USEDEFAULT, CW_USEDEFAULT, WINDOW_WIDTH,
		WINDOW_HEIGHT, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
	if (!s_hwnd)
	{
		Console.Error("CreateWindowEx failed.");
		return false;
	}

	ShowWindow(s_hwnd, SW_SHOW);
	UpdateWindow(s_hwnd);

	// make sure all messages are processed before returning
	PumpPlatformMessages();
	return true;
}

void GSRunner::DestroyPlatformWindow()
{
	if (!s_hwnd)
		return;

	PumpPlatformMessages();
	DestroyWindow(s_hwnd);
	s_hwnd = {};
}

std::optional<WindowInfo> GSRunner::GetPlatformWindowInfo()
{
	WindowInfo wi;

	if (s_hwnd)
	{
		RECT rc = {};
		GetWindowRect(s_hwnd, &rc);
		wi.surface_width = static_cast<u32>(rc.right - rc.left);
		wi.surface_height = static_cast<u32>(rc.bottom - rc.top);
		wi.surface_scale = 1.0f;
		wi.type = WindowInfo::Type::Win32;
		wi.window_handle = s_hwnd;
	}
	else
	{
		wi.type = WindowInfo::Type::Surfaceless;
	}

	return wi;
}

static constexpr int SHUTDOWN_MSG = WM_APP + 0x100;
static DWORD MainThreadID;

void GSRunner::PumpPlatformMessages(bool forever)
{
	MSG msg;
	while (true)
	{
		while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE))
		{
			if (msg.message == SHUTDOWN_MSG)
				return;
			TranslateMessage(&msg);
			DispatchMessageW(&msg);
		}
		if (!forever)
			return;
		WaitMessage();
	}
}

void GSRunner::StopPlatformMessagePump()
{
	PostThreadMessageW(MainThreadID, SHUTDOWN_MSG, 0, 0);
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
	return DefWindowProcW(hwnd, msg, wParam, lParam);
}

int wmain(int argc, wchar_t** argv)
{
	std::vector<std::string> u8_args;
	u8_args.reserve(static_cast<size_t>(argc));
	for (int i = 0; i < argc; i++)
		u8_args.push_back(StringUtil::WideStringToUTF8String(argv[i]));

	std::vector<char*> u8_argptrs;
	u8_argptrs.reserve(u8_args.size());
	for (int i = 0; i < argc; i++)
		u8_argptrs.push_back(u8_args[i].data());
	u8_argptrs.push_back(nullptr);

	MainThreadID = GetCurrentThreadId();

	return real_main(argc, u8_argptrs.data());
}

#elif defined(__APPLE__)

static void* s_window;
static WindowInfo s_wi;

bool GSRunner::CreatePlatformWindow()
{
	pxAssertRel(!s_window, "Tried to create window when there already was one!");
	s_window = CocoaTools::CreateWindow("PCSX2 GS Runner", WINDOW_WIDTH, WINDOW_HEIGHT);
	CocoaTools::GetWindowInfoFromWindow(&s_wi, s_window);
	PumpPlatformMessages();
	return s_window;
}

void GSRunner::DestroyPlatformWindow()
{
	if (s_window) {
		CocoaTools::DestroyWindow(s_window);
		s_window = nullptr;
	}
}

std::optional<WindowInfo> GSRunner::GetPlatformWindowInfo()
{
	WindowInfo wi;
	if (s_window)
		wi = s_wi;
	else
		wi.type = WindowInfo::Type::Surfaceless;
	return wi;
}

void GSRunner::PumpPlatformMessages(bool forever)
{
	CocoaTools::RunCocoaEventLoop(forever);
}

void GSRunner::StopPlatformMessagePump()
{
	CocoaTools::StopMainThreadEventLoop();
}

#elif defined(__linux__)
static Display* s_display = nullptr;
static Window s_window = None;
static WindowInfo s_wi;
static std::atomic<bool> s_shutdown_requested{false};

// ---- ps2x: Spielmenue (ImGui im Spielfenster, Gamepad/Tastatur/Maus), Einstellungen in ~/.config/sotc-recomp ----
#include "imgui.h"
#include "pcsx2/ImGui/ImGuiManager.h"
#include "pcsx2/MTGS.h"
#include <fstream>
#include <sys/stat.h>
namespace ps2xosd { std::atomic<u32> btns{0}, axes{0x80808080u}, keyBtns{0}; } // zuletzt ans Spiel gelieferte Eingabe
namespace ps2xmenu
{
	std::atomic<bool> open{false};
	std::atomic<bool> applyPending{false};
	std::atomic<bool> fsPending{false};
	std::atomic<u32> nav{0};          // gehaltene Menue-Tasten: 1 hoch 2 runter 4 links 8 rechts 16 OK 32 zurueck
	std::atomic<int> mouseX{-1}, mouseY{-1};
	std::atomic<u32> mouseBtn{0};
	std::atomic<int> wheel{0};
	std::atomic<bool> latch{false};   // nach dem Schliessen: Spiel-Eingabe erst nach Loslassen
	std::atomic<bool> gameWide{false};
	std::atomic<bool> active{false};   // Menue/Einstellungen aktiv (nicht in Testlaeufen ohne Fenster) // Spiel rendert im eigenen 16:9-Modus (set_screen_wide_and_progressive)

	struct Cfg
	{
		int upscale = 4, aspect = 0, cas = 1, casSharp = 25, tv = 0, filter = 2, aniso = 16, dither = 0;
		int sbB = 50, sbC = 50, sbS = 50, rumblePct = 100, hz = 50, volume = 100, camBlur = 1;
		bool fxaa = false, shadeboost = false, fullscreen = false, showFps = false, rumble = true, vsync = false, startMenu = true;
	};
	std::mutex m;
	Cfg cfg;

	std::string dir()
	{
		const char* xdg = std::getenv("XDG_CONFIG_HOME");
		const char* home = std::getenv("HOME");
		std::string d = (xdg && *xdg) ? std::string(xdg) : std::string(home ? home : "/tmp") + "/.config";
		d += "/sotc-recomp";
		mkdir(d.c_str(), 0755);
		return d;
	}
	std::string iniPath()
	{
		// PS2XGS_CFG=<Datei>: andere Einstellungsdatei (Vergleichsbilder in Testlaeufen)
		if (const char* c = std::getenv("PS2XGS_CFG"); c && *c)
			return c;
		return dir() + "/einstellungen.ini";
	}

	void load(float envUpscale)
	{
		std::lock_guard<std::mutex> lk(m);
		(void)envUpscale; // Standard: Remaster-Preset (4x); ein gespeicherter Wert hat Vorrang
		std::ifstream f(iniPath());
		std::string line;
		while (std::getline(f, line))
		{
			const auto eq = line.find('=');
			if (eq == std::string::npos || line[0] == '#')
				continue;
			const std::string k = line.substr(0, eq);
			const int v = std::atoi(line.c_str() + eq + 1);
			if (k == "aufloesung") cfg.upscale = std::clamp(v, 1, 8);
			else if (k == "bildformat") cfg.aspect = std::clamp(v, 0, 3);
			else if (k == "fxaa") cfg.fxaa = v != 0;
			else if (k == "cas") cfg.cas = std::clamp(v, 0, 2);
			else if (k == "cas_staerke") cfg.casSharp = std::clamp(v, 0, 100);
			else if (k == "dithering") cfg.dither = std::clamp(v, 0, 2);
			else if (k == "tv_shader") cfg.tv = std::clamp(v, 0, 7);
			else if (k == "farbe") cfg.shadeboost = v != 0;
			else if (k == "helligkeit") cfg.sbB = std::clamp(v, 1, 100);
			else if (k == "kontrast") cfg.sbC = std::clamp(v, 1, 100);
			else if (k == "saettigung") cfg.sbS = std::clamp(v, 1, 100);
			else if (k == "texturfilter") cfg.filter = std::clamp(v, 0, 3);
			else if (k == "anisotrop") cfg.aniso = std::clamp(v, 0, 16);
			else if (k == "vollbild") cfg.fullscreen = v != 0;
			else if (k == "fps_anzeige") cfg.showFps = v != 0;
			else if (k == "vibration") cfg.rumble = v != 0;
			else if (k == "vibration_staerke") cfg.rumblePct = std::clamp(v, 0, 100);
			else if (k == "vsync") cfg.vsync = v != 0;
			else if (k == "startmenue") cfg.startMenu = v != 0;
			else if (k == "bildrate") cfg.hz = (v == 60 && std::getenv("SOTC_60HZ_EXPERIMENT")) ? 60 : 50; // 60 Hz: Figur-Physik noch fehlerhaft
			else if (k == "lautstaerke") cfg.volume = std::clamp(v, 0, 200);
			else if (k == "kamera_unschaerfe") cfg.camBlur = v != 0 ? 1 : 0;
		}
	}
	void save()
	{
		Cfg c;
		{ std::lock_guard<std::mutex> lk(m); c = cfg; }
		std::ofstream f(iniPath(), std::ios::trunc);
		f << "# SotC Recomp - Einstellungen (werden im Spielmenue geaendert, ESC/F1 oder Guide-Taste)\n"
		  << "aufloesung=" << c.upscale << "\nbildformat=" << c.aspect << "\nfxaa=" << c.fxaa << "\ncas=" << c.cas
		  << "\ncas_staerke=" << c.casSharp << "\ndithering=" << c.dither << "\ntv_shader=" << c.tv << "\nfarbe=" << c.shadeboost << "\nhelligkeit=" << c.sbB
		  << "\nkontrast=" << c.sbC << "\nsaettigung=" << c.sbS << "\ntexturfilter=" << c.filter << "\nanisotrop=" << c.aniso
		  << "\nvollbild=" << c.fullscreen << "\nfps_anzeige=" << c.showFps << "\nvibration=" << c.rumble
		  << "\nvibration_staerke=" << c.rumblePct << "\nvsync=" << c.vsync << "\nbildrate=" << c.hz << "\nlautstaerke=" << c.volume << "\nkamera_unschaerfe=" << c.camBlur << "\nstartmenue=" << c.startMenu << "\n";
	}
	// in die PCSX2-Einstellungen uebertragen (CPU-Thread bzw. vor dem Start)
	void toSettings(SettingsInterface& si)
	{
		Cfg c;
		{ std::lock_guard<std::mutex> lk(m); c = cfg; }
		// Bildformat: 0 = 16:9 (Breitbild-Modus des Spiels erzwungen), 1 = wie Spieloption, 2 = 4:3, 3 = Fenster fuellen
		const bool wide = (c.aspect == 0) || (c.aspect == 1 && gameWide.load());
		si.SetFloatValue("EmuCore/GS", "upscale_multiplier", static_cast<float>(c.upscale));
		si.SetStringValue("EmuCore/GS", "AspectRatio", c.aspect == 3 ? "Stretch" : (wide ? "16:9" : "4:3"));
		si.SetBoolValue("EmuCore/GS", "fxaa", c.fxaa);
		si.SetIntValue("EmuCore/GS", "CASMode", c.cas);
		si.SetIntValue("EmuCore/GS", "CASSharpness", c.casSharp);
		// PS2-Dithering: hochskaliert wird es zum feinen Gittermuster auf Gesichtern/Waenden -> im Remaster aus
		si.SetIntValue("EmuCore/GS", "dithering_ps2", c.dither);
		// Oberste/unterste Bildzeile enthaelt Restdaten (bunte Pixel am Bildrand) -> je 2 Zeilen abschneiden
		si.SetIntValue("EmuCore/GS", "CropTop", 2);
		si.SetIntValue("EmuCore/GS", "CropBottom", 2);
		si.SetIntValue("EmuCore/GS", "TVShader", c.tv);
		si.SetBoolValue("EmuCore/GS", "ShadeBoost", c.shadeboost);
		si.SetIntValue("EmuCore/GS", "ShadeBoost_Brightness", c.sbB);
		si.SetIntValue("EmuCore/GS", "ShadeBoost_Contrast", c.sbC);
		si.SetIntValue("EmuCore/GS", "ShadeBoost_Saturation", c.sbS);
		si.SetIntValue("EmuCore/GS", "filter", c.filter);
		si.SetIntValue("EmuCore/GS", "MaxAnisotropy", c.aniso);
		si.SetBoolValue("EmuCore/GS", "OsdShowFPS", c.showFps);
		si.SetIntValue("EmuCore/GS", "OsdPerformancePos", c.showFps ? 3 : 0);
		si.SetIntValue("EmuCore/GS", "VsyncEnable", c.vsync ? 1 : 0);
		si.SetBoolValue("EmuCore/GS", "hw_mipmap", true);           // Mipmaps in Hardware: kein Flimmern in der Ferne
		si.SetIntValue("EmuCore/GS", "linear_present_mode", 1);     // weiches Herunterrechnen = Supersampling-Effekt
	}

	// ---------- Zeichnen (GS-Thread) ----------
	u32 s_prevNav = 0;
	void feedInput()
	{
		ImGuiIO& io = ImGui::GetIO();
		io.ConfigFlags |= ImGuiConfigFlags_NavEnableGamepad | ImGuiConfigFlags_NavEnableKeyboard;
		io.BackendFlags |= ImGuiBackendFlags_HasGamepad;
		const u32 n = nav.load();
		const u32 ch = n ^ s_prevNav;
		static const ImGuiKey kKeys[6] = {ImGuiKey_GamepadDpadUp, ImGuiKey_GamepadDpadDown, ImGuiKey_GamepadDpadLeft,
			ImGuiKey_GamepadDpadRight, ImGuiKey_GamepadFaceDown, ImGuiKey_GamepadFaceRight};
		for (int i = 0; i < 6; ++i)
			if (ch & (1u << i))
				io.AddKeyEvent(kKeys[i], (n >> i) & 1u);
		s_prevNav = n;
		const int mx = mouseX.load(), my = mouseY.load();
		if (mx >= 0)
			io.AddMousePosEvent(static_cast<float>(mx), static_cast<float>(my));
		static u32 s_prevMb = 0;
		const u32 mb = mouseBtn.load();
		for (int i = 0; i < 3; ++i)
			if ((mb ^ s_prevMb) & (1u << i))
				io.AddMouseButtonEvent(i, (mb >> i) & 1u);
		s_prevMb = mb;
		if (const int w = wheel.exchange(0))
			io.AddMouseWheelEvent(0.0f, static_cast<float>(w));
	}

	void style()
	{
		// Farben angelehnt an SotC: nebliges Grau, warmes Sonnenlicht, Steinbeige
		ImGuiStyle& st = ImGui::GetStyle();
		st.WindowRounding = 10.0f; st.FrameRounding = 6.0f; st.GrabRounding = 6.0f; st.TabRounding = 6.0f;
		st.WindowPadding = ImVec2(22, 18); st.FramePadding = ImVec2(12, 7); st.ItemSpacing = ImVec2(12, 10);
		ImVec4* c = st.Colors;
		c[ImGuiCol_WindowBg] = ImVec4(0.07f, 0.075f, 0.07f, 0.88f);
		c[ImGuiCol_Border] = ImVec4(0.78f, 0.70f, 0.52f, 0.35f);
		c[ImGuiCol_Text] = ImVec4(0.93f, 0.91f, 0.85f, 1.0f);
		c[ImGuiCol_TextDisabled] = ImVec4(0.60f, 0.58f, 0.52f, 1.0f);
		c[ImGuiCol_FrameBg] = ImVec4(0.18f, 0.18f, 0.16f, 0.9f);
		c[ImGuiCol_FrameBgHovered] = ImVec4(0.30f, 0.28f, 0.22f, 0.9f);
		c[ImGuiCol_FrameBgActive] = ImVec4(0.40f, 0.36f, 0.26f, 0.9f);
		c[ImGuiCol_Button] = ImVec4(0.22f, 0.21f, 0.18f, 0.95f);
		c[ImGuiCol_ButtonHovered] = ImVec4(0.55f, 0.47f, 0.30f, 0.95f);
		c[ImGuiCol_ButtonActive] = ImVec4(0.72f, 0.60f, 0.36f, 1.0f);
		c[ImGuiCol_Header] = ImVec4(0.30f, 0.27f, 0.20f, 0.9f);
		c[ImGuiCol_HeaderHovered] = ImVec4(0.55f, 0.47f, 0.30f, 0.9f);
		c[ImGuiCol_HeaderActive] = ImVec4(0.72f, 0.60f, 0.36f, 1.0f);
		c[ImGuiCol_Tab] = ImVec4(0.16f, 0.16f, 0.14f, 1.0f);
		c[ImGuiCol_TabHovered] = ImVec4(0.55f, 0.47f, 0.30f, 1.0f);
		c[ImGuiCol_TabSelected] = ImVec4(0.42f, 0.36f, 0.24f, 1.0f);
		c[ImGuiCol_SliderGrab] = ImVec4(0.85f, 0.74f, 0.48f, 1.0f);
		c[ImGuiCol_SliderGrabActive] = ImVec4(1.0f, 0.88f, 0.60f, 1.0f);
		c[ImGuiCol_CheckMark] = ImVec4(0.95f, 0.84f, 0.56f, 1.0f);
		c[ImGuiCol_NavCursor] = ImVec4(1.0f, 0.88f, 0.60f, 1.0f);
		c[ImGuiCol_Separator] = ImVec4(0.78f, 0.70f, 0.52f, 0.30f);
	}

	std::string memcardInfo()
	{
		const char* mc = std::getenv("SOTC_MEMCARD");
		if (!mc || !*mc)
			return "Speicherkarte: nicht gesetzt";
		struct stat sb{};
		if (stat(mc, &sb) != 0)
			return std::string("Speicherkarte: ") + mc + " (noch leer)";
		char tbuf[64];
		std::strftime(tbuf, sizeof(tbuf), "%d.%m.%Y %H:%M", std::localtime(&sb.st_mtime));
		return std::string("Speicherkarte: ") + mc + "\nZuletzt gespeichert: " + tbuf;
	}

	bool combo(const char* label, int* v, const char* const* items, int n)
	{
		return ImGui::Combo(label, v, items, n);
	}

	void draw()
	{
		static bool styled = false;
		if (!open.load())
		{
			s_prevNav = 0;
			// Die ersten Sekunden: dezenter Hinweis, wie das Menue aufgeht
			static const auto s_t0 = std::chrono::steady_clock::now();
			const float t = std::chrono::duration<float>(std::chrono::steady_clock::now() - s_t0).count();
			{
				// Eingabe-Anzeige (bei eingeschalteter FPS-Anzeige): was das Spiel gerade vom Pad/der Tastatur bekommt
				bool show; { std::lock_guard<std::mutex> lk(m); show = cfg.showFps; }
				if (show)
				{
					const u32 b = ps2xosd::btns.load(), ax = ps2xosd::axes.load(), kb = ps2xosd::keyBtns.load();
					static const char* kN[16] = {"SEL", "L3", "R3", "START", "UP", "RIGHT", "DOWN", "LEFT", "L2", "R2", "L1", "R1", "TRI", "O", "X", "SQ"};
					std::string txt = fmt::format("L {:3d},{:3d}  R {:3d},{:3d} ", (ax & 0xFF), ((ax >> 8) & 0xFF), ((ax >> 16) & 0xFF), (ax >> 24));
					for (int i = 0; i < 16; ++i) if (b & (1u << i)) { txt += kN[i]; txt += (kb & (1u << i)) ? "(Tast) " : " "; }
					ImDrawList* dl = ImGui::GetForegroundDrawList();
					const float fs = ImGui::GetFontSize();
					const ImVec2 p0(fs * 1.2f, fs * 1.0f);
					const ImVec2 ts = ImGui::CalcTextSize(txt.c_str());
					dl->AddRectFilled(ImVec2(p0.x - fs * 0.4f, p0.y - fs * 0.3f), ImVec2(p0.x + ts.x + fs * 0.4f, p0.y + ts.y + fs * 0.3f), IM_COL32(0, 0, 0, 150), fs * 0.2f);
					dl->AddText(p0, IM_COL32(120, 255, 140, 255), txt.c_str());
				}
			}
			if (t < 15.0f)
			{
				const float a = std::clamp((15.0f - t) / 2.0f, 0.0f, 1.0f);
				const char* txt = "Menue: ESC / F1   |   Gamepad: Select + Start (oder Guide)";
				ImDrawList* dl = ImGui::GetForegroundDrawList();
				const float fs = ImGui::GetFontSize();
				const ImVec2 ts = ImGui::CalcTextSize(txt);
				const ImVec2 p0(fs * 1.2f, ImGui::GetIO().DisplaySize.y - ts.y - fs * 1.6f);
				dl->AddRectFilled(ImVec2(p0.x - fs * 0.6f, p0.y - fs * 0.4f), ImVec2(p0.x + ts.x + fs * 0.6f, p0.y + ts.y + fs * 0.4f), IM_COL32(10, 10, 8, static_cast<int>(170 * a)), fs * 0.3f);
				dl->AddText(p0, IM_COL32(245, 222, 160, static_cast<int>(255 * a)), txt);
			}
			return;
		}
		feedInput();
		if (!styled) { style(); styled = true; }
		ImGuiIO& io = ImGui::GetIO();
		const ImVec2 ds = io.DisplaySize;
		// Hintergrund abdunkeln
		ImGui::GetBackgroundDrawList()->AddRectFilled(ImVec2(0, 0), ds, IM_COL32(8, 8, 6, 150));
		const float w = std::min(ds.x * 0.62f, 980.0f * ImGuiManager::GetGlobalScale());
		const float h = std::min(ds.y * 0.80f, 820.0f * ImGuiManager::GetGlobalScale());
		ImGui::SetNextWindowPos(ImVec2(ds.x * 0.5f, ds.y * 0.5f), ImGuiCond_Always, ImVec2(0.5f, 0.5f));
		ImGui::SetNextWindowSize(ImVec2(w, h), ImGuiCond_Always);
		ImGui::Begin("##ps2xmenu", nullptr, ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse);
		ImGui::PushFont(nullptr, ImGui::GetFontSize() * 1.6f);
		ImGui::TextColored(ImVec4(0.96f, 0.86f, 0.60f, 1.0f), "SHADOW OF THE COLOSSUS");
		ImGui::PopFont();
		ImGui::TextDisabled("Nativer Linux-Port  |  ESC / F1 / Guide: Menue  |  F11: Vollbild");
		ImGui::Separator();

		Cfg c;
		{ std::lock_guard<std::mutex> lk(m); c = cfg; }
		const Cfg before = c;
		bool closeReq = false, quitReq = false;

		if (ImGui::BeginTabBar("##tabs"))
		{
			if (ImGui::BeginTabItem("Grafik"))
			{
				ImGui::TextDisabled("Voreinstellung:");
				ImGui::SameLine();
				if (ImGui::Button("Remaster (scharf)"))
				{ c.upscale = 4; c.aniso = 16; c.filter = 2; c.cas = 1; c.casSharp = 25; c.fxaa = false; c.tv = 0; c.shadeboost = false; c.dither = 0; c.camBlur = 1; }
				ImGui::SameLine();
				if (ImGui::Button("Original-Look"))
				{ c.upscale = 1; c.aniso = 0; c.filter = 2; c.cas = 0; c.fxaa = false; c.tv = 5; c.shadeboost = false; c.aspect = 2; c.hz = 50; c.dither = 2; c.camBlur = 1; }
				ImGui::SameLine();
				if (ImGui::Button("Leistung"))
				{ c.upscale = 2; c.aniso = 4; c.filter = 2; c.cas = 1; c.casSharp = 40; c.fxaa = false; c.tv = 0; }
				ImGui::Separator();
				static const char* kRes[] = {"1x  Original (512x448)", "2x  (~720p)", "3x  (~1080p / 1440p intern)", "4x  (~1440p)", "5x", "6x  (~4K)", "7x", "8x  (~5K)"};
				int r = c.upscale - 1;
				if (combo("Interne Aufloesung", &r, kRes, 8)) c.upscale = r + 1;
				static const char* kAsp[] = {"16:9 Breitbild (mehr Sichtfeld)", "Wie im Spiel-Optionsmenue", "4:3 Original", "Fenster fuellen (gestreckt)"};
				combo("Bildformat", &c.aspect, kAsp, 4);
				if (c.aspect != before.aspect)
					ImGui::TextDisabled("Wirkt ab dem naechsten Bereichswechsel bzw. Neustart voll.");
				static const char* kFilt[] = {"Pixelig (Nearest)", "Bilinear (immer)", "Bilinear (wie PS2)", "Bilinear (ohne Sprites)"};
				combo("Texturfilter", &c.filter, kFilt, 4);
				static const char* kAni[] = {"Aus", "2x", "4x", "8x", "16x"};
				int ai = c.aniso == 0 ? 0 : c.aniso == 2 ? 1 : c.aniso == 4 ? 2 : c.aniso == 8 ? 3 : 4;
				if (combo("Anisotrope Filterung", &ai, kAni, 5)) c.aniso = ai == 0 ? 0 : (1 << ai);
				{
					ImGui::TextColored(ImVec4(0.96f, 0.86f, 0.60f, 1.0f), "60 FPS:");
					ImGui::SameLine();
					ImGui::TextWrapped("im Spiel unter Optionen den 60-Hz-Modus waehlen. Der Port stellt sich automatisch um (richtige Spielphysik).");
				}
				ImGui::Checkbox("Vollbild", &c.fullscreen);
				ImGui::Checkbox("V-Sync (gegen Tearing)", &c.vsync);
				ImGui::Checkbox("FPS-Anzeige", &c.showFps);
				ImGui::Checkbox("Menue beim Spielstart zeigen", &c.startMenu);
				ImGui::EndTabItem();
			}
			if (ImGui::BeginTabItem("Bild & Shader"))
			{
				ImGui::TextDisabled("Kamera-Bewegungsunschaerfe: wie im Original (Abschalten kommt in einer spaeteren Version).");
				ImGui::Checkbox("Kantenglaettung (FXAA)", &c.fxaa);
				static const char* kCas[] = {"Aus", "Schaerfen", "Schaerfen + Skalieren"};
				combo("Schaerfe (AMD CAS)", &c.cas, kCas, 3);
				if (c.cas) ImGui::SliderInt("Schaerfe-Staerke", &c.casSharp, 0, 100);
				static const char* kDith[] = {"Aus (sauber, empfohlen)", "Skaliert", "Original (unskaliert)"};
				combo("PS2-Dithering", &c.dither, kDith, 3);
				static const char* kTv[] = {"Kein Filter", "Scanlines", "Diagonal", "Dreieck", "Welle", "CRT (Lottes)", "4x RGSS", "NxAGSS"};
				combo("Bildfilter (TV/CRT)", &c.tv, kTv, 8);
				ImGui::Separator();
				ImGui::Checkbox("Farbanpassung", &c.shadeboost);
				if (c.shadeboost)
				{
					ImGui::SliderInt("Helligkeit", &c.sbB, 1, 100);
					ImGui::SliderInt("Kontrast", &c.sbC, 1, 100);
					ImGui::SliderInt("Saettigung", &c.sbS, 1, 100);
					if (ImGui::Button("Farben zuruecksetzen")) { c.sbB = c.sbC = c.sbS = 50; }
				}
				ImGui::EndTabItem();
			}
			if (ImGui::BeginTabItem("Ton"))
			{
				ImGui::SliderInt("Lautstaerke (%)", &c.volume, 0, 200);
				ImGui::TextDisabled("Original-Soundchip (SPU2) mit 48 kHz, Ausgabe ueber PipeWire/PulseAudio.");
				ImGui::TextDisabled("Im Menue ist der Ton angehalten.");
				ImGui::EndTabItem();
			}
			if (ImGui::BeginTabItem("Steuerung"))
			{
				ImGui::Checkbox("Vibration", &c.rumble);
				if (c.rumble) ImGui::SliderInt("Vibrationsstaerke (%)", &c.rumblePct, 0, 100);
				ImGui::Separator();
				ImGui::TextDisabled("Gamepad: wird automatisch erkannt (Xbox / PlayStation / Switch)");
				ImGui::TextDisabled("Tastatur: WASD laufen, IJKL Kamera, Pfeile Steuerkreuz");
				ImGui::TextDisabled("X/Leer Kreuz, C Kreis, Z Quadrat, V Dreieck, Q/E L1/R1");
				ImGui::TextDisabled("Shift/R L2/R2, F/G L3/R3, Enter Start, Tab Select");
				ImGui::EndTabItem();
			}
			if (ImGui::BeginTabItem("Speicherstand"))
			{
				ImGui::TextWrapped("%s", memcardInfo().c_str());
				ImGui::TextDisabled("Gespeichert wird im Spiel wie auf der PS2 (an den Schreinen).");
				ImGui::TextDisabled("Bei jedem Start wird automatisch eine Sicherungskopie angelegt.");
				ImGui::EndTabItem();
			}
			ImGui::EndTabBar();
		}
		ImGui::Separator();
		if (ImGui::Button("  Weiterspielen  "))
			closeReq = true;
		ImGui::SameLine();
		if (ImGui::Button("  Spiel beenden  "))
			quitReq = true;
		if (ImGui::IsWindowAppearing())
			ImGui::SetKeyboardFocusHere(-1);
		ImGui::End();

		const bool changed = std::memcmp(&before, &c, sizeof(Cfg)) != 0;
		if (changed)
		{
			{ std::lock_guard<std::mutex> lk(m); cfg = c; }
			if (c.fullscreen != before.fullscreen) fsPending.store(true);
			applyPending.store(true);
			save();
		}
		if (closeReq) { open.store(false); latch.store(true); }
		if (quitReq) { save(); std::fflush(nullptr); std::_Exit(0); }
	}

	void toggle()
	{
		const bool o = !open.load();
		open.store(o);
		if (!o) latch.store(true);
	}
}

// ---- ps2x: Eingabe direkt im Spielfenster (Tastatur) + Gamepad ueber SDL3 (auch ohne Fokus) ----------------
#include <SDL3/SDL.h>
#include <X11/keysym.h>
#include <X11/XKBlib.h>
namespace ps2xin
{
	// PS2-Tastenbits (1 = gedrueckt), wie libpad: Select L3 R3 Start Up Right Down Left L2 R2 L1 R1 Tri Circle Cross Square
	enum : u16 { SELECT = 0x0001, L3 = 0x0002, R3 = 0x0004, START = 0x0008, UP = 0x0010, RIGHT = 0x0020, DOWN = 0x0040, LEFT = 0x0080,
		L2 = 0x0100, R2 = 0x0200, L1 = 0x0400, R1 = 0x0800, TRI = 0x1000, CIRCLE = 0x2000, CROSS = 0x4000, SQUARE = 0x8000 };
	std::atomic<u32> keyBtns{0};      // Tastatur -> Tasten
	std::atomic<u32> keyStick{0};     // Tastatur -> Sticks: Bits 0-3 links (oben unten links rechts), 4-7 rechts
	std::atomic<u32> padBtns{0};
	std::atomic<u32> padAxes{0x80808080u}; // lx | ly<<8 | rx<<16 | ry<<24
	std::atomic<bool> padOn{false};
	std::atomic<u32> rumble{0}; // lo = kleiner Motor, hi = grosser Motor (0..65535)
	Atom wmDelete = None;
	bool fullscreen = false;
	SDL_Gamepad* gp = nullptr;
	bool sdlOk = false;

	void key(KeySym ks, bool down)
	{
		u32 b = 0, st = 0;
		switch (ks)
		{
			case XK_Up: b = UP; break;
			case XK_Down: b = DOWN; break;
			case XK_Left: b = LEFT; break;
			case XK_Right: b = RIGHT; break;
			case XK_w: case XK_W: st = 1; break;
			case XK_s: case XK_S: st = 2; break;
			case XK_a: case XK_A: st = 4; break;
			case XK_d: case XK_D: st = 8; break;
			case XK_i: case XK_I: st = 16; break;
			case XK_k: case XK_K: st = 32; break;
			case XK_j: case XK_J: st = 64; break;
			case XK_l: case XK_L: st = 128; break;
			case XK_x: case XK_X: case XK_space: b = CROSS; break;
			case XK_c: case XK_C: case XK_BackSpace: b = CIRCLE; break;
			case XK_z: case XK_Z: case XK_y: case XK_Y: b = SQUARE; break;
			case XK_v: case XK_V: b = TRI; break;
			case XK_q: case XK_Q: b = L1; break;
			case XK_e: case XK_E: b = R1; break;
			case XK_Shift_L: b = L2; break;
			case XK_Shift_R: case XK_r: case XK_R: b = R2; break;
			case XK_Return: case XK_KP_Enter: b = START; break;
			case XK_Tab: b = SELECT; break;
			case XK_f: case XK_F: b = L3; break;
			case XK_g: case XK_G: b = R3; break;
			default: break;
		}
		if (b) { if (down) keyBtns.fetch_or(b); else keyBtns.fetch_and(~b); }
		if (st) { if (down) keyStick.fetch_or(st); else keyStick.fetch_and(~st); }
	}

	void toggleFullscreen(Display* d, Window w)
	{
		fullscreen = !fullscreen;
		XEvent e{};
		e.xclient.type = ClientMessage;
		e.xclient.window = w;
		e.xclient.message_type = XInternAtom(d, "_NET_WM_STATE", False);
		e.xclient.format = 32;
		e.xclient.data.l[0] = fullscreen ? 1 : 0;
		e.xclient.data.l[1] = static_cast<long>(XInternAtom(d, "_NET_WM_STATE_FULLSCREEN", False));
		e.xclient.data.l[2] = 0;
		e.xclient.data.l[3] = 1;
		XSendEvent(d, DefaultRootWindow(d), False, SubstructureRedirectMask | SubstructureNotifyMask, &e);
		XFlush(d);
	}

	void pollPad()
	{
		if (!sdlOk)
		{
			static bool tried = false;
			if (tried)
				return;
			tried = true;
			SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
			sdlOk = SDL_InitSubSystem(SDL_INIT_GAMEPAD);
			if (!sdlOk)
			{
				Console.Error(fmt::format("[ps2x] Gamepad-Unterstuetzung (SDL) nicht verfuegbar: {}", SDL_GetError()));
				return;
			}
			int n0 = 0;
			if (SDL_JoystickID* ids0 = SDL_GetJoysticks(&n0)) SDL_free(ids0);
			Console.WriteLn(fmt::format("[ps2x] SDL bereit, {} Controller gefunden", n0));
		}
		SDL_PumpEvents();
		SDL_UpdateGamepads();
		if (gp && !SDL_GamepadConnected(gp)) { SDL_CloseGamepad(gp); gp = nullptr; }
		if (!gp)
		{
			int n = 0;
			if (SDL_JoystickID* ids = SDL_GetGamepads(&n))
			{
				if (n > 0)
				{
					gp = SDL_OpenGamepad(ids[0]);
					if (gp) Console.WriteLn(fmt::format("[ps2x] Gamepad: {}", SDL_GetGamepadName(gp) ? SDL_GetGamepadName(gp) : "?"));
				}
				SDL_free(ids);
			}
		}
		if (!gp) { padOn.store(false); padBtns.store(0); padAxes.store(0x80808080u); return; }
		auto btn = [](SDL_GamepadButton b) { return SDL_GetGamepadButton(gp, b); };
		u32 m = 0;
		if (btn(SDL_GAMEPAD_BUTTON_DPAD_UP)) m |= UP;
		if (btn(SDL_GAMEPAD_BUTTON_DPAD_DOWN)) m |= DOWN;
		if (btn(SDL_GAMEPAD_BUTTON_DPAD_LEFT)) m |= LEFT;
		if (btn(SDL_GAMEPAD_BUTTON_DPAD_RIGHT)) m |= RIGHT;
		if (btn(SDL_GAMEPAD_BUTTON_SOUTH)) m |= CROSS;
		if (btn(SDL_GAMEPAD_BUTTON_EAST)) m |= CIRCLE;
		if (btn(SDL_GAMEPAD_BUTTON_WEST)) m |= SQUARE;
		if (btn(SDL_GAMEPAD_BUTTON_NORTH)) m |= TRI;
		if (btn(SDL_GAMEPAD_BUTTON_LEFT_SHOULDER)) m |= L1;
		if (btn(SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER)) m |= R1;
		if (btn(SDL_GAMEPAD_BUTTON_START)) m |= START;
		if (btn(SDL_GAMEPAD_BUTTON_BACK)) m |= SELECT;
		if (btn(SDL_GAMEPAD_BUTTON_LEFT_STICK)) m |= L3;
		if (btn(SDL_GAMEPAD_BUTTON_RIGHT_STICK)) m |= R3;
		if (SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFT_TRIGGER) > 12000) m |= L2;
		if (SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER) > 12000) m |= R2;
		auto ax = [](SDL_GamepadAxis a) -> u32 { const int v = SDL_GetGamepadAxis(gp, a); return static_cast<u32>((v + 32768) >> 8) & 0xFFu; };
		{
			// Menue: Guide-Taste oder Select+Start; im Menue steuert das Pad die Navigation
			static bool s_prevToggle = false;
			const bool tog = btn(SDL_GAMEPAD_BUTTON_GUIDE) || (btn(SDL_GAMEPAD_BUTTON_BACK) && btn(SDL_GAMEPAD_BUTTON_START));
			if (tog && !s_prevToggle)
				ps2xmenu::toggle();
			s_prevToggle = tog;
			if (ps2xmenu::open.load())
			{
				const int lx = SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFTX), ly = SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFTY);
				u32 nb = 0;
				if ((m & UP) || ly < -20000) nb |= 1;
				if ((m & DOWN) || ly > 20000) nb |= 2;
				if ((m & LEFT) || lx < -20000) nb |= 4;
				if ((m & RIGHT) || lx > 20000) nb |= 8;
				if (m & CROSS) nb |= 16;
				if (m & CIRCLE) nb |= 32;
				static u32 s_prevPadNav = 0;
				const u32 chg = nb ^ s_prevPadNav;
				if (chg) { ps2xmenu::nav.fetch_or(nb & chg); ps2xmenu::nav.fetch_and(~(s_prevPadNav & chg & ~nb)); }
				s_prevPadNav = nb;
				m = 0;
			}
			// Vibration (vom Spiel ueber ps2xgs_rumble)
			static u32 s_lastRumble = 0;
			const u32 r = rumble.load();
			bool on; int pct;
			{ std::lock_guard<std::mutex> lk(ps2xmenu::m); on = ps2xmenu::cfg.rumble; pct = ps2xmenu::cfg.rumblePct; }
			const u32 eff = (on && !ps2xmenu::open.load()) ? r : 0u;
			if (eff != s_lastRumble || eff)
			{
				const u16 lo = static_cast<u16>(std::min(65535u, ((eff & 0xFFFFu) * static_cast<u32>(pct)) / 100u));
				const u16 hi = static_cast<u16>(std::min(65535u, ((eff >> 16) * static_cast<u32>(pct)) / 100u));
				SDL_RumbleGamepad(gp, lo, hi, 100);
				s_lastRumble = eff;
			}
		}
		padBtns.store(m);
		padAxes.store(ax(SDL_GAMEPAD_AXIS_LEFTX) | (ax(SDL_GAMEPAD_AXIS_LEFTY) << 8) | (ax(SDL_GAMEPAD_AXIS_RIGHTX) << 16) | (ax(SDL_GAMEPAD_AXIS_RIGHTY) << 24));
		padOn.store(true);
	}
}

namespace ps2xmenu
{
	// CPU-Thread (Live-Wiedergabe): Einstellungen anwenden; waehrend das Menue offen ist (Spiel pausiert) neu zeichnen
	void cpuHook()
	{
		if (applyPending.exchange(false))
		{
			toSettings(s_settings_interface);
			VMManager::ApplySettings();
		}
		if (fsPending.exchange(false) && s_display && s_window != None)
		{
			bool want;
			{ std::lock_guard<std::mutex> lk(m); want = cfg.fullscreen; }
			if (want != ps2xin::fullscreen)
				ps2xin::toggleFullscreen(s_display, s_window);
		}
		{
			// Ton: Lautstaerke nachfuehren, im Menue anhalten
			static int s_vol = -1;
			static bool s_paused = false;
			int vol;
			{ std::lock_guard<std::mutex> lk(m); vol = cfg.volume; }
			if (vol != s_vol && VMManager::HasValidVM()) { SPU2::SetOutputVolume(static_cast<u32>(vol)); s_vol = vol; }
			const bool o = open.load();
			if (o != s_paused && VMManager::HasValidVM()) { SPU2::SetOutputPaused(o); s_paused = o; }
		}
		if (open.load())
		{
			static auto s_last = std::chrono::steady_clock::now();
			const auto now = std::chrono::steady_clock::now();
			if (now - s_last > std::chrono::milliseconds(16))
			{
				s_last = now;
				MTGS::PresentCurrentFrame();
			}
		}
	}
}

bool GSRunner::CreatePlatformWindow()
{
	pxAssertRel(!s_display && s_window == None, "Tried to create window when there already was one!");

	XInitThreads();
	s_display = XOpenDisplay(nullptr);
	if (!s_display)
	{
		Console.Error("Failed to open X11 display");
		return false;
	}

	int screen = DefaultScreen(s_display);
	Window root = RootWindow(s_display, screen);

	s_window = XCreateSimpleWindow(s_display, root, 0, 0, WINDOW_WIDTH, WINDOW_HEIGHT, 1,
		BlackPixel(s_display, screen), WhitePixel(s_display, screen));

	if (s_window == None)
	{
		Console.Error("Failed to create X11 window");
		XCloseDisplay(s_display);
		s_display = nullptr;
		return false;
	}

	XStoreName(s_display, s_window, "Shadow of the Colossus (SotC Recomp)");
	XSelectInput(s_display, s_window, StructureNotifyMask | KeyPressMask | KeyReleaseMask | FocusChangeMask |
		ButtonPressMask | ButtonReleaseMask | PointerMotionMask);
	XkbSetDetectableAutoRepeat(s_display, True, nullptr); // gehaltene Tasten ohne Los-/Druck-Flackern
	ps2xin::wmDelete = XInternAtom(s_display, "WM_DELETE_WINDOW", False);
	XSetWMProtocols(s_display, s_window, &ps2xin::wmDelete, 1);
	{
		XClassHint* ch = XAllocClassHint();
		if (ch) { ch->res_name = const_cast<char*>("sotc-recomp"); ch->res_class = const_cast<char*>("SotC Recomp"); XSetClassHint(s_display, s_window, ch); XFree(ch); }
	}
	if (const char* fs = std::getenv("PS2XGS_FULLSCREEN"); fs && *fs == '1')
		ps2xin::fullscreen = true; // wird nach dem Anzeigen gesetzt
	{
		// Ohne WM_HINTS input=True geben manche Fenstermanager (KWin unter XWayland) dem Fenster keinen Tastaturfokus
		if (XWMHints* wh = XAllocWMHints())
		{
			wh->flags = InputHint | StateHint;
			wh->input = True;
			wh->initial_state = NormalState;
			XSetWMHints(s_display, s_window, wh);
			XFree(wh);
		}
	}
	XMapRaised(s_display, s_window);

	s_wi.type = WindowInfo::Type::X11;
	s_wi.display_connection = s_display;
	s_wi.window_handle = reinterpret_cast<void*>(s_window);
	s_wi.surface_width = WINDOW_WIDTH;
	s_wi.surface_height = WINDOW_HEIGHT;
	s_wi.surface_scale = 1.0f;

	XFlush(s_display);
	PumpPlatformMessages();
	if (ps2xin::fullscreen) { ps2xin::fullscreen = false; ps2xin::toggleFullscreen(s_display, s_window); }
	return true;
}

void GSRunner::DestroyPlatformWindow()
{
	if (s_display && s_window != None)
	{
		XDestroyWindow(s_display, s_window);
		s_window = None;
	}

	if (s_display)
	{
		XCloseDisplay(s_display);
		s_display = nullptr;
	}
}

std::optional<WindowInfo> GSRunner::GetPlatformWindowInfo()
{
	WindowInfo wi;
	if (s_display && s_window != None)
		wi = s_wi;
	else
		wi.type = WindowInfo::Type::Surfaceless;
	return wi;
}

void GSRunner::PumpPlatformMessages(bool forever)
{
	if (!s_display)
		return;

	do
	{
		while (XPending(s_display) > 0)
		{
			XEvent event;
			XNextEvent(s_display, &event);

			switch (event.type)
			{
				case ConfigureNotify:
				{
					const XConfigureEvent& configure = event.xconfigure;
					s_wi.surface_width = static_cast<u32>(configure.width);
					s_wi.surface_height = static_cast<u32>(configure.height);
					break;
				}
				case DestroyNotify:
					std::_Exit(0);
				case MapNotify:
					XSetInputFocus(s_display, s_window, RevertToParent, CurrentTime);
					break;
				case FocusIn:
					if (std::getenv("PS2X_KEYLOG"))
						std::fprintf(stderr, "[ps2x] Fokus erhalten\n");
					break;
				case KeyPress:
				case KeyRelease:
				{
					const KeySym ks = XLookupKeysym(&event.xkey, 0);
					const bool down = event.type == KeyPress;
					if (down)
					{
						static int s_keys = 0;
						if (s_keys++ == 0) Console.WriteLn("[ps2x] Tastatur aktiv");
						if (std::getenv("PS2X_KEYLOG")) std::fprintf(stderr, "[ps2x] Taste %lx\n", static_cast<unsigned long>(ks));
					}
					if (down && (ks == XK_F11 || (ks == XK_Return && (event.xkey.state & Mod1Mask))))
					{
						ps2xin::toggleFullscreen(s_display, s_window);
						std::lock_guard<std::mutex> lk(ps2xmenu::m);
						ps2xmenu::cfg.fullscreen = ps2xin::fullscreen;
					}
					else if (down && (ks == XK_Escape || ks == XK_F1))
						ps2xmenu::toggle();
					else if (ps2xmenu::open.load())
					{
						u32 bit = 0;
						switch (ks)
						{
							case XK_Up: case XK_w: bit = 1; break;
							case XK_Down: case XK_s: bit = 2; break;
							case XK_Left: case XK_a: bit = 4; break;
							case XK_Right: case XK_d: bit = 8; break;
							case XK_Return: case XK_KP_Enter: case XK_space: case XK_x: bit = 16; break;
							case XK_BackSpace: case XK_c: bit = 32; break;
							default: break;
						}
						if (bit) { if (down) ps2xmenu::nav.fetch_or(bit); else ps2xmenu::nav.fetch_and(~bit); }
					}
					else
						ps2xin::key(ks, down);
					break;
				}
				case MotionNotify:
					ps2xmenu::mouseX.store(event.xmotion.x);
					ps2xmenu::mouseY.store(event.xmotion.y);
					break;
				case ButtonPress:
				case ButtonRelease:
				{
					const unsigned b = event.xbutton.button;
					const bool down = event.type == ButtonPress;
					if (down) XSetInputFocus(s_display, s_window, RevertToParent, CurrentTime);
					if (b >= 1 && b <= 3)
					{
						const u32 bit = (b == 1) ? 1u : (b == 3) ? 2u : 4u;
						if (down) ps2xmenu::mouseBtn.fetch_or(bit); else ps2xmenu::mouseBtn.fetch_and(~bit);
					}
					else if (down && (b == 4 || b == 5))
						ps2xmenu::wheel.fetch_add(b == 4 ? 1 : -1);
					break;
				}
				case FocusOut:
					if (std::getenv("PS2X_KEYLOG"))
						std::fprintf(stderr, "[ps2x] Fokus verloren\n");
					ps2xin::keyBtns.store(0);
					ps2xin::keyStick.store(0);
					break;
				case ClientMessage:
					if (static_cast<Atom>(event.xclient.data.l[0]) == ps2xin::wmDelete)
					{
						Console.WriteLn("[ps2x] Fenster geschlossen -> Spiel beenden");
						std::fflush(nullptr);
						std::_Exit(0); // Speicherkarte wird beim Speichern sofort geschrieben
					}
					break;
				default:
					break;
			}
		}

		if (s_shutdown_requested.load())
			return;

		if (forever)
		{
			static u32 s_tick = 0;
			if ((++s_tick & 3u) == 0u)
				ps2xin::pollPad(); // Gamepad ~250 Hz
			std::this_thread::sleep_for(std::chrono::milliseconds(1));
		}
	} while (forever && !s_shutdown_requested.load());
}

void GSRunner::StopPlatformMessagePump()
{
	s_shutdown_requested.store(true);
}
#endif // _WIN32 / __APPLE__


//////////////////////////////////////////////////////////////////////////
// ps2x API (einzige exportierte Symbole der Bibliothek)
//////////////////////////////////////////////////////////////////////////
#define PS2XGS_API extern "C" __attribute__((visibility("default")))

static std::thread s_ps2x_cpu_thread;
static std::thread s_ps2x_pump_thread;
static std::atomic<int> s_ps2x_ret{0};
static VMBootParameters s_ps2x_params;

PS2XGS_API int ps2xgs_start(const char* live_path, int renderer, float upscale, int widescreen)
{
	CrashHandler::Install(); // Absturz-Aufrufstapel ins Log (libbacktrace)
	GSRunner::InitializeConsole();
	if (!GSRunner::InitializeConfig())
	{
		Console.Error("ps2xgs: Konfiguration fehlgeschlagen");
		return 0;
	}
	// PS2XGS_SURFACELESS=1 (Testlaeufe): ohne sichtbares Fenster rendern, Screenshots gehen trotzdem
	const char *surfaceless = std::getenv("PS2XGS_SURFACELESS");
	ps2xmenu::load(upscale);
	{
		std::lock_guard<std::mutex> lk(ps2xmenu::m);
		if (ps2xmenu::cfg.fullscreen)
			setenv("PS2XGS_FULLSCREEN", "1", 0);
	}
	if (!(surfaceless && *surfaceless && *surfaceless != '0') && !GSRunner::CreatePlatformWindow())
	{
		Console.Error("ps2xgs: Fenster fehlgeschlagen");
		return 0;
	}
	GSRunner::SettingsOverride();
	// Der Port taktet selbst (VBlank 50 Hz) -> kein eigener Limiter, kein VSync-Warten im GS (V-Sync optional im Menue)
	s_settings_interface.SetBoolValue("EmuCore/GS", "FrameLimitEnable", false);
	s_settings_interface.SetIntValue("EmuCore/GS", "Renderer", renderer);
	s_settings_interface.SetFloatValue("EmuCore/GS", "upscale_multiplier", upscale);
	if (widescreen)
		s_settings_interface.SetStringValue("EmuCore/GS", "AspectRatio", "16:9");
	s_settings_interface.SetBoolValue("EmuCore/GS", "OsdShowGSStats", false);
	s_settings_interface.SetBoolValue("Logging", "EnableVerbose", false);
	// SotC-Korrekturen aus PCSX2s GameIndex.yaml (SCES-53326). Ohne Disc-Start greift die Spieldatenbank nicht,
	// daher hier fest: Wasserfarbe (Blending 4), Raender/Versatz (Half-Pixel 5), Nachbearbeitung/DOF (Native Scaling 3)
	s_settings_interface.SetBoolValue("EmuCore/GS", "UserHacks", true);
	s_settings_interface.SetIntValue("EmuCore/GS", "accurate_blending_unit", 4);
	s_settings_interface.SetIntValue("EmuCore/GS", "UserHacks_HalfPixelOffset", 5);
	s_settings_interface.SetIntValue("EmuCore/GS", "UserHacks_native_scaling", 3);
	// Spielmenue: gespeicherte Einstellungen uebernehmen (Testlaeufe ohne Fenster: unveraendert, wie bisher)
	const bool cfgTest = std::getenv("PS2XGS_CFG") != nullptr; // Testlauf mit festen Menue-Einstellungen
	if (cfgTest)
		s_settings_interface.SetIntValue("EmuCore/GS", "ScreenshotSize", 1); // Screenshot in interner Aufloesung
	if (!(surfaceless && *surfaceless && *surfaceless != '0') || cfgTest)
	{
		// Ton: SPU2-Ausgabe ueber Cubeb (PulseAudio/PipeWire), 48 kHz, Zeitdehnung gegen Knacken
		if (!cfgTest)
		{
			s_settings_interface.SetStringValue("SPU2/Output", "Backend", "Cubeb");
			s_settings_interface.SetIntValue("SPU2/Output", "BufferMS", 50);
			s_settings_interface.SetIntValue("SPU2/Output", "OutputLatencyMS", 20);
		}
		ps2xmenu::toSettings(s_settings_interface);
		if (widescreen)
			s_settings_interface.SetStringValue("EmuCore/GS", "AspectRatio", "16:9");
		extern void (*g_ps2xMenuDraw)();
		extern void (*g_ps2xCpuHook)();
		g_ps2xMenuDraw = &ps2xmenu::draw;
		ps2xmenu::active.store(true);
		g_ps2xCpuHook = &ps2xmenu::cpuHook;
		// Startmenue: Einstellungen vor dem Spielen (abschaltbar im Menue)
		bool sm;
		{ std::lock_guard<std::mutex> lk(ps2xmenu::m); sm = ps2xmenu::cfg.startMenu; }
		if (sm && !cfgTest)
			ps2xmenu::open.store(true);
	}
	s_settings_interface.SetBoolValue("EmuCore", "WarnAboutUnsafeSettings", false); // keine PCSX2-Warnhinweise im Bild
	// Bildausgabe: SotC zeichnet volle Bilder, das PCRTC zeigt sie als Halbbilder (SMODE2 INT=1, FFMD=0). PCSX2s
	// automatisches Deinterlacing (adaptiv) laesst bewegte Figuren auf dem Bildschirm zittern/"wobbeln", in Screenshots
	// nicht sichtbar. Daher: Deinterlacing aus = das volle Bild direkt zeigen. PS2X_DEINTERLACE=<0..9> zum Testen.
	{
		int di = 1; // GSInterlaceMode::Off
		if (const char* e = std::getenv("PS2X_DEINTERLACE"); e && *e) di = std::atoi(e);
		s_settings_interface.SetIntValue("EmuCore/GS", "deinterlace_mode", di);
	}
	{
		// HD-Texturen (Schrift, Logos, Menues): eigener Ordner, PCSX2-Austauschmechanismus
		std::string tex;
		if (const char* t = std::getenv("PS2XGS_TEXDIR"); t && *t) tex = t;
		else tex = ps2xmenu::dir() + "/texturen";
		s_settings_interface.SetStringValue("Folders", "Textures", tex.c_str());
		EmuFolders::Textures = tex; // wurde schon beim Start aus der PCSX2-Konfiguration gesetzt
		s_settings_interface.SetBoolValue("EmuCore/GS", "LoadTextureReplacements", true);
		s_settings_interface.SetBoolValue("EmuCore/GS", "LoadTextureReplacementsAsync", false); // kein Aufploppen
		s_settings_interface.SetBoolValue("EmuCore/GS", "PrecacheTextureReplacements", true);
		const char* dump = std::getenv("PS2XGS_TEXDUMP");
		const bool d = dump && *dump == '1';
		s_settings_interface.SetBoolValue("EmuCore/GS", "DumpReplaceableTextures", d);
		s_settings_interface.SetBoolValue("EmuCore/GS", "DumpReplaceableMipmaps", false);
		s_settings_interface.SetBoolValue("EmuCore/GS", "DumpDirectTextures", true);
		s_settings_interface.SetBoolValue("EmuCore/GS", "DumpPaletteTextures", true);
		s_settings_interface.SetBoolValue("EmuCore/GS", "DumpTexturesWithFMVActive", false);
		Console.WriteLn(fmt::format("[ps2xgs] HD-Texturen: {}{}", tex, d ? " (Auslesen aktiv)" : ""));
	}

	s_ps2x_params.filename = live_path; // endet auf ps2x_live.gs -> GSDumpReplayer Live-Modus
	s_ps2x_cpu_thread = std::thread(CPUThreadMain, &s_ps2x_params, &s_ps2x_ret);
	s_ps2x_pump_thread = std::thread([] { GSRunner::PumpPlatformMessages(true); });
	// Warten, bis die VM (inkl. SPU2) bereit ist: der Port schreibt danach sofort SPU2-Register (Sound-Treiber-Start)
	for (int i = 0; i < 1000 && !VMManager::HasValidVM(); ++i)
		std::this_thread::sleep_for(std::chrono::milliseconds(10));
	return 1;
}

PS2XGS_API void ps2xgs_transfer(int path, const unsigned char* data, unsigned int size)
{
	GSDumpReplayer::LivePush(0, static_cast<u8>(path), data, size);
}

// Gesammelte Grafikpakete des aufrufenden Threads abgeben (Port: nach jedem VU1-Auftrag und vor jeder Uebergabe)
PS2XGS_API void ps2xgs_flush()
{
	GSDumpReplayer::LiveFlush();
}

PS2XGS_API void ps2xgs_vsync(const unsigned char* regs0x2000, int field)
{
	GSDumpReplayer::LivePush(3, 0, regs0x2000, 0x2000);
	GSDumpReplayer::LivePush(1, static_cast<u8>(field), nullptr, 0);
}

PS2XGS_API unsigned int ps2xgs_queue_size()
{
	return static_cast<unsigned int>(GSDumpReplayer::LiveQueueSize());
}

PS2XGS_API void ps2xgs_screenshot(const char* path)
{
	std::string p(path);
	MTGS::RunOnGSThread([p]() { GSQueueSnapshot(p); });
}

// Eingabe fuer den Port: gedrueckte PS2-Tasten (1 = gedrueckt) und Sticks (lx, ly, rx, ry; 0x80 = Mitte).
// Gamepad und Tastatur (im Spielfenster) werden zusammengefuehrt. Rueckgabe 1, wenn ein Fenster/Gamepad da ist.
PS2XGS_API int ps2xgs_pad(unsigned short* pressed, unsigned char* axes4)
{
	u32 b = ps2xin::keyBtns.load() | ps2xin::padBtns.load();
	u32 ax = ps2xin::padAxes.load();
	if (ps2xmenu::open.load() || ps2xmenu::latch.load())
	{
		// Menue offen bzw. gerade geschlossen: Spiel bekommt nichts, bis alles losgelassen ist
		if (!ps2xmenu::open.load() && b == 0 && (ps2xin::keyStick.load() == 0))
			ps2xmenu::latch.store(false);
		if (pressed) *pressed = 0;
		if (axes4) std::memset(axes4, 0x80, 4);
		return (s_display != nullptr || ps2xin::padOn.load()) ? 1 : 0;
	}
	unsigned char a[4] = {static_cast<unsigned char>(ax), static_cast<unsigned char>(ax >> 8), static_cast<unsigned char>(ax >> 16), static_cast<unsigned char>(ax >> 24)};
	const u32 ks = ps2xin::keyStick.load();
	if (ks & 1) a[1] = 0x00; if (ks & 2) a[1] = 0xFF; if (ks & 4) a[0] = 0x00; if (ks & 8) a[0] = 0xFF;
	if (ks & 16) a[3] = 0x00; if (ks & 32) a[3] = 0xFF; if (ks & 64) a[2] = 0x00; if (ks & 128) a[2] = 0xFF;
	if (pressed) *pressed = static_cast<unsigned short>(b);
	if (axes4) std::memcpy(axes4, a, 4);
	ps2xosd::btns.store(b); ps2xosd::axes.store(static_cast<u32>(a[0]) | (static_cast<u32>(a[1]) << 8) | (static_cast<u32>(a[2]) << 16) | (static_cast<u32>(a[3]) << 24));
	ps2xosd::keyBtns.store(ps2xin::keyBtns.load());
	{
		// Eingabe-Protokoll (letzter_start.log): bei Aenderung, max. ~10/s -> Diagnose Laufen/Stick
		static u32 s_last = 0xFFFFFFFFu, s_lastAx = 0;
		static auto s_t = std::chrono::steady_clock::now();
		const u32 axq = (a[0] >> 4) | ((a[1] >> 4) << 4) | ((a[2] >> 4) << 8) | ((a[3] >> 4) << 12);
		const auto now = std::chrono::steady_clock::now();
		if ((b != s_last || axq != s_lastAx) && now - s_t > std::chrono::milliseconds(100))
		{
			s_last = b; s_lastAx = axq; s_t = now;
			std::fprintf(stderr, "[Eingabe] tasten=%04x links=%02x/%02x rechts=%02x/%02x pad=%d tast=%02x\n", b, a[0], a[1], a[2], a[3],
				ps2xin::padOn.load() ? 1 : 0, ps2xin::keyStick.load());
		}
	}
	return (s_display != nullptr || ps2xin::padOn.load()) ? 1 : 0;
}

// Breitbild: 0 = Spiel entscheidet, 1 = 16:9 erzwingen, 2 = 4:3 erzwingen
PS2XGS_API int ps2xgs_wide_mode()
{
	if (!ps2xmenu::active.load())
		return 0;
	int a;
	{ std::lock_guard<std::mutex> lk(ps2xmenu::m); a = ps2xmenu::cfg.aspect; }
	return a == 0 ? 1 : a == 2 ? 2 : 0;
}
// Das Spiel meldet seinen Bildmodus -> Ausgabe-Seitenverhaeltnis passend umschalten
PS2XGS_API void ps2xgs_game_wide(int wide)
{
	if (ps2xmenu::gameWide.exchange(wide != 0) != (wide != 0))
		ps2xmenu::applyPending.store(true);
}
// ---- Ton: SPU2-Chip aus PCSX2, angesteuert vom IOP des Ports (Register, DMA, Takt, Interrupts) ----
extern void (*g_ps2xSpuIrqHook)(int); // IopIrq.cpp (globaler Namensraum!)
namespace ps2xspu
{
	std::atomic<u32> irqs{0}; // Bit 0 SPU-IRQ (INUM 9), Bit 1 DMA4 fertig, Bit 2 DMA7 fertig
	std::atomic<u64> nDma{0}, nDmaHw{0}, nIrq[3];
	void irqHook(int which) { irqs.fetch_or(1u << which); nIrq[which].fetch_add(1); }
	bool hooked = false;
	bool ready() { return VMManager::HasValidVM(); }
	void ensure()
	{
		if (!hooked)
		{
			g_ps2xSpuIrqHook = &irqHook;
			hooked = true;
		}
	}
}
PS2XGS_API void ps2xspu_write(unsigned int addr, unsigned short value, unsigned int cycle)
{
	if (!ps2xspu::ready()) return;
	ps2xspu::ensure();
	psxRegs.cycle = cycle;
	SPU2write(addr, value);
	{
		// PS2X_SPULOG: Aktivitaet des Sound-Treibers (Registerschreibungen, Key-On) alle 5 s
		static const bool log = std::getenv("PS2X_SPULOG") != nullptr;
		if (log)
		{
			static u64 n = 0, kon = 0;
			static auto last = std::chrono::steady_clock::now();
			++n;
			const u32 off = addr & 0x3FF;
			if ((off == 0x1A0 || off == 0x1A2) && value) ++kon;
			const auto now = std::chrono::steady_clock::now();
			if (now - last > std::chrono::seconds(5))
			{
				last = now;
				Console.WriteLn(fmt::format("[SPU2] {} Registerschreibungen, {} Key-On, {} DMA ({} KB), IRQ spu/dma4/dma7 {}/{}/{}", n, kon,
					ps2xspu::nDma.load(), ps2xspu::nDmaHw.load() / 512, ps2xspu::nIrq[0].load(), ps2xspu::nIrq[1].load(), ps2xspu::nIrq[2].load()));
				for (int c = 0; c < 2; ++c)
				{
					const V_Core& k = Cores[c];
					std::string vs;
					for (int v = 0; v < 24; ++v)
					{
						const V_Voice& vo = k.Voices[v];
						if (vo.ADSR.Phase == 0) continue;
						int nz = 0;
						for (u32 i = 0; i < 0x1000; ++i) nz += _spu2mem[(vo.StartA + i) & 0xFFFFF] != 0;
						vs += fmt::format(" v{}[a={:x} nz={} n={:x} p={:x} vol={}/{} env={} g={}/{}]", v, vo.StartA, nz, vo.NextA, vo.Pitch,
							vo.Volume.Left.Value, vo.Volume.Right.Value, vo.ADSR.Value, k.VoiceGates[v].DryL, k.VoiceGates[v].DryR);
					}
					Console.WriteLn(fmt::format("[SPU2] Kern{}: irq={} irqa={:x} master={}/{} inp={}/{} ext={}/{} fx={} admactl={:x} dry snd/inp/ext {}/{} {}/{} {}/{}{}", c,
						k.IRQEnable, k.IRQA, k.MasterVol.Left.Value, k.MasterVol.Right.Value, k.InpVol.Left, k.InpVol.Right, k.ExtVol.Left, k.ExtVol.Right, k.FxEnable,
						k.AutoDMACtrl, k.DryGate.SndL, k.DryGate.SndR, k.DryGate.InpL, k.DryGate.InpR, k.DryGate.ExtL, k.DryGate.ExtR, vs));
				}
			}
		}
	}
}
PS2XGS_API unsigned short ps2xspu_read(unsigned int addr, unsigned int cycle)
{
	if (!ps2xspu::ready()) return 0;
	ps2xspu::ensure();
	psxRegs.cycle = cycle;
	return SPU2read(addr);
}
// DMA: core 0 = DMA4, 1 = DMA7; toSpu = 1 Speicher -> SPU2; halfwords = Anzahl 16-Bit-Woerter
PS2XGS_API void ps2xspu_dma(int core, unsigned short* mem, unsigned int halfwords, int toSpu, unsigned int cycle)
{
	if (!ps2xspu::ready()) { ps2xspu::irqs.fetch_or(core == 0 ? 2u : 4u); return; }
	ps2xspu::ensure();
	ps2xspu::nDma.fetch_add(1); ps2xspu::nDmaHw.fetch_add(halfwords);
	psxRegs.cycle = cycle;
	if (core == 0) HW_DMA4_CHCR |= 0x01000000; else HW_DMA7_CHCR |= 0x01000000;
	{
		static const bool dlog = std::getenv("PS2X_SPUDMALOG") != nullptr;
		static int dn = 0;
		if (dlog && dn < 400)
		{
			++dn;
			int nz = 0;
			for (u32 i = 0; i < halfwords; ++i) nz += mem[i] != 0;
			Console.WriteLn(fmt::format("[SPU2-DMA] kern{} {} tsa={:x} aktiv={:x} hw={} nz={} adma={:x} rest={}", core, toSpu ? "->SPU" : "<-SPU",
				Cores[core].TSA, Cores[core].ActiveTSA, halfwords, nz, Cores[core].AutoDMACtrl, Cores[core].ReadSize));
		}
	}
	if (toSpu)
	{
		if (core == 0) SPU2writeDMA4Mem(mem, halfwords); else SPU2writeDMA7Mem(mem, halfwords);
	}
	else
	{
		if (core == 0) SPU2readDMA4Mem(mem, halfwords); else SPU2readDMA7Mem(mem, halfwords);
	}
}
PS2XGS_API void ps2xspu_async(unsigned int cycle)
{
	if (!ps2xspu::ready()) return;
	ps2xspu::ensure();
	psxRegs.cycle = cycle;
	SPU2async();
}
PS2XGS_API unsigned int ps2xspu_take_irqs()
{
	return ps2xspu::irqs.exchange(0);
}

// Spiel-Optionen fuer den Port (id 0 = Kamera-Unschaerfe). Ohne aktives Menue (Testlaeufe): fallback.
PS2XGS_API int ps2xgs_option(int id, int fallback)
{
	if (!ps2xmenu::active.load())
		return fallback;
	std::lock_guard<std::mutex> lk(ps2xmenu::m);
	switch (id)
	{
		case 0: return ps2xmenu::cfg.camBlur;
		default: return fallback;
	}
}
// Spielmenue offen? Der Port pausiert dann das Spiel (EE) am naechsten VBlank.
PS2XGS_API int ps2xgs_menu_open()
{
	return ps2xmenu::open.load() ? 1 : 0;
}
// Vibration vom Spiel: kleiner Motor (an/aus) und grosser Motor (0..255), wie DualShock 2
PS2XGS_API void ps2xgs_rumble(int small_on, int large)
{
	const u32 lo = small_on ? 0xFFFFu : 0u;
	const u32 hi = static_cast<u32>(std::clamp(large, 0, 255)) * 257u;
	ps2xin::rumble.store(lo | (hi << 16));
}

PS2XGS_API void ps2xgs_stop()
{
	Host::RequestVMShutdown(false, false, false);
	GSRunner::StopPlatformMessagePump();
	if (s_ps2x_cpu_thread.joinable())
		s_ps2x_cpu_thread.join();
	if (s_ps2x_pump_thread.joinable())
		s_ps2x_pump_thread.join();
	GSRunner::DestroyPlatformWindow();
}

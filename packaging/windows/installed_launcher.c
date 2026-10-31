/* Stable per-user entry point; versioned runtimes remain unchanged on upgrade. */
#ifndef UNICODE
#define UNICODE
#endif
#include <windows.h>
#include <stdlib.h>
#include <wchar.h>

static int launch_error(const wchar_t *message)
{
#ifdef HOOK_LAUNCHER
    (void)message;
#else
    MessageBoxW(NULL, message, L"Docking", MB_OK | MB_ICONERROR);
#endif
    return 1;
}

static int start_child(PWSTR arguments, PROCESS_INFORMATION *process)
{
    wchar_t root[32768], ini[32768], version[128], target[32768], owner[128];
    DWORD length = GetModuleFileNameW(NULL, root, 32768);
    if (!length || length >= 32768) return 1;
    wchar_t *separator = wcsrchr(root, L'\\');
    if (!separator) return 1;
    *separator = L'\0';
    if (swprintf(ini, 32768, L"%ls\\installation.ini", root) < 0) return 1;
    GetPrivateProfileStringW(L"Docking", L"Application", L"", owner, 128, ini);
    if (wcscmp(owner, L"org.docking.Docking") ||
        GetPrivateProfileIntW(L"Docking", L"Schema", 0, ini) != 1) return 1;
    if (swprintf(ini, 32768, L"%ls\\current.ini", root) < 0) return 1;
    length = GetPrivateProfileStringW(L"Docking", L"Current", L"", version, 128, ini);
    BOOL valid = length > 0 && length < 127 && version[0] != L'.';
    for (DWORD index = 0; index < length; ++index) {
        wchar_t value = version[index];
        if (!((value >= L'0' && value <= L'9') ||
              (value >= L'a' && value <= L'z') ||
              (value >= L'A' && value <= L'Z') || value == L'.' ||
              value == L'-' || value == L'_')) valid = FALSE;
    }
    if (!valid || version[length - 1] == L'.' || wcsstr(version, L".."))
        return launch_error(L"Docking's installation is incomplete. Reinstall Docking.");
#ifdef HOOK_LAUNCHER
    const wchar_t *suffix = L"runtime\\bin\\python.exe";
    const wchar_t *options = L" -X utf8 -I -m docking.applets.aiusage.hook ";
    DWORD flags = CREATE_NO_WINDOW;
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
#else
    const wchar_t *suffix = L"Docking.exe";
    const wchar_t *options = L" ";
    DWORD flags = 0;
#endif
    if (swprintf(target, 32768, L"%ls\\versions\\%ls\\%ls", root, version, suffix) < 0)
        return 1;
    size_t command_size = wcslen(target) + wcslen(options) + wcslen(arguments) + 4;
    wchar_t *command = calloc(command_size, sizeof(wchar_t));
    if (!command) return 1;
    swprintf(command, command_size, L"\"%ls\"%ls%ls", target, options, arguments);
    STARTUPINFOW startup = {0};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    BOOL started = CreateProcessW(target, command, NULL, NULL, TRUE, flags,
                                 NULL, root, &startup, process);
    free(command);
    if (!started) return launch_error(L"Docking could not start. Reinstall Docking.");
    return 0;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show)
{
    (void)instance;
    (void)previous;
    (void)show;
    /* Setup holds this mutex until it has finished switching/removing versions.
       Launchers hold it only through CreateProcess, so usage hooks can run while
       the dock is open. Restart Manager then sees the already-started children. */
    HANDLE mutex = CreateMutexW(NULL, FALSE, L"Local\\org.docking.Docking.Setup");
    if (!mutex) return 1;
    DWORD acquired = WaitForSingleObject(mutex, 0);
    if (acquired != WAIT_OBJECT_0 && acquired != WAIT_ABANDONED) {
        CloseHandle(mutex);
        return launch_error(L"Docking setup is in progress. Try again when it finishes.");
    }
    PROCESS_INFORMATION process = {0};
    int result = start_child(arguments, &process);
    ReleaseMutex(mutex);
    CloseHandle(mutex);
    if (result) return result;
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD status = 1;
    GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hProcess);
    return (int)status;
}

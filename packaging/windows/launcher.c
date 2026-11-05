/* Launch the bundled interpreter without a console or a machine-wide Python. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show)
{
    (void)instance;
    (void)previous;
    (void)show;
    wchar_t root[32768];
    DWORD length = GetModuleFileNameW(NULL, root, 32768);
    if (!length || length >= 32768) return 1;
    wchar_t *separator = wcsrchr(root, L'\\');
    if (!separator) return 1;
    *separator = L'\0';
    const wchar_t *relative = L"\\runtime\\bin\\pythonw.exe";
    size_t python_size = wcslen(root) + wcslen(relative) + 1;
    wchar_t *python = calloc(python_size, sizeof(wchar_t));
    size_t command_size = python_size + wcslen(arguments) + 64;
    wchar_t *command = calloc(command_size, sizeof(wchar_t));
    if (!python || !command) {
        free(python);
        free(command);
        return 1;
    }
    swprintf(python, python_size, L"%ls%ls", root, relative);
    swprintf(command, command_size, L"\"%ls\" -X utf8 -I -m docking.launcher %ls", python, arguments);
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    STARTUPINFOW startup = {0};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION process = {0};
    BOOL started = CreateProcessW(python, command, NULL, NULL, TRUE, 0,
                                 NULL, root, &startup, &process);
    free(command);
    free(python);
    if (!started) {
        MessageBoxW(NULL, L"Docking could not start its bundled Python runtime. "
                    L"Extract the complete application before launching Docking.exe.",
                    L"Docking", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD status = 1;
    GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hProcess);
    return (int)status;
}

/* Native executable oracle for installed launchers and Restart Manager ownership. */
#ifndef UNICODE
#define UNICODE
#endif
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include <stdio.h>
#include <wchar.h>

int wmain(int argc, wchar_t **argv)
{
    _setmode(_fileno(stdin), _O_U8TEXT);
    _setmode(_fileno(stdout), _O_U8TEXT);
    wchar_t path[32768], value[256], input[1024];
    if (!GetModuleFileNameW(NULL, path, 32768)) return 1;
    wprintf(L"EXE:%ls\n", path);
    for (int index = 1; index < argc; ++index) wprintf(L"ARG:%ls\n", argv[index]);
    if (GetEnvironmentVariableW(L"PYTHONUTF8", value, 256)) wprintf(L"UTF8:%ls\n", value);
    wprintf(L"READY\n");
    fflush(stdout);
    if (!fgetws(input, 1024, stdin)) return 1;
    wprintf(L"INPUT:%ls", input);
    fprintf(stderr, "child stderr\n");
    return 23;
}

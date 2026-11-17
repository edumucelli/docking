/* Compare the public SDK call with the Python boundary on owned test files. */
#define _WIN32_WINNT 0x0602
#include <windows.h>
#include <shlobj.h>
#include <stddef.h>
#include <stdio.h>

int wmain(int argc, wchar_t **argv) {
    if (argc != 2) return 2;
    HRESULT initialized = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    OPENASINFO info = {argv[1], NULL, OAIF_EXEC};
    HRESULT result = SHOpenWithDialog(NULL, &info);
    printf("{\"size\":%zu,\"file_offset\":%zu,\"class_offset\":%zu,"
           "\"flags_offset\":%zu,\"wchar_size\":%zu,\"initialized\":%ld,"
           "\"result\":%ld}\n", sizeof(info), offsetof(OPENASINFO, pcszFile),
           offsetof(OPENASINFO, pcszClass), offsetof(OPENASINFO, oaifInFlags),
           sizeof(wchar_t), (long)initialized, (long)result);
    if (SUCCEEDED(initialized)) CoUninitialize();
    return SUCCEEDED(result) || result == HRESULT_FROM_WIN32(ERROR_CANCELLED) ? 0 : 1;
}

/* User-level installation checks and manifest-only cleanup; never stop processes. */
#ifndef UNICODE
#define UNICODE
#endif
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <restartmanager.h>
#include <shlobj.h>
#include <knownfolders.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define PATH_CAP 32768
#define MANIFEST_HEADER L"Docking-Install-Manifest-1\r\n"

static BOOL extended_path(const wchar_t *path, wchar_t *target)
{
    int result;
    if (!wcsncmp(path, L"\\\\?\\", 4)) result = swprintf(target, PATH_CAP, L"%ls", path);
    else if (!wcsncmp(path, L"\\\\", 2)) result = swprintf(target, PATH_CAP, L"\\\\?\\UNC\\%ls", path + 2);
    else result = swprintf(target, PATH_CAP, L"\\\\?\\%ls", path);
    if (result >= 0) return TRUE;
    SetLastError(ERROR_FILENAME_EXCED_RANGE);
    return FALSE;
}

static DWORD file_attributes(const wchar_t *path)
{
    wchar_t target[PATH_CAP];
    return extended_path(path, target) ? GetFileAttributesW(target) : INVALID_FILE_ATTRIBUTES;
}

static HANDLE find_files(const wchar_t *path, WIN32_FIND_DATAW *entry)
{
    wchar_t target[PATH_CAP];
    return extended_path(path, target) ? FindFirstFileW(target, entry) : INVALID_HANDLE_VALUE;
}

static BOOL remove_directory(const wchar_t *path)
{
    wchar_t target[PATH_CAP];
    return extended_path(path, target) && RemoveDirectoryW(target);
}

static int supported_platform(void)
{
    typedef LONG (WINAPI *VersionFunction)(OSVERSIONINFOW *);
    typedef BOOL (WINAPI *MachineFunction)(HANDLE, USHORT *, USHORT *);
    FARPROC address = GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion");
    VersionFunction version_function = NULL;
    memcpy(&version_function, &address, sizeof(version_function));
    address = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "IsWow64Process2");
    MachineFunction machine_function = NULL;
    memcpy(&machine_function, &address, sizeof(machine_function));
    OSVERSIONINFOW version = {0};
    version.dwOSVersionInfoSize = sizeof(version);
    USHORT process_machine = 0, native_machine = 0;
    if (!version_function || !machine_function || version_function(&version) != 0 ||
        !machine_function(GetCurrentProcess(), &process_machine, &native_machine)) return 11;
    return version.dwMajorVersion >= 10 && version.dwBuildNumber >= 22000 &&
           native_machine == IMAGE_FILE_MACHINE_AMD64 ? 0 : 11;
}

static BOOL ordinary_directory(const wchar_t *path)
{
    DWORD attributes = file_attributes(path);
    return attributes != INVALID_FILE_ATTRIBUTES &&
           (attributes & FILE_ATTRIBUTE_DIRECTORY) &&
           !(attributes & FILE_ATTRIBUTE_REPARSE_POINT);
}

static BOOL safe_relative(const wchar_t *relative)
{
    if (!*relative || relative[0] == L'\\' || wcschr(relative, L'/') ||
        wcschr(relative, L':')) return FALSE;
    for (const wchar_t *value = relative; *value; ++value)
        if (*value < 32 || wcschr(L"<>\"|?*", *value)) return FALSE;
    const wchar_t *part = relative;
    while (*part) {
        const wchar_t *end = wcschr(part, L'\\');
        size_t length = end ? (size_t)(end - part) : wcslen(part);
        if (!length || (length == 1 && part[0] == L'.') ||
            (length == 2 && part[0] == L'.' && part[1] == L'.') ||
            part[length - 1] == L'.' || part[length - 1] == L' ') return FALSE;
        size_t stem = 0;
        while (stem < length && part[stem] != L'.') ++stem;
        if ((stem == 3 && (!_wcsnicmp(part, L"CON", 3) ||
                          !_wcsnicmp(part, L"PRN", 3) ||
                          !_wcsnicmp(part, L"AUX", 3) ||
                          !_wcsnicmp(part, L"NUL", 3))) ||
            (stem == 4 && part[3] >= L'1' && part[3] <= L'9' &&
             (!_wcsnicmp(part, L"COM", 3) || !_wcsnicmp(part, L"LPT", 3)))) return FALSE;
        if (!end) break;
        part = end + 1;
        if (!*part) return FALSE;
    }
    return TRUE;
}

static BOOL safe_target(const wchar_t *root, const wchar_t *relative, wchar_t *target)
{
    if (!safe_relative(relative) || !ordinary_directory(root) ||
        swprintf(target, PATH_CAP, L"%ls\\%ls", root, relative) < 0) return FALSE;
    size_t root_length = wcslen(root);
    for (wchar_t *end = target + root_length + 1; ; ++end) {
        if (*end != L'\\' && *end != L'\0') continue;
        wchar_t saved = *end;
        *end = L'\0';
        DWORD attributes = file_attributes(target);
        DWORD error = attributes == INVALID_FILE_ATTRIBUTES ? GetLastError() : 0;
        *end = saved;
        if (attributes == INVALID_FILE_ATTRIBUTES && error != ERROR_FILE_NOT_FOUND &&
            error != ERROR_PATH_NOT_FOUND) return FALSE;
        if (attributes != INVALID_FILE_ATTRIBUTES &&
            (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) return FALSE;
        if (!saved) return TRUE;
    }
}

static int register_resource(DWORD session, const wchar_t *path)
{
    DWORD attributes = file_attributes(path);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        DWORD error = GetLastError();
        return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND ? 0 : 12;
    }
    if (attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
    LPCWSTR resources[] = {path};
    return RmRegisterResources(session, 1, resources, 0, NULL, 0, NULL) == ERROR_SUCCESS ? 0 : 12;
}

static int validate_installation(const wchar_t *root)
{
    PWSTR local = NULL;
    if (SHGetKnownFolderPath(&FOLDERID_LocalAppData, 0, NULL, &local) != S_OK) return 12;
    size_t length = wcslen(local);
    BOOL below_local = !_wcsnicmp(root, local, length) && root[length] == L'\\';
    wchar_t target[PATH_CAP];
    BOOL valid = below_local && !_wcsnicmp(root + length + 1, L"Programs\\", 9) &&
                 safe_target(local, root + length + 1, target);
    CoTaskMemFree(local);
    if (!valid) return 12;
    DWORD attributes = file_attributes(root);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        DWORD error = GetLastError();
        return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND ? 0 : 12;
    }
    if (!ordinary_directory(root) || !safe_target(root, L"installation.ini", target)) return 12;
    wchar_t owner[128];
    GetPrivateProfileStringW(L"Docking", L"Application", L"", owner, 128, target);
    if (!wcscmp(owner, L"org.docking.Docking") &&
        GetPrivateProfileIntW(L"Docking", L"Schema", 0, target) == 1) {
        const wchar_t *owned[] = {L"versions", L"current.ini", L"Docking.exe", L"docking-hook.exe", L"Uninstall.exe"};
        for (unsigned int index = 0; index < 5; ++index)
            if (!safe_target(root, owned[index], target)) return 12;
        return 0;
    }
    /* An empty directory can be claimed; never overwrite an unrelated folder. */
    if (swprintf(target, PATH_CAP, L"%ls\\*", root) < 0) return 12;
    WIN32_FIND_DATAW entry;
    HANDLE iterator = find_files(target, &entry);
    if (iterator == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_FILE_NOT_FOUND ? 0 : 12;
    int result = 0;
    do {
        if (wcscmp(entry.cFileName, L".") && wcscmp(entry.cFileName, L"..")) result = 12;
    } while (!result && FindNextFileW(iterator, &entry));
    if (!result && GetLastError() != ERROR_NO_MORE_FILES) result = 12;
    FindClose(iterator);
    return result;
}

static int installation_in_use(const wchar_t *root)
{
    DWORD attributes = file_attributes(root);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        DWORD error = GetLastError();
        return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND ? 0 : 12;
    }
    if (!ordinary_directory(root)) return 12;
    DWORD session = 0;
    WCHAR key[CCH_RM_SESSION_KEY + 1] = {0};
    if (RmStartSession(&session, 0, key) != ERROR_SUCCESS) return 12;
    int result = 0;
    wchar_t path[PATH_CAP];
    if (swprintf(path, PATH_CAP, L"%ls\\Docking.exe", root) < 0) result = 12;
    else result = register_resource(session, path);
    if (!result) {
        if (swprintf(path, PATH_CAP, L"%ls\\docking-hook.exe", root) < 0) result = 12;
        else result = register_resource(session, path);
    }
    if (!result && !safe_target(root, L"versions", path)) result = 12;
    if (!result && swprintf(path, PATH_CAP, L"%ls\\versions\\*", root) >= 0) {
        WIN32_FIND_DATAW entry;
        HANDLE iterator = find_files(path, &entry);
        if (iterator != INVALID_HANDLE_VALUE) {
            do {
                if (!(entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ||
                    (entry.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
                    !safe_relative(entry.cFileName)) continue;
                const wchar_t *executables[] = {
                    L"Docking.exe", L"runtime\\bin\\python.exe", L"runtime\\bin\\pythonw.exe"
                };
                for (unsigned int index = 0; index < 3 && !result; ++index) {
                    wchar_t relative[1024];
                    if (swprintf(relative, 1024, L"versions\\%ls\\%ls",
                                 entry.cFileName, executables[index]) < 0 ||
                        !safe_target(root, relative, path)) result = 12;
                    else result = register_resource(session, path);
                }
            } while (!result && FindNextFileW(iterator, &entry));
            if (!result && GetLastError() != ERROR_NO_MORE_FILES) result = 12;
            FindClose(iterator);
        } else {
            DWORD error = GetLastError();
            if (error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND) result = 12;
        }
    }
    if (!result) {
        UINT needed = 0, count = 0;
        DWORD reboot = 0;
        DWORD status = RmGetList(session, &needed, &count, NULL, &reboot);
        if (status == ERROR_MORE_DATA || needed) result = 10;
        else if (status != ERROR_SUCCESS) result = 12;
    }
    RmEndSession(session);
    return result;
}

static BOOL delete_owned_file(const wchar_t *path)
{
    wchar_t target[PATH_CAP];
    if (!extended_path(path, target)) return FALSE;
    if (DeleteFileW(target)) return TRUE;
    DWORD error = GetLastError();
    return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND;
}

static BOOL remove_bytecode(const wchar_t *source)
{
    size_t length = wcslen(source);
    if (length < 3 || wcscmp(source + length - 3, L".py")) return TRUE;
    wchar_t folder[PATH_CAP], pattern[PATH_CAP], target[PATH_CAP];
    if (length >= PATH_CAP) return FALSE;
    wcscpy(folder, source);
    wchar_t *name = wcsrchr(folder, L'\\');
    if (!name) return FALSE;
    *name++ = L'\0';
    name[wcslen(name) - 3] = L'\0';
    if (!*name) return TRUE;
    if (!safe_target(folder, L"__pycache__", target)) return FALSE;
    if (swprintf(pattern, PATH_CAP, L"%ls\\__pycache__\\%ls.*.pyc", folder, name) < 0) return FALSE;
    BOOL success = TRUE;
    WIN32_FIND_DATAW entry;
    HANDLE iterator = find_files(pattern, &entry);
    if (iterator != INVALID_HANDLE_VALUE) {
        do {
            if (entry.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) continue;
            if (swprintf(target, PATH_CAP, L"__pycache__\\%ls", entry.cFileName) >= 0 &&
                safe_target(folder, target, pattern) && !delete_owned_file(pattern)) success = FALSE;
        } while (FindNextFileW(iterator, &entry));
        FindClose(iterator);
    }
    if (swprintf(pattern, PATH_CAP, L"%ls\\__pycache__", folder) >= 0) remove_directory(pattern);
    return success;
}

static int remove_version(const wchar_t *root, BOOL remove_files)
{
    if (!ordinary_directory(root)) {
        DWORD attributes = file_attributes(root);
        DWORD error = GetLastError();
        return attributes == INVALID_FILE_ATTRIBUTES &&
               (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) ? 0 : 12;
    }
    wchar_t filename[PATH_CAP], target[PATH_CAP];
    /* Only version directories beneath an installation we created are eligible. */
    if (wcslen(root) >= PATH_CAP) return 12;
    wcscpy(filename, root);
    wchar_t *version = wcsrchr(filename, L'\\');
    if (!version || !safe_relative(version + 1)) return 12;
    *version = L'\0';
    wchar_t *parent = wcsrchr(filename, L'\\');
    if (!parent || _wcsicmp(parent + 1, L"versions")) return 12;
    *parent = L'\0';
    wchar_t owner[128], relative[512];
    if (swprintf(target, PATH_CAP, L"%ls\\installation.ini", filename) < 0) return 12;
    GetPrivateProfileStringW(L"Docking", L"Application", L"", owner, 128, target);
    if (wcscmp(owner, L"org.docking.Docking") ||
        GetPrivateProfileIntW(L"Docking", L"Schema", 0, target) != 1 ||
        swprintf(relative, 512, L"versions\\%ls", version + 1) < 0 ||
        !safe_target(filename, relative, target)) return 12;
    if (!safe_target(root, L"uninstall-files.txt", filename)) return 12;
    HANDLE file = CreateFileW(filename, GENERIC_READ, FILE_SHARE_READ, NULL,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_FILE_NOT_FOUND ? 0 : 12;
    DWORD size = GetFileSize(file, NULL), read = 0;
    if (size == INVALID_FILE_SIZE || size > 16 * 1024 * 1024 || size % 2) {
        CloseHandle(file);
        return 12;
    }
    wchar_t *text = calloc(size / sizeof(wchar_t) + 2, sizeof(wchar_t));
    if (!text) { CloseHandle(file); return 12; }
    BOOL success = ReadFile(file, text, size, &read, NULL) && read == size;
    CloseHandle(file);
    wchar_t *start = text + (text[0] == 0xFEFF ? 1 : 0);
    if (!success || wcslen(text) != size / sizeof(wchar_t) ||
        wcsncmp(start, MANIFEST_HEADER, wcslen(MANIFEST_HEADER))) {
        free(text);
        return 12;
    }
    start += wcslen(MANIFEST_HEADER);
    /* Validate the complete inventory before deleting any installed file. */
    for (wchar_t *line = start; *line; ) {
        wchar_t *end = wcschr(line, L'\n');
        if (!end || end == line || end[-1] != L'\r') { free(text); return 12; }
        end[-1] = L'\0';
        BOOL valid = (line[0] == L'F' || line[0] == L'D') && line[1] == L'\t' &&
                     safe_target(root, line + 2, target);
        end[-1] = L'\r';
        if (!valid) { free(text); return 12; }
        line = end + 1;
    }
    if (!remove_files) { free(text); return 0; }
    int result = 0;
    for (wchar_t *line = start; *line && !result; ) {
        wchar_t *end = wcschr(line, L'\n');
        end[-1] = L'\0';
        if (safe_target(root, line + 2, target)) {
            if (line[0] == L'F') {
                if (!remove_bytecode(target) || !delete_owned_file(target)) result = 12;
            } else if (!remove_directory(target)) {
                DWORD error = GetLastError();
                if (error != ERROR_DIR_NOT_EMPTY && error != ERROR_FILE_NOT_FOUND &&
                    error != ERROR_PATH_NOT_FOUND) result = 12;
            }
        } else result = 12;
        line = end + 1;
    }
    free(text);
    if (result || !delete_owned_file(filename)) return 12;
    remove_directory(root);
    return 0;
}

static int validate_current(const wchar_t *root)
{
    wchar_t ini[PATH_CAP], version[128], relative[256], target[PATH_CAP];
    if (!safe_target(root, L"current.ini", ini)) return 12;
    DWORD length = GetPrivateProfileStringW(L"Docking", L"Current", L"", version, 128, ini);
    if (!length || length >= 127 || version[0] == L'.' || version[length - 1] == L'.' ||
        wcsstr(version, L"..")) return 12;
    for (DWORD index = 0; index < length; ++index) {
        wchar_t value = version[index];
        if (!((value >= L'0' && value <= L'9') || (value >= L'a' && value <= L'z') ||
              (value >= L'A' && value <= L'Z') || value == L'.' || value == L'-' || value == L'_')) return 12;
    }
    if (swprintf(relative, 256, L"versions\\%ls\\uninstall-files.txt", version) < 0 ||
        !safe_target(root, relative, target) || file_attributes(target) == INVALID_FILE_ATTRIBUTES) return 12;
    if (swprintf(relative, 256, L"versions\\%ls", version) < 0 || !safe_target(root, relative, target)) return 12;
    return remove_version(target, FALSE);
}

int wmain(int argc, wchar_t **argv)
{
    if (argc == 2 && !wcscmp(argv[1], L"--platform")) return supported_platform();
    if (argc != 3) return 12;
    wchar_t root[PATH_CAP];
    DWORD length = GetFullPathNameW(argv[2], PATH_CAP, root, NULL);
    if (!length || length >= PATH_CAP) return 12;
    if (!wcscmp(argv[1], L"--in-use")) return installation_in_use(root);
    if (!wcscmp(argv[1], L"--validate-root")) return validate_installation(root);
    if (!wcscmp(argv[1], L"--validate-current")) return validate_current(root);
    if (!wcscmp(argv[1], L"--validate-version")) return remove_version(root, FALSE);
    if (!wcscmp(argv[1], L"--remove-version")) return remove_version(root, TRUE);
    return 12;
}

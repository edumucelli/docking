// One bounded, thread-scoped input request. No keystrokes or composition text are read.
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <initguid.h>
#include <msctf.h>
#include <imm.h>
#include <stddef.h>

// Fixed-width wire representation is identical in the x86 and x64 helpers.
struct alignas(8) Profile {
    DWORD kind;
    WORD language, padding;
    GUID clsid, identifier, category;
    ULONGLONG substitute;
    DWORD capabilities, padding2;
    ULONGLONG layout;
    DWORD flags, padding3;
};
struct alignas(8) Request {
    DWORD magic, version, size, operation;
    DWORD pid, thread, root_pid, root_thread;
    ULONGLONG window, root, deadline, nonce;
    volatile LONG state, cancelled;
    HRESULT result;
    DWORD profile_known;
    Profile requested, active;
    ULONGLONG observed;
    LONG composition_bytes;
    DWORD ime_flags, conversion, sentence;
    DWORD machine;
    volatile DWORD authorized;
    DWORD parent_pid;
    volatile LONG phase;
    DWORD atom;
    volatile DWORD dispatched;
    WCHAR language_tag[96];
};
static_assert(sizeof(Profile) == 88 && sizeof(Request) == 496, "Input protocol size");
static_assert(offsetof(Request, active) == 168 && offsetof(Request, language_tag) == 304,
              "Input protocol offsets");
static const GUID profile_class = {0x33c53a50,0xf456,0x4884,{0xb0,0x49,0x85,0xfd,0x64,0x3e,0xcf,0xed}};
static const GUID keyboard_category = {0x34745c63,0xb2f0,0x4784,{0x8b,0x67,0x5e,0x12,0xc8,0x70,0x1a,0x31}};
static HMODULE module;
static const WCHAR message_name[] = L"Docking.Input.Profile.v1";
static const WCHAR mapping_prefix[] = L"Local\\DockingInput.";

// Freestanding helpers have no CRT, MinGW, MSYS2, or Python runtime dependency.
extern "C" void *memcpy(void *target, const void *source, size_t size) {
    for (size_t i = 0; i < size; ++i) ((BYTE *)target)[i] = ((const BYTE *)source)[i];
    return target;
}
extern "C" void *memset(void *target, int value, size_t size) {
    for (size_t i = 0; i < size; ++i) ((BYTE *)target)[i] = (BYTE)value;
    return target;
}
extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) { module = instance; DisableThreadLibraryCalls(instance); }
    return TRUE;
}
extern "C" __declspec(dllexport) DWORD WINAPI ProtocolSize() { return sizeof(Request); }

static HWND window(ULONGLONG value) { return (HWND)(UINT_PTR)value; }
static DWORD input_thread_id() {
    typedef DWORD (WINAPI *ReadThread)();
    ReadThread read = (ReadThread)(void *)GetProcAddress(
        GetModuleHandleW(L"kernel32.dll"), "GetCurrentThreadId");
    return read ? read() : 0;
}
static bool current(Request *r, bool foreground) {
    DWORD pid = 0, root_pid = 0;
    DWORD thread = GetWindowThreadProcessId(window(r->window), &pid);
    DWORD root_thread = GetWindowThreadProcessId(window(r->root), &root_pid);
    // Stable diagnostic phases identify the rejected ownership boundary. The
    // conditions and their HRESULT remain unchanged; no failed check is bypassed.
    if (r->cancelled) { r->phase=101; return false; }
    if (!r->authorized) { r->phase=102; return false; }
    if (r->machine != (sizeof(void *) == 8 ? 0x8664u : 0x14cu)) {
        r->phase=103; return false;
    }
    if (GetTickCount64() >= r->deadline) { r->phase=104; return false; }
    if (pid != r->pid || pid != GetCurrentProcessId()) { r->phase=105; return false; }
    if (thread != r->thread || thread != input_thread_id()) { r->phase=106; return false; }
    if (root_pid != r->root_pid || root_thread != r->root_thread) {
        r->phase=107; return false;
    }
    if (!IsWindowVisible(window(r->root)) ||
        (r->window != r->root && !IsChild(window(r->root), window(r->window)))) {
        r->phase=108; return false;
    }
    HDESK desktop = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
    BOOL receiving = FALSE;
    DWORD needed = 0;
    WCHAR input_name[256] = {}, thread_name[256] = {};
    HDESK own = GetThreadDesktop(input_thread_id());
    bool accessible = desktop && own && GetUserObjectInformationW(desktop, UOI_IO,
        &receiving, sizeof(receiving), &needed) && receiving &&
        GetUserObjectInformationW(desktop, UOI_NAME, input_name, sizeof(input_name), &needed) &&
        GetUserObjectInformationW(own, UOI_NAME, thread_name, sizeof(thread_name), &needed);
    if (accessible) {
        for (size_t i=0; i<256; ++i) {
            if (input_name[i] != thread_name[i]) { accessible=false; break; }
            if (!input_name[i]) break;
        }
    }
    if (desktop) CloseDesktop(desktop);
    if (!accessible) { r->phase=109; return false; }
    if (foreground) {
        GUITHREADINFO info = {};
        info.cbSize = sizeof(info);
        if (GetForegroundWindow() != window(r->root)) { r->phase=110; return false; }
        if (!GetGUIThreadInfo(r->thread, &info)) { r->phase=111; return false; }
        if (info.hwndFocus != window(r->window)) { r->phase=112; return false; }
    }
    return true;
}
static void copy_profile(Profile *out, const TF_INPUTPROCESSORPROFILE &value) {
    out->kind = value.dwProfileType; out->language = value.langid;
    out->clsid = value.clsid; out->identifier = value.guidProfile; out->category = value.catid;
    out->substitute = (UINT_PTR)value.hklSubstitute; out->capabilities = value.dwCaps;
    out->layout = (UINT_PTR)value.hkl; out->flags = value.dwFlags;
}
static HKL loaded_layout(ULONGLONG requested) {
    // Reject HKL_PREV/HKL_NEXT: the request names one exact loaded source.
    if ((DWORD)requested <= 1) return NULL;
    HKL loaded[256] = {};
    int count = GetKeyboardLayoutList(0, NULL);
    if (count <= 0 || count > 256) return NULL;
    count = GetKeyboardLayoutList(256, loaded);
    for (int i=0; i<count && i<256; ++i)
        if ((DWORD)(UINT_PTR)loaded[i] == (DWORD)requested) return loaded[i];
    return NULL;
}
static void language_tag(Request *r) {
    HMODULE library = LoadLibraryW(L"combase.dll");
    if (!library) return;
    typedef HRESULT (WINAPI *Initialize)(DWORD);
    typedef void (WINAPI *Uninitialize)();
    typedef HRESULT (WINAPI *Create)(const WCHAR *, UINT32, void **);
    typedef HRESULT (WINAPI *Delete)(void *);
    typedef const WCHAR *(WINAPI *Buffer)(void *, UINT32 *);
    typedef HRESULT (WINAPI *Factory)(void *, REFIID, void **);
    Initialize initialize = (Initialize)(void *)GetProcAddress(library, "RoInitialize");
    Uninitialize uninitialize = (Uninitialize)(void *)GetProcAddress(library, "RoUninitialize");
    Create create = (Create)(void *)GetProcAddress(library, "WindowsCreateString");
    Delete destroy = (Delete)(void *)GetProcAddress(library, "WindowsDeleteString");
    Buffer buffer = (Buffer)(void *)GetProcAddress(library, "WindowsGetStringRawBuffer");
    Factory factory = (Factory)(void *)GetProcAddress(library, "RoGetActivationFactory");
    if (initialize && uninitialize && create && destroy && buffer && factory) {
        HRESULT initialized = initialize(0);
        if (SUCCEEDED(initialized) || initialized == RPC_E_CHANGED_MODE) {
            static const WCHAR name[] = L"Windows.Globalization.Language";
            static const GUID iid = {0xb23cd557,0x0865,0x46d4,{0x89,0xb8,0xd5,0x9b,0xe8,0x99,0x0f,0x0d}};
            void *class_name = NULL, *object = NULL, *tag = NULL;
            if (SUCCEEDED(create(name, sizeof(name)/sizeof(WCHAR)-1, &class_name)) &&
                SUCCEEDED(factory(class_name, iid, &object)) && object) {
                void **table = *(void ***)object;
                typedef HRESULT (WINAPI *ReadTag)(void *, void **);
                if (SUCCEEDED(((ReadTag)table[7])(object, &tag)) && tag) {
                    UINT32 length = 0;
                    const WCHAR *text = buffer(tag, &length);
                    if (text && length < 96) {
                        for (UINT32 i=0; i<length; ++i) r->language_tag[i]=text[i];
                        r->language_tag[length]=0;
                    }
                }
            }
            // An external COM factory can return an interface alongside a
            // failed HRESULT. Release every interface it handed to us.
            if (object) {
                typedef ULONG (WINAPI *Release)(void *);
                void **table = *(void ***)object;
                ((Release)table[2])(object);
            }
            if (tag) destroy(tag);
            if (class_name) destroy(class_name);
            if (SUCCEEDED(initialized)) uninitialize();
        }
    }
    FreeLibrary(library);
}
static HRESULT execute(Request *r) {
    r->composition_bytes = -1;
    if (r->operation != 1 && r->operation != 2 && r->operation != 3) return E_INVALIDARG;
    if (!current(r, r->operation != 1)) return HRESULT_FROM_WIN32(ERROR_CANCELLED);
    r->observed = (UINT_PTR)GetKeyboardLayout(0);
    r->phase = 1;
    if (r->operation == 3) {
        // Classic windows need not initialize COM. Execute on their typing
        // thread, so a queued request still checks cancellation when dequeued.
        r->phase = 30;
        if (r->requested.kind != TF_PROFILETYPE_KEYBOARDLAYOUT) return E_INVALIDARG;
        HKL layout = loaded_layout(r->requested.layout);
        if (!layout) return E_INVALIDARG;
        if (!current(r, true)) return HRESULT_FROM_WIN32(ERROR_CANCELLED);
        r->phase = 31;
        SetLastError(ERROR_SUCCESS);
        if (!ActivateKeyboardLayout(layout, 0)) {
            DWORD error = GetLastError();
            return error ? HRESULT_FROM_WIN32(error) : E_FAIL;
        }
        r->observed = (UINT_PTR)GetKeyboardLayout(0);
        r->phase = 32;
        return (DWORD)r->observed == (DWORD)r->requested.layout ? S_OK : E_FAIL;
    }
    // The application owns its apartment. Never initialize/uninitialize COM
    // from a foreign-thread hook, including an application's startup reentry.
    typedef HRESULT (WINAPI *ApartmentType)(int *, int *);
    ApartmentType apartment_type = (ApartmentType)(void *)GetProcAddress(
        GetModuleHandleW(L"ole32.dll"), "CoGetApartmentType");
    int type = 0, qualifier = 0;
    HRESULT initialized = apartment_type ? apartment_type(&type, &qualifier) : E_NOINTERFACE;
    r->phase = 2;
    if (FAILED(initialized)) return r->operation == 1 ? S_OK : initialized;
    ITfInputProcessorProfiles *profiles = NULL;
    ITfInputProcessorProfileMgr *manager = NULL;
    HRESULT status = CoCreateInstance(profile_class, NULL,
        CLSCTX_INPROC_SERVER, IID_ITfInputProcessorProfiles, (void **)&profiles);
    r->phase = 3;
    if (SUCCEEDED(status) && profiles)
        status = profiles->QueryInterface(IID_ITfInputProcessorProfileMgr, (void **)&manager);
    r->phase = 4;
    if (SUCCEEDED(status) && manager && r->operation == 2) {
        BOOL enabled = FALSE;
        HKL layout = NULL;
        r->phase = 40;
        if (r->requested.kind == TF_PROFILETYPE_INPUTPROCESSOR) {
            status = profiles->IsEnabledLanguageProfile(r->requested.clsid,
                r->requested.language, r->requested.identifier, &enabled);
        } else if (r->requested.kind == TF_PROFILETYPE_KEYBOARDLAYOUT) {
            layout = loaded_layout(r->requested.layout);
            status = layout ? S_OK : E_INVALIDARG;
            enabled = layout != NULL;
        } else status = E_INVALIDARG;
        if (SUCCEEDED(status) && !enabled) status = E_ACCESSDENIED;
        if (SUCCEEDED(status)) {
            r->phase = 41;
            TF_INPUTPROCESSORPROFILE registered = {};
            status = manager->GetProfile(r->requested.kind,
                r->requested.language, r->requested.clsid, r->requested.identifier,
                layout, &registered);
            if (SUCCEEDED(status)) {
                r->phase = 42;
                r->requested.flags = registered.dwFlags;
                // TIPs use the dedicated enabled-state query above. Do not
                // veto it with a second metadata snapshot on an older thread.
                // Plain keyboard layouts have no dedicated enabled query.
                if (r->requested.kind == TF_PROFILETYPE_KEYBOARDLAYOUT &&
                    !(registered.dwFlags & 0x00000002 /* TF_IPP_FLAG_ENABLED */)) status=E_ACCESSDENIED;
                const BYTE *left = (const BYTE *)&registered.catid;
                const BYTE *right = (const BYTE *)&keyboard_category;
                for (size_t i=0; i<sizeof(GUID); ++i)
                    if (left[i] != right[i]) { status=E_INVALIDARG; break; }
            }
        }
        if (SUCCEEDED(status)) {
            r->phase = 43;
            if (!current(r, true)) status = HRESULT_FROM_WIN32(ERROR_CANCELLED);
        }
        if (SUCCEEDED(status)) {
            r->phase = 44;
            status = profiles->ChangeCurrentLanguage(r->requested.language);
        }
        if (SUCCEEDED(status)) {
            r->phase = 45;
            if (!current(r, true)) status = HRESULT_FROM_WIN32(ERROR_CANCELLED);
        }
        // Flags zero selects only this thread; never enable profiles or alter desktop defaults.
        if (SUCCEEDED(status)) {
            r->phase = 46;
            status = manager->ActivateProfile(r->requested.kind,
                r->requested.language, r->requested.clsid, r->requested.identifier, layout, 0);
        }
    }
    DWORD failed_phase = FAILED(status) ? r->phase : 0;
    r->phase = 5;
    if (SUCCEEDED(status) && manager) {
        TF_INPUTPROCESSORPROFILE active = {};
        HRESULT read = manager->GetActiveProfile(keyboard_category, &active);
        if (read == S_OK) { copy_profile(&r->active, active); r->profile_known = 1; }
        else if (FAILED(read)) { status = read; failed_phase = r->phase; }
    }
    r->phase = 6;
    r->observed = (UINT_PTR)GetKeyboardLayout(0);
    r->composition_bytes = -1;
    HIMC context = ImmGetContext(window(r->window));
    if (context) {
        r->ime_flags = 1 | (ImmGetOpenStatus(context) ? 2 : 0);
        r->composition_bytes = ImmGetCompositionStringW(context, GCS_COMPSTR, NULL, 0);
        ImmGetConversionStatus(context, &r->conversion, &r->sentence);
        ImmReleaseContext(window(r->window), context);
    }
    r->phase = 7;
    language_tag(r);
    r->phase = 8;
    if (manager) manager->Release();
    if (profiles) profiles->Release();
    if (failed_phase) r->phase = failed_phase;
    return status;
}
static Request *open_request(ATOM atom, HANDLE *mapping) {
    WCHAR name[128] = {};
    if (!GlobalGetAtomNameW(atom, name, 128)) return NULL;
    for (size_t i=0; i<sizeof(mapping_prefix)/sizeof(WCHAR)-1; ++i)
        if (name[i] != mapping_prefix[i]) return NULL;
    *mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name);
    if (!*mapping) return NULL;
    Request *r = (Request *)MapViewOfFile(*mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(Request));
    if (r && (r->magic != 0x44494e50 || r->version != 1 || r->size != sizeof(Request) || r->atom != atom)) {
        UnmapViewOfFile(r); r=NULL;
    }
    return r;
}
extern "C" __declspec(dllexport) LRESULT CALLBACK InputHook(int code, WPARAM parameter, LPARAM address) {
    if (code >= 0 && parameter == PM_REMOVE) {
        MSG *message = (MSG *)address;
        if (message->message == RegisterWindowMessageW(message_name)) {
            HANDLE mapping = NULL;
            Request *r = open_request((ATOM)message->wParam, &mapping);
            if (r) {
                if (r->nonce == (ULONGLONG)message->lParam && window(r->window) == message->hwnd &&
                    InterlockedCompareExchange(&r->state, 1, 0) == 0) {
                    r->result = execute(r);
                    InterlockedExchange(&r->state, 2);
                }
                UnmapViewOfFile(r);
            }
            if (mapping) CloseHandle(mapping);
        }
    }
    return CallNextHookEx(NULL, code, parameter, address);
}
// The x64 controller uses the system's x86 rundll32 host only for WOW64 targets.
// It assigns that host to an owned job before setting authorized in the mapping.
extern "C" __declspec(dllexport) void CALLBACK Dispatch(HWND, HINSTANCE, char *argument, int) {
    unsigned atom = 0;
    while (*argument >= '0' && *argument <= '9' && atom <= 65535)
        atom = atom * 10 + (unsigned)(*argument++ - '0');
    if (*argument || !atom || atom > 65535) return;
    HANDLE mapping = NULL;
    Request *r = open_request((ATOM)atom, &mapping);
    HHOOK hook = NULL;
    if (r) {
        while (!r->authorized && !r->cancelled && GetTickCount64() < r->deadline) Sleep(5);
        if (r->authorized && !r->cancelled && GetTickCount64() < r->deadline) {
            r->phase=210;
            hook = SetWindowsHookExW(WH_GETMESSAGE, InputHook, module, r->thread);
            if (hook) r->phase=211;
            if (!hook || !PostMessageW(window(r->window), RegisterWindowMessageW(message_name), atom, (LPARAM)r->nonce)) {
                r->result = HRESULT_FROM_WIN32(GetLastError());
                InterlockedExchange(&r->state, 2);
            }
            r->dispatched = 1;
            while (r->state != 2 && !r->cancelled && GetTickCount64() < r->deadline) Sleep(5);
        }
        if (hook) UnhookWindowsHookEx(hook);
        UnmapViewOfFile(r);
    }
    if (mapping) CloseHandle(mapping);
}

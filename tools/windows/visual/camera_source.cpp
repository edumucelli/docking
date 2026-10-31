// SPDX-License-Identifier: GPL-3.0-or-later
// Generated-frame Media Foundation source for an owned Windows lab.
// It has no hardware, file, network or audio input.
#define WIN32_LEAN_AND_MEAN
// Windows types must precede the Media Foundation and KS declarations.
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mferror.h>
#include <ks.h>
#include <ksmedia.h>
#include <ksproxy.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <new>

template <class T> class Ptr {
  T *value = nullptr;

public:
  Ptr() = default;
  explicit Ptr(T *p) : value(p) {
    if (value)
      value->AddRef();
  }
  Ptr(const Ptr &p) : Ptr(p.value) {}
  Ptr(Ptr &&p) : value(p.value) { p.value = nullptr; }
  ~Ptr() {
    if (value)
      value->Release();
  }
  Ptr &operator=(const Ptr &p) {
    if (this != &p)
      reset(p.value);
    return *this;
  }
  void reset(T *p = nullptr) {
    if (p)
      p->AddRef();
    if (value)
      value->Release();
    value = p;
  }
  T *get() const { return value; }
  T *operator->() const { return value; }
  T **out() {
    reset();
    return &value;
  }
  explicit operator bool() const { return value != nullptr; }
  HRESULT copy(T **p) const {
    if (!p)
      return E_POINTER;
    *p = value;
    if (value)
      value->AddRef();
    return S_OK;
  }
};
#define TRY(expr)                                                              \
  do {                                                                         \
    HRESULT status = (expr);                                                   \
    if (FAILED(status))                                                        \
      return status;                                                           \
  } while (0)
static std::atomic<long> objects{0};
static const GUID CLSID_LabCamera = {
    0x4a3b3ecf,
    0x5225,
    0x4af0,
    {0xb7, 0xf5, 0x63, 0x3f, 0x1c, 0xb4, 0xd9, 0x15}};
static const GUID Stream2IID = {
    0xc5bc37d6,
    0x75c7,
    0x46a1,
    {0xa1, 0x32, 0x81, 0xb5, 0xf7, 0x23, 0xc2, 0x0f}};
static const GUID AllocatorIID = {
    0xda62b958,
    0x3a38,
    0x4a97,
    {0xbd, 0x27, 0x14, 0x9c, 0x64, 0x0c, 0x07, 0x71}};
static const GUID StreamId = {0x11bd5120,
                              0xd124,
                              0x446b,
                              {0x88, 0xe6, 0x17, 0x06, 0x02, 0x57, 0xff, 0xf9}};
static const GUID StreamCategory = {
    0x2939e7b8,
    0xa62e,
    0x4579,
    {0xb6, 0x74, 0xd4, 0x07, 0x3d, 0xfa, 0xbb, 0xba}};
static const GUID StreamShared = {
    0x1cb378e9,
    0xb279,
    0x41d4,
    {0xaf, 0x97, 0x34, 0xa2, 0x43, 0xe6, 0x83, 0x20}};
// These documented interfaces are absent from the lab's MinGW headers.
struct LabMediaStream2 : IMFMediaStream {
  virtual HRESULT STDMETHODCALLTYPE SetStreamState(MF_STREAM_STATE) = 0;
  virtual HRESULT STDMETHODCALLTYPE GetStreamState(MF_STREAM_STATE *) = 0;
};
struct LabAllocatorControl : IUnknown {
  virtual HRESULT STDMETHODCALLTYPE SetDefaultAllocator(DWORD, IUnknown *) = 0;
  virtual HRESULT STDMETHODCALLTYPE GetAllocatorUsage(DWORD, DWORD *,
                                                      int *) = 0;
};
static constexpr UINT32 Width = 320, Height = 240;
static constexpr LONGLONG Duration = 10000000 / 30;

class Stream final : public LabMediaStream2 {
  std::atomic<ULONG> references{1};
  std::mutex lock;
  std::condition_variable wake;
  std::deque<Ptr<IUnknown>> requests;
  std::thread worker;
  Ptr<IMFMediaEventQueue> events;
  Ptr<IMFStreamDescriptor> descriptor;
  Ptr<IMFMediaSource> source;
  MF_STREAM_STATE state = MF_STREAM_STATE_STOPPED;
  bool closed = false;
  UINT64 generation = 0, frame = 0;

  HRESULT sample(IUnknown *token, IMFSample **result) {
    Ptr<IMFSample> value;
    Ptr<IMFMediaBuffer> buffer;
    Ptr<IMF2DBuffer> plane;
    TRY(MFCreateSample(value.out()));
    TRY(MFCreate2DMediaBuffer(Width, Height, MFVideoFormat_NV12.Data1, FALSE,
                              buffer.out()));
    TRY(buffer->QueryInterface(IID_IMF2DBuffer,
                               reinterpret_cast<void **>(plane.out())));
    BYTE *data = nullptr;
    LONG pitch = 0;
    TRY(plane->Lock2D(&data, &pitch));
    if (pitch < static_cast<LONG>(Width)) {
      plane->Unlock2D();
      return E_UNEXPECTED;
    }
    for (UINT32 y = 0; y < Height; y++)
      for (UINT32 x = 0; x < Width; x++)
        data[y * pitch + x] =
            static_cast<BYTE>(32 + ((x + frame * 5) % Width) * 188 / Width);
    for (UINT32 y = 0; y < Height / 2; y++)
      memset(data + (Height + y) * pitch, 128, Width);
    TRY(plane->Unlock2D());
    TRY(buffer->SetCurrentLength(Width * Height * 3 / 2));
    TRY(value->AddBuffer(buffer.get()));
    TRY(value->SetSampleTime(MFGetSystemTime()));
    TRY(value->SetSampleDuration(Duration));
    TRY(value->SetUINT32(MFSampleExtension_CleanPoint, TRUE));
    if (token)
      TRY(value->SetUnknown(MFSampleExtension_Token, token));
    frame++;
    return value.copy(result);
  }

  void produce() {
    HRESULT initialized = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    auto due = std::chrono::steady_clock::now();
    for (;;) {
      Ptr<IUnknown> token;
      UINT64 version = 0;
      {
        std::unique_lock<std::mutex> guard(lock);
        wake.wait(guard, [&] {
          return closed ||
                 (state == MF_STREAM_STATE_RUNNING && !requests.empty());
        });
        if (closed)
          break;
        version = generation;
        if (wake.wait_until(guard, due, [&] {
              return closed || version != generation ||
                     state != MF_STREAM_STATE_RUNNING;
            }))
          continue;
        token = requests.front();
        requests.pop_front();
      }
      Ptr<IMFSample> value;
      HRESULT status = sample(token.get(), value.out());
      {
        std::lock_guard<std::mutex> guard(lock);
        if (!closed && state == MF_STREAM_STATE_RUNNING &&
            version == generation) {
          if (SUCCEEDED(status))
            events->QueueEventParamUnk(MEMediaSample, GUID_NULL, S_OK,
                                       value.get());
          else
            events->QueueEventParamVar(MEError, GUID_NULL, status, nullptr);
        }
      }
      due = std::chrono::steady_clock::now() + std::chrono::microseconds(33333);
    }
    if (SUCCEEDED(initialized))
      CoUninitialize();
  }

public:
  Stream() { objects++; }
  ~Stream() {
    shutdown();
    objects--;
  }
  HRESULT initialize(IMFMediaSource *parent) {
    source.reset(parent);
    TRY(MFCreateEventQueue(events.out()));
    Ptr<IMFMediaType> type;
    TRY(MFCreateMediaType(type.out()));
    TRY(type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video));
    TRY(type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_NV12));
    TRY(MFSetAttributeSize(type.get(), MF_MT_FRAME_SIZE, Width, Height));
    TRY(MFSetAttributeRatio(type.get(), MF_MT_FRAME_RATE, 30, 1));
    TRY(MFSetAttributeRatio(type.get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1));
    TRY(type->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive));
    TRY(type->SetUINT32(MF_MT_DEFAULT_STRIDE, Width));
    TRY(type->SetUINT32(MF_MT_FIXED_SIZE_SAMPLES, TRUE));
    TRY(type->SetUINT32(MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE));
    TRY(type->SetUINT32(MF_MT_SAMPLE_SIZE, Width * Height * 3 / 2));
    IMFMediaType *types[] = {type.get()};
    TRY(MFCreateStreamDescriptor(0, 1, types, descriptor.out()));
    Ptr<IMFMediaTypeHandler> handler;
    TRY(descriptor->GetMediaTypeHandler(handler.out()));
    TRY(handler->SetCurrentMediaType(type.get()));
    TRY(descriptor->SetGUID(StreamCategory, PINNAME_VIDEO_CAPTURE));
    TRY(descriptor->SetUINT32(StreamId, 0));
    TRY(descriptor->SetUINT32(StreamShared, TRUE));
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **output) override {
    if (!output)
      return E_POINTER;
    *output = nullptr;
    if (id == IID_IUnknown || id == IID_IMFMediaEventGenerator ||
        id == IID_IMFMediaStream || id == Stream2IID)
      *output = static_cast<LabMediaStream2 *>(this);
    if (!*output)
      return E_NOINTERFACE;
    AddRef();
    return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return ++references; }
  ULONG STDMETHODCALLTYPE Release() override {
    auto count = --references;
    if (!count)
      delete this;
    return count;
  }
  HRESULT STDMETHODCALLTYPE GetEvent(DWORD flags,
                                     IMFMediaEvent **output) override {
    Ptr<IMFMediaEventQueue> queue;
    {
      std::lock_guard<std::mutex> guard(lock);
      if (closed)
        return MF_E_SHUTDOWN;
      queue = events;
    }
    return queue->GetEvent(flags, output);
  }
  HRESULT STDMETHODCALLTYPE BeginGetEvent(IMFAsyncCallback *cb,
                                          IUnknown *context) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : events->BeginGetEvent(cb, context);
  }
  HRESULT STDMETHODCALLTYPE EndGetEvent(IMFAsyncResult *result,
                                        IMFMediaEvent **output) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : events->EndGetEvent(result, output);
  }
  HRESULT STDMETHODCALLTYPE QueueEvent(MediaEventType kind, REFGUID extended,
                                       HRESULT status,
                                       const PROPVARIANT *data) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN
                  : events->QueueEventParamVar(kind, extended, status, data);
  }
  HRESULT STDMETHODCALLTYPE GetMediaSource(IMFMediaSource **output) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : source.copy(output);
  }
  HRESULT STDMETHODCALLTYPE
  GetStreamDescriptor(IMFStreamDescriptor **output) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : descriptor.copy(output);
  }
  HRESULT STDMETHODCALLTYPE RequestSample(IUnknown *token) override {
    std::lock_guard<std::mutex> guard(lock);
    if (closed)
      return MF_E_SHUTDOWN;
    if (state != MF_STREAM_STATE_RUNNING)
      return MF_E_INVALIDREQUEST;
    if (requests.size() >= 16)
      return MF_E_NOTACCEPTING;
    requests.emplace_back(token);
    wake.notify_all();
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE SetStreamState(MF_STREAM_STATE value) override {
    std::lock_guard<std::mutex> guard(lock);
    if (closed)
      return MF_E_SHUTDOWN;
    if (value != MF_STREAM_STATE_RUNNING && value != MF_STREAM_STATE_STOPPED &&
        value != MF_STREAM_STATE_PAUSED)
      return E_INVALIDARG;
    state = value;
    generation++;
    if (value != MF_STREAM_STATE_RUNNING)
      requests.clear();
    wake.notify_all();
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE GetStreamState(MF_STREAM_STATE *output) override {
    std::lock_guard<std::mutex> guard(lock);
    if (!output)
      return E_POINTER;
    if (closed)
      return MF_E_SHUTDOWN;
    *output = state;
    return S_OK;
  }
  HRESULT start(const PROPVARIANT *time) {
    std::lock_guard<std::mutex> guard(lock);
    if (closed)
      return MF_E_SHUTDOWN;
    state = MF_STREAM_STATE_RUNNING;
    generation++;
    if (!worker.joinable())
      worker = std::thread([this] { produce(); });
    wake.notify_all();
    return events->QueueEventParamVar(MEStreamStarted, GUID_NULL, S_OK, time);
  }
  HRESULT stop() {
    TRY(SetStreamState(MF_STREAM_STATE_STOPPED));
    return QueueEvent(MEStreamStopped, GUID_NULL, S_OK, nullptr);
  }
  void shutdown() {
    {
      std::lock_guard<std::mutex> guard(lock);
      if (closed)
        return;
      closed = true;
      generation++;
      requests.clear();
      wake.notify_all();
    }
    if (worker.joinable())
      worker.join();
    std::lock_guard<std::mutex> guard(lock);
    if (events)
      events->Shutdown();
    events.reset();
    descriptor.reset();
    source.reset();
  }
};

class Source final : public IMFMediaSourceEx,
                     public IMFGetService,
                     public IKsControl,
                     public LabAllocatorControl {
  std::atomic<ULONG> references{1};
  std::mutex lock;
  bool closed = false, announced = false;
  Ptr<IMFMediaEventQueue> events;
  Ptr<IMFAttributes> attributes;
  Ptr<IMFPresentationDescriptor> presentation;
  Ptr<Stream> stream;

public:
  Source() { objects++; }
  ~Source() {
    Shutdown();
    objects--;
  }
  HRESULT initialize() {
    TRY(MFCreateEventQueue(events.out()));
    TRY(MFCreateAttributes(attributes.out(), 4));
    TRY(attributes->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE,
                            MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID));
    TRY(attributes->SetString(MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME,
                              L"Docking Generated Camera Fixture"));
    *stream.out() = new (std::nothrow) Stream();
    if (!stream)
      return E_OUTOFMEMORY;
    TRY(stream->initialize(static_cast<IMFMediaSource *>(this)));
    Ptr<IMFStreamDescriptor> descriptor;
    TRY(stream->GetStreamDescriptor(descriptor.out()));
    IMFStreamDescriptor *descriptors[] = {descriptor.get()};
    TRY(MFCreatePresentationDescriptor(1, descriptors, presentation.out()));
    TRY(presentation->SelectStream(0));
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **output) override {
    if (!output)
      return E_POINTER;
    *output = nullptr;
    if (id == IID_IUnknown || id == IID_IMFMediaEventGenerator ||
        id == IID_IMFMediaSource || id == IID_IMFMediaSourceEx)
      *output = static_cast<IMFMediaSourceEx *>(this);
    else if (id == IID_IMFGetService)
      *output = static_cast<IMFGetService *>(this);
    else if (id == IID_IKsControl)
      *output = static_cast<IKsControl *>(this);
    else if (id == AllocatorIID)
      *output = static_cast<LabAllocatorControl *>(this);
    if (!*output)
      return E_NOINTERFACE;
    AddRef();
    return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return ++references; }
  ULONG STDMETHODCALLTYPE Release() override {
    auto count = --references;
    if (!count)
      delete this;
    return count;
  }
  HRESULT STDMETHODCALLTYPE GetEvent(DWORD flags,
                                     IMFMediaEvent **output) override {
    Ptr<IMFMediaEventQueue> queue;
    {
      std::lock_guard<std::mutex> guard(lock);
      if (closed)
        return MF_E_SHUTDOWN;
      queue = events;
    }
    return queue->GetEvent(flags, output);
  }
  HRESULT STDMETHODCALLTYPE BeginGetEvent(IMFAsyncCallback *cb,
                                          IUnknown *context) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : events->BeginGetEvent(cb, context);
  }
  HRESULT STDMETHODCALLTYPE EndGetEvent(IMFAsyncResult *result,
                                        IMFMediaEvent **output) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : events->EndGetEvent(result, output);
  }
  HRESULT STDMETHODCALLTYPE QueueEvent(MediaEventType kind, REFGUID extended,
                                       HRESULT status,
                                       const PROPVARIANT *data) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN
                  : events->QueueEventParamVar(kind, extended, status, data);
  }
  HRESULT STDMETHODCALLTYPE GetCharacteristics(DWORD *flags) override {
    std::lock_guard<std::mutex> guard(lock);
    if (!flags)
      return E_POINTER;
    if (closed)
      return MF_E_SHUTDOWN;
    *flags = MFMEDIASOURCE_IS_LIVE;
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE
  CreatePresentationDescriptor(IMFPresentationDescriptor **output) override {
    std::lock_guard<std::mutex> guard(lock);
    if (closed)
      return MF_E_SHUTDOWN;
    return presentation->Clone(output);
  }
  HRESULT STDMETHODCALLTYPE Start(IMFPresentationDescriptor *descriptor,
                                  const GUID *format,
                                  const PROPVARIANT *position) override {
    std::lock_guard<std::mutex> guard(lock);
    if (closed)
      return MF_E_SHUTDOWN;
    if (!descriptor || !position)
      return E_INVALIDARG;
    if (format && *format != GUID_NULL)
      return MF_E_UNSUPPORTED_TIME_FORMAT;
    DWORD count = 0, id = 0;
    BOOL selected = FALSE;
    TRY(descriptor->GetStreamDescriptorCount(&count));
    if (count != 1)
      return E_INVALIDARG;
    Ptr<IMFStreamDescriptor> chosen;
    TRY(descriptor->GetStreamDescriptorByIndex(0, &selected, chosen.out()));
    TRY(chosen->GetStreamIdentifier(&id));
    if (!selected || id != 0)
      return E_INVALIDARG;
    PROPVARIANT actual{};
    actual.vt = VT_I8;
    actual.hVal.QuadPart = MFGetSystemTime();
    TRY(events->QueueEventParamUnk(
        announced ? MEUpdatedStream : MENewStream, GUID_NULL, S_OK,
        static_cast<LabMediaStream2 *>(stream.get())));
    announced = true;
    TRY(stream->start(&actual));
    return events->QueueEventParamVar(MESourceStarted, GUID_NULL, S_OK,
                                      &actual);
  }
  HRESULT STDMETHODCALLTYPE Stop() override {
    std::lock_guard<std::mutex> guard(lock);
    if (closed)
      return MF_E_SHUTDOWN;
    TRY(stream->stop());
    return events->QueueEventParamVar(MESourceStopped, GUID_NULL, S_OK,
                                      nullptr);
  }
  HRESULT STDMETHODCALLTYPE Pause() override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : MF_E_INVALID_STATE_TRANSITION;
  }
  HRESULT STDMETHODCALLTYPE Shutdown() override {
    Ptr<Stream> owned;
    Ptr<IMFMediaEventQueue> queue;
    {
      std::lock_guard<std::mutex> guard(lock);
      if (closed)
        return S_OK;
      closed = true;
      owned = stream;
      stream.reset();
      queue = events;
      events.reset();
      presentation.reset();
      attributes.reset();
    }
    if (owned)
      owned->shutdown();
    if (queue)
      queue->Shutdown();
    return S_OK;
  }
  HRESULT STDMETHODCALLTYPE
  GetSourceAttributes(IMFAttributes **output) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : attributes.copy(output);
  }
  HRESULT STDMETHODCALLTYPE
  GetStreamAttributes(DWORD id, IMFAttributes **output) override {
    std::lock_guard<std::mutex> guard(lock);
    if (closed)
      return MF_E_SHUTDOWN;
    if (id != 0)
      return MF_E_INVALIDSTREAMNUMBER;
    Ptr<IMFStreamDescriptor> descriptor;
    TRY(stream->GetStreamDescriptor(descriptor.out()));
    return descriptor->QueryInterface(IID_IMFAttributes,
                                      reinterpret_cast<void **>(output));
  }
  HRESULT STDMETHODCALLTYPE SetD3DManager(IUnknown *) override {
    std::lock_guard<std::mutex> guard(lock);
    return closed ? MF_E_SHUTDOWN : S_OK;
  }
  HRESULT STDMETHODCALLTYPE GetService(REFGUID, REFIID,
                                       void **output) override {
    if (!output)
      return E_POINTER;
    *output = nullptr;
    return MF_E_UNSUPPORTED_SERVICE;
  }
  HRESULT STDMETHODCALLTYPE KsProperty(PKSPROPERTY, ULONG, LPVOID, ULONG,
                                       ULONG *returned) override {
    if (returned)
      *returned = 0;
    return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
  }
  HRESULT STDMETHODCALLTYPE KsMethod(PKSMETHOD, ULONG, LPVOID, ULONG,
                                     ULONG *returned) override {
    if (returned)
      *returned = 0;
    return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
  }
  HRESULT STDMETHODCALLTYPE KsEvent(PKSEVENT, ULONG, LPVOID, ULONG,
                                    ULONG *returned) override {
    if (returned)
      *returned = 0;
    return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
  }
  HRESULT STDMETHODCALLTYPE SetDefaultAllocator(DWORD id, IUnknown *) override {
    return id ? MF_E_INVALIDSTREAMNUMBER : S_OK;
  }
  HRESULT STDMETHODCALLTYPE GetAllocatorUsage(DWORD id, DWORD *input,
                                              int *usage) override {
    if (!input || !usage)
      return E_POINTER;
    if (id)
      return MF_E_INVALIDSTREAMNUMBER;
    *input = 0;
    *usage = 1;
    return S_OK;
  }
};

class Activate final : public IMFActivate {
  std::atomic<ULONG> references{1};
  std::mutex lock;
  Ptr<IMFAttributes> attributes;
  Ptr<IMFMediaSource> source;

public:
  Activate() { objects++; }
  ~Activate() { objects--; }
  HRESULT initialize() { return MFCreateAttributes(attributes.out(), 4); }
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **output) override {
    if (!output)
      return E_POINTER;
    *output = nullptr;
    if (id == IID_IUnknown || id == IID_IMFAttributes || id == IID_IMFActivate)
      *output = static_cast<IMFActivate *>(this);
    if (!*output)
      return E_NOINTERFACE;
    AddRef();
    return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return ++references; }
  ULONG STDMETHODCALLTYPE Release() override {
    auto count = --references;
    if (!count)
      delete this;
    return count;
  }
  HRESULT STDMETHODCALLTYPE ActivateObject(REFIID id, void **output) override {
    if (!output)
      return E_POINTER;
    *output = nullptr;
    std::lock_guard<std::mutex> guard(lock);
    const bool fresh = !source;
    if (!source) {
      Source *item = new (std::nothrow) Source();
      if (!item)
        return E_OUTOFMEMORY;
      HRESULT status = item->initialize();
      if (SUCCEEDED(status)) {
        Ptr<IMFAttributes> target;
        status = item->GetSourceAttributes(target.out());
        if (SUCCEEDED(status))
          status = attributes->CopyAllItems(target.get());
      }
      if (FAILED(status)) {
        item->Shutdown();
        item->Release();
        return status;
      }
      *source.out() = static_cast<IMFMediaSource *>(item);
    }
    HRESULT status = source->QueryInterface(id, output);
    if (FAILED(status) && fresh) {
      source->Shutdown();
      source.reset();
    }
    return status;
  }
  HRESULT STDMETHODCALLTYPE ShutdownObject() override { return E_NOTIMPL; }
  HRESULT STDMETHODCALLTYPE DetachObject() override { return E_NOTIMPL; }
#define ATTR(name, parameters, arguments)                                      \
  HRESULT STDMETHODCALLTYPE name parameters override {                         \
    return attributes->name arguments;                                         \
  }
  ATTR(GetItem, (REFGUID key, PROPVARIANT *value), (key, value))
  ATTR(GetItemType, (REFGUID key, MF_ATTRIBUTE_TYPE *type), (key, type))
  ATTR(CompareItem, (REFGUID key, REFPROPVARIANT value, BOOL *result),
       (key, value, result))
  ATTR(Compare,
       (IMFAttributes * other, MF_ATTRIBUTES_MATCH_TYPE type, BOOL *result),
       (other, type, result))
  ATTR(GetUINT32, (REFGUID key, UINT32 *value), (key, value))
  ATTR(GetUINT64, (REFGUID key, UINT64 *value), (key, value))
  ATTR(GetDouble, (REFGUID key, double *value), (key, value))
  ATTR(GetGUID, (REFGUID key, GUID *value), (key, value))
  ATTR(GetStringLength, (REFGUID key, UINT32 *count), (key, count))
  ATTR(GetString, (REFGUID key, LPWSTR value, UINT32 size, UINT32 *count),
       (key, value, size, count))
  ATTR(GetAllocatedString, (REFGUID key, LPWSTR *value, UINT32 *count),
       (key, value, count))
  ATTR(GetBlobSize, (REFGUID key, UINT32 *count), (key, count))
  ATTR(GetBlob, (REFGUID key, UINT8 *value, UINT32 size, UINT32 *count),
       (key, value, size, count))
  ATTR(GetAllocatedBlob, (REFGUID key, UINT8 **value, UINT32 *count),
       (key, value, count))
  ATTR(GetUnknown, (REFGUID key, REFIID id, LPVOID *value), (key, id, value))
  ATTR(SetItem, (REFGUID key, REFPROPVARIANT value), (key, value))
  ATTR(DeleteItem, (REFGUID key), (key))
  ATTR(DeleteAllItems, (), ())
  ATTR(SetUINT32, (REFGUID key, UINT32 value), (key, value))
  ATTR(SetUINT64, (REFGUID key, UINT64 value), (key, value))
  ATTR(SetDouble, (REFGUID key, double value), (key, value))
  ATTR(SetGUID, (REFGUID key, REFGUID value), (key, value))
  ATTR(SetString, (REFGUID key, LPCWSTR value), (key, value))
  ATTR(SetBlob, (REFGUID key, const UINT8 *value, UINT32 count),
       (key, value, count))
  ATTR(SetUnknown, (REFGUID key, IUnknown *value), (key, value))
  ATTR(LockStore, (), ())
  ATTR(UnlockStore, (), ())
  ATTR(GetCount, (UINT32 * count), (count))
  ATTR(GetItemByIndex, (UINT32 index, GUID *key, PROPVARIANT *value),
       (index, key, value))
  ATTR(CopyAllItems, (IMFAttributes * target), (target))
#undef ATTR
};

class Factory final : public IClassFactory {
  std::atomic<ULONG> references{1};

public:
  Factory() { objects++; }
  ~Factory() { objects--; }
  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **output) override {
    if (!output)
      return E_POINTER;
    *output = nullptr;
    if (id != IID_IUnknown && id != IID_IClassFactory)
      return E_NOINTERFACE;
    *output = static_cast<IClassFactory *>(this);
    AddRef();
    return S_OK;
  }
  ULONG STDMETHODCALLTYPE AddRef() override { return ++references; }
  ULONG STDMETHODCALLTYPE Release() override {
    auto count = --references;
    if (!count)
      delete this;
    return count;
  }
  HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown *outer, REFIID id,
                                           void **output) override {
    if (!output)
      return E_POINTER;
    *output = nullptr;
    if (outer)
      return CLASS_E_NOAGGREGATION;
    Activate *activate = new (std::nothrow) Activate();
    if (!activate)
      return E_OUTOFMEMORY;
    HRESULT status = activate->initialize();
    if (SUCCEEDED(status))
      status = activate->QueryInterface(id, output);
    activate->Release();
    return status;
  }
  HRESULT STDMETHODCALLTYPE LockServer(BOOL locked) override {
    if (locked)
      objects++;
    else
      objects--;
    return S_OK;
  }
};

extern "C" __declspec(dllexport) HRESULT WINAPI
DllGetClassObject(REFCLSID clsid, REFIID iid, void **output) {
  if (!output)
    return E_POINTER;
  *output = nullptr;
  if (clsid != CLSID_LabCamera)
    return CLASS_E_CLASSNOTAVAILABLE;
  Factory *factory = new (std::nothrow) Factory();
  if (!factory)
    return E_OUTOFMEMORY;
  HRESULT status = factory->QueryInterface(iid, output);
  factory->Release();
  return status;
}
extern "C" __declspec(dllexport) HRESULT WINAPI DllCanUnloadNow() {
  return objects.load() == 0 ? S_OK : S_FALSE;
}

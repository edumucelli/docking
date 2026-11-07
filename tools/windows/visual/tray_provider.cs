// Independent native Shell_NotifyIcon provider. No Docking code or bindings.
// Compile with Framework csc /platform:x64 or /platform:x86 and references:
// System.Windows.Forms.dll, System.Drawing.dll, System.Web.Extensions.dll.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class Native {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    internal struct DataW {
        internal uint cbSize; internal IntPtr hWnd; internal uint uID, uFlags, uCallbackMessage;
        internal IntPtr hIcon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] internal string szTip;
        internal uint dwState, dwStateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] internal string szInfo;
        internal uint uVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] internal string szInfoTitle;
        internal uint dwInfoFlags; internal Guid guidItem; internal IntPtr hBalloonIcon;
    }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Ansi)]
    internal struct DataA {
        internal uint cbSize; internal IntPtr hWnd; internal uint uID, uFlags, uCallbackMessage;
        internal IntPtr hIcon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=128)] internal string szTip;
        internal uint dwState, dwStateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)] internal string szInfo;
        internal uint uVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=64)] internal string szInfoTitle;
        internal uint dwInfoFlags; internal Guid guidItem; internal IntPtr hBalloonIcon;
    }
    [StructLayout(LayoutKind.Sequential)]
    internal struct Identifier {
        internal uint cbSize; internal IntPtr hWnd; internal uint uID; internal Guid guidItem;
    }
    [StructLayout(LayoutKind.Sequential)]
    internal struct Rect { internal int left, top, right, bottom; }
    [StructLayout(LayoutKind.Sequential)]
    internal struct Point { internal int x, y; }
    [DllImport("shell32.dll", EntryPoint="Shell_NotifyIconW", CharSet=CharSet.Unicode)]
    internal static extern bool NotifyW(uint operation, ref DataW data);
    [DllImport("shell32.dll", EntryPoint="Shell_NotifyIconA", CharSet=CharSet.Ansi)]
    internal static extern bool NotifyA(uint operation, ref DataA data);
    [DllImport("shell32.dll")]
    internal static extern int Shell_NotifyIconGetRect(ref Identifier id, out Rect rect);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)]
    internal static extern uint RegisterWindowMessage(string name);
    [DllImport("user32.dll")] internal static extern bool DestroyIcon(IntPtr icon);
    [DllImport("user32.dll")] internal static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] internal static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] internal static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] internal static extern bool SetWindowPos(IntPtr hwnd,
        IntPtr after,int x,int y,int width,int height,uint flags);
    [DllImport("user32.dll")] internal static extern IntPtr CreatePopupMenu();
    [DllImport("user32.dll", CharSet=CharSet.Unicode)]
    internal static extern bool AppendMenu(IntPtr menu, uint flags, UIntPtr id, string text);
    [DllImport("user32.dll")]
    internal static extern uint TrackPopupMenuEx(IntPtr menu, uint flags, int x, int y,
                                                IntPtr owner, IntPtr parameters);
    [DllImport("user32.dll")] internal static extern bool DestroyMenu(IntPtr menu);
    [DllImport("user32.dll")]
    internal static extern bool PostMessage(IntPtr hwnd, uint message, UIntPtr first, IntPtr second);
    [DllImport("kernel32.dll")] internal static extern uint GetACP();
}

sealed class RichPopup : Form {
    internal RichPopup(string label) {
        Text="Owned tray popup " + label; ShowInTaskbar=false;TopMost=true;
        FormBorderStyle=FormBorderStyle.FixedSingle;
        StartPosition=FormStartPosition.Manual; ClientSize=new Size(260,80);
        var text=new Label();text.Dock=DockStyle.Fill;text.TextAlign=ContentAlignment.MiddleCenter;
        text.Text="Independent rich popup " + label;Controls.Add(text);
    }
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get { var value=base.CreateParams;value.ExStyle|=0x08000080;return value; }
    }
}

sealed class ForegroundWindow : Form {
    internal ForegroundWindow(string label) {
        Text="Owned tray foreground " + label;ShowInTaskbar=false;
        StartPosition=FormStartPosition.Manual;Location=new Point(180,100);
        ClientSize=new Size(360,140);
        var text=new Label();text.Dock=DockStyle.Fill;text.TextAlign=ContentAlignment.MiddleCenter;
        text.Text="Independent foreground control " + label;Controls.Add(text);
    }
    protected override bool ShowWithoutActivation { get { return true; } }
}

sealed class Provider : NativeWindow, IDisposable {
    readonly string root, label, encoding, sizeKind;
    readonly bool useGuid, rich;
    readonly Guid guid;
    readonly uint uid, requestedVersion;
    readonly JavaScriptSerializer json=new JavaScriptSerializer();
    readonly Dictionary<string,object> report=new Dictionary<string,object>();
    readonly List<Dictionary<string,object>> calls=new List<Dictionary<string,object>>();
    readonly List<Dictionary<string,object>> callbacks=new List<Dictionary<string,object>>();
    readonly System.Windows.Forms.Timer timer=new System.Windows.Forms.Timer();
    readonly DateTime deadline;
    readonly uint taskbar;
    IntPtr icon; RichPopup popup; ForegroundWindow foregroundWindow;
    string tip;
    bool registered, showTip, disposed, commandActive;
    uint protocolVersion;
    long callbackSequence;
    int revision;

    internal Provider(string directory, Dictionary<string,object> config) {
        root=directory;Directory.CreateDirectory(root);
        label=Convert.ToString(config["label"]);
        encoding=Convert.ToString(config["encoding"]);
        sizeKind=Convert.ToString(config["size"]);
        useGuid=Convert.ToString(config["identity"])=="guid";
        guid=new Guid(Convert.ToString(config["guid"]));
        uid=Convert.ToUInt32(config["uid"]);
        requestedVersion=Convert.ToUInt32(config["version"]);
        showTip=Convert.ToBoolean(config["show_tip"]);rich=Convert.ToBoolean(config["rich_popup"]);
        if (requestedVersion!=0 && requestedVersion!=3 && requestedVersion!=4)
            throw new ArgumentException("Version must be 0, 3 or 4");
        if (encoding!="unicode" && encoding!="ansi") throw new ArgumentException("Encoding");
        if (sizeKind=="v1" && requestedVersion!=0) throw new ArgumentException("V1 has no uVersion");
        if (useGuid && (sizeKind=="v1" || sizeKind=="v2")) throw new ArgumentException("GUID needs V3/full");
        deadline=DateTime.UtcNow.AddSeconds(Math.Min(600, Math.Max(1,Convert.ToInt32(config["seconds"]))));
        tip="Owned tray " + label + (encoding=="unicode" ? " \u4e2d\u6587 \ud83d\ude42" : " ANSI");
        taskbar=Native.RegisterWindowMessage("TaskbarCreated");
        CreateHandle(new CreateParams {Caption="Owned independent tray " + label, Style=0});
        using (var bitmap=new Bitmap(32,32,PixelFormat.Format32bppArgb)) {
            using (var drawing=Graphics.FromImage(bitmap)) {
                drawing.Clear(Color.Transparent);
                using (var brush=new SolidBrush(label=="A" ? Color.RoyalBlue : Color.DarkOrange))
                    drawing.FillEllipse(brush,2,2,28,28);
                drawing.FillRectangle(Brushes.White,14,6,4,20);
            }
            icon=bitmap.GetHicon();
        }
        report["pid"]=Process.GetCurrentProcess().Id;report["bits"]=IntPtr.Size*8;
        report["label"]=label;report["window"]=Handle.ToInt64();report["uid"]=uid;
        report["guid"]=useGuid ? guid.ToString() : "";
        report["encoding"]=encoding;report["size"]=sizeKind;report["native_size"]=DataSize();
        report["version"]=requestedVersion;report["ansi_code_page"]=Native.GetACP();
        report["calls"]=calls;report["callbacks"]=callbacks;report["stage"]="ready";
        timer.Interval=50;timer.Tick+=Tick;timer.Start();Publish();
    }

    uint IdentityFlags { get { return useGuid ? 0x20u : 0u; } }
    uint DataSize() {
        Type type=encoding=="unicode" ? typeof(Native.DataW) : typeof(Native.DataA);
        if(sizeKind=="full") return (uint)Marshal.SizeOf(type);
        if(sizeKind=="v1") return (uint)Marshal.OffsetOf(type,"szTip").ToInt32()
            + (encoding=="unicode" ? 128u : 64u);
        if(sizeKind=="v2") return (uint)Marshal.OffsetOf(type,"guidItem").ToInt32();
        if(sizeKind=="v3") return (uint)Marshal.OffsetOf(type,"hBalloonIcon").ToInt32();
        throw new ArgumentException("Structure size");
    }
    bool Notify(uint operation, uint flags, bool guidOnly, uint version,
                string info, string title, uint infoFlags, uint state=0, uint mask=0) {
        bool accepted;
        IntPtr window=guidOnly ? IntPtr.Zero : Handle;
        uint identity=guidOnly ? 0u : uid;
        if(encoding=="unicode") {
            var data=new Native.DataW {cbSize=DataSize(),hWnd=window,uID=identity,uFlags=flags,
                uCallbackMessage=0x8001,hIcon=icon,szTip=tip,szInfo=info ?? "",uVersion=version,
                szInfoTitle=title ?? "",dwInfoFlags=infoFlags,guidItem=useGuid ? guid : Guid.Empty,
                dwState=state,dwStateMask=mask};
            accepted=Native.NotifyW(operation,ref data);
        } else {
            var data=new Native.DataA {cbSize=DataSize(),hWnd=window,uID=identity,uFlags=flags,
                uCallbackMessage=0x8001,hIcon=icon,szTip=tip,szInfo=info ?? "",uVersion=version,
                szInfoTitle=title ?? "",dwInfoFlags=infoFlags,guidItem=useGuid ? guid : Guid.Empty,
                dwState=state,dwStateMask=mask};
            accepted=Native.NotifyA(operation,ref data);
        }
        calls.Add(new Dictionary<string,object> { {"operation",operation},{"flags",flags},
            {"accepted",accepted},{"guid_only",guidOnly},{"window",window.ToInt64()},
            {"uid",identity},{"tip",tip},{"version_or_timeout",version},
            {"foreground",Native.GetForegroundWindow().ToInt64()} });
        if(calls.Count>256) calls.RemoveAt(0);
        return accepted;
    }
    bool Simple(uint operation, uint flags, bool guidOnly) {
        return Notify(operation,flags,guidOnly,0,"","",0);
    }
    string Error(Exception error) {
        return error.GetType().Name+": "+error.Message.Replace(root,"<owned-output>");
    }
    void Add() {
        bool accepted=Simple(0,IdentityFlags|7u|(showTip ? 0x80u : 0u),false);
        if(accepted) {
            registered=true;protocolVersion=0;
            if(sizeKind!="v1" && Notify(4,IdentityFlags,false,requestedVersion,"","",0))
                protocolVersion=requestedVersion;
        }
    }
    Dictionary<string,object> Bounds(bool guidOnly) {
        var id=new Native.Identifier {cbSize=(uint)Marshal.SizeOf(typeof(Native.Identifier)),
            hWnd=guidOnly ? IntPtr.Zero : Handle,uID=guidOnly ? 0u : uid,
            guidItem=useGuid ? guid : Guid.Empty};
        Native.Rect rect;int status=Native.Shell_NotifyIconGetRect(ref id,out rect);
        return new Dictionary<string,object> { {"status",status},
            {"rect",new int[]{rect.left,rect.top,rect.right,rect.bottom}} };
    }
    void ShowRich() {
        if(!rich) return;
        if(popup==null) popup=new RichPopup(label);
        var bounds=Bounds(useGuid);var values=(int[])bounds["rect"];
        Rectangle iconBounds;
        if(Convert.ToInt32(bounds["status"])==0)
            iconBounds=Rectangle.FromLTRB(values[0],values[1],values[2],values[3]);
        else {Native.Point cursor;Native.GetCursorPos(out cursor);
            iconBounds=new Rectangle(cursor.x,cursor.y,1,1);}
        Rectangle work=Screen.FromRectangle(iconBounds).WorkingArea;
        Point[] positions=new Point[] {
            new Point(iconBounds.Left,iconBounds.Top-popup.Height-4),
            new Point(iconBounds.Left,iconBounds.Bottom+4),
            new Point(iconBounds.Right+4,iconBounds.Top),
            new Point(iconBounds.Left-popup.Width-4,iconBounds.Top)};
        bool placed=false;
        foreach(Point candidate in positions) {
            var rect=new Rectangle(Math.Max(work.Left,Math.Min(candidate.X,work.Right-popup.Width)),
                Math.Max(work.Top,Math.Min(candidate.Y,work.Bottom-popup.Height)),popup.Width,popup.Height);
            if(work.Contains(rect) && !rect.IntersectsWith(iconBounds)) {
                popup.Location=rect.Location;placed=true;break;
            }
        }
        if(!placed) throw new InvalidOperationException("No unobstructed rich popup space");
        IntPtr previous=Native.GetForegroundWindow();popup.Show();
        if(!Native.SetWindowPos(popup.Handle,new IntPtr(-1),0,0,0,0,0x13))
            throw new InvalidOperationException("Cannot raise owned rich popup without activation");
        popup.Refresh();
        report["rich_visible"]=popup.Visible;
        report["rich_window"]=popup.Handle.ToInt64();
        report["rich_foreground"]=Native.GetForegroundWindow().ToInt64();
        report["rich_kept_foreground"]=Native.GetForegroundWindow()==previous;
    }
    void Menu() {
        var bounds=Bounds(useGuid);var values=(int[])bounds["rect"];
        Native.Point cursor;Native.GetCursorPos(out cursor);
        int x=Convert.ToInt32(bounds["status"])==0 ? values[0] : cursor.x;
        int y=Convert.ToInt32(bounds["status"])==0 ? values[3] : cursor.y;
        IntPtr menu=Native.CreatePopupMenu();if(menu==IntPtr.Zero) throw new Exception("Menu create");
        try {
            if(!Native.AppendMenu(menu,0,new UIntPtr(901),"Record owned action " + label))
                throw new Exception("Menu item");
            Native.SetForegroundWindow(Handle);
            report["menu_selected"]=Native.TrackPopupMenuEx(menu,0x102,x,y,Handle,IntPtr.Zero);
            Native.PostMessage(Handle,0,UIntPtr.Zero,IntPtr.Zero);
            report["setfocus_after_menu"]=Simple(3,IdentityFlags,false);
        } finally { Native.DestroyMenu(menu); }
    }
    protected override void WndProc(ref Message message) {
        try {
            if((uint)message.Msg==0x8001) {
                uint second=unchecked((uint)message.LParam.ToInt64());
                uint first=unchecked((uint)message.WParam.ToInt64());
                uint evt=protocolVersion==4 ? second & 0xffffu : second;
                uint observedUid=protocolVersion==4 ? second >> 16 : first;
                int x=(short)(first & 0xffffu), y=(short)(first >> 16);
                callbackSequence++;
                callbacks.Add(new Dictionary<string,object> { {"sequence",callbackSequence},
                    {"event",evt},{"uid",observedUid},{"window",message.HWnd.ToInt64()},
                    {"wparam",message.WParam.ToInt64()},{"lparam",message.LParam.ToInt64()},
                    {"x",x},{"y",y},{"keyboard_select",evt==0x401},
                    {"packed_anchor_defined",protocolVersion==4 &&
                        (evt==0x400 || evt==0x401 || evt==0x406 || (evt>=0x200 && evt<=0x20e))} });
                if(callbacks.Count>256) callbacks.RemoveAt(0);
                if(evt==0x406) ShowRich();
                if(evt==0x407 && popup!=null) {popup.Hide();report["rich_visible"]=false;}
                Publish();
                if(evt==0x7b || evt==0x205) Menu();
                Publish();message.Result=IntPtr.Zero;return;
            }
            if((uint)message.Msg==taskbar) {
                report["taskbar_created"]=Convert.ToInt32(report.ContainsKey("taskbar_created")
                    ? report["taskbar_created"] : 0)+1;
                if(registered) Add();Publish();message.Result=IntPtr.Zero;return;
            }
            if(message.Msg==0x10) {Application.ExitThread();message.Result=IntPtr.Zero;return;}
        } catch(Exception error) {report["error"]=Error(error);Environment.ExitCode=1;
            Publish();Application.ExitThread();}
        base.WndProc(ref message);
    }
    void Tick(object sender,EventArgs args) {
        if(commandActive) return;
        try {
            if(DateTime.UtcNow>=deadline || File.Exists(Path.Combine(root,"stop"))) {
                Application.ExitThread();return;
            }
            string path=Path.Combine(root,"command-"+(revision+1).ToString("D6")+".json");
            if(!File.Exists(path)) return;
            var command=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));
            commandActive=true;
            bool guidOnly=command.ContainsKey("guid_only") && Convert.ToBoolean(command["guid_only"]);
            if(guidOnly && !useGuid) throw new ArgumentException("GUID-only command for HWND icon");
            string action=Convert.ToString(command["action"]);
            if(action=="add") Add();
            else if(action=="delete") {if(Simple(2,IdentityFlags,guidOnly)) registered=false;
                if(popup!=null) popup.Hide();}
            else if(action=="showtip") {
                showTip=Convert.ToBoolean(command["enabled"]);
                // Deliberately omit NIF_TIP: this changes policy, not tooltip text.
                Simple(1,IdentityFlags|(showTip ? 0x80u : 0u),guidOnly);
            } else if(action=="tip") {
                tip=Convert.ToString(command["text"]);Simple(1,IdentityFlags|4u,guidOnly);
            } else if(action=="focus") Simple(3,IdentityFlags,guidOnly);
            else if(action=="hide" || action=="show")
                Notify(1,IdentityFlags|8u,guidOnly,0,"","",0,action=="hide" ? 1u : 0u,1);
            else if(action=="owner-window") {
                if(foregroundWindow==null) foregroundWindow=new ForegroundWindow(label);
                foregroundWindow.Show();report["owner_window"]=foregroundWindow.Handle.ToInt64();
            }
            else if(action=="balloon" || action=="balloon-clear") {
                string info=action=="balloon-clear" ? "" : "Owned native balloon " + label;
                Notify(1,IdentityFlags|0x10u,guidOnly,10000,info,"Owned tray probe " + label,0x91);
            } else if(action=="rect") report["bounds"]=Bounds(guidOnly);
            else if(action=="menu") Menu();
            else if(action=="snapshot") {}
            else if(action=="quit") Application.ExitThread();
            else throw new ArgumentException("Unknown command " + action);
            revision++;report["command_revision"]=revision;report["last_action"]=action;Publish();
        } catch(IOException) { /* Retry a bounded revision after a concurrent SMB open. */ }
          catch(Exception error) {report["error"]=Error(error);Environment.ExitCode=1;
              Publish();Application.ExitThread();}
          finally {commandActive=false;}
    }
    void Publish() {
        report["registered"]=registered;report["show_tip"]=showTip;
        report["tip"]=tip;report["callback_version"]=protocolVersion;
        report["callback_sequence"]=callbackSequence;
        report["command_revision"]=revision;
        string temporary=Path.Combine(root,"state.tmp");
        File.WriteAllText(temporary,json.Serialize(report),new UTF8Encoding(false));
        string target=Path.Combine(root,"state.json");
        for(int attempt=0;attempt<10;attempt++) {
            try {if(File.Exists(target)) File.Delete(target);File.Move(temporary,target);return;}
            catch(IOException) {if(attempt==9) throw;System.Threading.Thread.Sleep(20);}
            catch(UnauthorizedAccessException) {
                // Windows/SMB readers may temporarily deny deleting state.json.
                // Retry only publication; permanent denial remains a fatal error.
                if(attempt==9) throw;System.Threading.Thread.Sleep(20);
            }
        }
    }
    public void Dispose() {
        if(disposed) return;disposed=true;timer.Stop();timer.Dispose();
        if(registered) {
            bool removed=Simple(2,IdentityFlags,false);
            report["cleanup_delete_accepted"]=removed;
            var bounds=Bounds(useGuid);report["cleanup_bounds"]=bounds;
            registered=!removed && Convert.ToInt32(bounds["status"])==0;
        }
        if(popup!=null) {popup.Close();popup.Dispose();popup=null;}
        if(foregroundWindow!=null) {foregroundWindow.Close();foregroundWindow.Dispose();foregroundWindow=null;}
        if(icon!=IntPtr.Zero) {Native.DestroyIcon(icon);icon=IntPtr.Zero;}
        report["stage"]="closed";Publish();DestroyHandle();
    }
    [STAThread] internal static void Main(string[] args) {
        try {
            if(args.Length!=2) throw new ArgumentException("output-dir config-json");
            var json=new JavaScriptSerializer();
            var config=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[1]));
            using(var provider=new Provider(args[0],config)) Application.Run();
        } catch(Exception error) {
            if(args.Length==2) File.WriteAllText(Path.Combine(args[0],"failure.json"),
                "{\"error_type\":\""+error.GetType().Name+"\"}",new UTF8Encoding(false));
            Environment.ExitCode=1;
        }
    }
}

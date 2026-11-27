using System;
using System.Runtime.InteropServices;

internal sealed class WindowBranding : IDisposable
{
    private const string AppId = "Codingmosture.SpeakSpot.Desktop";
    private const uint GetIcon = 0x007F;
    private const uint SetIcon = 0x0080;
    private const uint LoadFromFile = 0x0010;
    private const uint AbortIfHung = 0x0002;
    private static readonly Guid AppModelProperties = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");

    private readonly string iconPath;
    private readonly string launcherPath;
    private IntPtr window;
    private IntPtr smallIcon;
    private IntPtr taskbarIcon;
    private uint windowDpi;

    public WindowBranding(string iconPath, string launcherPath)
    {
        this.iconPath = iconPath;
        this.launcherPath = launcherPath;
    }

    public void Update(IntPtr handle)
    {
        if (handle == IntPtr.Zero)
            return;
        if (handle != window)
        {
            ReleaseIcons();
            window = handle;
            windowDpi = 0;
            SetTaskbarIdentity();
        }

        uint dpi = GetDpiForWindow(window);
        if (dpi == 0)
            return;
        if (dpi != windowDpi)
        {
            ReleaseIcons();
            windowDpi = dpi;
            smallIcon = LoadIcon((int)((16 * dpi + 48) / 96));
            taskbarIcon = LoadIcon((int)((24 * dpi + 48) / 96));
        }

        // Chrome may update its own window icons after navigation or a DPI change.
        ApplyIcon(0, smallIcon);
        ApplyIcon(1, taskbarIcon);
    }

    private void SetTaskbarIdentity()
    {
        Guid iid = typeof(IPropertyStore).GUID;
        IPropertyStore store;
        if (SHGetPropertyStoreForWindow(window, ref iid, out store) < 0)
            return;
        try
        {
            // Set relaunch properties before the app ID so a pin starts the launcher.
            SetProperty(store, 2, "\"" + launcherPath + "\"");
            SetProperty(store, 3, iconPath + ",0");
            SetProperty(store, 4, "SpeakSpot");
            SetProperty(store, 5, AppId);
        }
        finally
        {
            Marshal.ReleaseComObject(store);
        }
    }

    private static void SetProperty(IPropertyStore store, uint id, string text)
    {
        var key = new PropertyKey { FormatId = AppModelProperties, Id = id };
        var value = new PropertyVariant { Type = 31, Text = Marshal.StringToCoTaskMemUni(text) }; // VT_LPWSTR
        try { store.SetValue(ref key, ref value); }
        finally { Marshal.FreeCoTaskMem(value.Text); }
    }

    private IntPtr LoadIcon(int size)
    {
        const uint imageIcon = 1;
        return LoadImage(IntPtr.Zero, iconPath, imageIcon, size, size, LoadFromFile);
    }

    private void ApplyIcon(int kind, IntPtr icon)
    {
        if (icon == IntPtr.Zero)
            return;
        IntPtr current;
        if (SendIconMessage(GetIcon, kind, IntPtr.Zero, out current) && current != icon)
            SendIconMessage(SetIcon, kind, icon, out current);
    }

    private bool SendIconMessage(uint message, int kind, IntPtr value, out IntPtr previous)
    {
        return SendMessageTimeout(window, message, new IntPtr(kind), value, AbortIfHung, 100, out previous) != IntPtr.Zero;
    }

    private void ReleaseIcon(int kind, ref IntPtr icon)
    {
        if (icon == IntPtr.Zero)
            return;
        IntPtr current;
        if (SendIconMessage(GetIcon, kind, IntPtr.Zero, out current) && current == icon)
            SendIconMessage(SetIcon, kind, IntPtr.Zero, out current);
        DestroyIcon(icon);
        icon = IntPtr.Zero;
    }

    private void ReleaseIcons()
    {
        ReleaseIcon(0, ref smallIcon);
        ReleaseIcon(1, ref taskbarIcon);
    }

    public void Dispose()
    {
        ReleaseIcons();
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct PropertyKey
    {
        public Guid FormatId;
        public uint Id;
    }

    // The launcher is built for x64, where PROPVARIANT has room for a 16-byte union.
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    private struct PropertyVariant
    {
        [FieldOffset(0)] public ushort Type;
        [FieldOffset(8)] public IntPtr Text;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore
    {
        [PreserveSig] int GetCount(out uint count);
        [PreserveSig] int GetAt(uint index, out PropertyKey key);
        [PreserveSig] int GetValue(ref PropertyKey key, out PropertyVariant value);
        [PreserveSig] int SetValue(ref PropertyKey key, ref PropertyVariant value);
        [PreserveSig] int Commit();
    }

    [DllImport("shell32.dll")]
    private static extern int SHGetPropertyStoreForWindow(IntPtr window, ref Guid iid, out IPropertyStore store);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr window);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr LoadImage(IntPtr module, string path, uint type, int width, int height, uint flags);

    [DllImport("user32.dll")]
    private static extern bool DestroyIcon(IntPtr icon);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr SendMessageTimeout(IntPtr window, uint message, IntPtr wParam,
        IntPtr lParam, uint flags, uint timeout, out IntPtr result);
}

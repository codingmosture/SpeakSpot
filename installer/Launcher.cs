using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

internal static class Launcher
{
    private const string Origin = "http://127.0.0.1:27395";
    private const string Instance = @"Global\SpeakSpotDesktop-27395";
    private static readonly string AppDir = AppDomain.CurrentDomain.BaseDirectory;

    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr window, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    [STAThread]
    private static int Main(string[] args)
    {
        string chrome = FindChrome();
        if (args.Length == 1 && args[0] == "--check-chrome")
            return chrome == null ? 1 : 0;

        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string data = Path.Combine(local, "SpeakSpotData");
        try
        {
            if (chrome == null)
                throw new InvalidOperationException("Google Chrome을 찾지 못했습니다. SpeakSpot 설치 프로그램을 다시 실행해 주세요.");

            using (var activate = new EventWaitHandle(false, EventResetMode.AutoReset, Instance + "-Activate"))
            using (var instance = new Mutex(false, Instance))
            {
                if (!Acquire(instance))
                {
                    activate.Set();
                    return 0;
                }
                try { RunApp(chrome, data, activate); }
                finally { instance.ReleaseMutex(); }
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message + "\n\n로그: " + Path.Combine(data, "Logs", "backend.log"),
                "SpeakSpot", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static bool Acquire(Mutex instance)
    {
        try { return instance.WaitOne(0); }
        catch (AbandonedMutexException) { return true; }
    }

    private static string FindChrome()
    {
        const string appPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe";
        foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        {
            foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                using (var root = RegistryKey.OpenBaseKey(hive, view))
                using (var key = root.OpenSubKey(appPath))
                {
                    var path = key == null ? null : key.GetValue(null) as string;
                    if (!String.IsNullOrWhiteSpace(path) && File.Exists(path.Trim('"')))
                        return path.Trim('"');
                }
            }
        }

        foreach (var folder in new[] { Environment.SpecialFolder.ProgramFiles,
            Environment.SpecialFolder.ProgramFilesX86, Environment.SpecialFolder.LocalApplicationData })
        {
            var path = Path.Combine(Environment.GetFolderPath(folder), @"Google\Chrome\Application\chrome.exe");
            if (File.Exists(path))
                return path;
        }
        return null;
    }

    private static void RunApp(string chrome, string data, EventWaitHandle activate)
    {
        var profile = Path.Combine(data, "Documents", "Chrome");
        Directory.CreateDirectory(profile);
        var token = Guid.NewGuid().ToString("N");
        var pid = Process.GetCurrentProcess().Id.ToString(CultureInfo.InvariantCulture);
        var python = new ProcessStartInfo(Path.Combine(AppDir, "python", "python.exe"),
            "-B " + Quoted(Path.Combine(AppDir, "backend", "installed.py")) +
            " serve " + Quoted(data) + " --parent " + pid);
        python.WorkingDirectory = AppDir;
        python.UseShellExecute = false;
        python.CreateNoWindow = true;
        python.EnvironmentVariables["SPEAKSPOT_LAUNCH_TOKEN"] = token;

        using (var backend = Process.Start(python))
        {
            try
            {
                WaitForBackend(backend, token);
                var browserStart = new ProcessStartInfo(chrome,
                    "--user-data-dir=" + Quoted(profile) + " --no-first-run --no-default-browser-check" +
                    " --disable-background-mode --app=" + Origin + "/");
                browserStart.UseShellExecute = false;
                using (var browser = Process.Start(browserStart))
                using (var branding = new WindowBranding(Path.Combine(AppDir, "SpeakSpot.ico"), Path.Combine(AppDir, "SpeakSpot.exe")))
                {
                    while (!browser.WaitForExit(250))
                    {
                        if (backend.HasExited)
                            throw new InvalidOperationException("SpeakSpot 백엔드가 종료되었습니다. 앱 창을 닫은 뒤 다시 실행해 주세요.");
                        browser.Refresh();
                        var window = browser.MainWindowHandle;
                        branding.Update(window);
                        if (activate.WaitOne(0) && window != IntPtr.Zero)
                        {
                            ShowWindowAsync(window, 9); // SW_RESTORE
                            SetForegroundWindow(window);
                        }
                    }
                }
            }
            finally
            {
                if (!backend.HasExited)
                {
                    Control(token, "POST");
                    if (!backend.WaitForExit(4000))
                    {
                        try { backend.Kill(); }
                        catch (InvalidOperationException) { }
                        backend.WaitForExit();
                    }
                }
            }
        }
    }

    private static void WaitForBackend(Process backend, string token)
    {
        var timer = Stopwatch.StartNew();
        while (timer.Elapsed < TimeSpan.FromSeconds(45))
        {
            if (backend.HasExited)
                break;
            if (Control(token, "GET"))
                return;
            Thread.Sleep(150);
        }
        throw new InvalidOperationException("SpeakSpot 백엔드를 시작하지 못했습니다. 앱을 다시 실행하거나 로그를 확인해 주세요.");
    }

    private static bool Control(string token, string method)
    {
        var request = (HttpWebRequest)WebRequest.Create(Origin + "/_speakspot/control");
        request.Method = method;
        request.Timeout = 500;
        request.Proxy = null;
        request.Headers.Add("X-SpeakSpot-Launch-Token", token);
        if (method == "POST")
            request.ContentLength = 0;
        try
        {
            using (var response = (HttpWebResponse)request.GetResponse())
                return response.StatusCode == HttpStatusCode.NoContent;
        }
        catch (WebException error)
        {
            if (error.Response != null)
                error.Response.Dispose();
            return false;
        }
    }

    private static string Quoted(string path)
    {
        return "\"" + path + "\"";
    }
}

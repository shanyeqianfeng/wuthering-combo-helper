using System;
using System.IO;
using System.Diagnostics;
using System.Windows.Forms;

class Launcher
{
    [STAThread]
    static void Main(string[] args)
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        string script = Path.Combine(baseDir, @"program\combo_overlay.py");
        string dataDir = Path.Combine(baseDir, "方案");

        if (!File.Exists(script))
        {
            MessageBox.Show("找不到程序文件：\n" + script + "\n\n请确认 program 文件夹没有被移动或删除。",
                "鸣潮连招助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        if (!Directory.Exists(dataDir))
        {
            Directory.CreateDirectory(dataDir);
        }

        string py = FindPython();
        if (py == null)
        {
            MessageBox.Show("没有找到 Python 3。\n\n请先安装 Python 3.10 以上版本（安装时勾选 Add to PATH），再运行本程序。",
                "鸣潮连招助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        try
        {
            ProcessStartInfo psi = new ProcessStartInfo(py);
            psi.Arguments = "\"" + script + "\"";
            psi.WorkingDirectory = baseDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["COMBO_DATA_DIR"] = dataDir;
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show("启动失败：\n" + ex.Message, "鸣潮连招助手",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static string FindPython()
    {
        // 1) 常见安装位置（优先 pythonw，不弹黑框）
        string local = Environment.GetEnvironmentVariable("LOCALAPPDATA");
        string[] candidates = new string[]
        {
            Path.Combine(local, @"Programs\Python\Python313\pythonw.exe"),
            Path.Combine(local, @"Programs\Python\Python312\pythonw.exe"),
            Path.Combine(local, @"Programs\Python\Python311\pythonw.exe"),
            Path.Combine(local, @"Programs\Python\Python310\pythonw.exe"),
            @"C:\Python313\pythonw.exe",
            @"C:\Python312\pythonw.exe",
            @"C:\Python311\pythonw.exe",
        };
        foreach (string p in candidates)
        {
            if (File.Exists(p)) return p;
        }

        // 2) PATH 里找 pythonw / python
        string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string dir in pathEnv.Split(';'))
        {
            if (dir.Trim().Length == 0) continue;
            foreach (string name in new string[] { "pythonw.exe", "python.exe" })
            {
                try
                {
                    string p = Path.Combine(dir.Trim(), name);
                    if (File.Exists(p)) return p;
                }
                catch { }
            }
        }

        // 3) py 启动器
        foreach (string dir in pathEnv.Split(';'))
        {
            if (dir.Trim().Length == 0) continue;
            try
            {
                string p = Path.Combine(dir.Trim(), "pyw.exe");
                if (File.Exists(p)) return p;
            }
            catch { }
        }
        return null;
    }
}

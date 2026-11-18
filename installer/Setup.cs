using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using Microsoft.Win32;

namespace BedTubeSetup
{
    public class SliceStream : Stream
    {
        private readonly Stream _inner;
        private readonly long _offset;
        private readonly long _length;
        private long _pos;

        public SliceStream(Stream inner, long offset, long length)
        {
            _inner = inner;
            _offset = offset;
            _length = length;
            _pos = 0;
            _inner.Position = _offset;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return _length; } }

        public override long Position
        {
            get { return _pos; }
            set { Seek(value, SeekOrigin.Begin); }
        }

        public override void Flush() { }

        public override int Read(byte[] buffer, int offset, int count)
        {
            long remaining = _length - _pos;
            if (remaining <= 0) return 0;
            if (count > remaining) count = (int)remaining;
            _inner.Position = _offset + _pos;
            int read = _inner.Read(buffer, offset, count);
            _pos += read;
            return read;
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            long target = _pos;
            if (origin == SeekOrigin.Begin) target = offset;
            else if (origin == SeekOrigin.Current) target = _pos + offset;
            else if (origin == SeekOrigin.End) target = _length + offset;

            if (target < 0) target = 0;
            if (target > _length) target = _length;
            _pos = target;
            _inner.Position = _offset + _pos;
            return _pos;
        }

        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }

        protected override void Dispose(bool disposing)
        {
            if (disposing) _inner.Dispose();
            base.Dispose(disposing);
        }
    }

    public class InstallOptions
    {
        public string InstallDir;
        public bool DesktopShortcut = true;
        public bool StartMenuShortcut = true;
        public bool Autostart = true;
        public bool LaunchAfter = true;
    }

    public static class SetupEngine
    {
        public const string APP_VERSION = "0.0.2";
        public const string MAGIC = "BTSETUP1";

        public static bool LocatePayload(string exePath, out long zipOffset, out long zipLength)
        {
            zipOffset = 0;
            zipLength = 0;
            using (FileStream fs = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (fs.Length < 32) return false;
                fs.Seek(-16, SeekOrigin.End);
                byte[] footer = new byte[16];
                int r = fs.Read(footer, 0, 16);
                if (r != 16) return false;
                string magic = Encoding.ASCII.GetString(footer, 8, 8);
                if (magic != MAGIC) return false;
                zipOffset = BitConverter.ToInt64(footer, 0);
                zipLength = (fs.Length - 16) - zipOffset;
                return zipOffset > 0 && zipLength > 0;
            }
        }

        public static void StopExistingApp(string targetDir)
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:5050/api/local/shutdown");
                req.Method = "POST";
                req.Timeout = 1500;
                req.ContentLength = 0;
                using (WebResponse resp = req.GetResponse()) { }
                System.Threading.Thread.Sleep(900);
            }
            catch { }

            string[] names = new string[] { "BedTube", "cloudflared", "electron" };
            foreach (string n in names)
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(n))
                    {
                        try
                        {
                            bool shouldKill = (n == "BedTube");
                            if (!shouldKill)
                            {
                                string fn = p.MainModule.FileName;
                                shouldKill = fn != null && fn.StartsWith(targetDir, StringComparison.OrdinalIgnoreCase);
                            }
                            if (shouldKill)
                            {
                                p.Kill();
                                p.WaitForExit(2500);
                            }
                        }
                        catch { }
                    }
                }
                catch { }
            }
        }

        public static void CreateShortcut(string lnkPath, string targetExe, string workDir, string description)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnkPath });
                Type scType = shortcut.GetType();
                scType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe });
                scType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workDir });
                scType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetExe + ",0" });
                scType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { description });
                scType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
            }
            catch { }
        }

        public static void PerformInstall(string selfExe, InstallOptions opts, Action<int, string> onProgress)
        {
            long zipOffset, zipLength;
            if (!LocatePayload(selfExe, out zipOffset, out zipLength))
            {
                throw new InvalidOperationException("Pacote embutido não encontrado no instalador.");
            }

            if (onProgress != null) onProgress(2, "Preparando pasta de destino...");
            StopExistingApp(opts.InstallDir);
            Directory.CreateDirectory(opts.InstallDir);

            long totalUncompressed = 0;
            long extractedBytes = 0;

            using (FileStream fs = new FileStream(selfExe, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (SliceStream slice = new SliceStream(fs, zipOffset, zipLength))
            using (ZipArchive archive = new ZipArchive(slice, ZipArchiveMode.Read))
            {
                foreach (ZipArchiveEntry e in archive.Entries)
                {
                    totalUncompressed += e.Length;
                }
                if (totalUncompressed <= 0) totalUncompressed = 1;

                byte[] buffer = new byte[131072];
                int fileIdx = 0;
                int totalFiles = archive.Entries.Count;

                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    fileIdx++;
                    string relPath = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string destPath = Path.Combine(opts.InstallDir, relPath);

                    if (string.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(destPath);
                        continue;
                    }

                    string parent = Path.GetDirectoryName(destPath);
                    if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);

                    using (Stream inStream = entry.Open())
                    {
                        FileStream outStream = null;
                        for (int attempt = 0; attempt < 6; attempt++)
                        {
                            try
                            {
                                outStream = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None);
                                break;
                            }
                            catch (IOException)
                            {
                                if (attempt == 5) throw;
                                System.Threading.Thread.Sleep(300);
                            }
                        }
                        using (outStream)
                        {
                            int read;
                            while ((read = inStream.Read(buffer, 0, buffer.Length)) > 0)
                            {
                                outStream.Write(buffer, 0, read);
                                extractedBytes += read;
                            }
                        }
                    }

                    if (onProgress != null && (fileIdx % 3 == 0 || fileIdx == totalFiles))
                    {
                        int pct = 5 + (int)((extractedBytes * 88L) / totalUncompressed);
                        if (pct > 94) pct = 94;
                        double mbDone = extractedBytes / (1024.0 * 1024.0);
                        double mbTotal = totalUncompressed / (1024.0 * 1024.0);
                        onProgress(pct, string.Format("{0} ({1:0} / {2:0} MB)", entry.Name, mbDone, mbTotal));
                    }
                }
            }

            string mainExe = Path.Combine(opts.InstallDir, "BedTube.exe");
            string uninstallExe = Path.Combine(opts.InstallDir, "Uninstall-BedTube.exe");

            if (onProgress != null) onProgress(96, "Configurando atalhos e integração com o Windows...");

            // Remove atalho legado da pasta Startup para não duplicar instância
            try
            {
                string legacyStartupLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "BedTube Controller.lnk");
                if (File.Exists(legacyStartupLnk)) File.Delete(legacyStartupLnk);
            }
            catch { }

            if (opts.DesktopShortcut)
            {
                string deskLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "BedTube Controller.lnk");
                CreateShortcut(deskLnk, mainExe, opts.InstallDir, "BedTube Controller — YouTube TV 1080p/4K & Audio Switcher");
            }

            if (opts.StartMenuShortcut)
            {
                string startLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "BedTube Controller.lnk");
                CreateShortcut(startLnk, mainExe, opts.InstallDir, "BedTube Controller — YouTube TV 1080p/4K & Audio Switcher");
            }

            try
            {
                using (RegistryKey runKey = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"))
                {
                    if (runKey != null)
                    {
                        if (opts.Autostart)
                            runKey.SetValue("BedTubeController", string.Format("\"{0}\" --background", mainExe), RegistryValueKind.String);
                        else
                            runKey.DeleteValue("BedTubeController", false);
                    }
                }
            }
            catch { }

            try
            {
                using (RegistryKey unKey = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\BedTubeController"))
                {
                    if (unKey != null)
                    {
                        unKey.SetValue("DisplayName", "BedTube Controller", RegistryValueKind.String);
                        unKey.SetValue("DisplayVersion", APP_VERSION, RegistryValueKind.String);
                        unKey.SetValue("Publisher", "BedTube Open Source", RegistryValueKind.String);
                        unKey.SetValue("DisplayIcon", mainExe, RegistryValueKind.String);
                        unKey.SetValue("InstallLocation", opts.InstallDir, RegistryValueKind.String);
                        unKey.SetValue("UninstallString", string.Format("\"{0}\"", uninstallExe), RegistryValueKind.String);
                        unKey.SetValue("QuietUninstallString", string.Format("\"{0}\" --silent", uninstallExe), RegistryValueKind.String);
                        unKey.SetValue("NoModify", 1, RegistryValueKind.DWord);
                        unKey.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                        unKey.SetValue("EstimatedSize", (int)(totalUncompressed / 1024L), RegistryValueKind.DWord);
                    }
                }
            }
            catch { }

            if (onProgress != null) onProgress(100, "Concluído!");
        }
    }

    public class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            bool silent = false;
            bool noLaunch = false;
            string customDir = null;

            for (int i = 0; i < args.Length; i++)
            {
                if (string.Equals(args[i], "--silent", StringComparison.OrdinalIgnoreCase)) silent = true;
                else if (string.Equals(args[i], "--no-launch", StringComparison.OrdinalIgnoreCase)) noLaunch = true;
                else if (string.Equals(args[i], "--dir", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    customDir = args[++i];
                }
            }

            string defaultDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                "BedTube Controller"
            );

            string selfExe = Assembly.GetExecutingAssembly().Location;

            if (silent)
            {
                InstallOptions opts = new InstallOptions
                {
                    InstallDir = string.IsNullOrEmpty(customDir) ? defaultDir : customDir,
                    DesktopShortcut = true,
                    StartMenuShortcut = true,
                    Autostart = true,
                    LaunchAfter = !noLaunch
                };
                SetupEngine.PerformInstall(selfExe, opts, null);
                if (opts.LaunchAfter)
                {
                    string mainExe = Path.Combine(opts.InstallDir, "BedTube.exe");
                    if (File.Exists(mainExe))
                    {
                        Process.Start(new ProcessStartInfo { FileName = mainExe, Arguments = "--show-qr", WorkingDirectory = opts.InstallDir });
                    }
                }
                return 0;
            }

            System.Windows.Application app = new System.Windows.Application();
            SetupWindow win = new SetupWindow(selfExe, string.IsNullOrEmpty(customDir) ? defaultDir : customDir);
            app.Run(win);
            return 0;
        }
    }

    public class SetupWindow : Window
    {
        private readonly string _selfExe;
        private string _lang;

        private Grid _pageWelcome;
        private Grid _pageProgress;
        private Grid _pageFinish;

        private TextBlock _lblSubtitle;
        private TextBlock _lblFeat1, _lblFeat2, _lblFeat3;
        private TextBlock _lblFolderCap;
        private System.Windows.Controls.TextBox _txtDir;
        private System.Windows.Controls.Button _btnBrowse;
        private System.Windows.Controls.CheckBox _chkDesktop;
        private System.Windows.Controls.CheckBox _chkStartMenu;
        private System.Windows.Controls.CheckBox _chkAutostart;
        private System.Windows.Controls.CheckBox _chkLaunchNow;
        private System.Windows.Controls.Button _btnInstall;

        private TextBlock _lblInstallingTitle;
        private TextBlock _lblProgressStep;
        private TextBlock _lblProgressPct;
        private Border _progressFill;
        private double _progressBarMaxWidth = 524;

        private TextBlock _lblDoneTitle;
        private TextBlock _lblDoneDesc;
        private TextBlock _lblInstalledAt;
        private System.Windows.Controls.Button _btnFinish;

        private readonly Dictionary<string, System.Windows.Controls.Button> _langButtons = new Dictionary<string, System.Windows.Controls.Button>();

        private static readonly Dictionary<string, Dictionary<string, string>> L = new Dictionary<string, Dictionary<string, string>>
        {
            { "pt", new Dictionary<string, string> {
                { "sub", "Transforme seu PC em uma Smart TV de cama com controle pelo celular" },
                { "f1", "🎬 YouTube TV 1080p60 / 4K" },
                { "f2", "🔊 Troca Auto Fone ↔ Soundbar" },
                { "f3", "☀️ Ligar/Apagar Tela & Desligar PC" },
                { "folder", "Pasta de instalação (não requer Administrador):" },
                { "browse", "Procurar..." },
                { "c_desk", "Criar atalho na Área de Trabalho" },
                { "c_menu", "Adicionar ao Menu Iniciar do Windows" },
                { "c_auto", "Iniciar silenciosamente junto com o Windows (Bandeja)" },
                { "c_open", "Abrir BedTube e exibir QR Code do celular ao concluir" },
                { "install", "Instalar BedTube Controller v0.0.2" },
                { "inst_title", "Instalando BedTube Controller..." },
                { "inst_wait", "Extraindo motor YouTube TV 1080p/4K e módulos de áudio..." },
                { "done_title", "Instalação Concluída com Sucesso!" },
                { "done_desc", "O BedTube Controller v0.0.2 está pronto. Escaneie o QR Code com a câmera do seu celular para controlar a TV, tela e desligamento do PC direto da cama." },
                { "finish", "Concluir e Abrir BedTube" }
            }},
            { "en", new Dictionary<string, string> {
                { "sub", "Turn your Windows PC into a Bedside Smart TV controlled from your phone" },
                { "f1", "🎬 YouTube TV 1080p60 / 4K" },
                { "f2", "🔊 Auto Headset ↔ Soundbar" },
                { "f3", "☀️ Screen Wake/Off & PC Shutdown" },
                { "folder", "Installation folder (no Admin rights required):" },
                { "browse", "Browse..." },
                { "c_desk", "Create Desktop shortcut" },
                { "c_menu", "Create Start Menu shortcut" },
                { "c_auto", "Start silently with Windows (System Tray)" },
                { "c_open", "Launch BedTube & show Phone QR Code when finished" },
                { "install", "Install BedTube Controller v0.0.2" },
                { "inst_title", "Installing BedTube Controller..." },
                { "inst_wait", "Extracting 1080p/4K YouTube TV engine and CoreAudio modules..." },
                { "done_title", "Installation Completed Successfully!" },
                { "done_desc", "BedTube Controller v0.0.2 is ready. Scan the QR Code with your phone camera to control YouTube TV, monitor power, and PC shutdown right from bed." },
                { "finish", "Finish & Launch BedTube" }
            }},
            { "es", new Dictionary<string, string> {
                { "sub", "Convierte tu PC en un Smart TV de dormitorio controlado desde tu móvil" },
                { "f1", "🎬 YouTube TV 1080p60 / 4K" },
                { "f2", "🔊 Cambio Auto Auriculares ↔ TV" },
                { "f3", "☀️ Pantalla y Apagar PC" },
                { "folder", "Carpeta de instalación (no requiere Administrador):" },
                { "browse", "Examinar..." },
                { "c_desk", "Crear acceso directo en el Escritorio" },
                { "c_menu", "Crear acceso directo en el Menú Inicio" },
                { "c_auto", "Iniciar silenciosamente con Windows (Bandeja)" },
                { "c_open", "Abrir BedTube y mostrar código QR al finalizar" },
                { "install", "Instalar BedTube Controller v0.0.2" },
                { "inst_title", "Instalando BedTube Controller..." },
                { "inst_wait", "Extrayendo motor YouTube TV 1080p/4K y módulos de audio..." },
                { "done_title", "¡Instalación Completada con Éxito!" },
                { "done_desc", "BedTube Controller v0.0.2 está listo. Escanea el código QR con tu celular para controlar YouTube TV, pantalla y apagado del PC desde la cama." },
                { "finish", "Finalizar y Abrir BedTube" }
            }}
        };

        public SetupWindow(string selfExe, string defaultDir)
        {
            _selfExe = selfExe;
            string iso = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();
            _lang = L.ContainsKey(iso) ? iso : "pt";

            Width = 620;
            Height = 520;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            ResizeMode = ResizeMode.NoResize;
            Title = "BedTube Controller v0.0.2 Setup";

            Border card = new Border();
            card.Margin = new Thickness(16);
            card.CornerRadius = new CornerRadius(22);
            card.Background = BrushHex("#0B0B12");
            card.BorderBrush = BrushHex("#262638");
            card.BorderThickness = new Thickness(1);
            card.Effect = new DropShadowEffect { BlurRadius = 28, ShadowDepth = 8, Opacity = 0.72, Color = Colors.Black };
            card.MouseLeftButtonDown += delegate(object s, MouseButtonEventArgs e)
            {
                if (e.ButtonState == MouseButtonState.Pressed) DragMove();
            };

            Grid mainGrid = new Grid { Margin = new Thickness(28, 22, 28, 24) };
            mainGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            mainGrid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

            mainGrid.Children.Add(BuildHeader());

            Grid bodyHost = new Grid { Margin = new Thickness(0, 16, 0, 0) };
            Grid.SetRow(bodyHost, 1);

            _pageWelcome = BuildWelcomePage(defaultDir);
            _pageProgress = BuildProgressPage();
            _pageFinish = BuildFinishPage();

            _pageProgress.Visibility = Visibility.Collapsed;
            _pageFinish.Visibility = Visibility.Collapsed;

            bodyHost.Children.Add(_pageWelcome);
            bodyHost.Children.Add(_pageProgress);
            bodyHost.Children.Add(_pageFinish);

            mainGrid.Children.Add(bodyHost);
            card.Child = mainGrid;
            Content = card;

            ApplyLanguage();
        }

        private UIElement BuildHeader()
        {
            DockPanel header = new DockPanel();

            StackPanel rightControls = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Top };
            DockPanel.SetDock(rightControls, Dock.Right);

            foreach (string code in new string[] { "pt", "en", "es" })
            {
                string c = code;
                System.Windows.Controls.Button b = MakeButton(c.ToUpperInvariant(), "#171724", "#A1A1B5", 34, 26, 8);
                b.FontSize = 11;
                b.Margin = new Thickness(4, 0, 0, 0);
                b.Click += delegate { _lang = c; ApplyLanguage(); };
                _langButtons[c] = b;
                rightControls.Children.Add(b);
            }

            System.Windows.Controls.Button btnClose = MakeButton("✕", "#171724", "#9CA3AF", 30, 26, 8);
            btnClose.Margin = new Thickness(10, 0, 0, 0);
            btnClose.Click += delegate { Close(); };
            rightControls.Children.Add(btnClose);
            header.Children.Add(rightControls);

            StackPanel brand = new StackPanel { Orientation = Orientation.Horizontal };

            // Ícone vetorial BedTube (TV OLED + Play + Soundbar — Indigo-Violet v0.0.2)
            Border logoBox = new Border
            {
                Width = 48,
                Height = 48,
                CornerRadius = new CornerRadius(14),
                Background = BrushHex("#12121F"),
                BorderBrush = BrushHex("#6366F1"),
                BorderThickness = new Thickness(2),
                Margin = new Thickness(0, 0, 14, 0)
            };
            Canvas cv = new Canvas { Width = 44, Height = 44 };
            Border tvRect = new Border
            {
                Width = 28,
                Height = 19,
                CornerRadius = new CornerRadius(4),
                Background = BrushHex("#5B5BF0")
            };
            Canvas.SetLeft(tvRect, 8);
            Canvas.SetTop(tvRect, 8);
            System.Windows.Shapes.Polygon playTri = new System.Windows.Shapes.Polygon
            {
                Points = new PointCollection { new Point(19, 13), new Point(19, 22), new Point(27, 17.5) },
                Fill = Brushes.White
            };
            Border sbRect = new Border
            {
                Width = 22,
                Height = 3.5,
                CornerRadius = new CornerRadius(2),
                Background = BrushHex("#10B981")
            };
            Canvas.SetLeft(sbRect, 11);
            Canvas.SetTop(sbRect, 31);
            cv.Children.Add(tvRect);
            cv.Children.Add(playTri);
            cv.Children.Add(sbRect);
            logoBox.Child = cv;
            brand.Children.Add(logoBox);

            StackPanel titles = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            StackPanel titleRow = new StackPanel { Orientation = Orientation.Horizontal };
            titleRow.Children.Add(new TextBlock
            {
                Text = "BedTube Controller",
                FontSize = 21,
                FontWeight = FontWeights.Bold,
                Foreground = BrushHex("#F4F4F6")
            });
            Border verBadge = new Border
            {
                CornerRadius = new CornerRadius(999),
                Background = BrushHex("#191936"),
                BorderBrush = BrushHex("#6366F1"),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(8, 2, 8, 2),
                Margin = new Thickness(10, 3, 0, 0),
                VerticalAlignment = VerticalAlignment.Center
            };
            verBadge.Child = new TextBlock { Text = "v0.0.2", FontSize = 11, FontWeight = FontWeights.Bold, Foreground = BrushHex("#A5B4FC") };
            titleRow.Children.Add(verBadge);
            titles.Children.Add(titleRow);

            _lblSubtitle = new TextBlock
            {
                FontSize = 12.5,
                Foreground = BrushHex("#9CA3AF"),
                Margin = new Thickness(0, 3, 0, 0)
            };
            titles.Children.Add(_lblSubtitle);
            brand.Children.Add(titles);
            header.Children.Add(brand);

            return header;
        }

        private Grid BuildWelcomePage(string defaultDir)
        {
            Grid g = new Grid();
            g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            g.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            // Feature pills
            Grid pills = new Grid { Margin = new Thickness(0, 0, 0, 16) };
            pills.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            pills.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(8) });
            pills.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            pills.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(8) });
            pills.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

            _lblFeat1 = MakeFeaturePill(pills, 0);
            _lblFeat2 = MakeFeaturePill(pills, 2);
            _lblFeat3 = MakeFeaturePill(pills, 4);
            Grid.SetRow(pills, 0);
            g.Children.Add(pills);

            // Install folder picker
            StackPanel dirBox = new StackPanel { Margin = new Thickness(0, 0, 0, 16) };
            _lblFolderCap = new TextBlock { FontSize = 12, FontWeight = FontWeights.SemiBold, Foreground = BrushHex("#A1A1B5"), Margin = new Thickness(0, 0, 0, 6) };
            dirBox.Children.Add(_lblFolderCap);

            Grid dirRow = new Grid();
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(8) });
            dirRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            Border txtBorder = new Border
            {
                CornerRadius = new CornerRadius(11),
                Background = BrushHex("#13131E"),
                BorderBrush = BrushHex("#28283C"),
                BorderThickness = new Thickness(1),
                Height = 38,
                Padding = new Thickness(10, 0, 10, 0)
            };
            _txtDir = new System.Windows.Controls.TextBox
            {
                Text = defaultDir,
                Background = Brushes.Transparent,
                BorderThickness = new Thickness(0),
                Foreground = BrushHex("#E4E4ED"),
                FontSize = 12.5,
                VerticalContentAlignment = VerticalAlignment.Center
            };
            txtBorder.Child = _txtDir;
            Grid.SetColumn(txtBorder, 0);
            dirRow.Children.Add(txtBorder);

            _btnBrowse = MakeButton("...", "#1D1D2C", "#F4F4F6", 104, 38, 11);
            _btnBrowse.FontSize = 12.5;
            _btnBrowse.Click += OnBrowseClick;
            Grid.SetColumn(_btnBrowse, 2);
            dirRow.Children.Add(_btnBrowse);

            dirBox.Children.Add(dirRow);
            Grid.SetRow(dirBox, 1);
            g.Children.Add(dirBox);

            // Checkboxes card
            Border optsCard = new Border
            {
                CornerRadius = new CornerRadius(15),
                Background = BrushHex("#12121C"),
                BorderBrush = BrushHex("#222233"),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(16, 13, 16, 13),
                Margin = new Thickness(0, 0, 0, 16)
            };
            StackPanel chks = new StackPanel();
            _chkDesktop = MakeCheck(true);
            _chkStartMenu = MakeCheck(true);
            _chkAutostart = MakeCheck(true);
            _chkLaunchNow = MakeCheck(true);
            chks.Children.Add(_chkDesktop);
            chks.Children.Add(_chkStartMenu);
            chks.Children.Add(_chkAutostart);
            chks.Children.Add(_chkLaunchNow);
            optsCard.Child = chks;
            Grid.SetRow(optsCard, 2);
            g.Children.Add(optsCard);

            // Primary CTA
            _btnInstall = MakeGradientButton("", "#3F37C9", "#5B5BF0", 48, 14);
            _btnInstall.FontSize = 15;
            _btnInstall.Click += OnInstallClick;
            Grid.SetRow(_btnInstall, 3);
            g.Children.Add(_btnInstall);

            return g;
        }

        private Grid BuildProgressPage()
        {
            Grid g = new Grid { VerticalAlignment = VerticalAlignment.Center };
            StackPanel sp = new StackPanel();

            _lblInstallingTitle = new TextBlock
            {
                FontSize = 19,
                FontWeight = FontWeights.Bold,
                Foreground = BrushHex("#F4F4F6"),
                Margin = new Thickness(0, 0, 0, 8)
            };
            sp.Children.Add(_lblInstallingTitle);

            _lblProgressStep = new TextBlock
            {
                FontSize = 13,
                Foreground = BrushHex("#9CA3AF"),
                Margin = new Thickness(0, 0, 0, 22),
                TextTrimming = TextTrimming.CharacterEllipsis
            };
            sp.Children.Add(_lblProgressStep);

            Border track = new Border
            {
                Height = 16,
                Width = _progressBarMaxWidth,
                HorizontalAlignment = HorizontalAlignment.Left,
                CornerRadius = new CornerRadius(8),
                Background = BrushHex("#181826"),
                BorderBrush = BrushHex("#28283E"),
                BorderThickness = new Thickness(1),
                ClipToBounds = true
            };
            _progressFill = new Border
            {
                Height = 16,
                Width = 0,
                HorizontalAlignment = HorizontalAlignment.Left,
                CornerRadius = new CornerRadius(8),
                Background = new LinearGradientBrush(
                    (Color)ColorConverter.ConvertFromString("#3F37C9"),
                    (Color)ColorConverter.ConvertFromString("#7C83FF"),
                    0.0)
            };
            track.Child = _progressFill;
            sp.Children.Add(track);

            _lblProgressPct = new TextBlock
            {
                Text = "0%",
                FontSize = 24,
                FontWeight = FontWeights.Bold,
                Foreground = BrushHex("#6EE7B7"),
                Margin = new Thickness(0, 14, 0, 0),
                HorizontalAlignment = HorizontalAlignment.Right
            };
            sp.Children.Add(_lblProgressPct);

            g.Children.Add(sp);
            return g;
        }

        private Grid BuildFinishPage()
        {
            Grid g = new Grid();
            g.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

            StackPanel center = new StackPanel { VerticalAlignment = VerticalAlignment.Center };

            Border okCircle = new Border
            {
                Width = 62,
                Height = 62,
                CornerRadius = new CornerRadius(31),
                Background = BrushHex("#0F2922"),
                BorderBrush = BrushHex("#10B981"),
                BorderThickness = new Thickness(2),
                HorizontalAlignment = HorizontalAlignment.Left,
                Margin = new Thickness(0, 0, 0, 16)
            };
            okCircle.Child = new TextBlock
            {
                Text = "✓",
                FontSize = 30,
                FontWeight = FontWeights.Bold,
                Foreground = BrushHex("#34D399"),
                HorizontalAlignment = HorizontalAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center
            };
            center.Children.Add(okCircle);

            _lblDoneTitle = new TextBlock
            {
                FontSize = 21,
                FontWeight = FontWeights.Bold,
                Foreground = BrushHex("#F4F4F6"),
                Margin = new Thickness(0, 0, 0, 10)
            };
            center.Children.Add(_lblDoneTitle);

            _lblDoneDesc = new TextBlock
            {
                FontSize = 13.5,
                Foreground = BrushHex("#A1A1B5"),
                TextWrapping = TextWrapping.Wrap,
                LineHeight = 20,
                Margin = new Thickness(0, 0, 0, 16)
            };
            center.Children.Add(_lblDoneDesc);

            Border pathBox = new Border
            {
                CornerRadius = new CornerRadius(10),
                Background = BrushHex("#13131E"),
                BorderBrush = BrushHex("#252538"),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(12, 8, 12, 8)
            };
            _lblInstalledAt = new TextBlock
            {
                FontSize = 12,
                FontFamily = new FontFamily("Consolas"),
                Foreground = BrushHex("#6EE7B7"),
                TextTrimming = TextTrimming.CharacterEllipsis
            };
            pathBox.Child = _lblInstalledAt;
            center.Children.Add(pathBox);

            Grid.SetRow(center, 0);
            g.Children.Add(center);

            _btnFinish = MakeGradientButton("", "#10B981", "#059669", 48, 14);
            _btnFinish.FontSize = 15;
            _btnFinish.Click += OnFinishClick;
            Grid.SetRow(_btnFinish, 1);
            g.Children.Add(_btnFinish);

            return g;
        }

        private void ApplyLanguage()
        {
            Dictionary<string, string> d = L[_lang];
            _lblSubtitle.Text = d["sub"];
            _lblFeat1.Text = d["f1"];
            _lblFeat2.Text = d["f2"];
            _lblFeat3.Text = d["f3"];
            _lblFolderCap.Text = d["folder"];
            _btnBrowse.Content = d["browse"];
            _chkDesktop.Content = "  " + d["c_desk"];
            _chkStartMenu.Content = "  " + d["c_menu"];
            _chkAutostart.Content = "  " + d["c_auto"];
            _chkLaunchNow.Content = "  " + d["c_open"];
            _btnInstall.Content = d["install"];
            _lblInstallingTitle.Text = d["inst_title"];
            if (_pageProgress.Visibility != Visibility.Visible)
                _lblProgressStep.Text = d["inst_wait"];
            _lblDoneTitle.Text = d["done_title"];
            _lblDoneDesc.Text = d["done_desc"];
            _btnFinish.Content = d["finish"];

            foreach (KeyValuePair<string, System.Windows.Controls.Button> kv in _langButtons)
            {
                bool active = kv.Key == _lang;
                kv.Value.Background = BrushHex(active ? "#252552" : "#171724");
                kv.Value.Foreground = BrushHex(active ? "#C7D2FE" : "#A1A1B5");
            }
        }

        private void OnBrowseClick(object sender, RoutedEventArgs e)
        {
            using (System.Windows.Forms.FolderBrowserDialog dlg = new System.Windows.Forms.FolderBrowserDialog())
            {
                dlg.SelectedPath = _txtDir.Text;
                if (dlg.ShowDialog() == System.Windows.Forms.DialogResult.OK)
                {
                    string picked = dlg.SelectedPath;
                    if (!picked.EndsWith("BedTube Controller", StringComparison.OrdinalIgnoreCase))
                        picked = System.IO.Path.Combine(picked, "BedTube Controller");
                    _txtDir.Text = picked;
                }
            }
        }

        private async void OnInstallClick(object sender, RoutedEventArgs e)
        {
            InstallOptions opts = new InstallOptions
            {
                InstallDir = _txtDir.Text.Trim(),
                DesktopShortcut = _chkDesktop.IsChecked == true,
                StartMenuShortcut = _chkStartMenu.IsChecked == true,
                Autostart = _chkAutostart.IsChecked == true,
                LaunchAfter = _chkLaunchNow.IsChecked == true
            };

            _pageWelcome.Visibility = Visibility.Collapsed;
            _pageProgress.Visibility = Visibility.Visible;

            try
            {
                await Task.Run(delegate
                {
                    SetupEngine.PerformInstall(_selfExe, opts, delegate(int pct, string msg)
                    {
                        Dispatcher.BeginInvoke(new Action(delegate
                        {
                            _lblProgressPct.Text = pct + "%";
                            _lblProgressStep.Text = msg;
                            _progressFill.Width = (_progressBarMaxWidth * pct) / 100.0;
                        }));
                    });
                });

                _lblInstalledAt.Text = opts.InstallDir;
                _pageProgress.Visibility = Visibility.Collapsed;
                _pageFinish.Visibility = Visibility.Visible;
            }
            catch (Exception ex)
            {
                System.Windows.MessageBox.Show("Erro durante a instalação:\n" + ex.Message, "BedTube Setup", MessageBoxButton.OK, MessageBoxImage.Error);
                _pageProgress.Visibility = Visibility.Collapsed;
                _pageWelcome.Visibility = Visibility.Visible;
            }
        }

        private void OnFinishClick(object sender, RoutedEventArgs e)
        {
            if (_chkLaunchNow.IsChecked == true)
            {
                try
                {
                    string exe = System.IO.Path.Combine(_txtDir.Text.Trim(), "BedTube.exe");
                    if (File.Exists(exe))
                    {
                        Process.Start(new ProcessStartInfo
                        {
                            FileName = exe,
                            Arguments = "--show-qr",
                            WorkingDirectory = _txtDir.Text.Trim()
                        });
                    }
                }
                catch { }
            }
            Close();
        }

        private static TextBlock MakeFeaturePill(Grid parent, int col)
        {
            Border b = new Border
            {
                CornerRadius = new CornerRadius(10),
                Background = BrushHex("#141421"),
                BorderBrush = BrushHex("#25253A"),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(8, 8, 8, 8)
            };
            TextBlock tb = new TextBlock
            {
                FontSize = 11.2,
                FontWeight = FontWeights.SemiBold,
                Foreground = BrushHex("#D4D4E0"),
                HorizontalAlignment = HorizontalAlignment.Center,
                TextTrimming = TextTrimming.CharacterEllipsis
            };
            b.Child = tb;
            Grid.SetColumn(b, col);
            parent.Children.Add(b);
            return tb;
        }

        private static System.Windows.Controls.CheckBox MakeCheck(bool isChecked)
        {
            return new System.Windows.Controls.CheckBox
            {
                IsChecked = isChecked,
                Foreground = BrushHex("#E4E4EE"),
                FontSize = 13,
                Margin = new Thickness(0, 5, 0, 5),
                Cursor = System.Windows.Input.Cursors.Hand
            };
        }

        private static SolidColorBrush BrushHex(string hex)
        {
            return (SolidColorBrush)new BrushConverter().ConvertFromString(hex);
        }

        private static System.Windows.Controls.Button MakeButton(string text, string bgHex, string fgHex, double w, double h, double radius)
        {
            System.Windows.Controls.Button b = new System.Windows.Controls.Button
            {
                Content = text,
                Width = w,
                Height = h,
                Background = BrushHex(bgHex),
                Foreground = BrushHex(fgHex),
                BorderThickness = new Thickness(0),
                FontWeight = FontWeights.SemiBold,
                Cursor = System.Windows.Input.Cursors.Hand
            };
            b.Template = BuildRoundedTemplate(radius);
            return b;
        }

        private static System.Windows.Controls.Button MakeGradientButton(string text, string c1, string c2, double h, double radius)
        {
            System.Windows.Controls.Button b = new System.Windows.Controls.Button
            {
                Content = text,
                Height = h,
                Foreground = Brushes.White,
                Background = new LinearGradientBrush(
                    (Color)ColorConverter.ConvertFromString(c1),
                    (Color)ColorConverter.ConvertFromString(c2),
                    0.0),
                BorderThickness = new Thickness(0),
                FontWeight = FontWeights.Bold,
                Cursor = System.Windows.Input.Cursors.Hand
            };
            b.Template = BuildRoundedTemplate(radius);
            return b;
        }

        private static ControlTemplate BuildRoundedTemplate(double radius)
        {
            FrameworkElementFactory border = new FrameworkElementFactory(typeof(Border));
            border.SetValue(Border.BackgroundProperty, new TemplateBindingExtension(System.Windows.Controls.Control.BackgroundProperty));
            border.SetValue(Border.CornerRadiusProperty, new CornerRadius(radius));
            FrameworkElementFactory cp = new FrameworkElementFactory(typeof(ContentPresenter));
            cp.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center);
            cp.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
            border.AppendChild(cp);
            ControlTemplate tpl = new ControlTemplate(typeof(System.Windows.Controls.Button));
            tpl.VisualTree = border;
            return tpl;
        }
    }
}

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using Microsoft.Win32;

namespace BedTubeUninstaller
{
    public class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            bool silent = false;
            foreach (string a in args)
            {
                if (string.Equals(a, "--silent", StringComparison.OrdinalIgnoreCase))
                    silent = true;
            }

            string installDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            if (silent)
            {
                UninstallEngine.RunUninstall(installDir, true);
                return 0;
            }

            Application app = new Application();
            UninstallWindow win = new UninstallWindow(installDir);
            app.Run(win);
            return 0;
        }
    }

    public static class UninstallEngine
    {
        public static void StopRunningInstances(string installDir)
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:5050/api/local/shutdown");
                req.Method = "POST";
                req.Timeout = 1200;
                req.ContentLength = 0;
                using (WebResponse resp = req.GetResponse()) { }
                System.Threading.Thread.Sleep(500);
            }
            catch { }

            string[] procNames = new string[] { "BedTube", "cloudflared", "electron" };
            foreach (string name in procNames)
            {
                try
                {
                    foreach (Process p in Process.GetProcessesByName(name))
                    {
                        try
                        {
                            string pPath = p.MainModule.FileName;
                            if (pPath != null && pPath.StartsWith(installDir, StringComparison.OrdinalIgnoreCase))
                            {
                                p.Kill();
                                p.WaitForExit(1500);
                            }
                        }
                        catch { }
                    }
                }
                catch { }
            }
        }

        public static void RunUninstall(string installDir, bool keepUserData)
        {
            StopRunningInstances(installDir);

            try
            {
                string desktopLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "BedTube Controller.lnk");
                if (File.Exists(desktopLnk)) File.Delete(desktopLnk);

                string startMenuLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "BedTube Controller.lnk");
                if (File.Exists(startMenuLnk)) File.Delete(startMenuLnk);

                string startupLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "BedTube Controller.lnk");
                if (File.Exists(startupLnk)) File.Delete(startupLnk);
            }
            catch { }

            try
            {
                using (RegistryKey runKey = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                {
                    if (runKey != null) runKey.DeleteValue("BedTubeController", false);
                }
                Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\BedTubeController", false);
            }
            catch { }

            if (!keepUserData)
            {
                try
                {
                    string appData = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "BedTubeController");
                    string localData = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BedTubeController");
                    string tvData = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BedTube_TV_Data");
                    if (Directory.Exists(appData)) Directory.Delete(appData, true);
                    if (Directory.Exists(localData)) Directory.Delete(localData, true);
                    if (Directory.Exists(tvData)) Directory.Delete(tvData, true);
                }
                catch { }
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "cmd.exe";
                psi.Arguments = string.Format("/C ping 127.0.0.1 -n 3 > nul & rmdir /s /q \"{0}\"", installDir);
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                Process.Start(psi);
            }
            catch { }
        }
    }

    public class UninstallWindow : Window
    {
        private string _installDir;
        private string _lang;
        private TextBlock _lblTitle;
        private TextBlock _lblDesc;
        private CheckBox _chkKeep;
        private Button _btnUninstall;
        private Button _btnCancel;

        private static readonly Dictionary<string, Dictionary<string, string>> T = new Dictionary<string, Dictionary<string, string>>
        {
            { "pt", new Dictionary<string, string> {
                { "title", "Desinstalar BedTube Controller" },
                { "desc", "Deseja remover o BedTube Controller v0.0.1 do seu computador?" },
                { "keep", "Manter minhas configurações de áudio e celulares pareados" },
                { "uninstall", "Desinstalar Agora" },
                { "cancel", "Cancelar" },
                { "working", "Removendo arquivos..." },
                { "done", "BedTube Controller foi removido com sucesso." }
            }},
            { "en", new Dictionary<string, string> {
                { "title", "Uninstall BedTube Controller" },
                { "desc", "Do you want to remove BedTube Controller v0.0.1 from your PC?" },
                { "keep", "Keep my audio settings and paired smartphones" },
                { "uninstall", "Uninstall Now" },
                { "cancel", "Cancel" },
                { "working", "Removing files..." },
                { "done", "BedTube Controller has been uninstalled." }
            }},
            { "es", new Dictionary<string, string> {
                { "title", "Desinstalar BedTube Controller" },
                { "desc", "¿Deseas eliminar BedTube Controller v0.0.1 de tu computadora?" },
                { "keep", "Mantener mis ajustes de audio y móviles vinculados" },
                { "uninstall", "Desinstalar Ahora" },
                { "cancel", "Cancelar" },
                { "working", "Eliminando archivos..." },
                { "done", "BedTube Controller se eliminó correctamente." }
            }}
        };

        public UninstallWindow(string installDir)
        {
            _installDir = installDir;
            string iso = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();
            _lang = T.ContainsKey(iso) ? iso : "pt";

            Width = 480;
            Height = 290;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            Background = Brushes.Transparent;
            ResizeMode = ResizeMode.NoResize;
            Title = "Uninstall BedTube Controller";

            Border outer = new Border();
            outer.Margin = new Thickness(14);
            outer.CornerRadius = new CornerRadius(20);
            outer.Background = BrushHex("#0C0C14");
            outer.BorderBrush = BrushHex("#2A2A3C");
            outer.BorderThickness = new Thickness(1);
            outer.Effect = new DropShadowEffect { BlurRadius = 24, ShadowDepth = 6, Opacity = 0.65, Color = Colors.Black };
            outer.MouseLeftButtonDown += delegate(object s, MouseButtonEventArgs e) { if (e.ButtonState == MouseButtonState.Pressed) DragMove(); };

            StackPanel root = new StackPanel { Margin = new Thickness(24, 20, 24, 20) };

            DockPanel top = new DockPanel { Margin = new Thickness(0, 0, 0, 14) };
            StackPanel langPanel = new StackPanel { Orientation = Orientation.Horizontal };
            DockPanel.SetDock(langPanel, Dock.Right);
            foreach (string code in new string[] { "pt", "en", "es" })
            {
                string c = code;
                Button lb = MakeFlatButton(c.ToUpperInvariant(), "#1A1A28", "#F4F4F6", 34, 24);
                lb.Margin = new Thickness(4, 0, 0, 0);
                lb.FontSize = 11;
                lb.Click += delegate { _lang = c; RefreshText(); };
                langPanel.Children.Add(lb);
            }
            top.Children.Add(langPanel);

            _lblTitle = new TextBlock { FontSize = 18, FontWeight = FontWeights.Bold, Foreground = BrushHex("#F4F4F6") };
            top.Children.Add(_lblTitle);
            root.Children.Add(top);

            _lblDesc = new TextBlock { FontSize = 13.5, Foreground = BrushHex("#A1A1B5"), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 18) };
            root.Children.Add(_lblDesc);

            _chkKeep = new CheckBox { IsChecked = true, Foreground = BrushHex("#E4E4EB"), FontSize = 13, Margin = new Thickness(0, 0, 0, 26) };
            root.Children.Add(_chkKeep);

            Grid btns = new Grid();
            btns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            btns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(12) });
            btns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.2, GridUnitType.Star) });

            _btnCancel = MakeFlatButton("", "#1B1B28", "#D4D4DF", 0, 42);
            _btnCancel.Click += delegate { Close(); };
            Grid.SetColumn(_btnCancel, 0);
            btns.Children.Add(_btnCancel);

            _btnUninstall = MakeFlatButton("", "#E11D48", "#FFFFFF", 0, 42);
            _btnUninstall.Click += OnUninstallClick;
            Grid.SetColumn(_btnUninstall, 2);
            btns.Children.Add(_btnUninstall);

            root.Children.Add(btns);
            outer.Child = root;
            Content = outer;

            RefreshText();
        }

        private void RefreshText()
        {
            Dictionary<string, string> d = T[_lang];
            _lblTitle.Text = d["title"];
            _lblDesc.Text = d["desc"];
            _chkKeep.Content = "  " + d["keep"];
            _btnCancel.Content = d["cancel"];
            _btnUninstall.Content = d["uninstall"];
        }

        private async void OnUninstallClick(object sender, RoutedEventArgs e)
        {
            _btnUninstall.IsEnabled = false;
            _btnCancel.IsEnabled = false;
            bool keep = _chkKeep.IsChecked == true;
            _lblDesc.Text = T[_lang]["working"];

            await Task.Run(delegate { UninstallEngine.RunUninstall(_installDir, keep); });

            MessageBox.Show(T[_lang]["done"], "BedTube Controller", MessageBoxButton.OK, MessageBoxImage.Information);
            Close();
        }

        private static SolidColorBrush BrushHex(string hex)
        {
            return (SolidColorBrush)new BrushConverter().ConvertFromString(hex);
        }

        private static Button MakeFlatButton(string text, string bgHex, string fgHex, double width, double height)
        {
            Button b = new Button();
            b.Content = text;
            if (width > 0) b.Width = width;
            b.Height = height;
            b.Foreground = BrushHex(fgHex);
            b.Background = BrushHex(bgHex);
            b.BorderThickness = new Thickness(0);
            b.FontWeight = FontWeights.SemiBold;
            b.Cursor = Cursors.Hand;

            FrameworkElementFactory border = new FrameworkElementFactory(typeof(Border));
            border.SetValue(Border.BackgroundProperty, new TemplateBindingExtension(Control.BackgroundProperty));
            border.SetValue(Border.CornerRadiusProperty, new CornerRadius(11));
            FrameworkElementFactory cp = new FrameworkElementFactory(typeof(ContentPresenter));
            cp.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center);
            cp.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
            border.AppendChild(cp);

            ControlTemplate tpl = new ControlTemplate(typeof(Button));
            tpl.VisualTree = border;
            b.Template = tpl;
            return b;
        }
    }
}

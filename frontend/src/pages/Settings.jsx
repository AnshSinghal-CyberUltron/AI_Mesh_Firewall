import { useState } from 'react';
import { Card, CardContent } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { useTheme } from '../context/ThemeContext';
import { Palette, Bell, ShieldCheck, Info, Sun, Moon, Monitor } from 'lucide-react';

function ToggleSwitch({ checked, onChange }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
        checked ? 'bg-teal-600' : 'bg-slate-300 dark:bg-slate-600'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white dark:bg-slate-800 transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

function getNotificationPref(key, fallback = false) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

export function Settings() {
  const { theme, setTheme } = useTheme();
  const [desktopNotifications, setDesktopNotifications] = useState(() => getNotificationPref('zeroshield_desktop_notifs'));
  const [soundNotifications, setSoundNotifications] = useState(() => getNotificationPref('zeroshield_sound_notifs'));

  const handleDesktopNotifToggle = (val) => {
    if (val && 'Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
    setDesktopNotifications(val);
    localStorage.setItem('zeroshield_desktop_notifs', JSON.stringify(val));
  };

  const handleSoundNotifToggle = (val) => {
    setSoundNotifications(val);
    localStorage.setItem('zeroshield_sound_notifs', JSON.stringify(val));
  };

  const themeOptions = [
    { value: 'light', label: 'Light', icon: Sun },
    { value: 'dark', label: 'Dark', icon: Moon },
    { value: 'system', label: 'System', icon: Monitor },
  ];

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Settings</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Manage your application preferences</p>
      </div>

      {/* Appearance */}
      <Card>
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-purple-100 dark:bg-purple-800/30 dark:bg-purple-900/50 flex items-center justify-center">
              <Palette className="w-4 h-4 text-purple-600 dark:text-purple-400" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Appearance</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">Customize how ZeroShield looks</p>
            </div>
          </div>
        </div>
        <CardContent>
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-3">Theme</label>
          <div className="grid grid-cols-3 gap-3">
            {themeOptions.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setTheme(opt.value)}
                className={`flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all ${
                  theme === opt.value
                    ? 'border-teal-500 bg-teal-50 dark:bg-teal-900/20'
                    : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:border-slate-600 dark:hover:border-slate-600'
                }`}
              >
                <opt.icon className={`w-5 h-5 ${theme === opt.value ? 'text-teal-600 dark:text-teal-400' : 'text-slate-500 dark:text-slate-400'}`} />
                <span className={`text-sm font-medium ${theme === opt.value ? 'text-teal-700 dark:text-teal-400' : 'text-slate-700 dark:text-slate-300'}`}>
                  {opt.label}
                </span>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Notifications */}
      <Card>
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-amber-100 dark:bg-amber-800/30 dark:bg-amber-900/50 flex items-center justify-center">
              <Bell className="w-4 h-4 text-amber-600 dark:text-amber-400" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Notifications</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">Configure notification preferences</p>
            </div>
          </div>
        </div>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Desktop Notifications</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">Receive browser notifications for security alerts</p>
            </div>
            <ToggleSwitch checked={desktopNotifications} onChange={handleDesktopNotifToggle} />
          </div>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Sound Notifications</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">Play a sound when new notifications arrive</p>
            </div>
            <ToggleSwitch checked={soundNotifications} onChange={handleSoundNotifToggle} />
          </div>
        </CardContent>
      </Card>

      {/* Session & Security */}
      <Card>
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-teal-100 dark:bg-teal-800/30 dark:bg-teal-900/50 flex items-center justify-center">
              <ShieldCheck className="w-4 h-4 text-teal-600 dark:text-teal-400" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Session & Security</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">Manage session and security settings</p>
            </div>
          </div>
        </div>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Inactivity Timeout</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">Automatically log out after inactivity</p>
            </div>
            <span className="text-sm font-medium text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-700 px-3 py-1 rounded-lg">15 minutes</span>
          </div>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Session Status</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">Your current authentication status</p>
            </div>
            <span className="flex items-center gap-2 text-sm font-medium text-green-700 dark:text-green-400">
              <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
              Active
            </span>
          </div>
        </CardContent>
      </Card>

      {/* About */}
      <Card>
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-blue-100 dark:bg-blue-800/30 dark:bg-blue-900/50 flex items-center justify-center">
              <Info className="w-4 h-4 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">About</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">Application information</p>
            </div>
          </div>
        </div>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-600 dark:text-slate-400">Application</span>
            <span className="text-sm font-medium text-slate-900 dark:text-slate-100">ZeroShield</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-600 dark:text-slate-400">Version</span>
            <span className="text-sm font-medium text-slate-900 dark:text-slate-100">0.0.0</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-600 dark:text-slate-400">API Docs</span>
            <a
              href={`${window.location.origin.replace(/:\d+$/, ':8100')}/docs/`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-teal-600 dark:text-teal-400 hover:underline"
            >
              Open API Documentation
            </a>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

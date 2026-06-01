import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Card, CardContent } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { ChangePasswordForm } from '../components/ChangePasswordForm';
import { User, Mail, Shield, Building2, ShieldCheck, Settings2 } from 'lucide-react';

export function Profile() {
  const { user, fetchWithAuth, setUser } = useAuth();
  const [profileForm, setProfileForm] = useState({
    first_name: user?.first_name || '',
    last_name: user?.last_name || '',
    email: user?.email || '',
    current_password: '',
  });
  const [preferencesForm, setPreferencesForm] = useState({
    theme: user?.preferences?.theme || 'system',
    email_notifications: user?.preferences?.email_notifications ?? true,
    security_alerts: user?.preferences?.security_alerts ?? true,
  });
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [profileMessage, setProfileMessage] = useState({ type: '', text: '' });
  const [prefsMessage, setPrefsMessage] = useState({ type: '', text: '' });

  const displayName = user ? [user.first_name, user.last_name].filter(Boolean).join(' ') : '';
  const initials = user
    ? [user.first_name, user.last_name]
        .filter(Boolean)
        .map((n) => n[0])
        .join('')
        .toUpperCase() || (user.email?.[0]?.toUpperCase() ?? 'U')
    : 'U';

  const emailChanged = useMemo(
    () => profileForm.email.trim().toLowerCase() !== (user?.email || '').toLowerCase(),
    [profileForm.email, user?.email]
  );

  useEffect(() => {
    if (!user) return;
    setProfileForm((prev) => ({
      ...prev,
      first_name: user.first_name || '',
      last_name: user.last_name || '',
      email: user.email || '',
      current_password: '',
    }));
    setPreferencesForm({
      theme: user?.preferences?.theme || 'system',
      email_notifications: user?.preferences?.email_notifications ?? true,
      security_alerts: user?.preferences?.security_alerts ?? true,
    });
  }, [
    user?.id,
    user?.first_name,
    user?.last_name,
    user?.email,
    user?.preferences?.theme,
    user?.preferences?.email_notifications,
    user?.preferences?.security_alerts,
  ]);

  useEffect(() => {
    if (!emailChanged && profileForm.current_password) {
      setProfileForm((prev) => ({ ...prev, current_password: '' }));
    }
  }, [emailChanged, profileForm.current_password]);

  const profileDirty = useMemo(() => {
    return (
      profileForm.first_name !== (user?.first_name || '') ||
      profileForm.last_name !== (user?.last_name || '') ||
      profileForm.email !== (user?.email || '') ||
      profileForm.current_password.length > 0
    );
  }, [profileForm, user]);

  const prefsDirty = useMemo(() => {
    return (
      preferencesForm.theme !== (user?.preferences?.theme || 'system') ||
      preferencesForm.email_notifications !== (user?.preferences?.email_notifications ?? true) ||
      preferencesForm.security_alerts !== (user?.preferences?.security_alerts ?? true)
    );
  }, [preferencesForm, user]);

  async function saveProfile(e) {
    e.preventDefault();
    setProfileMessage({ type: '', text: '' });
    if (!profileForm.first_name.trim()) {
      setProfileMessage({ type: 'error', text: 'First name is required.' });
      return;
    }
    if (emailChanged && !profileForm.current_password) {
      setProfileMessage({ type: 'error', text: 'Current password is required to change email.' });
      return;
    }

    const payload = {
      first_name: profileForm.first_name.trim(),
      last_name: profileForm.last_name.trim(),
    };
    if (emailChanged) {
      payload.email = profileForm.email.trim().toLowerCase();
      payload.current_password = profileForm.current_password;
    }

    setSavingProfile(true);
    try {
      const res = await fetchWithAuth('/api/auth/me/profile/', {
        method: 'PATCH',
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const firstFieldError = Object.values(data || {}).find((value) => Array.isArray(value));
        setProfileMessage({
          type: 'error',
          text:
            data.detail ||
            (Array.isArray(firstFieldError) ? firstFieldError[0] : null) ||
            'Failed to update profile details.',
        });
        return;
      }
      setUser(data);
      setProfileForm((prev) => ({ ...prev, current_password: '' }));
      setProfileMessage({ type: 'success', text: 'Profile details updated successfully.' });
    } catch {
      setProfileMessage({ type: 'error', text: 'Network error while updating profile details.' });
    } finally {
      setSavingProfile(false);
    }
  }

  async function savePreferences(e) {
    e.preventDefault();
    setPrefsMessage({ type: '', text: '' });
    setSavingPrefs(true);
    try {
      const res = await fetchWithAuth('/api/auth/me/profile/', {
        method: 'PATCH',
        body: JSON.stringify({ preferences: preferencesForm }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const firstFieldError = Object.values(data || {}).find((value) => Array.isArray(value));
        setPrefsMessage({
          type: 'error',
          text:
            data.detail ||
            (Array.isArray(firstFieldError) ? firstFieldError[0] : null) ||
            'Failed to update preferences.',
        });
        return;
      }
      setUser(data);
      setPrefsMessage({ type: 'success', text: 'Preferences updated successfully.' });
    } catch {
      setPrefsMessage({ type: 'error', text: 'Network error while updating preferences.' });
    } finally {
      setSavingPrefs(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Profile Header Card */}
      <Card>
        <CardContent className="p-8">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start">
            <div className="w-20 h-20 bg-gradient-to-br from-teal-500 to-cyan-600 rounded-2xl flex items-center justify-center shadow-lg flex-shrink-0">
              <span className="text-2xl font-bold text-white">{initials}</span>
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                {displayName || 'User'}
              </h1>
              <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">{user?.email}</p>
              <div className="flex flex-wrap gap-2 mt-3">
                {user?.is_superuser && (
                  <Badge variant="warning">Superuser</Badge>
                )}
                {(user?.roles || []).map((role) => (
                  <Badge key={role} variant="info">{role}</Badge>
                ))}
                {user?.is_active && (
                  <Badge variant="success">Active</Badge>
                )}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Account Details */}
      <Card>
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Account Details</h2>
        </div>
        <CardContent className="p-0">
          <div className="divide-y divide-slate-100 dark:divide-slate-700">
            <div className="flex items-center gap-4 px-6 py-4">
              <div className="w-9 h-9 rounded-lg bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
                <User className="w-4 h-4 text-slate-600 dark:text-slate-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-500 dark:text-slate-400">Full Name</p>
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  {displayName || 'Not set'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-4 px-6 py-4">
              <div className="w-9 h-9 rounded-lg bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
                <Mail className="w-4 h-4 text-slate-600 dark:text-slate-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-500 dark:text-slate-400">Email Address</p>
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  {user?.email || 'Not set'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-4 px-6 py-4">
              <div className="w-9 h-9 rounded-lg bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
                <Shield className="w-4 h-4 text-slate-600 dark:text-slate-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-500 dark:text-slate-400">Roles</p>
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {(user?.roles || ['user']).map((role) => (
                    <Badge key={role} variant="outline" className="text-xs">{role}</Badge>
                  ))}
                </div>
              </div>
            </div>
            {user?.organization && (
              <div className="flex items-center gap-4 px-6 py-4">
                <div className="w-9 h-9 rounded-lg bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
                  <Building2 className="w-4 h-4 text-slate-600 dark:text-slate-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-slate-500 dark:text-slate-400">Organization</p>
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                    {user.organization.name}
                  </p>
                </div>
              </div>
            )}
            <div className="flex items-center gap-4 px-6 py-4">
              <div className="w-9 h-9 rounded-lg bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
                <ShieldCheck className="w-4 h-4 text-slate-600 dark:text-slate-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-slate-500 dark:text-slate-400">Account Status</p>
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  {user?.is_active ? 'Active' : 'Inactive'}
                  {user?.is_superuser && ' (Superuser)'}
                </p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Edit Profile</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Update your name and email. Email changes require current password verification.
            </p>
          </div>
          <CardContent>
            <form onSubmit={saveProfile} className="space-y-4">
              {profileMessage.text && (
                <div
                  className={`rounded-lg border px-4 py-3 text-sm ${
                    profileMessage.type === 'success'
                      ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400'
                      : 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400'
                  }`}
                >
                  {profileMessage.text}
                </div>
              )}
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">First Name</label>
                  <input
                    type="text"
                    value={profileForm.first_name}
                    onChange={(e) => setProfileForm((prev) => ({ ...prev, first_name: e.target.value }))}
                    required
                    className="w-full rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">Last Name</label>
                  <input
                    type="text"
                    value={profileForm.last_name}
                    onChange={(e) => setProfileForm((prev) => ({ ...prev, last_name: e.target.value }))}
                    className="w-full rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">Email</label>
                <input
                  type="email"
                  value={profileForm.email}
                  onChange={(e) => setProfileForm((prev) => ({ ...prev, email: e.target.value }))}
                  required
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                />
              </div>
              {emailChanged && (
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">Current Password</label>
                  <input
                    type="password"
                    value={profileForm.current_password}
                    onChange={(e) => setProfileForm((prev) => ({ ...prev, current_password: e.target.value }))}
                    required
                    className="w-full rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                    placeholder="Required to confirm email change"
                  />
                </div>
              )}
              <Button type="submit" disabled={savingProfile || !profileDirty} className="bg-gradient-to-r from-teal-600 to-cyan-600 text-white hover:from-teal-700 hover:to-cyan-700">
                {savingProfile ? 'Saving...' : 'Save Profile'}
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card>
          <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
            <div className="flex items-center gap-2">
              <Settings2 className="h-4 w-4 text-slate-500 dark:text-slate-400" />
              <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Preferences</h2>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Manage your personal experience defaults.</p>
          </div>
          <CardContent>
            <form onSubmit={savePreferences} className="space-y-4">
              {prefsMessage.text && (
                <div
                  className={`rounded-lg border px-4 py-3 text-sm ${
                    prefsMessage.type === 'success'
                      ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400'
                      : 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400'
                  }`}
                >
                  {prefsMessage.text}
                </div>
              )}
              <div>
                <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">Theme Preference</label>
                <select
                  value={preferencesForm.theme}
                  onChange={(e) => setPreferencesForm((prev) => ({ ...prev, theme: e.target.value }))}
                  className="w-full rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-slate-900 focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-100"
                >
                  <option value="system">System</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </div>
              <label className="flex items-center gap-3 rounded-lg border border-slate-200 px-4 py-3 text-sm text-slate-700 dark:border-slate-700 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={preferencesForm.email_notifications}
                  onChange={(e) => setPreferencesForm((prev) => ({ ...prev, email_notifications: e.target.checked }))}
                  className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
                />
                Email Notifications
              </label>
              <label className="flex items-center gap-3 rounded-lg border border-slate-200 px-4 py-3 text-sm text-slate-700 dark:border-slate-700 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={preferencesForm.security_alerts}
                  onChange={(e) => setPreferencesForm((prev) => ({ ...prev, security_alerts: e.target.checked }))}
                  className="h-4 w-4 rounded border-slate-300 text-teal-600 focus:ring-teal-500"
                />
                Security Alerts
              </label>
              <Button type="submit" disabled={savingPrefs || !prefsDirty} className="bg-gradient-to-r from-teal-600 to-cyan-600 text-white hover:from-teal-700 hover:to-cyan-700">
                {savingPrefs ? 'Saving...' : 'Save Preferences'}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>

      {/* Change Password */}
      <Card>
        <div className="px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Change Password</h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Update your password to keep your account secure</p>
        </div>
        <CardContent>
          <ChangePasswordForm />
        </CardContent>
      </Card>
    </div>
  );
}

import { useAuth } from '../context/AuthContext';
import { Card, CardContent } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { ChangePasswordForm } from '../components/ChangePasswordForm';
import { User, Mail, Shield, Building2, Clock, ShieldCheck } from 'lucide-react';

export function Profile() {
  const { user } = useAuth();

  const displayName = user ? [user.first_name, user.last_name].filter(Boolean).join(' ') : '';
  const initials = user
    ? [user.first_name, user.last_name]
        .filter(Boolean)
        .map((n) => n[0])
        .join('')
        .toUpperCase() || (user.email?.[0]?.toUpperCase() ?? 'U')
    : 'U';

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Profile Header Card */}
      <Card>
        <CardContent className="p-8">
          <div className="flex items-start gap-6">
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

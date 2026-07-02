import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  Shield,
  Lock,
  Zap,
  Eye,
  EyeOff,
  Mail,
  ArrowRight,
  ExternalLink,
  Activity,
  FileCheck,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/ui/Button';

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from?.pathname || '/';
  const signupSuccess = location.state?.signupSuccess;
  const signupEmail = location.state?.email;

  useEffect(() => {
    if (isAuthenticated) navigate(from, { replace: true });
  }, [isAuthenticated, from, navigate]);

  if (isAuthenticated) return null;

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setNotice('');
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err.message || 'Login failed.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen lg:h-screen lg:overflow-hidden bg-slate-50 dark:bg-slate-950">
      <div className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-teal-50/80 via-slate-50 to-cyan-100/70 dark:from-teal-950/40 dark:via-slate-950 dark:to-cyan-950/40" />
        <div className="absolute -top-24 left-1/3 h-80 w-80 rounded-full bg-teal-300/20 blur-3xl dark:bg-teal-500/10" />
        <div className="absolute bottom-0 -right-16 h-80 w-80 rounded-full bg-cyan-400/20 blur-3xl dark:bg-cyan-500/10" />
      </div>

      <div className="relative mx-auto grid min-h-screen lg:h-screen w-full max-w-[1480px] grid-cols-1 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="hidden lg:flex flex-col justify-between border-r border-slate-200/70 dark:border-slate-800 px-10 py-8 xl:px-14">
          <div className="inline-flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-teal-600 to-cyan-600 shadow-lg">
              <Shield className="h-6 w-6 text-white" />
            </div>
            <div>
              <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">ZeroShield</p>
              <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">AI Mesh Firewall</p>
            </div>
          </div>

          <div className="space-y-6">
            <div className="space-y-3">
              <p className="inline-flex items-center rounded-full border border-teal-200/70 bg-teal-50 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-teal-700 dark:border-teal-800 dark:bg-teal-900/30 dark:text-teal-300">
                Enterprise-Grade AI Security
              </p>
              <h1 className="text-3xl font-bold leading-tight text-slate-900 dark:text-slate-100 xl:text-4xl">
                Secure your <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-600 to-cyan-600">LLM infrastructure</span> with policy-first control.
              </h1>
              <p className="max-w-xl text-sm text-slate-600 dark:text-slate-300">
                Manage enforcement, monitor threats, and enforce zero-trust MCP controls from one operator-focused platform built for production workloads.
              </p>
            </div>

            <div className="grid gap-3">
              {[
                {
                  icon: Shield,
                  title: 'AI Mesh Firewall',
                  text: 'Protect prompts, context windows, and model outputs across multi-provider LLM traffic.',
                },
                {
                  icon: Eye,
                  title: 'Real-time Threat Intelligence',
                  text: 'Detect policy violations, injection attempts, and suspicious tool usage patterns instantly.',
                },
                {
                  icon: Lock,
                  title: 'MCP Policy Guardrails',
                  text: 'Apply role-aware restrictions, redaction controls, and rule-based execution governance.',
                },
              ].map(({ icon: Icon, title, text }) => (
                <div key={title} className="flex items-start gap-3 rounded-xl border border-slate-200/70 bg-white/70 p-3 dark:border-slate-800 dark:bg-slate-900/40">
                  <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-teal-100 dark:bg-teal-900/40">
                    <Icon className="h-4.5 w-4.5 text-teal-700 dark:text-teal-300" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</p>
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-600 dark:text-slate-400">{text}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {[
              { icon: Activity, label: 'Uptime', value: '99.99%' },
              { icon: FileCheck, label: 'Compliance', value: 'SOC2 / ISO' },
              { icon: Shield, label: 'Protected', value: '10M+ calls' },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} className="rounded-xl border border-slate-200/70 bg-white/70 p-2.5 dark:border-slate-800 dark:bg-slate-900/40">
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-teal-600 dark:text-teal-300" />
                  <p className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
                </div>
                <p className="mt-2 text-sm font-semibold text-slate-900 dark:text-slate-100">{value}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="flex items-center justify-center px-5 py-8 sm:px-8 lg:px-10 lg:py-6">
          <div className="w-full max-w-xl space-y-4">
            <div className="lg:hidden flex items-center justify-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-teal-600 to-cyan-600 shadow-lg">
                <Shield className="h-6 w-6 text-white" />
              </div>
              <div>
                <p className="text-base font-semibold text-slate-900 dark:text-slate-100">ZeroShield</p>
                <p className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">AI Mesh Firewall</p>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200/80 bg-white/90 p-6 shadow-xl shadow-slate-200/60 backdrop-blur dark:border-slate-800 dark:bg-slate-900/70 dark:shadow-black/20 sm:p-8">
              <div className="mb-5">
                <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Sign in to ZeroShield</h1>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  Access policy management, threat telemetry, and enterprise firewall controls.
                </p>
              </div>

              <div className="mb-5 space-y-3">
                {/*
                  Social sign-in is not enabled for production yet.
                  Keeping the markup here (commented) makes it easy to re-enable when backend + providers are ready.
                */}
                {/*
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <button
                    type="button"
                    onClick={() => handleSocialSignin('google')}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden="true" focusable="false">
                      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.24 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A10.96 10.96 0 0 0 1 12c0 1.77.42 3.44 1.18 4.93l2.86-2.84z" />
                      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                    </svg>
                    Continue with Google
                  </button>
                  <button
                    type="button"
                    onClick={() => handleSocialSignin('github')}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    <Github className="h-5 w-5" />
                    Continue with GitHub
                  </button>
                </div>
                */}
                <div className="flex items-center gap-3">
                  <span className="h-px flex-1 bg-slate-200 dark:bg-slate-700" />
                  <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">operator login</span>
                  <span className="h-px flex-1 bg-slate-200 dark:bg-slate-700" />
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                {signupSuccess && (
                  <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/25 dark:text-emerald-300">
                    Sign-up request submitted for {signupEmail || 'your account'}. Sign in after your account is approved.
                  </div>
                )}
                {notice && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
                    {notice}
                  </div>
                )}
                {error && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                    {error}
                  </div>
                )}
                <div>
                  <label htmlFor="email" className="mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-300">
                    Work email
                  </label>
                  <div className="relative">
                    <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                      id="email"
                      type="email"
                      autoComplete="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      required
                      className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 pl-10 pr-4 text-slate-900 placeholder:text-slate-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500"
                      placeholder="operator@company.com"
                    />
                  </div>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Use your approved organization account.</p>
                </div>
                <div>
                  <label htmlFor="password" className="mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-300">
                    Password
                  </label>
                  <div className="relative">
                    <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input
                      id="password"
                      type={showPassword ? 'text' : 'password'}
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 pl-10 pr-11 text-slate-900 placeholder:text-slate-400 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-teal-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500"
                      placeholder="Enter your password"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((prev) => !prev)}
                      className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-teal-500 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                      aria-label={showPassword ? 'Hide password' : 'Show password'}
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>
                <Button
                  type="submit"
                  disabled={submitting}
                  className="h-11 w-full bg-gradient-to-r from-teal-600 to-cyan-600 text-white hover:from-teal-700 hover:to-cyan-700"
                >
                  {submitting ? 'Signing in...' : 'Sign in'}
                  {!submitting ? <ArrowRight className="ml-1 h-4 w-4" /> : null}
                </Button>
                {/*
                  Self-serve signup is not enabled for production yet.
                  Accounts are provisioned/approved by an administrator.
                */}
                {/*
                <p className="text-center text-sm text-slate-600 dark:text-slate-300">
                  Don&apos;t have an account?{' '}
                  <Link to="/signup" className="font-semibold text-teal-600 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-300">
                    Sign up
                  </Link>
                </p>
                */}
              </form>

              <div className="mt-5 border-t border-slate-200 pt-4 dark:border-slate-700">
                <a
                  href="#support"
                  className="inline-flex items-center gap-1.5 text-sm text-slate-600 transition-colors hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
                >
                  Need assistance? Contact support
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              </div>
            </div>

            <div className="lg:hidden grid grid-cols-3 gap-3">
              {[
                { icon: Activity, label: 'Uptime', value: '99.99%' },
                { icon: FileCheck, label: 'Compliance', value: 'SOC2' },
                { icon: Shield, label: 'Protected', value: '10M+' },
              ].map(({ icon: Icon, label, value }) => (
                <div key={label} className="rounded-xl border border-slate-200/70 bg-white/85 p-3 dark:border-slate-800 dark:bg-slate-900/60">
                  <div className="flex items-center gap-1.5">
                    <Icon className="h-3.5 w-3.5 text-teal-600 dark:text-teal-300" />
                    <p className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
                  </div>
                  <p className="mt-1.5 text-xs font-semibold text-slate-900 dark:text-slate-100">{value}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}


import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, ArrowRight, CalendarClock, Check, Copy, Eye, EyeOff, Inbox, Layers3, Send } from 'lucide-react';
import Registration from './Registration';
import { api, type User } from './session';
import './sign-in.css';

const accountCommands = {
  compose: 'python scripts/compose_local.py run --rm users create admin --role admin',
  kind: 'bash scripts/users.sh create admin --role admin',
};
type AccountOptions = { registration: boolean; demo_username: string | null };
const accountDefaults: AccountOptions = { registration: false, demo_username: null };

export default function SignIn({ onSignIn }: { onSignIn: (user: User) => void }) {
  const registrationPage = window.location.pathname === '/register';
  const [options, setOptions] = useState<AccountOptions | null>(null);
  const [setup, setSetup] = useState(registrationPage);
  const [deployment, setDeployment] = useState<keyof typeof accountCommands>('compose');
  const [copyStatus, setCopyStatus] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);
  const managedRegistration = registrationPage && options?.registration === false;
  const demoSelected = Boolean(options?.demo_username && username.trim().toLowerCase() === options.demo_username);

  useEffect(() => {
    const request = new AbortController();
    api<AccountOptions>('/auth/options', { signal: request.signal }).then(setOptions)
      .catch(() => { if (!request.signal.aborted) setOptions(accountDefaults); });
    return () => request.abort();
  }, []);

  useEffect(() => {
    heading.current?.focus();
  }, [setup, managedRegistration]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      const user = await api<User>('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, code: demoSelected ? '' : code }),
      });
      onSignIn(user);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Sign-in failed. Try again.');
    } finally {
      setPassword('');
      setShowPassword(false);
      setCode('');
      setBusy(false);
    }
  }

  function navigate(openSetup: boolean) {
    setSetup(openSetup);
    setError('');
    setCopyStatus('');
  }

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(accountCommands[deployment]);
      setCopyStatus('Command copied.');
    } catch {
      setCopyStatus('Select the command to copy it.');
    }
  }

  return <div className="access-layout">
    <div className="access-brand">
      <div className="access-logo"><span><Send size={19} aria-hidden="true" /></span>EmailFlow</div>
      <div className="access-intro">
        <p className="access-tagline">Schedule emails.<br />Track delivery.</p>
        <p>Test email campaigns with local, simulated delivery.</p>
        <div className="access-workflow" aria-hidden="true">
          <span><CalendarClock size={18} /> Schedule</span>
          <span><Layers3 size={18} /> Queue</span>
          <span><Inbox size={18} /> Receipts</span>
        </div>
      </div>
    </div>
    <main className="access-main">
      <div className="access-content">
        {registrationPage && options === null ? <p role="status">Loading account setup…</p>
          : registrationPage && options?.registration ? <Registration onSignIn={onSignIn} /> : <>
        <div className="access-heading">
          <h1 ref={heading} tabIndex={-1}>{setup ? 'Set up an account' : 'Sign in'}</h1>
            <p>{setup ? 'Create your first administrator from the terminal where EmailFlow is running.'
            : demoSelected ? 'Enter the password for the local demo account.'
              : 'Enter your account details and the code from your authenticator app.'}</p>
        </div>
        {setup ? <>
          <ol className="access-steps">
            <li>
              <label className="access-field" htmlFor="setup-deployment">Local deployment
                <select id="setup-deployment" value={deployment} onChange={event => {
                  setDeployment(event.target.value === 'kind' ? 'kind' : 'compose');
                  setCopyStatus('');
                }}>
                  <option value="compose">Docker Compose</option>
                  <option value="kind">Kind (Kubernetes)</option>
                </select>
              </label>
              <div className="access-command">
                <code>{accountCommands[deployment]}</code>
                <button type="button" aria-label="Copy account setup command" onClick={() => void copyCommand()}>
                  {copyStatus === 'Command copied.' ? <Check size={17} /> : <Copy size={17} />}
                </button>
              </div>
              {copyStatus && <p className="access-copy-status" role="status">{copyStatus}</p>}
            </li>
            <li>Run the command from the repository root. Choose a password of at least 15 characters.</li>
            <li>Add the secret printed in your terminal to your authenticator app, then sign in as <strong>admin</strong>.</li>
          </ol>
          <p className="access-help">Joining an existing deployment? Ask its administrator to create your account.</p>
          <button className="access-link" type="button" onClick={() => navigate(false)}>
            <ArrowLeft size={16} aria-hidden="true" /> Back to sign in
          </button>
        </> : <>
          <form className="access-form" onSubmit={submit} aria-busy={busy}>
            <label className="access-field" htmlFor="sign-in-username">Username
              <input id="sign-in-username" name="username" autoComplete="username" autoCapitalize="none"
                spellCheck={false} required maxLength={128} value={username} onChange={event => setUsername(event.target.value)} />
            </label>
            <div className="access-field">
              <label htmlFor="sign-in-password">Password</label>
              <div className="access-password">
                <input ref={passwordInput} id="sign-in-password" name="password" type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password" required maxLength={256} value={password}
                  onChange={event => setPassword(event.target.value)} />
                <button type="button" aria-label={showPassword ? 'Hide password' : 'Show password'}
                  onClick={() => setShowPassword(visible => !visible)}>
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>
            {!demoSelected && <label className="access-field" htmlFor="sign-in-code">Authenticator code
              <input id="sign-in-code" name="code" inputMode="numeric" autoComplete="one-time-code"
                pattern="[0-9]{6}" required placeholder="6-digit code" value={code}
                onChange={event => setCode(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))} />
            </label>}
            {error && <p className="access-error" role="alert">{error}</p>}
            <button className="access-submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}
              <ArrowRight size={17} aria-hidden="true" />
            </button>
          </form>
          <div className="access-footer">
            <span>First time using EmailFlow?</span>
            {options?.registration ? <a className="access-link" href="/register">
              Create an account <ArrowRight size={15} aria-hidden="true" />
            </a> : <button className="access-link" type="button" disabled={busy} onClick={() => navigate(true)}>
              Account setup <ArrowRight size={15} aria-hidden="true" />
            </button>}
          </div>
          <div className="access-secondary">
            {options?.demo_username && !demoSelected && <button className="access-link" type="button" disabled={busy} onClick={() => {
              setUsername(options.demo_username ?? '');
              setPassword('');
              setCode('');
              setError('');
              passwordInput.current?.focus();
            }}>Use demo account</button>}
            {options?.registration && <button type="button" className="access-link" disabled={busy}
              onClick={() => navigate(true)}>Administrator setup</button>}
          </div>
        </>}
        </>}
      </div>
    </main>
  </div>;
}

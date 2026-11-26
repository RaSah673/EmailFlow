import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { api, type User } from './session';

type Enrollment = { enrollment_token: string; secret: string };

export default function Registration({ onSignIn }: { onSignIn: (user: User) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => { heading.current?.focus(); }, [enrollment]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enrollment && password !== confirmation) {
      setError('Passwords must match.');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const request = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
      if (enrollment) {
        const user = await api<User>('/auth/register/verify', {
          ...request, body: JSON.stringify({ enrollment_token: enrollment.enrollment_token, code }),
        });
        window.history.replaceState(null, '', '/');
        onSignIn(user);
      } else {
        const next = await api<Enrollment>('/auth/register', {
          ...request, body: JSON.stringify({ email, password }),
        });
        setPassword('');
        setConfirmation('');
        setEnrollment(next);
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Registration failed. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return <>
    <div className="access-heading">
      <h1 ref={heading} tabIndex={-1}>{enrollment ? 'Set up your authenticator' : 'Create an account'}</h1>
      <p>{enrollment ? 'Add this setup key to your authenticator app, then enter the code it generates.'
        : 'Create a local account to schedule campaigns and review delivery.'}</p>
    </div>
    <form className="access-form" onSubmit={submit} aria-busy={busy}>
      {enrollment ? <>
        <label className="access-field" htmlFor="registration-key">Setup key
          <input id="registration-key" value={enrollment.secret} readOnly onFocus={event => event.target.select()} />
        </label>
        <label className="access-field" htmlFor="registration-code">Authenticator code
          <input id="registration-code" inputMode="numeric" autoComplete="one-time-code" required
            pattern="[0-9]{6}" placeholder="6-digit code" value={code}
            onChange={event => setCode(event.target.value.replace(/[^0-9]/g, '').slice(0, 6))} />
        </label>
      </> : <>
        <label className="access-field" htmlFor="registration-email">Email address
          <input id="registration-email" type="email" autoComplete="username" autoCapitalize="none"
            maxLength={128} required value={email} onChange={event => setEmail(event.target.value)} />
        </label>
        <label className="access-field" htmlFor="registration-password">Password
          <input id="registration-password" type="password" autoComplete="new-password" required
            minLength={15} maxLength={256} value={password} onChange={event => setPassword(event.target.value)} />
          <small>Use at least 15 characters.</small>
        </label>
        <label className="access-field" htmlFor="registration-confirm">Confirm password
          <input id="registration-confirm" type="password" autoComplete="new-password" required
            maxLength={256} value={confirmation} onChange={event => setConfirmation(event.target.value)} />
        </label>
      </>}
      {error && <p className="access-error" role="alert">{error}</p>}
      <button className="access-submit" disabled={busy}>
        {busy ? 'Working…' : enrollment ? 'Verify and create account' : 'Continue'}
        <ArrowRight size={17} aria-hidden="true" />
      </button>
    </form>
    <div className="access-footer">
      {enrollment ? <button className="access-link" type="button" disabled={busy} onClick={() => {
        setEnrollment(null);
        setCode('');
        setError('');
      }}><ArrowLeft size={16} aria-hidden="true" /> Back to registration</button>
        : <>Already have an account? <a className="access-link" href="/">Sign in</a></>}
    </div>
  </>;
}

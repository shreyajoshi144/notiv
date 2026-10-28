const card = document.querySelector('#auth-card');
const mode = location.pathname;
const params = new URLSearchParams(location.search);
const resetToken = mode === '/reset-password' ? (params.get('token') || '') : '';
const safeNext = value => typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') &&
  !value.includes('\\') && !/[\r\n]/.test(value) && (value === '/app' || value.startsWith('/app/')) ? value : '/app';
const next = safeNext(params.get('next'));
const reason = params.get('reason');
if (mode === '/reset-password' && location.search) history.replaceState(null, '', '/reset-password');

function node(tag, attrs = {}, ...children) {
  const item = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'text') item.textContent = value;
    else if (key === 'className') item.className = value;
    else if (key in item && key !== 'list') item[key] = value;
    else item.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child) item.append(child);
  return item;
}
function message(text, type = 'error') {
  const alert = document.querySelector('#alert');
  if (!alert) return;
  alert.replaceChildren(node('div', { className: `auth-alert ${type}`, text: String(text) }));
}
function field(id, labelText, type, autocomplete, options = {}) {
  const label = node('label', { htmlFor: id, text: labelText });
  const input = node('input', { id, name: id, type, autocomplete, required: true });
  if (options.minlength) input.minLength = options.minlength;
  if (options.maxlength) input.maxLength = options.maxlength;
  if (type === 'email') input.inputMode = 'email';
  const inner = node('div', { className: type === 'password' ? 'auth-password' : '' }, input);
  if (type === 'password') {
    const toggle = node('button', { type: 'button', className: 'auth-toggle', text: 'Show', 'aria-label': `Show ${labelText.toLowerCase()}` });
    toggle.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      toggle.textContent = show ? 'Hide' : 'Show';
      toggle.setAttribute('aria-label', `${show ? 'Hide' : 'Show'} ${labelText.toLowerCase()}`);
    });
    inner.append(toggle);
  }
  const wrap = node('div', { className: 'auth-field' }, label, inner);
  if (type === 'password') {
    const caps = node('p', { className: 'auth-hint', id: `${id}-caps`, text: 'Caps Lock is on.', hidden: true });
    const check = e => { if (e.getModifierState) caps.hidden = !e.getModifierState('CapsLock'); };
    input.addEventListener('keydown', check); input.addEventListener('keyup', check);
    input.addEventListener('blur', () => { caps.hidden = true; });
    wrap.append(caps);
    if (options.meter) {
      const meter = node('div', { className: 'auth-meter', 'aria-hidden': 'true' }, node('i'), node('i'), node('i'), node('i'));
      const said = node('p', { className: 'auth-meter-t', id: `${id}-strength`, 'aria-live': 'polite' });
      input.setAttribute('aria-describedby', `${id}-strength`);
      input.addEventListener('input', () => {
        const v = input.value, kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].filter(r => r.test(v)).length;
        const score = !v ? 0 : v.length < 12 ? 1 : (v.length >= 16 && kinds >= 2) || kinds >= 3 ? (v.length >= 20 || kinds === 4 ? 4 : 3) : 2;
        meter.dataset.s = String(score);
        said.textContent = !v ? '' : v.length < 12 ? `${12 - v.length} more characters needed` : ['', 'Too short', 'Okay — longer is stronger', 'Strong', 'Very strong'][score];
      });
      wrap.append(meter, said);
    }
  }
  return wrap;
}
function link(href, text) { return node('a', { href, text }); }

function render() {
  let title, sub, fields, button, links, kind;
  if (mode === '/register') {
    kind = 'register'; title = 'Make room to think.';
    sub = 'Create your Notiv account and keep your learning in one place.';
    fields = [field('name', 'Your name', 'text', 'name', { maxlength: 120 }),
      field('email', 'Email address', 'email', 'email', { maxlength: 254 }),
      field('password', 'Password', 'password', 'new-password', { minlength: 12, maxlength: 128, meter: true }),
      field('confirmation', 'Confirm password', 'password', 'new-password', { minlength: 12, maxlength: 128 })];
    button = 'Create account'; links = [link('/sign-in', 'Already have an account? Sign in'), link('/forgot-password', 'Forgot password?')];
  } else if (mode === '/forgot-password') {
    kind = 'forgot'; title = 'Reset your password.';
    sub = 'We’ll send a one-time reset link if an account matches that email.';
    fields = [field('email', 'Email address', 'email', 'email', { maxlength: 254 })];
    button = 'Send reset link'; links = [link('/sign-in', 'Back to sign in'), link('/register', 'Create an account')];
  } else if (mode === '/reset-password') {
    kind = 'reset'; title = 'Choose a new password.';
    sub = 'Reset links expire after 30 minutes and can only be used once.';
    fields = [field('password', 'New password', 'password', 'new-password', { minlength: 12, maxlength: 128, meter: true }),
      field('confirmation', 'Confirm new password', 'password', 'new-password', { minlength: 12, maxlength: 128 })];
    button = 'Update password'; links = [link('/sign-in', 'Back to sign in')];
  } else {
    kind = 'login'; title = 'Welcome back.';
    sub = 'Sign in to return to your notebooks and learning.';
    fields = [field('email', 'Email address', 'email', 'email', { maxlength: 254 }),
      field('password', 'Password', 'password', 'current-password', { maxlength: 128 })];
    button = 'Sign in'; links = [link('/forgot-password', 'Forgot password?'), link('/register', 'Create an account')];
  }

  const seg = (kind === 'login' || kind === 'register') ? node('nav', { className: 'auth-seg', 'aria-label': 'Account' },
    node('a', { href: '/sign-in' + location.search, text: 'Sign in', ...(kind === 'login' ? { 'aria-current': 'page' } : {}) }),
    node('a', { href: '/register' + location.search, text: 'Create account', ...(kind === 'register' ? { 'aria-current': 'page' } : {}) })) : null;
  const heading = node('h1', { text: title });
  const description = node('p', { className: 'auth-sub', text: sub });
  const alert = node('div', { id: 'alert', role: 'status', 'aria-live': 'polite' });
  const form = node('form', { id: 'auth-form', noValidate: true }, node('div', { className: 'auth-group' }, ...fields));
  const submitButton = node('button', { className: 'auth-btn', type: 'submit', text: button });
  form.append(submitButton);
  const navigation = node('nav', { className: 'auth-links', 'aria-label': 'Account navigation' }, ...links);
  const content = [seg, heading, description, alert].filter(Boolean);
  content.push(form, navigation);
  card.replaceChildren(...content);
  form.addEventListener('submit', submit);
}

async function call(path, data) {
  const response = await fetch(path, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  let dataOut = {};
  try { dataOut = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(typeof dataOut.detail === 'string' ? dataOut.detail : (dataOut.detail?.message || 'Request failed. Try again.'));
  return dataOut;
}
async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const button = form.querySelector('button[type="submit"]');
  const values = Object.fromEntries(new FormData(form));
  if (values.password && values.password !== values.confirmation && (mode === '/register' || mode === '/reset-password')) {
    message('The passwords do not match.'); return;
  }
  if (values.password && values.password.length < 12) { message('Use at least 12 characters for your password.'); return; }
  button.disabled = true; button.textContent = 'Please wait…';
  try {
    if (mode === '/register') {
      await call('/auth/register', values);
      location.replace(next);
    } else if (mode === '/forgot-password') {
      const result = await call('/auth/password/forgot', values);
      message(result.message || 'If an account exists, reset instructions will be sent.', 'success');
    } else if (mode === '/reset-password') {
      const result = await call('/auth/password/reset', { ...values, token: resetToken });
      message(result.message || 'Password updated. Sign in with your new password.', 'success');
      setTimeout(() => location.replace('/sign-in'), 900);
    } else {
      await call('/auth/password/login', values);
      location.replace(next);
    }
  } catch (error) {
    message(error.message || 'Something went wrong. Please try again.');
  } finally {
    button.disabled = false;
    button.textContent = mode === '/register' ? 'Create account' : mode === '/forgot-password' ? 'Send reset link' : mode === '/reset-password' ? 'Update password' : 'Sign in';
  }
}
async function handleExistingCookieSession() {
  if (!['/sign-in', '/register'].includes(mode)) return false;
  try {
    const response = await fetch('/auth/password/me', { credentials: 'same-origin', cache: 'no-store' });
    if (response.ok) { location.replace(next); return true; }
  } catch (_) {}
  return false;
}
(async () => {
  if (await handleExistingCookieSession()) return;
  render();
  if (reason === 'expired') message('Your session expired. Please sign in again.', 'error');
  else if (reason === 'signed-out') message('You are signed out.', 'success');
})();

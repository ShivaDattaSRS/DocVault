/* Login / register + SMTP OTP verification flow. */
(() => {
  if (API.getToken()) { window.location.href = '/dashboard'; return; }

  const el = (id) => document.getElementById(id);
  const credentialsStep = el('credentials-step');
  const otpStep = el('otp-step');
  const loginForm = el('login-form');
  const registerForm = el('register-form');
  const otpForm = el('otp-form');
  const otpBoxes = [...el('otp-inputs').querySelectorAll('input')];

  let pendingEmail = '';
  let resendTimer = null;

  function alertIn(container, message, type = 'error') {
    container.innerHTML = message ? `<div class="alert ${type}">${UI.escape(message)}</div>` : '';
  }

  function busy(button, isBusy, label) {
    button.disabled = isBusy;
    if (isBusy) {
      button.dataset.label = button.textContent;
      button.innerHTML = '<span class="spinner"></span>';
    } else {
      button.textContent = label || button.dataset.label || button.textContent;
    }
  }

  /* --------------------------------------------------------------- tabs */
  document.querySelectorAll('.tabs button').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tabs button').forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      const isLogin = tab.dataset.tab === 'login';
      loginForm.classList.toggle('hidden', !isLogin);
      registerForm.classList.toggle('hidden', isLogin);
      alertIn(el('auth-alert'), '');
    });
  });

  /* ------------------------------------------------------------ OTP step */
  function showOtpStep(email, response) {
    pendingEmail = email;
    el('otp-email-label').textContent = email;
    credentialsStep.classList.add('hidden');
    otpStep.classList.remove('hidden');
    otpBoxes.forEach((b) => { b.value = ''; });
    otpBoxes[0].focus();

    // When SMTP isn't configured the server returns the code so dev can proceed.
    if (response && response.dev_otp) {
      alertIn(el('otp-alert'), `SMTP unavailable — development code: ${response.dev_otp}`, 'info');
    } else {
      alertIn(el('otp-alert'), response ? response.message : '', 'success');
    }
    startResendCooldown(30);
  }

  function startResendCooldown(seconds) {
    const button = el('otp-resend');
    const timer = el('otp-timer');
    clearInterval(resendTimer);
    let left = seconds;
    button.disabled = true;
    timer.textContent = `(${left}s)`;
    resendTimer = setInterval(() => {
      left -= 1;
      timer.textContent = left > 0 ? `(${left}s)` : '';
      if (left <= 0) { clearInterval(resendTimer); button.disabled = false; }
    }, 1000);
  }

  otpBoxes.forEach((box, index) => {
    box.addEventListener('input', () => {
      box.value = box.value.replace(/\D/g, '').slice(0, 1);
      if (box.value && index < otpBoxes.length - 1) otpBoxes[index + 1].focus();
      if (otpBoxes.every((b) => b.value)) otpForm.requestSubmit();
    });
    box.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !box.value && index > 0) otpBoxes[index - 1].focus();
      if (e.key === 'ArrowLeft' && index > 0) otpBoxes[index - 1].focus();
      if (e.key === 'ArrowRight' && index < otpBoxes.length - 1) otpBoxes[index + 1].focus();
    });
    box.addEventListener('paste', (e) => {
      e.preventDefault();
      const digits = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
      digits.split('').forEach((d, i) => { if (otpBoxes[i]) otpBoxes[i].value = d; });
      if (digits.length === 6) otpForm.requestSubmit();
      else otpBoxes[Math.min(digits.length, 5)].focus();
    });
  });

  /* --------------------------------------------------------------- forms */
  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const button = el('login-submit');
    const email = el('login-email').value.trim().toLowerCase();
    alertIn(el('auth-alert'), '');
    busy(button, true);
    try {
      const res = await API.post('/api/auth/login', { email, password: el('login-password').value });
      showOtpStep(email, res);
    } catch (err) {
      alertIn(el('auth-alert'), err.message);
    } finally {
      busy(button, false, 'Continue');
    }
  });

  registerForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const button = el('register-submit');
    const email = el('reg-email').value.trim().toLowerCase();
    alertIn(el('auth-alert'), '');
    busy(button, true);
    try {
      const res = await API.post('/api/auth/register', {
        email,
        full_name: el('reg-name').value.trim(),
        password: el('reg-password').value,
      });
      showOtpStep(email, res);
    } catch (err) {
      alertIn(el('auth-alert'), err.message);
    } finally {
      busy(button, false, 'Create account');
    }
  });

  otpForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = otpBoxes.map((b) => b.value).join('');
    if (code.length !== 6) { alertIn(el('otp-alert'), 'Enter all six digits'); return; }

    const button = el('otp-submit');
    busy(button, true);
    try {
      const res = await API.post('/api/auth/verify-otp', { email: pendingEmail, code });
      API.setSession(res.access_token, res.user);
      window.location.href = res.user.is_admin ? '/admin' : '/dashboard';
    } catch (err) {
      alertIn(el('otp-alert'), err.message);
      otpBoxes.forEach((b) => { b.value = ''; });
      otpBoxes[0].focus();
    } finally {
      busy(button, false, 'Verify & sign in');
    }
  });

  el('otp-resend').addEventListener('click', async () => {
    try {
      const res = await API.post('/api/auth/resend-otp', { email: pendingEmail });
      if (res.dev_otp) alertIn(el('otp-alert'), `SMTP unavailable — development code: ${res.dev_otp}`, 'info');
      else alertIn(el('otp-alert'), res.message, 'success');
      startResendCooldown(30);
    } catch (err) {
      alertIn(el('otp-alert'), err.message);
    }
  });

  el('otp-back').addEventListener('click', () => {
    clearInterval(resendTimer);
    otpStep.classList.add('hidden');
    credentialsStep.classList.remove('hidden');
    alertIn(el('auth-alert'), '');
  });
})();

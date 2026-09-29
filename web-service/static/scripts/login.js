/* Passo intermédio de diagnóstico — ver comentário em dashboard.js. */
const AUTH_BASE = "/api/auth";

/* === Redireciona para dashboard se já autenticado === */
if (localStorage.getItem('access_token')) {
    window.location.replace('/dashboard');
}

/* === Auth: toggle de visibilidade da password === */
document.querySelectorAll('.pwd-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
        const input = btn.parentElement.querySelector('input');
        if (!input) return;
        const isPwd = input.type === 'password';
        input.type = isPwd ? 'text' : 'password';
        btn.classList.toggle('is-visible', isPwd);
        btn.setAttribute('aria-label', isPwd ? 'Ocultar password' : 'Mostrar password');
    });
});

/* === Auth: medidor de força da password === */
const regPwd = document.getElementById('regPwd');
const strengthBar = document.querySelector('.pwd-strength');
const strengthLabel = document.querySelector('.pwd-strength-label');
const STRENGTH_TEXT = [
    'Use letras, números e um símbolo para uma password forte',
    'Password fraca',
    'Password razoável',
    'Password boa',
    'Password forte'
];

if (regPwd && strengthBar) {
    regPwd.addEventListener('input', () => {
        const v = regPwd.value;
        let score = 0;
        if (v.length >= 8) score++;
        if (/[A-Z]/.test(v) && /[a-z]/.test(v)) score++;
        if (/[0-9]/.test(v)) score++;
        if (/[^A-Za-z0-9]/.test(v)) score++;
        if (v.length === 0) score = 0;

        strengthBar.dataset.strength = score;
        if (strengthLabel) strengthLabel.textContent = STRENGTH_TEXT[score];
    });
}

/* === Auth: mostrar erro no formulário === */
function showAuthError(form, message) {
    let errEl = form.querySelector('.auth-error');
    if (!errEl) {
        errEl = document.createElement('p');
        errEl.className = 'auth-error';
        errEl.style.cssText = 'color:#ef4444;font-size:14px;margin-top:8px;text-align:center;';
        form.appendChild(errEl);
    }
    errEl.textContent = message;
}

/* === Auth: submit com chamada real à API === */
['loginForm', 'registerForm'].forEach(id => {
    const form = document.getElementById(id);
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!form.checkValidity()) { form.reportValidity(); return; }

        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.classList.add('is-loading');

        // Limpa erro anterior
        const prev = form.querySelector('.auth-error');
        if (prev) prev.textContent = '';

        try {
            let url, body;

            if (id === 'loginForm') {
                url = `${AUTH_BASE}/login`;
                body = {
                    email: form.querySelector('[name="email"]').value.trim(),
                    senha: form.querySelector('[name="password"]').value,
                };
            } else {
                url = `${AUTH_BASE}/criar_conta`;
                body = {
                    nome: form.querySelector('[name="name"]').value.trim(),
                    email: form.querySelector('[name="email"]').value.trim(),
                    senha: form.querySelector('[name="password"]').value,
                    nome_empresa: form.querySelector('[name="company"]').value.trim(),
                    whatsapp: form.querySelector('[name="whatsapp"]').value.trim(),
                };
            }

            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include', // necessário para receber o cookie HttpOnly do refresh token
                body: JSON.stringify(body),
            });

            const data = await res.json();

            if (!res.ok) {
                showAuthError(form, data.detail || 'Ocorreu um erro. Tente novamente.');
                return;
            }

            // O refresh_token já não vem no corpo da resposta — é entregue via cookie
            // HttpOnly/Secure/SameSite=Strict pelo próprio auth-service, inacessível a JS.
            localStorage.setItem('access_token', data.access_token);
            window.location.href = '/dashboard';

        } catch (err) {
            showAuthError(form, 'Não foi possível ligar ao servidor. Verifique a sua ligação.');
        } finally {
            if (submitBtn) submitBtn.classList.remove('is-loading');
        }
    });
});

/* === Auth: ano dinâmico (versão multi-element) === */
document.querySelectorAll('.year').forEach(el => {
    el.textContent = new Date().getFullYear();
});

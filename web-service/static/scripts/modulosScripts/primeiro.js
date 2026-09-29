export default function primeiro(){
    (() => {
    'use strict';

    const header = document.getElementById('siteHeader');
    const navToggle = document.getElementById('navToggle');
    const navMenu = document.getElementById('navMenu');
    const heroStats = document.getElementById('heroStats');

    /* Header shadow / border quando faz scroll */
    const handleScroll = () => {
        if (window.scrollY > 16) header.classList.add('is-scrolled');
        else header.classList.remove('is-scrolled');
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll();

    /* Toggle do menu mobile */
    if (navToggle && navMenu) {
        navToggle.addEventListener('click', () => {
            const isOpen = navMenu.classList.toggle('is-open');
            navToggle.classList.toggle('is-open', isOpen);
            navToggle.setAttribute('aria-expanded', String(isOpen));
            navToggle.setAttribute('aria-label', isOpen ? 'Fechar menu' : 'Abrir menu');
        });

        navMenu.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                navMenu.classList.remove('is-open');
                navToggle.classList.remove('is-open');
                navToggle.setAttribute('aria-expanded', 'false');
            });
        });
    }

    /* Counter animation */
    const animateCount = (el, target, duration = 1600) => {
        const start = performance.now();
        const tick = (now) => {
            const progress = Math.min((now - start) / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            const value = target < 10
                ? (target * eased).toFixed(target % 1 === 0 ? 0 : 1)
                : Math.round(target * eased);
            el.textContent = value;
            if (progress < 1) requestAnimationFrame(tick);
            else el.textContent = target;
        };
        requestAnimationFrame(tick);
    };

    /* Reveal das stats + counters quando entram na viewport */
    if (heroStats && 'IntersectionObserver' in window) {
        const statsObserver = new IntersectionObserver((entries, obs) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    heroStats.querySelectorAll('.stat').forEach(stat => stat.classList.add('is-visible'));
                    heroStats.querySelectorAll('[data-count-to]').forEach(el => {
                        const target = parseFloat(el.dataset.countTo);
                        if (!isNaN(target)) animateCount(el, target);
                    });
                    obs.unobserve(entry.target);
                }
            });
        }, { threshold: 0.35 });
        statsObserver.observe(heroStats);
    }

    /* Smooth scroll para âncoras */
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', (e) => {
            const href = anchor.getAttribute('href');
            if (!href || href === '#' || href.length < 2) return;
            const target = document.querySelector(href);
            if (target) {
                e.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });

    /* Parallax leve nos cards quando mexes o rato sobre o hero */
    const heroVisual = document.querySelector('.hero-visual');
    if (heroVisual && window.matchMedia('(hover: hover)').matches) {
        const cards = heroVisual.querySelectorAll('.float-card');
        heroVisual.addEventListener('mousemove', (e) => {
            const rect = heroVisual.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width - 0.5;
            const y = (e.clientY - rect.top) / rect.height - 0.5;
            cards.forEach((card, i) => {
                const depth = (i + 1) * 6;
                card.style.setProperty('--mx', `${x * depth}px`);
                card.style.setProperty('--my', `${y * depth}px`);
                card.style.transform = `translate(${x * depth}px, ${y * depth}px)`;
            });
        });
        heroVisual.addEventListener('mouseleave', () => {
            cards.forEach(card => { card.style.transform = ''; });
        });
    }
})();
}
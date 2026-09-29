/* Pop dos features quando o plano entra na vista */
const plans = document.querySelectorAll('.plan');
if (plans.length && 'IntersectionObserver' in window) {
    const planObs = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('is-revealed');
                obs.unobserve(entry.target);
            }
        });
    }, { threshold: 0.25 });
    plans.forEach(p => planObs.observe(p));
} else {
    plans.forEach(p => p.classList.add('is-revealed'));
}
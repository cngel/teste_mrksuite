export default function scroll(){
    /* Reveal genérico on-scroll */
const revealEls = document.querySelectorAll('[data-reveal]');
if (revealEls.length && 'IntersectionObserver' in window) {
    const revealObs = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('is-visible');
                obs.unobserve(entry.target);
            }
        });
    }, { threshold: 0.15, rootMargin: '0px 0px -60px 0px' });
    revealEls.forEach(el => revealObs.observe(el));
} else {
    revealEls.forEach(el => el.classList.add('is-visible'));
}
}
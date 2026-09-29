export default function competition(){
    /* Stagger nas rows da tabela comparativa */
const compareRows = document.querySelectorAll('[data-row]');
if (compareRows.length && 'IntersectionObserver' in window) {
    const tableObs = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const rows = entry.target.parentElement.querySelectorAll('[data-row]');
                rows.forEach((row, i) => {
                    row.style.setProperty('--row-delay', `${i * 0.07}s`);
                    row.classList.add('is-visible');
                });
                obs.unobserve(entry.target);
            }
        });
    }, { threshold: 0.2 });
    if (compareRows[0]) tableObs.observe(compareRows[0]);
} else {
    compareRows.forEach(r => r.classList.add('is-visible'));
}
}
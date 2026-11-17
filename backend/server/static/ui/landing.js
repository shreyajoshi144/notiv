// Landing top bar: turns solid once the hero scrolls away and marks the section in view.
const bar = document.querySelector(".lp-top"), hero = document.querySelector(".hero");
if (bar && hero) {
  new IntersectionObserver(([e]) => bar.classList.toggle("solid", !e.isIntersecting),
    { rootMargin: `-${bar.offsetHeight}px 0px 0px 0px` }).observe(hero);
  const links = new Map([...bar.querySelectorAll('nav a[href^="#"]')].map(a => [a.hash.slice(1), a]));
  const seen = new Set();
  const io = new IntersectionObserver(es => {
    es.forEach(e => e.isIntersecting ? seen.add(e.target.id) : seen.delete(e.target.id));
    const cur = [...links.keys()].filter(id => seen.has(id)).pop();
    links.forEach((a, id) => id === cur ? a.setAttribute("aria-current", "true") : a.removeAttribute("aria-current"));
  }, { rootMargin: "-40% 0px -55% 0px" });
  links.forEach((_, id) => { const s = document.getElementById(id); if (s) io.observe(s); });
}

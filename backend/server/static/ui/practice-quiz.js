// AI practice quiz: pick a lecture, describe a study goal, and get a multiple-choice quiz written from that
// lecture's own evidence (POST /lectures/{id}/ai-quiz). Every question links back to its source in the lecture.
import { h, clear, api, mountShell, toast, explain, notice, announce } from "/static/ui/core.js";
import { openBank } from "/static/ui/question-bank.js";
import { citeLink } from "/static/ui/render.js";

const qs = new URLSearchParams(location.search);
let main, lectures = [], quiz = null, answers = [];

async function boot() {
  main = await mountShell("practice");
  clear(main, h("div", { class: "skeleton block" }));
  try { lectures = ((await api("/lectures?limit=200")).items || []); }
  catch (e) { clear(main, notice("err", h("p", { text: explain(e, "load your lectures") }), h("button", { class: "btn", type: "button", onclick: boot }, "Try again"))); return; }
  form();
}

function bank(lecture) {
  openBank(main, lectures, lecture || quiz?.lecture_id || qs.get("lecture"), lid => form({lecture: lid}), result => {quiz = result; answers = quiz.questions.map(() => null); render();});
}

function head() {
  return h("div", { class: "pq-head" }, h("p", { class: "eyebrow faint mono", text: "Practice" }), h("h1", { text: "A quiz for what you want to learn" }),
    h("p", { class: "lede", text: "Pick a lecture and tell Notiv what you’re working on. The quiz is written only from that lecture, and every answer links to where it was said or shown." }),
    h("div", {class:"actions"}, h("button", {class:"btn primary",type:"button", "aria-current":"page"}, "AI quiz"), h("button", {class:"btn",type:"button",onclick:() => bank(main.querySelector("#pq-lec")?.value)}, "My question bank")));
}

function form(prev = {}) {
  const ready = lectures.filter(l => l.status === "done");
  if (!ready.length) {
    clear(main, h("div", { class: "pq" }, head(), h("div", { class: "empty" }, h("h2", { text: "No processed lecture yet" }),
      h("p", { text: "Upload a recording (MP3 or MP4) with its slides (PDF or PPTX). Once it’s processed you can make a practice quiz from it." }),
      h("div", { class: "actions" }, h("a", { class: "btn primary", href: "/app" }, "Go to your desk")))));
    return;
  }
  const want = Number(prev.lecture || qs.get("lecture"));
  const sel = h("select", { id: "pq-lec", required: true }, ...ready.map(l => h("option", { value: l.id, selected: l.id === want, text: l.title || `Lecture ${l.id}` })));
  const count = h("select", { id: "pq-n" }, ...[3, 5, 8, 10].map(n => h("option", { value: n, selected: n === (prev.count || 5), text: `${n} questions` })));
  const goal = h("textarea", { id: "pq-goal", rows: 3, minlength: 3, maxlength: 300, required: true, placeholder: "e.g. I keep mixing up the learning rate and regularization — test me on when each one matters." });
  goal.value = prev.goal || "";
  const err = h("p", { class: "err", id: "pq-err", hidden: true });
  const btn = h("button", { class: "btn primary lg", type: "submit" }, "Make my quiz");
  const f = h("form", { class: "pq-form", novalidate: true, onsubmit: async e => {
      e.preventDefault();
      const g = goal.value.trim();
      if (g.length < 3) { err.textContent = "Describe your study goal in a few words."; err.hidden = false; goal.focus(); return; }
      err.hidden = true;
      await generate({ lecture: Number(sel.value), goal: g, count: Number(count.value) });
    } },
    h("div", { class: "field" }, h("label", { for: "pq-lec", text: "Lecture" }), sel),
    h("div", { class: "field" }, h("label", { for: "pq-n", text: "Length" }), count),
    h("div", { class: "field goal" }, h("label", { for: "pq-goal", text: "Your study goal" }), goal, h("p", { class: "hint", text: "What do you want to understand or be tested on? Up to 300 characters." }), err),
    h("div", { class: "go" }, btn, h("span", { class: "faint small", text: "Written by AI from the lecture’s transcript and slides." })));
  clear(main, h("div", { class: "pq" }, head(), f));
}

async function generate(req) {
  clear(main, h("div", { class: "pq" }, head(), h("div", { class: "pq-wait", role: "status", text: "Reading the lecture and writing your quiz… this can take a little while." })));
  try {
    quiz = await api(`/lectures/${req.lecture}/ai-quiz`, { method: "POST", json: { goal: req.goal, count: req.count } });
    quiz.req = req; answers = quiz.questions.map(() => null);
    announce("Your quiz is ready."); render();
  } catch (e) {
    const msg = e.status === 429 ? ((e.detail && e.detail.message) || "Too many requests right now. Try again in a minute.") : explain(e, "write the quiz");
    form(req);
    main.querySelector(".pq-head").after(notice("err", h("p", { text: msg })));
    toast(msg, "err");
  }
}

function render() {
  const done = answers.filter(a => a !== null).length, right = answers.filter((a, i) => a === quiz.questions[i].correct_index).length;
  const lid = quiz.lecture_id;
  clear(main, h("div", { class: "pq" },
    h("div", { class: "pq-head" }, h("p", { class: "eyebrow faint mono", text: quiz.lecture_title || "Practice quiz" }), h("h1", { text: "Your practice quiz" }),
      h("p", { class: "lede", text: `Goal: ${quiz.goal}` })),
    h("div", { class: "pq-score", role: "status", "aria-live": "polite" },
      h("b", { text: done === quiz.questions.length ? `You got ${right} of ${quiz.questions.length}` : `${done} of ${quiz.questions.length} answered` }),
      h("span", null, h("button", { class: "btn sm", type: "button", onclick: () => quiz.manual ? bank(lid) : form(quiz.req) }, quiz.manual ? "Back to my bank" : "Change goal"), " ",
        h("button", { class: "btn sm", type: "button", onclick: () => quiz.manual ? (answers = quiz.questions.map(() => null), render()) : generate(quiz.req) }, quiz.manual ? "Try again" : "New quiz, same goal"))),
    ...quiz.questions.map((q, i) => question(q, i, lid)),
    quiz.dropped ? h("p", { class: "faint small", text: `${quiz.dropped} question${quiz.dropped === 1 ? " was" : "s were"} left out because ${quiz.dropped === 1 ? "it" : "they"} couldn’t be tied to the lecture.` }) : null,
    h("p", { class: "faint small", text: quiz.manual ? "Written by you." : "Written by AI from this lecture. Check anything important against the linked sources." })));
}

function question(q, i, lid) {
  const a = answers[i], answered = a !== null;
  return h("div", { class: "pq-q" + (answered ? " answered" : "") },
    h("fieldset", null, h("legend", null, h("span", { class: "pq-num", text: `Question ${i + 1}` }), q.question),
      ...q.options.map((o, j) => h("label", { class: "pq-opt" + (answered && j === q.correct_index ? " right" : "") + (answered && j === a && a !== q.correct_index ? " wrong" : "") },
        h("input", { type: "radio", name: `q${i}`, value: j, checked: a === j, disabled: answered, onchange: () => { answers[i] = j; render(); main.querySelectorAll(".pq-q")[i].querySelector("input:checked")?.focus(); } }),
        h("span", { text: o + (answered && j === q.correct_index ? "  ✓" : "") })))),
    answered ? h("div", { class: "pq-exp" },
      h("p", null, h("strong", { text: a === q.correct_index ? "Correct. " : "Not quite. " }), q.explanation),
      q.citations.length ? h("div", { class: "cites" }, h("span", { class: "faint small", text: "From the lecture:" }), ...q.citations.map(c => citeLink({ ...c, lecture_id: lid }))) : null) : null);
}

boot();

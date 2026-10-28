import { h, clear, api, explain, notice, toast, icon, confirmDialog } from '/static/ui/core.js';

export async function openBank(main, lectures, preferred, onAI, onPractice) {
  let lecture = Number(preferred) || lectures[0]?.id, items = [], editing = null, loading = false;
  if (!lecture) { clear(main, h('div', {class:'pq'}, h('h1',{text:'Your question bank'}), h('p',{text:'Add a lecture to start your bank.'}), h('a',{class:'btn primary',href:'/app'},'Go to your desk'))); return; }
  const path = () => `/lectures/${lecture}/question-bank`;
  async function load() {
    loading = true;
    draw();
    try { items = (await api(path())).items; loading = false; draw(); }
    catch(e) { loading = false; draw(); main.querySelector('.pq-head').after(notice('err',h('p',{text:explain(e,'load your question bank')}),h('button',{class:'btn',onclick:load},'Try again'))); }
  }
  function draw() {
    const select = h('select',{id:'bank-lecture',disabled:loading,onchange:() => {lecture=Number(select.value);items=[];editing=null;load();}},...lectures.map(l=>h('option',{value:l.id,selected:l.id===lecture,text:l.title || `Lecture ${l.id}`})));
    const root = h('div',{class:'pq'},h('div',{class:'pq-head'},h('p',{class:'eyebrow faint mono',text:'Practice'}),h('h1',{text:'Your question bank'}),h('div',{class:'actions'},h('button',{class:'btn',type:'button',onclick:()=>onAI(lecture)},'AI quiz'),h('button',{class:'btn primary',type:'button','aria-current':'page'},'My question bank'))),
      h('div',{class:'field'},h('label',{for:'bank-lecture',text:'Lecture'}),select),
      h('div',{class:'pq-score'},h('b',{text:loading?'Loading…':`${items.length} saved question${items.length===1?'':'s'}`}),h('button',{class:'btn primary',disabled:loading||!items.length,type:'button',onclick:()=>onPractice({lecture_id:lecture,lecture_title:lectures.find(l=>l.id===lecture)?.title,goal:'Your own questions',questions:items,manual:true})},icon('play'),'Practise my questions')));
    if (!loading) {
      root.append(editor());
      if (!items.length) root.append(h('p',{class:'empty',text:'No saved questions for this lecture yet.'}));
      for (const q of items) root.append(h('article',{class:'pq-q'},h('h2',{text:q.question}),h('p',{class:'faint small',text:'Written by you'}),
        h('ol',null,...q.options.map((o,i)=>h('li',{text:o+(i===q.correct_index?' — Correct answer':'')}))),q.explanation?h('p',{text:q.explanation}):null,
        h('div',{class:'actions'},h('button',{class:'btn sm',type:'button',title:'Edit question',onclick:()=>{editing=q;draw();main.querySelector('#bank-question').focus();}},icon('edit'),'Edit'),
          h('button',{class:'btn sm',type:'button',title:'Delete question',onclick:async e=>{const btn=e.currentTarget;if(!await confirmDialog({title:'Delete this question?',body:'It is removed from your question bank.',confirm:'Delete',danger:true}))return;btn.disabled=true;try{await api(`${path()}/${q.id}`,{method:'DELETE'});if(editing?.id===q.id)editing=null;await load();toast('Question deleted.');}catch(err){btn.disabled=false;toast(explain(err,'delete your question'),'err');}}},icon('trash'),'Delete'))));
    }
    clear(main,root);
  }
  function editor() {
    const q=h('textarea',{id:'bank-question',rows:2,required:true,maxlength:2000});q.value=editing?.question||'';
    const opts=Array.from({length:4},(_,i)=>{const x=h('input',{id:`bank-option-${i}`,required:true,maxlength:1000});x.value=editing?.options[i]||'';return x;});
    const correct=h('select',{id:'bank-correct'},...opts.map((_,i)=>h('option',{value:i,selected:i===(editing?.correct_index||0),text:`Answer ${i+1}`})));
    const exp=h('textarea',{id:'bank-explanation',rows:2,maxlength:4000});exp.value=editing?.explanation||'';
    const err=h('p',{class:'err',role:'alert',hidden:true});
    const submit=h('button',{class:'btn primary',type:'submit'},icon('plus'),editing?'Save changes':'Add question');
    const f=h('form',{class:'bank-editor',onsubmit:async e=>{e.preventDefault();err.hidden=true;const data={question:q.value.trim(),options:opts.map(o=>o.value.trim()),correct_index:Number(correct.value),explanation:exp.value.trim()};submit.disabled=true;try{await api(editing?`${path()}/${editing.id}`:path(),{method:editing?'PUT':'POST',json:data});editing=null;await load();toast('Question saved.');}catch(error){err.textContent=explain(error,'save your question');err.hidden=false;submit.disabled=false;}}},
      h('h2',{text:editing?'Edit question':'Add a question'}),h('div',{class:'field'},h('label',{for:q.id,text:'Question'}),q),
      h('div',{class:'bank-options'},...opts.map((o,i)=>h('div',{class:'field'},h('label',{for:o.id,text:`Answer ${i+1}`}),o))),
      h('div',{class:'field'},h('label',{for:correct.id,text:'Correct answer'}),correct),h('div',{class:'field'},h('label',{for:exp.id,text:'Explanation (optional)'}),exp),err,
      h('div',{class:'actions'},submit,editing?h('button',{class:'btn',type:'button',onclick:()=>{editing=null;draw();}},'Cancel'):null));
    return f;
  }
  await load();
}
